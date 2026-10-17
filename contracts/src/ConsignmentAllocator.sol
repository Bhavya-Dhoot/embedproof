// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";

import {IConsignmentAllocator, Allocation, Status} from "./interfaces/IEmbedProof.sol";
import {IRegistryView, IAttestationView, ZeroAddress} from "./interfaces/IRegistryView.sol";

/// @title ConsignmentAllocator
/// @notice Splits the verified tonnes of an Attested batch across consignments. For every batch the
///         running total of allocations can never exceed `verifiedTonnes` (the double-claim guard)
///         and every consignment id maps to exactly one allocation. Only the exporter that operates
///         the batch's installation can allocate; only the named importer can claim.
/// @dev UUPS upgradeable; roles, pause flag, operators and the upgrade timelock live in the registry,
///      batch data in `EmissionsAttestation`. An Attested batch is final (no transition leaves
///      Attested), so an allocation can never be orphaned by a later status change.
///      Storage: an allocation is ONE word, `importer(20) | cnCode(4) | tonnes(4) | batchId(4)`, so the
///      first allocation on a batch costs two cold writes (record + running total). A consignment is
///      "used" iff its importer is non-zero. `claimed` is a separate mapping written only by `claim`.
///      Per-consignment `tonnes` and `batchId` are therefore capped at uint32 (`TonnesTooLarge`,
///      `BatchIdTooLarge`); the per-batch running total and the cap check stay uint64, so a batch of any
///      verified size can still be split into consignments of up to 4,294,967,295 t each.
contract ConsignmentAllocator is Initializable, UUPSUpgradeable, IConsignmentAllocator {
    /// @dev importer(20) + cnCode(4) + tonnes(4) + batchId(4) = 32 bytes: a single storage word.
    struct AllocationStore {
        address importer;
        uint32 cnCode;
        uint32 tonnes;
        uint32 batchId;
    }

    /// @dev A consignment's per-allocation tonnage does not fit the packed record (> type(uint32).max).
    ///      Split it over several consignment ids.
    error TonnesTooLarge(uint64 tonnes);
    /// @dev Batch id does not fit the packed record (> type(uint32).max).
    error BatchIdTooLarge(uint256 batchId);

    address private _registry;
    address private _attestation;
    mapping(bytes32 consignmentId => AllocationStore) private _allocations;
    mapping(bytes32 consignmentId => bool) private _claimed;
    mapping(uint256 batchId => uint64) private _allocated;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    // ----------------------------------------------------------- initialiser

    /// @notice Initialise the proxy.
    /// @param registry_ InstallationRegistry proxy (operators, pause flag, timelock). Non-zero.
    /// @param attestation_ EmissionsAttestation proxy (batch status and verified tonnes). Non-zero.
    function initialize(address registry_, address attestation_) external initializer {
        if (registry_ == address(0) || attestation_ == address(0)) revert ZeroAddress();
        _registry = registry_;
        _attestation = attestation_;
    }

    // -------------------------------------------------------------- allocate

    /// @notice Allocate `tonnes` of an Attested batch to one consignment of one importer.
    /// @dev Caller must be `registry.operatorOf(batch.installationId)` (an importer cannot self-allocate,
    ///      which closes the front-running vector). Reverts: `ZeroTonnes`, `ZeroImporter`,
    ///      `BatchNotAttested` (unknown, pending or rejected batch), `NotBatchOperator`,
    ///      `ConsignmentReused`, `DoubleClaimBlocked(batchId, requested, remaining)`, and the packing
    ///      guards `BatchIdTooLarge` / `TonnesTooLarge` (the cap check runs first, so an over-cap request
    ///      always reports `DoubleClaimBlocked`).
    /// @param batchId The Attested batch to draw from.
    /// @param consignmentId Globally unique consignment identifier; can never be reused.
    /// @param importer The only address that may later `claim` this consignment.
    /// @param cnCode Combined Nomenclature code of the goods (e.g. 72083900).
    /// @param tonnes Whole tonnes of product to allocate; must be > 0, <= remaining and <= type(uint32).max.
    function allocate(uint256 batchId, bytes32 consignmentId, address importer, uint32 cnCode, uint64 tonnes)
        external
    {
        IRegistryView reg = _enforceNotPaused();
        if (tonnes == 0) revert ZeroTonnes();
        if (importer == address(0)) revert ZeroImporter();

        (Status status, uint64 verified, bytes32 installationId) = IAttestationView(_attestation).allocInfo(batchId);
        if (status != Status.Attested) revert BatchNotAttested(batchId);
        if (batchId > type(uint32).max) revert BatchIdTooLarge(batchId);
        if (reg.operatorOf(installationId) != msg.sender) revert NotBatchOperator(batchId, msg.sender);

        AllocationStore storage rec = _allocations[consignmentId];
        if (rec.importer != address(0)) revert ConsignmentReused(consignmentId);

        uint64 already = _allocated[batchId];
        // `already <= verified` is an invariant: it only grows through the check below and
        // `verified` is immutable once a batch is Attested.
        uint64 remaining = verified - already;
        if (tonnes > remaining) revert DoubleClaimBlocked(batchId, tonnes, remaining);
        if (tonnes > type(uint32).max) revert TonnesTooLarge(tonnes);

        // effects (both narrowing casts are guarded above)
        _allocated[batchId] = already + tonnes;
        // forge-lint: disable-next-line(unsafe-typecast)
        _allocations[consignmentId] = AllocationStore(importer, cnCode, uint32(tonnes), uint32(batchId));

        emit Allocated(batchId, consignmentId, importer, cnCode, tonnes, remaining - tonnes);
    }

    /// @notice The named importer takes ownership of an allocation. One-shot.
    /// @param consignmentId The consignment to claim.
    function claim(bytes32 consignmentId) external {
        _enforceNotPaused();
        AllocationStore memory rec = _allocations[consignmentId];
        if (rec.importer == address(0)) revert UnknownConsignment(consignmentId);
        if (rec.importer != msg.sender) revert NotConsignee(consignmentId, msg.sender);
        if (_claimed[consignmentId]) revert AlreadyClaimed(consignmentId);

        _claimed[consignmentId] = true;

        emit Claimed(rec.batchId, consignmentId, msg.sender);
    }

    // ---------------------------------------------------------------- views

    /// @notice Read an allocation.
    /// @param consignmentId The consignment identifier.
    /// @return a The allocation record.
    function getAllocation(bytes32 consignmentId) external view returns (Allocation memory a) {
        AllocationStore memory rec = _allocations[consignmentId];
        if (rec.importer == address(0)) revert UnknownConsignment(consignmentId);
        a = Allocation({
            batchId: rec.batchId,
            importer: rec.importer,
            cnCode: rec.cnCode,
            tonnes: rec.tonnes,
            claimed: _claimed[consignmentId]
        });
    }

    /// @notice Tonnes of a batch already allocated to consignments.
    /// @param batchId The batch.
    /// @return The allocated tonnes (0 for an unknown or untouched batch).
    function allocatedTonnes(uint256 batchId) external view returns (uint64) {
        return _allocated[batchId];
    }

    /// @notice Verified tonnes of a batch not yet allocated (`verifiedTonnes - allocatedTonnes`).
    /// @dev Pure arithmetic: it does not look at status, so a PendingVerifier batch reports its full
    ///      verified tonnage even though `allocate` rejects it until the verifier approves.
    /// @param batchId The batch.
    /// @return The remaining tonnes (0 for an unknown batch).
    function remainingTonnes(uint256 batchId) external view returns (uint64) {
        // status and installationId are intentionally ignored: remaining is pure arithmetic.
        // forge-lint: disable-next-line(unused-return)
        (, uint64 verified,) = IAttestationView(_attestation).allocInfo(batchId);
        return verified - _allocated[batchId];
    }

    /// @notice The registry that supplies operators, the pause flag and the timelock.
    /// @return The registry proxy address.
    function registry() external view returns (address) {
        return _registry;
    }

    /// @notice The attestation contract whose batches are allocated.
    /// @return The attestation proxy address.
    function attestation() external view returns (address) {
        return _attestation;
    }

    // -------------------------------------------------------------- upgrades

    /// @dev UUPS authorisation hook: only the registry's timelock may upgrade.
    function _authorizeUpgrade(address) internal view override {
        if (msg.sender != IRegistryView(_registry).timelock()) revert NotTimelock();
    }

    // ------------------------------------------------------------- internals

    function _enforceNotPaused() private view returns (IRegistryView reg) {
        reg = IRegistryView(_registry);
        if (reg.paused()) revert PausableUpgradeable.EnforcedPause();
    }
}
