// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {StdInvariant} from "forge-std/StdInvariant.sol";
import {Base} from "./Base.t.sol";
import {Status, Allocation} from "../src/interfaces/IEmbedProof.sol";

/// @dev Stateful invariant suite. The test contract is its own handler: the fuzzer calls
///      `makeBatch`, `decide`, `allocate` and `claim` in random order and with random arguments, while
///      ghost variables record what *should* have happened independently of the contracts.
contract AllocationInvariantTest is StdInvariant, Base {
    uint32 internal nextPeriod = 202000;
    uint256[] internal batchIds;
    bytes32[] internal consignments;
    mapping(uint256 batchId => uint256) internal ghostAllocated;
    mapping(uint256 batchId => bool) internal ghostPending;

    function setUp() public override {
        super.setUp();
        targetContract(address(this));
        bytes4[] memory sels = new bytes4[](4);
        sels[0] = this.makeBatch.selector;
        sels[1] = this.decide.selector;
        sels[2] = this.allocate.selector;
        sels[3] = this.claim.selector;
        targetSelector(FuzzSelector({addr: address(this), selectors: sels}));
    }

    // ------------------------------------------------------------ handlers

    function makeBatch(uint8 which, uint8 risk, uint64 tonnes) external {
        risk = uint8(bound(risk, 0, 100));
        tonnes = uint64(bound(tonnes, 1, 1_000_000));
        bytes32 id = which % 2 == 0 ? EAF_ID : BOF_ID;
        uint256 batchId = _attest(id, nextPeriod++, risk, tonnes);
        batchIds.push(batchId);
        if (risk > 60) ghostPending[batchId] = true;
    }

    function decide(uint256 seed, bool approve) external {
        if (batchIds.length == 0) return;
        uint256 id = batchIds[seed % batchIds.length];
        if (!ghostPending[id]) return;
        ghostPending[id] = false;
        _verify(id, approve);
    }

    function allocate(uint256 seed, uint64 tonnes, bytes32 consignmentSeed) external {
        if (batchIds.length == 0) return;
        uint256 id = batchIds[seed % batchIds.length];
        tonnes = uint64(bound(tonnes, 0, 600_000));
        (,, bytes32 installation) = attestation.allocInfo(id);
        address op = installation == EAF_ID ? exporterA : exporterB;
        bytes32 cid = keccak256(abi.encode(consignmentSeed, consignments.length));
        vm.prank(op);
        (bool ok,) = address(allocator).call(
            abi.encodeCall(allocator.allocate, (id, cid, seed % 2 == 0 ? importerA : importerB, 72083900, tonnes))
        );
        if (ok) {
            ghostAllocated[id] += tonnes;
            consignments.push(cid);
        }
    }

    function claim(uint256 seed) external {
        if (consignments.length == 0) return;
        bytes32 cid = consignments[seed % consignments.length];
        address who = allocator.getAllocation(cid).importer;
        vm.prank(who);
        try allocator.claim(cid) {} catch {}
    }

    // ---------------------------------------------------------- invariants

    /// @notice Σ allocations of a batch never exceed its verified tonnes and equal the independent ghost sum.
    function invariant_AllocationsNeverExceedVerifiedTonnes() public view {
        for (uint256 i; i < batchIds.length; i++) {
            uint256 id = batchIds[i];
            (, uint64 verified,) = attestation.allocInfo(id);
            uint256 allocated = allocator.allocatedTonnes(id);
            assertLe(allocated, verified, "allocated > verified");
            assertEq(allocated, ghostAllocated[id], "allocated != ghost sum");
            assertEq(allocator.remainingTonnes(id), uint256(verified) - allocated, "remaining");
        }
    }

    /// @notice Pending and rejected batches never hold allocations.
    function invariant_NonAttestedBatchesHaveNoAllocations() public view {
        for (uint256 i; i < batchIds.length; i++) {
            (Status s,,) = attestation.allocInfo(batchIds[i]);
            if (s != Status.Attested) {
                assertEq(allocator.allocatedTonnes(batchIds[i]), 0, "allocation on a non-attested batch");
            }
        }
    }

    /// @notice The consignment records of a batch sum to the batch's running total.
    function invariant_ConsignmentRecordsSumToBatchTotals() public view {
        for (uint256 i; i < batchIds.length; i++) {
            uint256 sum;
            for (uint256 j; j < consignments.length; j++) {
                Allocation memory a = allocator.getAllocation(consignments[j]);
                if (a.batchId == batchIds[i]) sum += a.tonnes;
            }
            assertEq(sum, allocator.allocatedTonnes(batchIds[i]), "consignment records disagree with batch total");
        }
    }

    /// @notice Batch ids are dense from 1 and never skip.
    function invariant_BatchIdsAreDenseFromOne() public view {
        assertEq(attestation.batchCount(), batchIds.length);
        for (uint256 i; i < batchIds.length; i++) {
            assertEq(batchIds[i], i + 1);
        }
    }
}
