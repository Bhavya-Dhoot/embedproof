// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

import {IEmissionsAttestation, IInstallationRegistry, AttestInput, Batch, Status} from "./interfaces/IEmbedProof.sol";
import {IRegistryView, ZeroAddress} from "./interfaces/IRegistryView.sol";

/// @title EmissionsAttestation
/// @notice Stores one attestation per (installation, period): a keccak commitment to the off-chain
///         attestation document, the declared specific embedded emissions (SEE), verified tonnes,
///         the AI risk score and the hashes that bind the post-quantum signature and the IPFS bundle.
///         Scores above the risk threshold must be co-signed by an accredited verifier.
/// @dev UUPS upgradeable; roles, pause flag and the upgrade timelock live in `InstallationRegistry`.
///
///      Attestor signature (INTERFACES section 9.A): an EIP-712 signature over the nine-field
///      `Attestation` struct (everything in `AttestInput` except `cidDigest` and the signature itself)
///      under the domain {EmbedProof, 1, block.chainid, address(this)}. The domain separator is rebuilt
///      from constant name/version hashes, `block.chainid` and `address(this)`: no storage reads, and
///      it is correct behind the proxy and across chain forks. Because installation, period, tonnes,
///      score, model and PQ-signature hash are signed, a valid signature cannot be replayed with
///      altered numbers, for another period, on another chain or against another contract.
///
///      Gas layout (attest <= 250,000, allocate <= 120,000, see test/Gas.t.sol). The public `Batch`
///      struct is rebuilt on read from a 7-word packed `BatchStore`; `allocInfo` needs only words 0-1:
///        word 0  installationId
///        word 1  attestor(20) verifiedTonnes(8) riskScore(1) algId(1) status(1) modelIdx(1)
///        word 2  verifier(20) period(4) seeDeclared(4) attestedAt(4)
///        word 3  commitment | word 4 pqSigHash | word 5 cidDigest | word 6 reasonHash
///      `modelIdx` indexes the registered-model list (hence `ModelApproved(hash, index)`), so the
///      32-byte model hash is stored once at first approval rather than once per batch. Revoking a model
///      only flips its `revoked` flag: the list entry stays, so existing batches keep resolving their
///      `modelVersionHash` and a later re-approval reuses the same index. `verifier` and
///      `reasonHash` are only written by `verifierSign`, so `attest` never pays for them.
///      The CID string is emitted in the Attested event only; clients rebuild it from `cidDigest`.
contract EmissionsAttestation is Initializable, UUPSUpgradeable, IEmissionsAttestation {
    // ---------------------------------------------------------------- roles
    bytes32 public constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    /// @dev OZ DEFAULT_ADMIN_ROLE as held in the registry.
    bytes32 private constant ADMIN_ROLE = 0x00;

    /// @dev The only algorithm id defined in v1: ECDSA-secp256k1 (EIP-712) + ML-DSA-44.
    uint8 public constant ALG_ID_ECDSA_MLDSA44 = 1;
    uint8 public constant MAX_RISK_SCORE = 100;
    uint8 private constant DEFAULT_RISK_THRESHOLD = 60;
    /// @dev `modelIdx` is a uint8.
    uint256 private constant MAX_MODELS = 256;

    // EIP-712 (INTERFACES section 9.A). All hashes are compile-time constants.
    bytes32 private constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 private constant NAME_HASH = keccak256("EmbedProof");
    bytes32 private constant VERSION_HASH = keccak256("1");
    bytes32 private constant ATTESTATION_TYPEHASH = keccak256(
        "Attestation(bytes32 installationId,uint32 period,bytes32 commitment,uint32 seeDeclared,uint64 verifiedTonnes,uint8 riskScore,bytes32 modelVersionHash,uint8 algId,bytes32 pqSigHash)"
    );

    /// @dev More than 256 model hashes cannot be indexed by `ModelApproved(hash, uint8 index)`.
    error TooManyModels();
    /// @dev The verifier is the batch's attestor or the operator of its installation (separation of duties).
    error VerifierConflict(uint256 batchId, address verifier);
    /// @dev `verifierSign` / `revokeBatch` need a non-zero reason hash.
    error ZeroReasonHash();
    /// @dev `revokeBatch` on a batch that is already Rejected.
    error AlreadyRejected(uint256 batchId);

    /// @notice An admin withdrew a batch (PendingVerifier or Attested -> Rejected).
    /// @dev Allocations already made against the batch are not unwound; the allocator refuses new ones.
    event BatchRevoked(uint256 indexed batchId, address indexed by, bytes32 reasonHash);

    // -------------------------------------------------------------- storage
    struct BatchStore {
        bytes32 installationId; // word 0
        address attestor; // word 1: 20 + 8 + 4 = 32 bytes (everything the allocator reads)
        uint64 verifiedTonnes;
        uint8 riskScore;
        uint8 algId;
        Status status;
        uint8 modelIdx;
        address verifier; // word 2: 20 + 3 * 4 = 32 bytes
        uint32 period;
        uint32 seeDeclared;
        uint32 attestedAt;
        bytes32 commitment; // word 3
        bytes32 pqSigHash; // word 4
        bytes32 cidDigest; // word 5
        bytes32 reasonHash; // word 6
    }

    // word 0 of contract storage: registry(20) + batchCount(8) + riskThreshold(1) = 29 bytes.
    address private _registry;
    uint64 private _batchCount;
    uint8 private _riskThreshold;

    mapping(uint256 batchId => BatchStore) private _batches;
    mapping(bytes32 installationId => mapping(uint32 period => uint256 batchId)) private _batchOf;
    bytes32[] private _models;
    /// @dev One word per model hash: `slotPlusOne` = index in `_models` + 1 (0 = never approved).
    struct ModelInfo {
        uint16 slotPlusOne;
        bool revoked;
    }

    mapping(bytes32 modelVersionHash => ModelInfo) private _modelInfo;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    // ----------------------------------------------------------- initialiser

    /// @notice Initialise the proxy with the registry that supplies roles, pause state and the timelock.
    /// @param registry_ Address of the InstallationRegistry proxy. Must be non-zero.
    function initialize(address registry_) external initializer {
        if (registry_ == address(0)) revert ZeroAddress();
        _registry = registry_;
        _riskThreshold = DEFAULT_RISK_THRESHOLD;
        // forge-lint: disable-next-line(reentrancy-events)
        emit RiskThresholdSet(DEFAULT_RISK_THRESHOLD);
    }

    // --------------------------------------------------------------- attest

    /// @notice Record an attestation for (installationId, period).
    /// @dev Checks, in order: system not paused; msg.sender holds ATTESTOR_ROLE; algId == 1;
    ///      riskScore <= 100; installation registered; model hash approved; period unused (or its
    ///      previous batch Rejected); `ecdsaSig` is a canonical (low-s) 65-byte EIP-712 signature over the
    ///      `Attestation` struct (see `hashAttestation`) whose signer also holds ATTESTOR_ROLE. Then writes
    ///      state and emits.
    ///      status = riskScore > riskThreshold ? PendingVerifier : Attested.
    /// @param a The attestation fields (see `AttestInput`).
    /// @param cid IPFS CIDv1 of the bundle; emitted only, never stored.
    /// @return batchId Sequential id, starting at 1.
    function attest(AttestInput calldata a, string calldata cid) external returns (uint256 batchId) {
        IRegistryView reg = _enforceNotPaused();
        if (!reg.hasRole(ATTESTOR_ROLE, msg.sender)) revert MissingRole(ATTESTOR_ROLE, msg.sender);
        if (a.algId != ALG_ID_ECDSA_MLDSA44) revert InvalidAlgId(a.algId);
        if (a.riskScore > MAX_RISK_SCORE) revert InvalidRiskScore(a.riskScore);
        if (reg.operatorOf(a.installationId) == address(0)) {
            revert IInstallationRegistry.UnknownInstallation(a.installationId);
        }
        ModelInfo memory model = _modelInfo[a.modelVersionHash];
        if (model.slotPlusOne == 0 || model.revoked) revert ModelNotApproved(a.modelVersionHash);

        uint256 existing = _batchOf[a.installationId][a.period];
        if (existing != 0 && _batches[existing].status != Status.Rejected) {
            revert DuplicatePeriod(a.installationId, a.period, existing);
        }

        address signer = _recoverAttestor(reg, _digest(a), a.ecdsaSig);

        Status status = a.riskScore > _riskThreshold ? Status.PendingVerifier : Status.Attested;
        batchId = ++_batchCount;
        _batchOf[a.installationId][a.period] = batchId;

        BatchStore storage b = _batches[batchId];
        b.installationId = a.installationId;
        b.attestor = signer;
        b.period = a.period;
        b.seeDeclared = a.seeDeclared;
        // forge-lint: disable-next-line(unsafe-typecast)
        b.attestedAt = uint32(block.timestamp); // uint32 seconds are good until 2106
        b.verifiedTonnes = a.verifiedTonnes;
        b.riskScore = a.riskScore;
        b.algId = a.algId;
        b.status = status;
        // forge-lint: disable-next-line(unsafe-typecast)
        b.modelIdx = uint8(model.slotPlusOne - 1); // slotPlusOne is in 1..256
        b.commitment = a.commitment;
        b.pqSigHash = a.pqSigHash;
        b.cidDigest = a.cidDigest;

        // The only external calls above are staticcalls to the trusted registry proxy.
        // forge-lint: disable-next-line(reentrancy-events)
        emit Attested(batchId, a.installationId, a.period, a.commitment, a.riskScore, status, cid);
    }

    /// @notice An accredited verifier resolves a batch that scored above the risk threshold.
    /// @dev VERIFIER_ROLE only; `reasonHash` must be non-zero; the batch must be PendingVerifier; the
    ///      verifier must be neither the batch's attestor nor the current operator of its installation
    ///      (`VerifierConflict`). Approve -> Attested, reject -> Rejected (which frees the
    ///      (installation, period) slot for a corrected resubmission).
    /// @param batchId The pending batch.
    /// @param approve True to approve, false to reject.
    /// @param reasonHash keccak256 of the verifier's written reasoning (text is held off-chain). Non-zero.
    function verifierSign(uint256 batchId, bool approve, bytes32 reasonHash) external {
        IRegistryView reg = _enforceNotPaused();
        if (!reg.hasRole(VERIFIER_ROLE, msg.sender)) revert MissingRole(VERIFIER_ROLE, msg.sender);
        if (reasonHash == bytes32(0)) revert ZeroReasonHash();

        BatchStore storage b = _batches[batchId];
        Status current = b.status;
        if (current == Status.None) revert UnknownBatch(batchId);
        if (current != Status.PendingVerifier) revert NotPending(batchId);
        if (msg.sender == b.attestor || msg.sender == reg.operatorOf(b.installationId)) {
            revert VerifierConflict(batchId, msg.sender);
        }

        b.verifier = msg.sender;
        b.reasonHash = reasonHash;
        b.status = approve ? Status.Attested : Status.Rejected;

        emit VerifierDecision(batchId, msg.sender, approve, reasonHash);
    }

    /// @notice Withdraw a batch that turned out to be wrong. ADMIN only.
    /// @dev PendingVerifier or Attested -> Rejected, emitting `BatchRevoked`. The reason lives in the event
    ///      (the stored `reasonHash` / `verifier` fields keep the original verifier decision). The
    ///      (installation, period) slot is freed for a corrected resubmission, exactly as after a verifier
    ///      rejection, and the allocator refuses new allocations. Allocations already made are not unwound.
    ///      Works while paused (incident response).
    /// @param batchId The batch to withdraw.
    /// @param reasonHash keccak256 of the written justification (text held off-chain). Non-zero.
    function revokeBatch(uint256 batchId, bytes32 reasonHash) external {
        _requireAdmin();
        if (reasonHash == bytes32(0)) revert ZeroReasonHash();
        BatchStore storage b = _batches[batchId];
        Status current = b.status;
        if (current == Status.None) revert UnknownBatch(batchId);
        if (current == Status.Rejected) revert AlreadyRejected(batchId);
        b.status = Status.Rejected;
        emit BatchRevoked(batchId, msg.sender, reasonHash);
    }

    // ---------------------------------------------------------------- views

    /// @notice Full record of a batch.
    /// @param batchId Batch id (1-based).
    /// @return r The batch; `attestedAt` is the submission time, `verifier`/`reasonHash` are zero until a decision.
    function getBatch(uint256 batchId) external view returns (Batch memory r) {
        BatchStore memory b = _batches[batchId];
        if (b.status == Status.None) revert UnknownBatch(batchId);
        r.installationId = b.installationId;
        r.period = b.period;
        r.seeDeclared = b.seeDeclared;
        r.verifiedTonnes = b.verifiedTonnes;
        r.riskScore = b.riskScore;
        r.algId = b.algId;
        r.status = b.status;
        r.attestedAt = b.attestedAt;
        r.commitment = b.commitment;
        r.modelVersionHash = _models[b.modelIdx];
        r.pqSigHash = b.pqSigHash;
        r.cidDigest = b.cidDigest;
        r.attestor = b.attestor;
        r.verifier = b.verifier;
        r.reasonHash = b.reasonHash;
    }

    /// @notice The current batch for an installation-period.
    /// @param installationId Installation identifier.
    /// @param period YYYYMM.
    /// @return The batch id, or 0 if none. After a rejection and resubmission this is the newest batch.
    function batchOf(bytes32 installationId, uint32 period) external view returns (uint256) {
        return _batchOf[installationId][period];
    }

    /// @notice Number of batches ever created (also the id of the newest batch).
    /// @return The batch count.
    function batchCount() external view returns (uint256) {
        return _batchCount;
    }

    /// @notice Slim read for the allocator: two storage words instead of the full record.
    /// @param batchId Batch id.
    /// @return status Batch status (None if the batch does not exist).
    /// @return verifiedTonnes Verified tonnes of product.
    /// @return installationId Installation the batch belongs to.
    /// @return attestor The recovered ECDSA signer that attested the batch.
    function allocInfo(uint256 batchId)
        external
        view
        returns (Status status, uint64 verifiedTonnes, bytes32 installationId, address attestor)
    {
        BatchStore storage b = _batches[batchId];
        return (b.status, b.verifiedTonnes, b.installationId, b.attestor);
    }

    /// @notice The EIP-712 digest the attestor signs for these fields (`cidDigest` and `ecdsaSig` are ignored).
    /// @dev Domain {name "EmbedProof", version "1", chainId block.chainid, verifyingContract address(this)};
    ///      struct `Attestation(bytes32 installationId,uint32 period,bytes32 commitment,uint32 seeDeclared,
    ///      uint64 verifiedTonnes,uint8 riskScore,bytes32 modelVersionHash,uint8 algId,bytes32 pqSigHash)`.
    /// @param a The attestation fields.
    /// @return The digest `keccak256("\x19\x01" || domainSeparator || structHash)`.
    function hashAttestation(AttestInput calldata a) external view returns (bytes32) {
        return _digest(a);
    }

    /// @notice The EIP-712 domain separator for this deployment.
    /// @return The separator for the current chain id and this contract's address.
    function domainSeparator() external view returns (bytes32) {
        return _domainSeparator();
    }

    /// @notice Risk score above which a verifier co-signature is required.
    /// @return The threshold (default 60).
    function riskThreshold() external view returns (uint8) {
        return _riskThreshold;
    }

    /// @notice Whether a model-version hash may be attested against.
    /// @param modelVersionHash sha256 of the frozen model artefact.
    /// @return True if approved.
    function isModelApproved(bytes32 modelVersionHash) external view returns (bool) {
        ModelInfo memory model = _modelInfo[modelVersionHash];
        return model.slotPlusOne != 0 && !model.revoked;
    }

    /// @notice The registry that supplies roles, pause state and the timelock.
    /// @return The registry proxy address.
    function registry() external view returns (address) {
        return _registry;
    }

    // ---------------------------------------------------------------- admin

    /// @notice Set the risk score above which a batch needs verifier co-signature. TIMELOCK only: the
    ///         threshold gates the verifier co-sign, so changing it takes the same 24 h delay as an upgrade.
    /// @param t New threshold, 0..100.
    function setRiskThreshold(uint8 t) external {
        if (msg.sender != IRegistryView(_registry).timelock()) revert NotTimelock();
        if (t > MAX_RISK_SCORE) revert InvalidRiskScore(t);
        _riskThreshold = t;
        emit RiskThresholdSet(t);
    }

    /// @notice Approve a model-version hash for attestation. ADMIN only. Idempotent for an approved hash.
    /// @dev At most 256 distinct models can be registered (`ModelApproved` carries a uint8 index). Approving a
    ///      previously revoked hash re-enables it under its original index.
    /// @param modelVersionHash sha256 of the frozen model artefact.
    function approveModel(bytes32 modelVersionHash) external {
        _requireAdmin();
        ModelInfo storage model = _modelInfo[modelVersionHash];
        uint16 slotPlusOne = model.slotPlusOne;
        if (slotPlusOne == 0) {
            uint256 n = _models.length;
            if (n >= MAX_MODELS) revert TooManyModels();
            _models.push(modelVersionHash);
            // n < 256 (checked above), so both narrowing casts are lossless.
            // forge-lint: disable-next-line(unsafe-typecast)
            model.slotPlusOne = uint16(n + 1);
            // forge-lint: disable-next-line(unsafe-typecast)
            emit ModelApproved(modelVersionHash, uint8(n));
        } else if (model.revoked) {
            model.revoked = false;
            // slotPlusOne is in 1..256, so the index fits a uint8.
            // forge-lint: disable-next-line(unsafe-typecast)
            emit ModelApproved(modelVersionHash, uint8(slotPlusOne - 1));
        }
    }

    /// @notice Withdraw a model-version hash: new `attest` calls with it revert `ModelNotApproved`. ADMIN only.
    /// @dev Reverts `ModelNotApproved` if the hash is not currently approved (never approved, or already
    ///      revoked). Batches already created, including PendingVerifier ones, are unaffected and keep
    ///      resolving their model hash through `getBatch`.
    /// @param modelVersionHash sha256 of the model artefact to withdraw.
    function revokeModel(bytes32 modelVersionHash) external {
        _requireAdmin();
        ModelInfo storage model = _modelInfo[modelVersionHash];
        if (model.slotPlusOne == 0 || model.revoked) revert ModelNotApproved(modelVersionHash);
        model.revoked = true;
        emit ModelRevoked(modelVersionHash);
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

    function _requireAdmin() private view {
        if (!IRegistryView(_registry).hasRole(ADMIN_ROLE, msg.sender)) revert MissingRole(ADMIN_ROLE, msg.sender);
    }

    function _domainSeparator() private view returns (bytes32) {
        return keccak256(abi.encode(DOMAIN_TYPEHASH, NAME_HASH, VERSION_HASH, block.chainid, address(this)));
    }

    function _digest(AttestInput calldata a) private view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                ATTESTATION_TYPEHASH,
                a.installationId,
                a.period,
                a.commitment,
                a.seeDeclared,
                a.verifiedTonnes,
                a.riskScore,
                a.modelVersionHash,
                a.algId,
                a.pqSigHash
            )
        );
        return MessageHashUtils.toTypedDataHash(_domainSeparator(), structHash);
    }

    /// @dev Recover the EIP-712 signer; it must hold ATTESTOR_ROLE. A malformed or non-canonical
    ///      signature reports `address(0)`.
    function _recoverAttestor(IRegistryView reg, bytes32 digest, bytes calldata sig)
        private
        view
        returns (address signer)
    {
        ECDSA.RecoverError err;
        (signer, err,) = ECDSA.tryRecoverCalldata(digest, sig);
        if (err != ECDSA.RecoverError.NoError) revert BadAttestorSignature(address(0));
        if (!reg.hasRole(ATTESTOR_ROLE, signer)) revert BadAttestorSignature(signer);
    }
}
