// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {InstallationRegistry} from "../src/InstallationRegistry.sol";
import {EmissionsAttestation} from "../src/EmissionsAttestation.sol";
import {ConsignmentAllocator} from "../src/ConsignmentAllocator.sol";
import {AttestInput, Status, Sector, Route} from "../src/interfaces/IEmbedProof.sol";

/// @dev Shared fixture: the full system behind ERC1967 proxies, wired exactly as script/Deploy.s.sol does.
abstract contract Base is Test {
    uint256 internal constant TIMELOCK_DELAY = 86400;
    uint32 internal constant PERIOD = 202609;
    string internal constant CID = "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy";
    // Literal on purpose: a `constant` built with sha256(...) is an external precompile call at every use,
    // which would silently consume a pending vm.prank / vm.expectRevert.
    bytes32 internal constant CID_DIGEST = 0xeb333942340dfa7da54597d78b894f35310289e75ec3a84137a197a37ab1d164; // sha256("bundle-bytes")
    bytes32 internal constant PQ_SIG_HASH = keccak256("ml-dsa-44-signature");
    bytes32 internal constant REASON = keccak256("reason");

    // EIP-712, re-implemented here independently of the contract (INTERFACES section 9.A).
    bytes32 internal constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 internal constant ATTESTATION_TYPEHASH = keccak256(
        "Attestation(bytes32 installationId,uint32 period,bytes32 commitment,uint32 seeDeclared,uint64 verifiedTonnes,uint8 riskScore,bytes32 modelVersionHash,uint8 algId,bytes32 pqSigHash)"
    );
    bytes32 internal constant MODEL = 0x83d653bca132c08183f985fb30e35a317074429d5cb89d1747bab4f81c380a82; // sha256("embedproof_model_v1.json")

    bytes32 internal constant EAF_ID = bytes32("TR-EAF-0001");
    bytes32 internal constant BOF_ID = bytes32("TR-BOF-0002");

    bytes32 internal constant ADMIN_ROLE = 0x00;
    bytes32 internal OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 internal VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 internal ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");
    bytes32 internal REGULATOR_ROLE = keccak256("REGULATOR_ROLE");

    address internal admin = makeAddr("admin");
    address internal verifier = makeAddr("verifier");
    address internal exporterA = makeAddr("exporterA");
    address internal exporterB = makeAddr("exporterB");
    address internal importerA = makeAddr("importerA");
    address internal importerB = makeAddr("importerB");
    address internal regulator = makeAddr("regulator");
    address internal stranger = makeAddr("stranger");
    address internal attestor;
    uint256 internal attestorPk;
    address internal rogue;
    uint256 internal roguePk;

    TimelockController internal timelock;
    InstallationRegistry internal registry;
    EmissionsAttestation internal attestation;
    ConsignmentAllocator internal allocator;
    address internal registryImpl;
    address internal attestationImpl;
    address internal allocatorImpl;

    function setUp() public virtual {
        (attestor, attestorPk) = makeAddrAndKey("attestor");
        (rogue, roguePk) = makeAddrAndKey("rogue");

        address[] memory who = new address[](1);
        who[0] = admin;
        timelock = new TimelockController(TIMELOCK_DELAY, who, who, address(0));

        registryImpl = address(new InstallationRegistry());
        registry = InstallationRegistry(
            address(
                new ERC1967Proxy(
                    registryImpl, abi.encodeCall(InstallationRegistry.initialize, (admin, address(timelock)))
                )
            )
        );
        attestationImpl = address(new EmissionsAttestation());
        attestation = EmissionsAttestation(
            address(
                new ERC1967Proxy(attestationImpl, abi.encodeCall(EmissionsAttestation.initialize, (address(registry))))
            )
        );
        allocatorImpl = address(new ConsignmentAllocator());
        allocator = ConsignmentAllocator(
            address(
                new ERC1967Proxy(
                    allocatorImpl,
                    abi.encodeCall(ConsignmentAllocator.initialize, (address(registry), address(attestation)))
                )
            )
        );

        vm.startPrank(admin);
        registry.grantRole(ATTESTOR_ROLE, attestor);
        registry.grantRole(VERIFIER_ROLE, verifier);
        registry.grantRole(REGULATOR_ROLE, regulator);
        attestation.approveModel(MODEL);
        registry.registerInstallation(EAF_ID, "TR", Sector.STEEL, Route.EAF, 300_000, exporterA);
        registry.registerInstallation(BOF_ID, "TR", Sector.STEEL, Route.BF_BOF, 3_500_000, exporterB);
        vm.stopPrank();
    }

    // ------------------------------------------------------------ helpers

    function _domainSeparator(uint256 chainId_, address verifyingContract) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(DOMAIN_TYPEHASH, keccak256("EmbedProof"), keccak256("1"), chainId_, verifyingContract)
        );
    }

    function _structHash(AttestInput memory a) internal pure returns (bytes32) {
        return keccak256(
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
    }

    /// @dev EIP-712 digest for an explicit (chain id, verifying contract) pair.
    function _digestFor(AttestInput memory a, uint256 chainId_, address verifyingContract)
        internal
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked("\x19\x01", _domainSeparator(chainId_, verifyingContract), _structHash(a)));
    }

    /// @dev EIP-712 digest for the attestation proxy of this fixture on the current chain.
    function _digest(AttestInput memory a) internal view returns (bytes32) {
        return _digestFor(a, block.chainid, address(attestation));
    }

    /// @dev Real secp256k1 EIP-712 signature over `digest`.
    function _signDigest(uint256 pk, bytes32 digest) internal pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    /// @dev Sign the Attestation struct of `a` for this fixture's attestation proxy.
    function _signAttestation(uint256 pk, AttestInput memory a) internal view returns (bytes memory) {
        return _signDigest(pk, _digest(a));
    }

    function _commitmentFor(bytes32 id, uint32 period, uint8 risk) internal pure returns (bytes32) {
        return keccak256(abi.encode("embedproof.attestation.v1", id, period, risk));
    }

    function _input(bytes32 id, uint32 period, uint8 risk, uint64 tonnes) internal view returns (AttestInput memory a) {
        bytes32 commitment = _commitmentFor(id, period, risk);
        a = AttestInput({
            installationId: id,
            period: period,
            commitment: commitment,
            seeDeclared: 4190,
            verifiedTonnes: tonnes,
            riskScore: risk,
            modelVersionHash: MODEL,
            algId: 1,
            pqSigHash: PQ_SIG_HASH,
            cidDigest: CID_DIGEST,
            ecdsaSig: ""
        });
        a.ecdsaSig = _signAttestation(attestorPk, a);
    }

    /// @dev Attest as the attestor service and return the new batch id.
    function _attest(bytes32 id, uint32 period, uint8 risk, uint64 tonnes) internal returns (uint256 batchId) {
        AttestInput memory a = _input(id, period, risk, tonnes);
        vm.prank(attestor);
        batchId = attestation.attest(a, CID);
    }

    /// @dev A green EAF batch (risk 10 <= 60 -> Attested immediately).
    function _greenBatch(uint64 tonnes) internal returns (uint256) {
        return _attest(EAF_ID, PERIOD, 10, tonnes);
    }

    /// @dev A red BOF batch (risk 90 > 60 -> PendingVerifier).
    function _redBatch(uint64 tonnes) internal returns (uint256) {
        return _attest(BOF_ID, PERIOD, 90, tonnes);
    }

    function _verify(uint256 batchId, bool approve) internal {
        vm.prank(verifier);
        attestation.verifierSign(batchId, approve, REASON);
    }

    function _allocate(uint256 batchId, bytes32 consignmentId, address importer, uint64 tonnes) internal {
        vm.prank(exporterA);
        allocator.allocate(batchId, consignmentId, importer, 72083900, tonnes);
    }

    function _selectorOf(bytes memory revertData) internal pure returns (bytes4 sel) {
        if (revertData.length >= 4) {
            assembly {
                sel := mload(add(revertData, 0x20))
            }
        }
    }
}
