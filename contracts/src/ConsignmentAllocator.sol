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
///      The cap is per (installationId, period), not per batch id: `_allocated` is keyed by
///      keccak256(abi.encode(installationId, period)), read from the batch. A batch that is revoked and
///      resubmitted for the same period therefore inherits what was already allocated and cannot be
///      allocated a second time (PRD: sum of allocations <= verified tonnes per installation-period).
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
    /// @dev The batch's attestor no longer holds ATTESTOR_ROLE (key compromise / offboarding).
    error AttestorRevoked(uint256 batchId, address attestor);

    bytes32 private constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 private constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");

    address private _registry;
    address private _attestation;
    mapping(bytes32 consignmentId => AllocationStore) private _allocations;
    mapping(bytes32 consignmentId => bool) private _claimed;
    /// @dev Running total of allocated tonnes per keccak256(abi.encode(installationId, period)).
    mapping(bytes32 periodKey => uint64) private _allocated;

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
    /// @dev Caller must hold OPERATOR_ROLE and be `registry.operatorOf(batch.installationId)` (an importer
    ///      cannot self-allocate, which closes the front-running vector, and a role revocation stops an
    ///      operator immediately). The batch's attestor must still hold ATTESTOR_ROLE (`AttestorRevoked`).
    ///      Reverts: `ZeroTonnes`, `ZeroImporter`,
    ///      `BatchNotAttested` (unknown, pending or rejected/revoked batch), `NotBatchOperator`,
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

        (uint64 verified, bytes32 periodKey) = _authoriseBatch(reg, batchId);

        AllocationStore storage rec = _allocations[consignmentId];
        if (rec.importer != address(0)) revert ConsignmentReused(consignmentId);

        uint64 already = _allocated[periodKey];
        // After revokeBatch + resubmission with fewer tonnes `already` can exceed the new `verified`:
        // nothing more can then be allocated (remaining is floored at 0, never an underflow).
        uint64 remaining = verified > already ? verified - already : 0;
        if (tonnes > remaining) revert DoubleClaimBlocked(batchId, tonnes, remaining);
        if (tonnes > type(uint32).max) revert TonnesTooLarge(tonnes);

        // effects (both narrowing casts are guarded above; already + tonnes <= verified)
        _allocated[periodKey] = already + tonnes;
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

    /// @notice Tonnes already allocated for the (installation, period) of a batch.
    /// @dev The total is per installation-period, not per batch id: allocations made against an earlier,
    ///      since revoked batch of the same period are included. Signature unchanged from v1.
    /// @param batchId The batch.
    /// @return The allocated tonnes for the batch's period (0 for an unknown batch).
    function allocatedTonnes(uint256 batchId) external view returns (uint64) {
        // verified tonnes and attestor are not needed for the period total.
        // forge-lint: disable-next-line(unused-return)
        (Status status,, bytes32 installationId,, uint32 period) = IAttestationView(_attestation).allocData(batchId);
        if (status == Status.None) return 0;
        return _allocated[_periodKey(installationId, period)];
    }

    /// @notice Verified tonnes of a batch not yet allocated for its period:
    ///         `max(0, verifiedTonnes - allocatedTonnes(batchId))`.
    /// @dev Pure arithmetic on the period total: it does not look at status, so a PendingVerifier batch
    ///      reports its full verified tonnage even though `allocate` rejects it until the verifier approves,
    ///      and a resubmission with fewer tonnes than were already allocated reports 0, not an underflow.
    /// @param batchId The batch.
    /// @return The remaining tonnes (0 for an unknown batch).
    function remainingTonnes(uint256 batchId) external view returns (uint64) {
        // the attestor is not needed for the remaining total.
        // forge-lint: disable-next-line(unused-return)
        (, uint64 verified, bytes32 installationId,, uint32 period) = IAttestationView(_attestation).allocData(batchId);
        // An unknown batch reads verified == 0, so the floor below already returns 0.
        uint64 already = _allocated[_periodKey(installationId, period)];
        return verified > already ? verified - already : 0;
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

    function _periodKey(bytes32 installationId, uint32 period) private pure returns (bytes32) {
        return keccak256(abi.encode(installationId, period));
    }

    /// @dev Batch-level gates of `allocate`; returns the batch's verified tonnes and its period key.
    function _authoriseBatch(IRegistryView reg, uint256 batchId)
        private
        view
        returns (uint64 verified, bytes32 periodKey)
    {
        Status status;
        bytes32 installationId;
        address attestor;
        uint32 period;
        (status, verified, installationId, attestor, period) = IAttestationView(_attestation).allocData(batchId);
        if (status != Status.Attested) revert BatchNotAttested(batchId);
        if (batchId > type(uint32).max) revert BatchIdTooLarge(batchId);
        if (!reg.hasRole(OPERATOR_ROLE, msg.sender) || reg.operatorOf(installationId) != msg.sender) {
            revert NotBatchOperator(batchId, msg.sender);
        }
        if (!reg.hasRole(ATTESTOR_ROLE, attestor)) revert AttestorRevoked(batchId, attestor);
        periodKey = _periodKey(installationId, period);
    }

    function _enforceNotPaused() private view returns (IRegistryView reg) {
        reg = IRegistryView(_registry);
        if (reg.paused()) revert PausableUpgradeable.EnforcedPause();
    }
}
