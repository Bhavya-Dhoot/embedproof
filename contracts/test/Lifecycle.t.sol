// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Vm} from "forge-std/Vm.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";

import {Base} from "./Base.t.sol";
import {
    IEmissionsAttestation,
    IConsignmentAllocator,
    IInstallationRegistry,
    AttestInput,
    Batch,
    Status,
    Sector,
    Route
} from "../src/interfaces/IEmbedProof.sol";

/// @dev Cross-contract behaviour: FR-C2 (role matrix), FR-C7 (pause), FR-C9 (events carry batchId).
contract LifecycleTest is Base {
    bytes32 internal constant C1 = keccak256("consignment-1");
    uint32 internal constant CN = 72083900;

    // ------------------------------------------------------------ FR-C9

    function test_Events_FullLifecycleCarriesBatchId() public {
        vm.recordLogs();

        uint256 id = _redBatch(250_000); // Attested(PendingVerifier)
        _verify(id, true); // VerifierDecision
        vm.prank(exporterB);
        allocator.allocate(id, C1, importerA, CN, 1000); // Allocated
        vm.prank(importerA);
        allocator.claim(C1); // Claimed

        Vm.Log[] memory logs = vm.getRecordedLogs();
        assertEq(logs.length, 4, "one event per state change");

        bytes32[4] memory sigs = [
            IEmissionsAttestation.Attested.selector,
            IEmissionsAttestation.VerifierDecision.selector,
            IConsignmentAllocator.Allocated.selector,
            IConsignmentAllocator.Claimed.selector
        ];
        address[4] memory emitters = [address(attestation), address(attestation), address(allocator), address(allocator)];
        for (uint256 i; i < 4; i++) {
            assertEq(logs[i].topics[0], sigs[i], "event signature");
            assertEq(logs[i].topics[1], bytes32(id), "topic1 is the batchId");
            assertEq(logs[i].emitter, emitters[i], "emitting contract");
        }
    }

    function test_Events_RejectionAndResubmissionLogsBothBatchIds() public {
        vm.recordLogs();
        uint256 first = _redBatch(250_000);
        _verify(first, false);
        uint256 second = _attest(BOF_ID, PERIOD, 20, 4_000);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        assertEq(logs.length, 3);
        assertEq(logs[0].topics[1], bytes32(first));
        assertEq(logs[1].topics[0], IEmissionsAttestation.VerifierDecision.selector);
        assertEq(logs[1].topics[1], bytes32(first));
        assertEq(logs[2].topics[1], bytes32(second));
    }

    // ------------------------------------------------------------ FR-C7

    function test_Pause_BlocksAttestVerifierSignAllocateAndClaim() public {
        // Prepare one live object for each of the four entry points before pausing.
        uint256 pending = _redBatch(250_000);
        uint256 green = _greenBatch(5000);
        _allocate(green, C1, importerA, 100);
        AttestInput memory fresh = _input(EAF_ID, 202610, 10, 1);

        vm.prank(admin);
        registry.pause();

        bytes memory paused = abi.encodeWithSelector(PausableUpgradeable.EnforcedPause.selector);

        vm.expectRevert(paused);
        vm.prank(attestor);
        attestation.attest(fresh, CID);

        vm.expectRevert(paused);
        vm.prank(verifier);
        attestation.verifierSign(pending, true, bytes32(0));

        vm.expectRevert(paused);
        vm.prank(exporterA);
        allocator.allocate(green, keccak256("c2"), importerA, CN, 1);

        vm.expectRevert(paused);
        vm.prank(importerA);
        allocator.claim(C1);

        // Nothing changed while paused.
        assertEq(attestation.batchCount(), 2);
        assertEq(uint8(attestation.getBatch(pending).status), uint8(Status.PendingVerifier));
        assertEq(allocator.allocatedTonnes(green), 100);
        assertFalse(allocator.getAllocation(C1).claimed);

        // Reads stay available during an incident.
        assertEq(allocator.remainingTonnes(green), 4900);
        assertEq(attestation.getBatch(green).verifiedTonnes, 5000);

        // Unpause restores every entry point.
        vm.prank(admin);
        registry.unpause();
        vm.prank(attestor);
        attestation.attest(fresh, CID);
        _verify(pending, true);
        _allocate(green, keccak256("c2"), importerA, 1);
        vm.prank(importerA);
        allocator.claim(C1);
        assertTrue(allocator.getAllocation(C1).claimed);
    }

    function test_Pause_IsCheckedBeforeRoleAndInputValidation() public {
        vm.prank(admin);
        registry.pause();
        // Even a caller without the role sees the pause first: the system is simply closed.
        AttestInput memory fresh = _input(EAF_ID, PERIOD, 10, 1);
        vm.expectRevert(abi.encodeWithSelector(PausableUpgradeable.EnforcedPause.selector));
        vm.prank(stranger);
        attestation.attest(fresh, CID);
    }

    function test_Pause_AdminKnobsStillWorkWhilePaused() public {
        vm.startPrank(admin);
        registry.pause();
        attestation.setRiskThreshold(50);
        attestation.approveModel(sha256("another"));
        vm.stopPrank();
        assertEq(attestation.riskThreshold(), 50);
    }

    // ------------------------------------------------------------ FR-C2

    /// @dev Every privileged entry point, probed with every actor that does not hold the required role.
    function test_RoleMatrix_EveryPrivilegedCallRevertsForTheWrongRole() public {
        uint256 pending = _redBatch(250_000);
        uint256 green = _greenBatch(5000);
        AttestInput memory fresh = _input(EAF_ID, 202610, 10, 1);
        bytes32 newId = bytes32("TR-NEW-0004");
        address[7] memory actors = [stranger, admin, attestor, verifier, exporterA, importerA, regulator];

        for (uint256 i; i < actors.length; i++) {
            address who = actors[i];

            // --- registry: ADMIN only
            if (who != admin) {
                bytes memory unauthorised =
                    abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, who, bytes32(0));
                _denied(who, address(registry), abi.encodeCall(registry.registerInstallation, (newId, "TR", Sector.STEEL, Route.EAF, 1, who)), unauthorised);
                _denied(who, address(registry), abi.encodeCall(registry.pause, ()), unauthorised);
                _denied(who, address(registry), abi.encodeCall(registry.unpause, ()), unauthorised);
                _denied(who, address(registry), abi.encodeCall(registry.setAttestorPqKey, (attestor, keccak256("k"))), unauthorised);
                _denied(who, address(registry), abi.encodeCall(registry.grantRole, (ATTESTOR_ROLE, who)), unauthorised);

                // --- attestation: ADMIN only
                bytes memory notAdmin = abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, who);
                _denied(who, address(attestation), abi.encodeCall(attestation.setRiskThreshold, (1)), notAdmin);
                _denied(who, address(attestation), abi.encodeCall(attestation.approveModel, (bytes32(uint256(7)))), notAdmin);
                _denied(who, address(attestation), abi.encodeCall(attestation.revokeModel, (MODEL)), notAdmin);
            }

            // --- attestation.attest: ATTESTOR only (the admin is not an implicit attestor)
            if (who != attestor) {
                _denied(who, address(attestation), abi.encodeCall(attestation.attest, (fresh, CID)),
                    abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ATTESTOR_ROLE, who));
            }

            // --- attestation.verifierSign: VERIFIER only
            if (who != verifier) {
                _denied(who, address(attestation), abi.encodeCall(attestation.verifierSign, (pending, true, bytes32(0))),
                    abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, VERIFIER_ROLE, who));
            }

            // --- allocator.allocate: batch operator only (exporter B operates the BOF installation, not EAF)
            _denied(who, address(allocator), abi.encodeCall(allocator.allocate, (green, C1, importerA, CN, 1)),
                who == exporterA
                    ? bytes("")
                    : abi.encodeWithSelector(IConsignmentAllocator.NotBatchOperator.selector, green, who));
        }
        assertEq(attestation.batchCount(), 2, "no probe created a batch");
        assertEq(allocator.allocatedTonnes(green), 0);
    }

    function test_RoleMatrix_RightRoleSucceeds() public {
        // The same entry points succeed for exactly the documented roles.
        vm.startPrank(admin);
        registry.registerInstallation(bytes32("TR-NEW-0004"), "TR", Sector.CEMENT, Route.CEMENT_DRY, 1, exporterB);
        attestation.setRiskThreshold(70);
        attestation.approveModel(bytes32(uint256(7)));
        attestation.revokeModel(bytes32(uint256(7)));
        registry.pause();
        registry.unpause();
        vm.stopPrank();

        uint256 pending = _attest(BOF_ID, PERIOD, 90, 10);
        _verify(pending, true); // VERIFIER
        _allocate(_greenBatch(10), C1, importerA, 1); // OPERATOR of the batch's installation
    }

    function test_RoleMatrix_RoleChangesTakeEffectImmediatelyAcrossContracts() public {
        // Roles live in the registry; the other two contracts see revocation in the same block.
        _greenBatch(5000);
        vm.prank(admin);
        registry.revokeRole(VERIFIER_ROLE, verifier);
        uint256 pending = _attest(BOF_ID, PERIOD, 90, 100);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, VERIFIER_ROLE, verifier));
        vm.prank(verifier);
        attestation.verifierSign(pending, true, bytes32(0));

        vm.prank(admin);
        registry.grantRole(VERIFIER_ROLE, stranger);
        vm.prank(stranger);
        attestation.verifierSign(pending, true, bytes32(0));
        assertEq(attestation.getBatch(pending).verifier, stranger);
    }

    // -------------------------------------------------------------- helpers

    /// @dev `expected` empty means "this actor is authorised, skip".
    function _denied(address who, address target, bytes memory data, bytes memory expected) internal {
        if (expected.length == 0) return;
        vm.prank(who);
        (bool ok, bytes memory ret) = target.call(data);
        assertFalse(ok, "privileged call must revert");
        assertEq(ret, expected, "revert reason");
    }
}
