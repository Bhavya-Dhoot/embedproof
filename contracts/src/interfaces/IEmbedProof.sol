// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// EmbedProof shared interface contract (v1) - verbatim from docs/INTERFACES.md section 3.
// Do not edit independently of that document: web, attestor and deploy tooling bind to these ABIs.

enum Sector { STEEL, CEMENT }                                   // 0,1
enum Route  { BF_BOF, EAF, DRI_EAF, CEMENT_DRY, CEMENT_WET }    // 0..4
enum Status { None, PendingVerifier, Attested, Rejected }       // 0..3

struct Installation {
    bytes32 id; bytes2 country; Sector sector; Route route;
    uint64 capacityTpy; address operator; bool exists;
}

interface IInstallationRegistry {
    event InstallationRequested(bytes32 indexed id, address indexed requester, bytes2 country, Sector sector, Route route, uint64 capacityTpy);
    event InstallationRegistered(bytes32 indexed id, address indexed operator, bytes2 country, Sector sector, Route route, uint64 capacityTpy);
    error UnknownInstallation(bytes32 id);
    error InstallationExists(bytes32 id);
    error NotTimelock();
    event AttestorPqKeySet(address indexed attestor, bytes32 pqKeyHash);

    function initialize(address admin, address timelock) external;
    function requestInstallation(bytes32 id, bytes2 country, Sector sector, Route route, uint64 capacityTpy) external; // anyone; event only (no storage)
    function registerInstallation(bytes32 id, bytes2 country, Sector sector, Route route, uint64 capacityTpy, address operator) external; // ADMIN; also grants OPERATOR_ROLE to operator
    function getInstallation(bytes32 id) external view returns (Installation memory); // reverts UnknownInstallation
    function operatorOf(bytes32 id) external view returns (address);                   // address(0) if unknown
    function installationIds() external view returns (bytes32[] memory);
    function timelock() external view returns (address);
    function setAttestorPqKey(address attestor, bytes32 pqKeyHash) external; // ADMIN; anchors keccak256(raw ML-DSA-44 public key)
    function attestorPqKey(address attestor) external view returns (bytes32); // 0 if none
    function pause() external;   // ADMIN
    function unpause() external; // ADMIN
    // + OZ AccessControl: hasRole, grantRole, revokeRole, getRoleAdmin; OZ Pausable: paused()
    // + role constants as public bytes32: OPERATOR_ROLE, VERIFIER_ROLE, ATTESTOR_ROLE, REGULATOR_ROLE
}

struct AttestInput {
    bytes32 installationId;
    uint32  period;            // YYYYMM, e.g. 202609
    bytes32 commitment;        // keccak256(JCS(attestation document)) - see section 5
    uint32  seeDeclared;       // tCO2 per t product x 1e4 (round half up)
    uint64  verifiedTonnes;    // whole tonnes of product
    uint8   riskScore;         // 0..100
    bytes32 modelVersionHash;  // sha256 of the frozen model artefact; must be approved
    uint8   algId;             // 1 = ECDSA-secp256k1 (EIP-191 over commitment) + ML-DSA-44 (FIPS 204)
    bytes32 pqSigHash;         // keccak256(raw ML-DSA-44 signature bytes)
    bytes32 cidDigest;         // sha2-256 digest of the bundle bytes = digest inside CIDv1(raw, sha2-256)
    bytes   ecdsaSig;          // 65-byte EIP-191 signature over `commitment`; signer must hold ATTESTOR_ROLE
}

struct Batch {
    bytes32 installationId; uint32 period; uint32 seeDeclared; uint64 verifiedTonnes;
    uint8 riskScore; uint8 algId; Status status; uint32 attestedAt;
    bytes32 commitment; bytes32 modelVersionHash; bytes32 pqSigHash; bytes32 cidDigest;
    address attestor;   // recovered ECDSA signer
    address verifier;   // set by verifierSign
    bytes32 reasonHash; // set by verifierSign
}

