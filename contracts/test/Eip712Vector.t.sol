// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

import {Base} from "./Base.t.sol";
import {EmissionsAttestation} from "../src/EmissionsAttestation.sol";
import {IEmissionsAttestation, AttestInput, Batch, Status} from "../src/interfaces/IEmbedProof.sol";

/// @dev Golden vector shared with the attestor and web builds (test/vectors/eip712_vector.json).
///      The file was produced against a live Anvil deployment and cross-checked three ways: Python
///      `eth_account.encode_typed_data`, the deployed contract's `hashAttestation`, and this test, which
///      re-creates the proxy at the vector's verifyingContract address and demands the same digest,
///      the same domain separator and an attest() that accepts the signed vector.
contract Eip712VectorTest is Base {
    string internal json;

    function _vector() internal returns (AttestInput memory a, address vc) {
        json = vm.readFile("test/vectors/eip712_vector.json");
        vc = vm.parseJsonAddress(json, ".domain.verifyingContract");
        a = AttestInput({
            installationId: vm.parseJsonBytes32(json, ".message.installationId"),
            period: uint32(vm.parseJsonUint(json, ".message.period")),
            commitment: vm.parseJsonBytes32(json, ".message.commitment"),
            seeDeclared: uint32(vm.parseJsonUint(json, ".message.seeDeclared")),
            verifiedTonnes: uint64(vm.parseJsonUint(json, ".message.verifiedTonnes")),
            riskScore: uint8(vm.parseJsonUint(json, ".message.riskScore")),
            modelVersionHash: vm.parseJsonBytes32(json, ".message.modelVersionHash"),
            algId: uint8(vm.parseJsonUint(json, ".message.algId")),
            pqSigHash: vm.parseJsonBytes32(json, ".message.pqSigHash"),
            cidDigest: vm.parseJsonBytes32(json, ".cidDigest"),
            ecdsaSig: vm.parseJsonBytes(json, ".signature")
        });
    }

    function test_Eip712_GoldenVectorReproducedByTheContract() public {
        (AttestInput memory a, address vc) = _vector();
        vm.chainId(vm.parseJsonUint(json, ".domain.chainId"));
        // Put a fresh attestation proxy at the vector's verifyingContract (the standard Anvil address).
        deployCodeTo(
            "ERC1967Proxy.sol:ERC1967Proxy",
            abi.encode(attestationImpl, abi.encodeCall(EmissionsAttestation.initialize, (address(registry)))),
            vc
        );
        EmissionsAttestation att = EmissionsAttestation(vc);

        // 1. domain separator, struct hash and digest agree with the file (and with the independent re-implementation here)
        assertEq(att.domainSeparator(), vm.parseJsonBytes32(json, ".domainSeparator"), "domain separator");
        assertEq(_structHash(a), vm.parseJsonBytes32(json, ".structHash"), "struct hash");
        bytes32 digest = vm.parseJsonBytes32(json, ".digest");
        assertEq(att.hashAttestation(a), digest, "contract digest");
        assertEq(_digestFor(a, block.chainid, vc), digest, "test-side digest");

        // 2. the signature recovers to the documented signer (Anvil account #1)
        address signer = vm.parseJsonAddress(json, ".signer");
        assertEq(ECDSA.recover(digest, a.ecdsaSig), signer, "signer");
        assertEq(signer, 0x70997970C51812dc3A010C7d01b50e0d17dc79C8);

        // 3. attest() accepts the vector verbatim
        vm.startPrank(admin);
        registry.grantRole(ATTESTOR_ROLE, signer);
        att.approveModel(a.modelVersionHash);
        vm.stopPrank();
        vm.prank(signer);
        uint256 id = att.attest(a, "bafkreivector");
        Batch memory b = att.getBatch(id);
        assertEq(b.attestor, signer);
        assertEq(b.commitment, a.commitment);
        assertEq(uint8(b.status), uint8(Status.Attested));

        // 4. and rejects it the moment one signed field changes
        a.verifiedTonnes += 1;
        a.period += 1;
        address recovered = ECDSA.recover(att.hashAttestation(a), a.ecdsaSig);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(signer);
        att.attest(a, "bafkreivector");
    }
}
