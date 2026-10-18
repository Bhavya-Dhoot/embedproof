// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {StdInvariant} from "forge-std/StdInvariant.sol";
import {Base} from "./Base.t.sol";
import {Status, Allocation} from "../src/interfaces/IEmbedProof.sol";

/// @dev Stateful invariant suite. The test contract is its own handler: the fuzzer calls `makeBatch`,
///      `decide`, `allocate`, `claim` and `revokeAndResubmit` in random order and with random arguments,
///      while ghost variables record what *should* have happened independently of the contracts.
///
///      The cap is per (installationId, period), not per batch id: `revokeAndResubmit` revokes an Attested
///      batch and files a new one for the same period, and the allocator must keep counting what the
///      revoked batch already allocated.
contract AllocationInvariantTest is StdInvariant, Base {
    uint32 internal nextPeriod = 202000;
    uint256[] internal batchIds;
    bytes32[] internal consignments;
    mapping(uint256 batchId => bytes32 periodKey) internal keyOf;
    mapping(uint256 batchId => uint64) internal verifiedOf;
    mapping(bytes32 periodKey => uint256) internal ghostPeriodTotal;
    mapping(uint256 batchId => bool) internal ghostPending;
    mapping(uint256 batchId => bool) internal ghostRevoked;
    /// @dev Set when a successful allocation left the period total above the verified tonnes of the batch it
    ///      drew from, or succeeded on a batch that was not Attested.
    bool internal violation;
    uint256 internal resubmissions;

    function setUp() public override {
        super.setUp();
        targetContract(address(this));
        bytes4[] memory sels = new bytes4[](5);
        sels[0] = this.makeBatch.selector;
        sels[1] = this.decide.selector;
        sels[2] = this.allocate.selector;
        sels[3] = this.claim.selector;
        sels[4] = this.revokeAndResubmit.selector;
        targetSelector(FuzzSelector({addr: address(this), selectors: sels}));
    }

    // ------------------------------------------------------------ handlers

    function makeBatch(uint8 which, uint8 risk, uint64 tonnes) external {
        risk = uint8(bound(risk, 0, 100));
        tonnes = uint64(bound(tonnes, 1, 1_000_000));
        bytes32 id = which % 2 == 0 ? EAF_ID : BOF_ID;
        uint32 period = nextPeriod++;
        uint256 batchId = _attest(id, period, risk, tonnes);
        _record(batchId, id, period, tonnes);
        if (risk > 60) ghostPending[batchId] = true;
    }

    function decide(uint256 seed, bool approve) external {
        if (batchIds.length == 0) return;
        uint256 id = batchIds[seed % batchIds.length];
        if (!ghostPending[id] || ghostRevoked[id]) return;
        ghostPending[id] = false;
        _verify(id, approve);
    }

    function allocate(uint256 seed, uint64 tonnes, bytes32 consignmentSeed) external {
        if (batchIds.length == 0) return;
        uint256 id = batchIds[seed % batchIds.length];
        tonnes = uint64(bound(tonnes, 0, 600_000));
        (Status before_,, bytes32 installation,) = attestation.allocInfo(id);
        address op = installation == EAF_ID ? exporterA : exporterB;
        bytes32 cid = keccak256(abi.encode(consignmentSeed, consignments.length));
        vm.prank(op);
        (bool ok,) = address(allocator).call(
            abi.encodeCall(allocator.allocate, (id, cid, seed % 2 == 0 ? importerA : importerB, 72083900, tonnes))
        );
        if (ok) {
            bytes32 key = keyOf[id];
            ghostPeriodTotal[key] += tonnes;
            consignments.push(cid);
            if (before_ != Status.Attested || ghostPeriodTotal[key] > verifiedOf[id]) violation = true;
        }
    }

    function claim(uint256 seed) external {
        if (consignments.length == 0) return;
        bytes32 cid = consignments[seed % consignments.length];
        address who = allocator.getAllocation(cid).importer;
        vm.prank(who);
        try allocator.claim(cid) {} catch {}
    }

    /// @dev The double-count attack: revoke an Attested batch that already has allocations, then file a
    ///      replacement for the same (installation, period) with a different tonnage.
    function revokeAndResubmit(uint256 seed, uint64 tonnes) external {
        if (batchIds.length == 0) return;
        uint256 id = batchIds[seed % batchIds.length];
        (Status status,, bytes32 installation,) = attestation.allocInfo(id);
        if (status != Status.Attested) return;
        tonnes = uint64(bound(tonnes, 1, 1_000_000));

        vm.prank(admin);
        attestation.revokeBatch(id, REASON);
        ghostRevoked[id] = true;

        uint32 period = uint32(attestation.getBatch(id).period);
        uint256 newId = _attest(installation, period, 10, tonnes); // risk 10: Attested straight away
        _record(newId, installation, period, tonnes);
        resubmissions++;
    }

    function _record(uint256 batchId, bytes32 installation, uint32 period, uint64 tonnes) internal {
        batchIds.push(batchId);
        keyOf[batchId] = keccak256(abi.encode(installation, period));
        verifiedOf[batchId] = tonnes;
    }

    // ---------------------------------------------------------- invariants

    /// @notice The allocated total is per (installation, period): every batch of a period reports the same
    ///         total, equal to the independent ghost sum, and `remaining` is that total floored at zero.
    function invariant_PeriodTotalIsSharedAcrossResubmissions() public view {
        for (uint256 i; i < batchIds.length; i++) {
            uint256 id = batchIds[i];
            uint256 total = allocator.allocatedTonnes(id);
            assertEq(total, ghostPeriodTotal[keyOf[id]], "allocated != ghost period total");
            uint256 verified = verifiedOf[id];
            assertEq(allocator.remainingTonnes(id), verified > total ? verified - total : 0, "remaining");
        }
    }

    /// @notice No successful allocation ever pushed a period's total past the verified tonnes of the batch it
    ///         drew from, and none succeeded on a batch that was not Attested.
    function invariant_NoAllocationBeyondTheBatchCapOrOnNonAttestedBatches() public view {
        assertFalse(violation, "an allocation exceeded the period cap or used a non-attested batch");
    }

    /// @notice The consignment records of a period sum to the period total.
    function invariant_ConsignmentRecordsSumToPeriodTotals() public view {
        for (uint256 i; i < batchIds.length; i++) {
            bytes32 key = keyOf[batchIds[i]];
            uint256 sum;
            for (uint256 j; j < consignments.length; j++) {
                Allocation memory a = allocator.getAllocation(consignments[j]);
                if (keyOf[a.batchId] == key) sum += a.tonnes;
            }
            assertEq(sum, allocator.allocatedTonnes(batchIds[i]), "consignment records disagree with period total");
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