interface IEmissionsAttestation {
    event Attested(uint256 indexed batchId, bytes32 indexed installationId, uint32 period, bytes32 commitment, uint8 riskScore, Status status, string cid);
    event VerifierDecision(uint256 indexed batchId, address indexed verifier, bool approved, bytes32 reasonHash);
    event ModelApproved(bytes32 indexed modelVersionHash, uint8 index);
    event RiskThresholdSet(uint8 threshold);
    event ModelRevoked(bytes32 indexed modelVersionHash);
    error DuplicatePeriod(bytes32 installationId, uint32 period, uint256 existingBatchId);
    error UnknownBatch(uint256 batchId);
    error NotPending(uint256 batchId);
    error InvalidRiskScore(uint8 riskScore);
    error InvalidAlgId(uint8 algId);
    error ModelNotApproved(bytes32 modelVersionHash);
    error BadAttestorSignature(address recovered);
    error MissingRole(bytes32 role, address account);
    error NotTimelock();

    function initialize(address registry) external;
    function attest(AttestInput calldata a, string calldata cid) external returns (uint256 batchId);
        // ATTESTOR_ROLE on msg.sender; installation must exist; algId==1; riskScore<=100; model approved;
        // ecrecover(toEthSignedMessageHash(commitment), ecdsaSig) must hold ATTESTOR_ROLE;
        // (installationId, period) unused unless the previous batch for it is Rejected -> else DuplicatePeriod;
        // status = riskScore > riskThreshold ? PendingVerifier : Attested. batchIds start at 1.
        // `cid` is emitted in the event only (not stored); the client rebuilds it from cidDigest.
    function verifierSign(uint256 batchId, bool approve, bytes32 reasonHash) external; // VERIFIER_ROLE; PendingVerifier -> Attested|Rejected
    function getBatch(uint256 batchId) external view returns (Batch memory);           // reverts UnknownBatch
    function batchOf(bytes32 installationId, uint32 period) external view returns (uint256); // 0 if none
    function batchCount() external view returns (uint256);
    function allocInfo(uint256 batchId) external view returns (Status status, uint64 verifiedTonnes, bytes32 installationId); // slim view for the allocator
    function riskThreshold() external view returns (uint8);       // default 60
    function setRiskThreshold(uint8 t) external;                  // ADMIN
    function approveModel(bytes32 modelVersionHash) external;     // ADMIN
    function revokeModel(bytes32 modelVersionHash) external;      // ADMIN; emits ModelRevoked; later attests revert ModelNotApproved
    function isModelApproved(bytes32 modelVersionHash) external view returns (bool);
    function registry() external view returns (address);
}

struct Allocation { uint256 batchId; address importer; uint32 cnCode; uint64 tonnes; bool claimed; }

interface IConsignmentAllocator {
    event Allocated(uint256 indexed batchId, bytes32 indexed consignmentId, address indexed importer, uint32 cnCode, uint64 tonnes, uint64 remaining);
    event Claimed(uint256 indexed batchId, bytes32 indexed consignmentId, address indexed importer);
    error DoubleClaimBlocked(uint256 batchId, uint64 requested, uint64 remaining);
    error ConsignmentReused(bytes32 consignmentId);
    error BatchNotAttested(uint256 batchId);
    error NotBatchOperator(uint256 batchId, address caller);
    error NotConsignee(bytes32 consignmentId, address caller);
    error AlreadyClaimed(bytes32 consignmentId);
    error UnknownConsignment(bytes32 consignmentId);
    error ZeroTonnes();
    error ZeroImporter();
    error NotTimelock();

    function initialize(address registry, address attestation) external;
    function allocate(uint256 batchId, bytes32 consignmentId, address importer, uint32 cnCode, uint64 tonnes) external;
        // caller must be registry.operatorOf(batch.installationId); batch status Attested;
        // consignmentId never used -> else ConsignmentReused; allocated + tonnes <= verifiedTonnes -> else DoubleClaimBlocked
    function claim(bytes32 consignmentId) external;               // only allocation.importer; once
    function getAllocation(bytes32 consignmentId) external view returns (Allocation memory); // reverts UnknownConsignment
    function allocatedTonnes(uint256 batchId) external view returns (uint64);
    function remainingTonnes(uint256 batchId) external view returns (uint64);
}
