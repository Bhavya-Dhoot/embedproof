// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {Base} from "./Base.t.sol";
import {MockAttestation} from "./mocks/MockAttestation.sol";
import {ConsignmentAllocator} from "../src/ConsignmentAllocator.sol";
import {IConsignmentAllocator, Allocation, Status} from "../src/interfaces/IEmbedProof.sol";

/// @dev FR-C5 (cap, DoubleClaimBlocked), FR-C6 (consignment id uniqueness) and the double-claim and
///      front-running rows of the PRD section 9 STRIDE table.
contract ConsignmentAllocatorTest is Base {
    uint32 internal constant CN = 72083900;
    bytes32 internal constant C1 = keccak256("consignment-1");
    bytes32 internal constant C2 = keccak256("consignment-2");
    bytes32 internal constant C3 = keccak256("consignment-3");

    // ------------------------------------------------------ FR-C5: the cap

    function test_Allocate_FirstAllocation_UpdatesStateAndEmits() public {
        uint256 id = _greenBatch(5000);

        vm.expectEmit(true, true, true, true, address(allocator));
        emit IConsignmentAllocator.Allocated(id, C1, importerA, CN, 3000, 2000);
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 3000);

        assertEq(allocator.allocatedTonnes(id), 3000);
        assertEq(allocator.remainingTonnes(id), 2000);
        Allocation memory a = allocator.getAllocation(C1);
        assertEq(a.batchId, id);
        assertEq(a.importer, importerA);
        assertEq(a.cnCode, CN);
        assertEq(a.tonnes, 3000);
        assertFalse(a.claimed);
    }

    function test_Allocate_OverCapReverts_WithExactDoubleClaimBlockedArgs() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 3000);

        // Second claim asks for 2001 t but only 2000 t remain.
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, uint64(2001), uint64(2000)));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerB, CN, 2001);

        // Nothing moved: the rejected allocation left no trace.
        assertEq(allocator.allocatedTonnes(id), 3000);
        assertEq(allocator.remainingTonnes(id), 2000);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.UnknownConsignment.selector, C2));
        allocator.getAllocation(C2);

        // The blocked consignment id was not burnt: the same id works for an in-cap amount.
        _allocate(id, C2, importerB, 2000);
        assertEq(allocator.remainingTonnes(id), 0);
    }

    function test_Allocate_SingleAllocationAboveVerifiedReverts() public {
        uint256 id = _greenBatch(5000);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, uint64(5001), uint64(5000)));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 5001);
        assertEq(allocator.allocatedTonnes(id), 0);
    }

    function test_Allocate_ExactlyToCapSucceeds_ThenAnyMoreReverts() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 5000);
        assertEq(allocator.remainingTonnes(id), 0);

        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, uint64(1), uint64(0)));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerB, CN, 1);
    }

    function test_Allocate_TwoImportersCannotExceedTheSameBatch() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 3000);
        _allocate(id, C2, importerB, 2000);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, uint64(1), uint64(0)));
        vm.prank(exporterA);
        allocator.allocate(id, C3, importerA, CN, 1);
        assertEq(allocator.allocatedTonnes(id), 5000);
    }

    function test_Allocate_CapsAreIndependentPerBatch() public {
        uint256 b1 = _attest(EAF_ID, 202601, 10, 100);
        uint256 b2 = _attest(EAF_ID, 202602, 10, 100);
        _allocate(b1, C1, importerA, 100);
        // Batch 1 is exhausted, batch 2 untouched.
        assertEq(allocator.remainingTonnes(b1), 0);
        assertEq(allocator.remainingTonnes(b2), 100);
        _allocate(b2, C2, importerA, 100);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, b2, uint64(1), uint64(0)));
        vm.prank(exporterA);
        allocator.allocate(b2, C3, importerA, CN, 1);
    }

    function test_Allocate_Uint64VerifiedTonnes_CapMathNeverOverflows() public {
        uint256 id = _greenBatch(type(uint64).max);
        _allocate(id, C1, importerA, type(uint32).max);
        uint64 remaining = type(uint64).max - type(uint32).max;
        assertEq(allocator.remainingTonnes(id), remaining);

        // Over the cap: DoubleClaimBlocked wins over the packing guard, with exact args.
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, type(uint64).max, remaining));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerB, CN, type(uint64).max);

        // Within the cap but above the per-consignment limit: TonnesTooLarge.
        uint64 tooBig = uint64(type(uint32).max) + 1;
        vm.expectRevert(abi.encodeWithSelector(ConsignmentAllocator.TonnesTooLarge.selector, tooBig));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerB, CN, tooBig);
        assertEq(allocator.allocatedTonnes(id), type(uint32).max, "rejected calls left no trace");

        // A second maximal consignment is fine; the running total is uint64.
        _allocate(id, C2, importerB, type(uint32).max);
        assertEq(allocator.allocatedTonnes(id), uint64(type(uint32).max) * 2);
    }

    function test_Allocate_PackedRecordRoundTripsMaxValues() public {
        uint256 id = _greenBatch(type(uint64).max);
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, type(uint32).max, type(uint32).max);
        Allocation memory a = allocator.getAllocation(C1);
        assertEq(a.batchId, id);
        assertEq(a.importer, importerA);
        assertEq(a.cnCode, type(uint32).max);
        assertEq(a.tonnes, type(uint32).max);
        assertFalse(a.claimed);
    }

    function test_Allocate_TonnesJustAboveUint32WithinCap_Reverts() public {
        uint256 id = _greenBatch(type(uint64).max);
        uint64 tooBig = uint64(type(uint32).max) + 1;
        vm.expectRevert(abi.encodeWithSelector(ConsignmentAllocator.TonnesTooLarge.selector, tooBig));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, tooBig);
        assertEq(allocator.allocatedTonnes(id), 0);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.UnknownConsignment.selector, C1));
        allocator.getAllocation(C1);
    }

    function test_Allocate_BatchIdAboveUint32Reverts_AndMaxUint32Works() public {
        MockAttestation mock = new MockAttestation();
        ConsignmentAllocator impl = new ConsignmentAllocator();
        ConsignmentAllocator alloc2 = ConsignmentAllocator(
            address(
                new ERC1967Proxy(
                    address(impl), abi.encodeCall(ConsignmentAllocator.initialize, (address(registry), address(mock)))
                )
            )
        );
        mock.set(Status.Attested, 1000, EAF_ID, attestor);

        uint256 big = uint256(type(uint32).max) + 1;
        vm.expectRevert(abi.encodeWithSelector(ConsignmentAllocator.BatchIdTooLarge.selector, big));
        vm.prank(exporterA);
        alloc2.allocate(big, C1, importerA, CN, 1);
        vm.expectRevert(abi.encodeWithSelector(ConsignmentAllocator.BatchIdTooLarge.selector, type(uint256).max));
        vm.prank(exporterA);
        alloc2.allocate(type(uint256).max, C1, importerA, CN, 1);

        // Unknown (None) batches still report BatchNotAttested first, whatever their id.
        mock.set(Status.None, 0, bytes32(0), address(0));
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, big));
        vm.prank(exporterA);
        alloc2.allocate(big, C1, importerA, CN, 1);

        mock.set(Status.Attested, 1000, EAF_ID, attestor);
        vm.prank(exporterA);
        alloc2.allocate(type(uint32).max, C1, importerA, CN, 7);
        assertEq(alloc2.getAllocation(C1).batchId, type(uint32).max);
        assertEq(alloc2.allocatedTonnes(type(uint32).max), 7);
    }

    function test_Allocate_ZeroTonnesReverts() public {
        uint256 id = _greenBatch(5000);
        vm.expectRevert(IConsignmentAllocator.ZeroTonnes.selector);
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 0);
    }

    function test_Allocate_ZeroImporterReverts() public {
        uint256 id = _greenBatch(5000);
        vm.expectRevert(IConsignmentAllocator.ZeroImporter.selector);
        vm.prank(exporterA);
        allocator.allocate(id, C1, address(0), CN, 10);
    }

    // ------------------------------------------- FR-C6: consignment-id reuse

    function test_Allocate_ConsignmentIdReuseReverts() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 1000);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.ConsignmentReused.selector, C1));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 1000); // same everything
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.ConsignmentReused.selector, C1));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerB, 99999999, 1); // same id, different importer / tonnes
        assertEq(allocator.allocatedTonnes(id), 1000);
        assertEq(allocator.getAllocation(C1).importer, importerA, "original mapping is intact");
    }

    function test_Allocate_ConsignmentIdReuseAcrossBatchesReverts() public {
        uint256 b1 = _attest(EAF_ID, 202601, 10, 100);
        uint256 b2 = _attest(EAF_ID, 202602, 10, 100);
        _allocate(b1, C1, importerA, 10);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.ConsignmentReused.selector, C1));
        vm.prank(exporterA);
        allocator.allocate(b2, C1, importerA, CN, 10);
        assertEq(allocator.getAllocation(C1).batchId, b1, "one consignment id maps to exactly one batch");
        assertEq(allocator.allocatedTonnes(b2), 0);
    }

    function test_Allocate_ConsignmentIdStaysBurntAfterClaim() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 10);
        vm.prank(importerA);
        allocator.claim(C1);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.ConsignmentReused.selector, C1));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 10);
    }

    // ------------------------------------------------- batch status gating

    function test_Allocate_PendingBatchReverts_UntilVerifierApproves() public {
        uint256 id = _redBatch(250_000);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, id));
        vm.prank(exporterB);
        allocator.allocate(id, C1, importerA, CN, 100);

        _verify(id, true);
        vm.prank(exporterB);
        allocator.allocate(id, C1, importerA, CN, 100);
        assertEq(allocator.allocatedTonnes(id), 100);
    }

    function test_Allocate_RejectedBatchReverts() public {
        uint256 id = _redBatch(250_000);
        _verify(id, false);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, id));
        vm.prank(exporterB);
        allocator.allocate(id, C1, importerA, CN, 100);
    }

    function test_Allocate_UnknownBatchReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, uint256(0)));
        vm.prank(exporterA);
        allocator.allocate(0, C1, importerA, CN, 1);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, uint256(42)));
        vm.prank(exporterA);
        allocator.allocate(42, C1, importerA, CN, 1);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, type(uint256).max));
        vm.prank(exporterA);
        allocator.allocate(type(uint256).max, C1, importerA, CN, 1);
    }

    function test_Allocate_RejectedBatchStaysDeadAfterResubmission() public {
        uint256 bad = _redBatch(250_000);
        _verify(bad, false);
        uint256 good = _attest(BOF_ID, PERIOD, 20, 4_000);

        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, bad));
        vm.prank(exporterB);
        allocator.allocate(bad, C1, importerA, CN, 1);

        vm.prank(exporterB);
        allocator.allocate(good, C1, importerA, CN, 4_000);
        assertEq(allocator.allocatedTonnes(good), 4_000);
    }

    // --------------------------- operator-only allocation (front-running row)

    function test_Allocate_OnlyBatchOperator_ImporterCannotSelfAllocate() public {
        uint256 id = _greenBatch(5000);
        // Importer B sees exporter A's pending tx and tries to race it with a self-allocation.
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, importerB));
        vm.prank(importerB);
        allocator.allocate(id, C1, importerB, CN, 5000);
        assertEq(allocator.allocatedTonnes(id), 0);
    }

    function test_Allocate_OtherInstallationsOperatorCannotAllocate() public {
        uint256 id = _greenBatch(5000); // EAF batch, operator = exporterA
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, exporterB));
        vm.prank(exporterB); // holds OPERATOR_ROLE, but for the other installation
        allocator.allocate(id, C1, importerA, CN, 1);
    }

    function test_Allocate_PrivilegedNonOperatorsCannotAllocate() public {
        uint256 id = _greenBatch(5000);
        address[5] memory callers = [admin, attestor, verifier, regulator, stranger];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, callers[i]));
            vm.prank(callers[i]);
            allocator.allocate(id, C1, importerA, CN, 1);
        }
    }

    function test_Allocate_OperatorOfPendingBatchGetsBatchNotAttestedBeforeOperatorCheck() public {
        uint256 id = _redBatch(250_000);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, id));
        vm.prank(stranger);
        allocator.allocate(id, C1, importerA, CN, 1);
    }

    // ---------------------------------------------------------------- claim

    function test_Claim_OnlyConsignee_Once() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 1200);

        // Neither the other importer, the exporter nor the admin can claim A's consignment.
        address[4] memory callers = [importerB, exporterA, admin, stranger];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotConsignee.selector, C1, callers[i]));
            vm.prank(callers[i]);
            allocator.claim(C1);
        }
        assertFalse(allocator.getAllocation(C1).claimed);

        vm.expectEmit(true, true, true, true, address(allocator));
        emit IConsignmentAllocator.Claimed(id, C1, importerA);
        vm.prank(importerA);
        allocator.claim(C1);
        assertTrue(allocator.getAllocation(C1).claimed);

        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.AlreadyClaimed.selector, C1));
        vm.prank(importerA);
        allocator.claim(C1);
    }

    function test_Claim_UnknownConsignmentReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.UnknownConsignment.selector, C1));
        vm.prank(importerA);
        allocator.claim(C1);
    }

    function test_Claim_DoesNotChangeAllocatedTonnes() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 1200);
        vm.prank(importerA);
        allocator.claim(C1);
        assertEq(allocator.allocatedTonnes(id), 1200);
        assertEq(allocator.remainingTonnes(id), 3800);
    }

    function test_Claim_OnlyFlipsClaimedFlag_RecordUntouched() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 1200);
        Allocation memory before_ = allocator.getAllocation(C1);
        vm.prank(importerA);
        allocator.claim(C1);
        Allocation memory after_ = allocator.getAllocation(C1);
        assertTrue(after_.claimed);
        assertEq(after_.batchId, before_.batchId);
        assertEq(after_.importer, before_.importer);
        assertEq(after_.cnCode, before_.cnCode);
        assertEq(after_.tonnes, before_.tonnes);
    }

    function test_Claim_OtherConsignmentsUnaffected() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 100);
        _allocate(id, C2, importerA, 100);
        vm.prank(importerA);
        allocator.claim(C1);
        assertTrue(allocator.getAllocation(C1).claimed);
        assertFalse(allocator.getAllocation(C2).claimed);
    }

    // ---------------------------------------------------------------- views

    function test_Views_UnknownBatchAndConsignment() public {
        assertEq(allocator.allocatedTonnes(99), 0);
        assertEq(allocator.remainingTonnes(99), 0);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.UnknownConsignment.selector, C3));
        allocator.getAllocation(C3);
    }

    function test_Views_RemainingOfPendingBatchIsVerifiedTonnage() public {
        uint256 id = _redBatch(250_000);
        assertEq(allocator.allocatedTonnes(id), 0);
        assertEq(allocator.remainingTonnes(id), 250_000);
    }

    // ------------------------------------- operator / attestor liveness (9.B, 9.C)

    function test_Allocate_OperatorRoleRevoked_StopsAllocation() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 100);
        vm.prank(admin);
        registry.revokeRole(OPERATOR_ROLE, exporterA); // still operatorOf(EAF) in the registry data
        assertEq(registry.operatorOf(EAF_ID), exporterA);

        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, exporterA));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerA, CN, 1);
        assertEq(allocator.allocatedTonnes(id), 100);

        // Restoring the role restores the ability (the role is live, not cached).
        vm.prank(admin);
        registry.grantRole(OPERATOR_ROLE, exporterA);
        _allocate(id, C2, importerA, 1);
    }

    function test_Allocate_AttestorRevoked_StopsAllocation() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 100);
        vm.prank(admin);
        registry.revokeRole(ATTESTOR_ROLE, attestor); // key compromise / offboarding

        vm.expectRevert(abi.encodeWithSelector(ConsignmentAllocator.AttestorRevoked.selector, id, attestor));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerA, CN, 1);
        assertEq(allocator.allocatedTonnes(id), 100);

        // Earlier allocations are untouched and can still be claimed.
        vm.prank(importerA);
        allocator.claim(C1);

        vm.prank(admin);
        registry.grantRole(ATTESTOR_ROLE, attestor);
        _allocate(id, C2, importerA, 1);
    }

    function test_Allocate_AttestorRevoked_AlsoBlocksVerifierApprovedBatches() public {
        uint256 id = _redBatch(250_000);
        _verify(id, true);
        vm.prank(admin);
        registry.revokeRole(ATTESTOR_ROLE, attestor);
        vm.expectRevert(abi.encodeWithSelector(ConsignmentAllocator.AttestorRevoked.selector, id, attestor));
        vm.prank(exporterB);
        allocator.allocate(id, C1, importerA, CN, 1);
    }

    function test_Allocate_NonOperatorSeesNotBatchOperatorBeforeAttestorRevoked() public {
        uint256 id = _greenBatch(5000);
        vm.prank(admin);
        registry.revokeRole(ATTESTOR_ROLE, attestor);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, stranger));
        vm.prank(stranger);
        allocator.allocate(id, C1, importerA, CN, 1);
    }

    function test_Allocate_RevokedBatchCannotBeAllocated() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, C1, importerA, 100);
        vm.prank(admin);
        attestation.revokeBatch(id, REASON);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.BatchNotAttested.selector, id));
        vm.prank(exporterA);
        allocator.allocate(id, C2, importerA, CN, 1);
        assertEq(allocator.allocatedTonnes(id), 100, "earlier allocation is not unwound");
    }

    function test_Allocate_AfterSetOperator_NewOperatorAllocatesOldOneCannot() public {
        uint256 id = _greenBatch(5000);
        address newOp = makeAddr("newOperator");
        vm.prank(admin);
        registry.setOperator(EAF_ID, newOp);

        // exporterA still holds OPERATOR_ROLE but is no longer operatorOf(EAF).
        assertTrue(registry.hasRole(OPERATOR_ROLE, exporterA));
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, exporterA));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 1);

        vm.prank(newOp);
        allocator.allocate(id, C1, importerA, CN, 1);
        assertEq(allocator.allocatedTonnes(id), 1);
    }

    function test_Allocate_OperatorOfRecordButNoRoleIsRefused() public {
        // The operator address is registered, then its role is revoked: both conditions are required.
        uint256 id = _greenBatch(5000);
        vm.startPrank(admin);
        registry.revokeRole(OPERATOR_ROLE, exporterA);
        vm.stopPrank();
        assertEq(registry.operatorOf(EAF_ID), exporterA);
        assertFalse(registry.hasRole(OPERATOR_ROLE, exporterA));
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, id, exporterA));
        vm.prank(exporterA);
        allocator.allocate(id, C1, importerA, CN, 1);
    }

    // -------------------------------------------------------------- fuzzing

    /// @notice Σ allocations never exceed verifiedTonnes, whatever sequence of asks the operator makes,
    ///         and each ask succeeds exactly when it is non-zero and within the remainder.
    function testFuzz_SumOfAllocationsNeverExceedsVerifiedTonnes(uint64 verified, uint64[8] calldata asks) public {
        verified = uint64(bound(verified, 1, type(uint64).max));
        uint256 id = _attest(EAF_ID, PERIOD, 10, verified);

        uint256 sum; // uint256: cannot overflow, unlike the contract's uint64
        for (uint256 i; i < asks.length; i++) {
            uint64 ask = asks[i];
            vm.prank(exporterA);
            (bool ok, bytes memory ret) = address(allocator).call(
                abi.encodeCall(allocator.allocate, (id, bytes32(i + 1), importerA, CN, ask))
            );
            uint256 remainingBefore = uint256(verified) - sum;
            if (ask == 0) {
                assertFalse(ok);
                assertEq(_selectorOf(ret), IConsignmentAllocator.ZeroTonnes.selector);
            } else if (ask > remainingBefore) {
                assertFalse(ok);
                assertEq(
                    keccak256(ret),
                    keccak256(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, ask, uint64(remainingBefore)))
                );
            } else if (ask > type(uint32).max) {
                assertFalse(ok);
                assertEq(keccak256(ret), keccak256(abi.encodeWithSelector(ConsignmentAllocator.TonnesTooLarge.selector, ask)));
            } else {
                assertTrue(ok);
                sum += ask;
            }
            assertLe(sum, verified);
            assertEq(allocator.allocatedTonnes(id), sum);
            assertEq(allocator.remainingTonnes(id), verified - sum);
        }
    }

    /// @notice Each consignment id can be bound at most once, regardless of caller-chosen id collisions.
    function testFuzz_ConsignmentIdUniqueness(bytes32 cid, uint64 t1, uint64 t2) public {
        t1 = uint64(bound(t1, 1, 1_000_000));
        t2 = uint64(bound(t2, 1, 1_000_000));
        uint256 id = _greenBatch(5_000_000);
        _allocate(id, cid, importerA, t1);
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.ConsignmentReused.selector, cid));
        vm.prank(exporterA);
        allocator.allocate(id, cid, importerB, CN, t2);
        assertEq(allocator.allocatedTonnes(id), t1);
        assertEq(allocator.getAllocation(cid).importer, importerA);
    }
}
