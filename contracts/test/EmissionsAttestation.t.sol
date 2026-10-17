// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {Base} from "./Base.t.sol";
import {EmissionsAttestation} from "../src/EmissionsAttestation.sol";
import {
    IEmissionsAttestation, IInstallationRegistry, AttestInput, Batch, Status
} from "../src/interfaces/IEmbedProof.sol";

/// @dev FR-C3 (attest), FR-C4 (verifierSign) and the attestation-side threats of PRD section 9
///      (replay, forged / malleable signatures, unapproved model).
contract EmissionsAttestationTest is Base {
    // secp256k1 group order, for the malleability test
    uint256 internal constant SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;

    // ---------------------------------------------------- FR-C3: attest ok

    function test_Attest_Green_Succeeds() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.prank(attestor);
        uint256 id = attestation.attest(a, CID);

        assertEq(id, 1, "batchIds start at 1");
        assertEq(attestation.batchCount(), 1);
        assertEq(attestation.batchOf(EAF_ID, PERIOD), 1);

        Batch memory b = attestation.getBatch(1);
        assertEq(uint8(b.status), uint8(Status.Attested));
        assertEq(b.installationId, EAF_ID);
        assertEq(b.period, PERIOD);
        assertEq(b.seeDeclared, 4190);
        assertEq(b.verifiedTonnes, 5000);
        assertEq(b.riskScore, 10);
        assertEq(b.algId, 1);
        assertEq(b.attestedAt, uint32(block.timestamp));
        assertEq(b.commitment, a.commitment);
        assertEq(b.modelVersionHash, MODEL);
        assertEq(b.pqSigHash, PQ_SIG_HASH);
        assertEq(b.cidDigest, CID_DIGEST);
        assertEq(b.attestor, attestor, "attestor = recovered ECDSA signer");
        assertEq(b.verifier, address(0));
        assertEq(b.reasonHash, bytes32(0));
    }

    function test_Attest_AboveThreshold_SetsPendingVerifier() public {
        uint256 id = _redBatch(250_000);
        Batch memory b = attestation.getBatch(id);
        assertEq(uint8(b.status), uint8(Status.PendingVerifier));
        assertEq(b.riskScore, 90);
        assertEq(b.verifier, address(0));
    }

    function test_Attest_ThresholdBoundary_RiskEqualToThresholdIsAttested() public {
        assertEq(attestation.riskThreshold(), 60, "default threshold");
        uint256 atThreshold = _attest(EAF_ID, 202601, 60, 100);
        uint256 aboveThreshold = _attest(EAF_ID, 202602, 61, 100);
        assertEq(uint8(attestation.getBatch(atThreshold).status), uint8(Status.Attested));
        assertEq(uint8(attestation.getBatch(aboveThreshold).status), uint8(Status.PendingVerifier));
    }

    function test_Attest_RiskScoreBounds() public {
        _attest(EAF_ID, 202601, 0, 1);
        _attest(EAF_ID, 202602, 100, 1);
        AttestInput memory a = _input(EAF_ID, 202603, 101, 1);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.InvalidRiskScore.selector, uint8(101)));
        vm.prank(attestor);
        attestation.attest(a, CID);
        a.riskScore = 255;
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.InvalidRiskScore.selector, uint8(255)));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BatchIdsIncrementAcrossInstallationsAndPeriods() public {
        assertEq(_attest(EAF_ID, 202601, 10, 1), 1);
        assertEq(_attest(EAF_ID, 202602, 10, 1), 2);
        assertEq(_attest(BOF_ID, 202601, 10, 1), 3);
        assertEq(attestation.batchCount(), 3);
        assertEq(attestation.batchOf(BOF_ID, 202601), 3);
        assertEq(attestation.batchOf(BOF_ID, 202602), 0, "unused period");
    }

    function test_Attest_EventCarriesBatchIdAndCid() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.expectEmit(true, true, false, true, address(attestation));
        emit IEmissionsAttestation.Attested(1, EAF_ID, PERIOD, a.commitment, 10, Status.Attested, CID);
        vm.prank(attestor);
        attestation.attest(a, CID);

        AttestInput memory r = _input(BOF_ID, PERIOD, 90, 250_000);
        vm.expectEmit(true, true, false, true, address(attestation));
        emit IEmissionsAttestation.Attested(2, BOF_ID, PERIOD, r.commitment, 90, Status.PendingVerifier, CID);
        vm.prank(attestor);
        attestation.attest(r, CID);
    }

    function test_Attest_CidIsEmittedNotStored() public {
        // One attest writes at most 8 distinct words: batch words 0-5 (verifier/reasonHash words are
        // untouched), the batchOf mapping slot and the packed registry|count|threshold word.
        // A stored 59-byte CID string would add at least 2 more.
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.record();
        vm.prank(attestor);
        attestation.attest(a, CID);
        (, bytes32[] memory writes) = vm.accesses(address(attestation));
        uint256 distinct;
        for (uint256 i; i < writes.length; i++) {
            bool seen;
            for (uint256 j; j < i; j++) {
                if (writes[j] == writes[i]) {
                    seen = true;
                    break;
                }
            }
            if (!seen) distinct++;
        }
        assertEq(distinct, 8, "exactly 8 distinct storage words written");
    }

    function test_Attest_SignerIsStoredNotSender() public {
        // A second ATTESTOR_ROLE holder relays a signature produced by the attestor key.
        address relayer = makeAddr("relayer");
        vm.prank(admin);
        registry.grantRole(ATTESTOR_ROLE, relayer);

        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.prank(relayer);
        uint256 id = attestation.attest(a, CID);
        assertEq(attestation.getBatch(id).attestor, attestor);
    }

    // ------------------------------------------------- attest: access/input

    function test_Attest_NonAttestorSenderReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        address[5] memory callers = [stranger, admin, verifier, exporterA, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ATTESTOR_ROLE, callers[i]));
            vm.prank(callers[i]);
            attestation.attest(a, CID);
        }
        assertEq(attestation.batchCount(), 0);
    }

    function test_Attest_RevokedAttestorReverts() public {
        vm.prank(admin);
        registry.revokeRole(ATTESTOR_ROLE, attestor);
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ATTESTOR_ROLE, attestor));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_UnknownInstallationReverts() public {
        bytes32 ghost = bytes32("TR-GHOST-0009");
        AttestInput memory a = _input(ghost, PERIOD, 10, 5000);
        vm.expectRevert(abi.encodeWithSelector(IInstallationRegistry.UnknownInstallation.selector, ghost));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_InvalidAlgIdReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.algId = 0;
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.InvalidAlgId.selector, uint8(0)));
        vm.prank(attestor);
        attestation.attest(a, CID);
        a.algId = 2;
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.InvalidAlgId.selector, uint8(2)));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_UnapprovedModelReverts() public {
        bytes32 rogueModel = sha256("embedproof_model_v2_unreviewed.json");
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.modelVersionHash = rogueModel;
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.ModelNotApproved.selector, rogueModel));
        vm.prank(attestor);
        attestation.attest(a, CID);

        // After the admin approves it, the very same input is accepted.
        vm.prank(admin);
        attestation.approveModel(rogueModel);
        vm.prank(attestor);
        uint256 id = attestation.attest(a, CID);
        assertEq(attestation.getBatch(id).modelVersionHash, rogueModel, "second model resolves through its index");
    }

    // ------------------------------------------------ attest: signatures

    function test_Attest_BadSignature_UnauthorisedSignerReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.ecdsaSig = _sign(roguePk, a.commitment); // valid ECDSA, but the key holds no ATTESTOR_ROLE
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, rogue));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_OverDifferentCommitmentReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        // Attestor signed another document; swapping in this commitment must not verify.
        bytes memory other = _sign(attestorPk, keccak256("some other document"));
        a.ecdsaSig = other;
        address recovered = ECDSA.recover(MessageHashUtils.toEthSignedMessageHash(a.commitment), other);
        assertTrue(recovered != attestor, "sanity: recovers a different, unauthorised address");
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_TamperedCommitmentReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.commitment = bytes32(uint256(a.commitment) ^ 1); // one flipped bit after signing
        address recovered = ECDSA.recover(MessageHashUtils.toEthSignedMessageHash(a.commitment), a.ecdsaSig);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_WrongLengthReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.ecdsaSig = hex"deadbeef";
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, address(0)));
        vm.prank(attestor);
        attestation.attest(a, CID);

        a.ecdsaSig = "";
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, address(0)));
        vm.prank(attestor);
        attestation.attest(a, CID);

        a.ecdsaSig = new bytes(64); // ERC-2098 compact length is rejected too
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, address(0)));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_ZeroSignatureReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.ecdsaSig = new bytes(65); // r = s = 0, v = 0 -> ecrecover fails, must not authenticate address(0)
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, address(0)));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_MalleableHighSReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        (bytes32 r, bytes32 s, uint8 v) = _split(a.ecdsaSig);
        // (r, n - s, v ^ 1) recovers the same signer under raw ecrecover; the contract must refuse it.
        bytes32 sHigh = bytes32(SECP256K1_N - uint256(s));
        uint8 vFlip = v == 27 ? 28 : 27;
        assertEq(ecrecover(MessageHashUtils.toEthSignedMessageHash(a.commitment), vFlip, r, sHigh), attestor, "twin is a valid ecrecover signature");
        a.ecdsaSig = abi.encodePacked(r, sHigh, vFlip);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, address(0)));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    // ----------------------------------------- replay / duplicate periods

    function test_Attest_ReplayOfSamePeriodReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.startPrank(attestor);
        uint256 id = attestation.attest(a, CID);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.DuplicatePeriod.selector, EAF_ID, PERIOD, id));
        attestation.attest(a, CID); // byte-identical replay
        vm.stopPrank();
        assertEq(attestation.batchCount(), 1);
    }

    function test_Attest_FreshSignatureForSamePeriodAlsoReverts() public {
        _greenBatch(5000);
        AttestInput memory again = _input(EAF_ID, PERIOD, 5, 9999);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.DuplicatePeriod.selector, EAF_ID, PERIOD, 1));
        vm.prank(attestor);
        attestation.attest(again, CID);
    }

    function test_Attest_PendingPeriodCannotBeReplaced() public {
        uint256 id = _redBatch(250_000);
        AttestInput memory again = _input(BOF_ID, PERIOD, 5, 100);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.DuplicatePeriod.selector, BOF_ID, PERIOD, id));
        vm.prank(attestor);
        attestation.attest(again, CID);
    }

    function test_Attest_ApprovedPendingPeriodCannotBeReplaced() public {
        uint256 id = _redBatch(250_000);
        _verify(id, true);
        AttestInput memory again = _input(BOF_ID, PERIOD, 5, 100);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.DuplicatePeriod.selector, BOF_ID, PERIOD, id));
        vm.prank(attestor);
        attestation.attest(again, CID);
    }

    function test_Attest_ResubmissionAfterRejectedSucceeds() public {
        uint256 first = _redBatch(250_000);
        _verify(first, false);
        assertEq(uint8(attestation.getBatch(first).status), uint8(Status.Rejected));

        uint256 second = _attest(BOF_ID, PERIOD, 20, 5_000);
        assertEq(second, 2);
        assertEq(attestation.batchOf(BOF_ID, PERIOD), 2, "points at the newest batch");
        assertEq(uint8(attestation.getBatch(second).status), uint8(Status.Attested));
        assertEq(uint8(attestation.getBatch(first).status), uint8(Status.Rejected), "audit trail kept");
        assertEq(attestation.getBatch(first).verifiedTonnes, 250_000);

        // The period is now taken again by the new, non-rejected batch.
        AttestInput memory third = _input(BOF_ID, PERIOD, 5, 1);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.DuplicatePeriod.selector, BOF_ID, PERIOD, second));
        vm.prank(attestor);
        attestation.attest(third, CID);
    }

    function test_Attest_RejectedTwiceThenAccepted() public {
        uint256 b1 = _redBatch(250_000);
        _verify(b1, false);
        uint256 b2 = _attest(BOF_ID, PERIOD, 95, 250_000);
        assertEq(uint8(attestation.getBatch(b2).status), uint8(Status.PendingVerifier));
        _verify(b2, false);
        uint256 b3 = _attest(BOF_ID, PERIOD, 40, 4_000);
        assertEq(b3, 3);
        assertEq(attestation.batchOf(BOF_ID, PERIOD), 3);
    }

    // --------------------------------------------------- FR-C4: verifierSign

    function test_VerifierSign_Approve_EmitsAndMovesToAttested() public {
        uint256 id = _redBatch(250_000);
        bytes32 reason = keccak256("operator evidence reviewed on site");

        vm.expectEmit(true, true, false, true, address(attestation));
        emit IEmissionsAttestation.VerifierDecision(id, verifier, true, reason);
        vm.prank(verifier);
        attestation.verifierSign(id, true, reason);

        Batch memory b = attestation.getBatch(id);
        assertEq(uint8(b.status), uint8(Status.Attested));
        assertEq(b.verifier, verifier);
        assertEq(b.reasonHash, reason);
        assertEq(b.attestor, attestor, "attestor untouched by the verifier write");
        assertEq(b.verifiedTonnes, 250_000);
        assertEq(b.riskScore, 90);
        assertEq(b.modelVersionHash, MODEL);
    }

    function test_VerifierSign_Reject_EmitsAndMovesToRejected() public {
        uint256 id = _redBatch(250_000);
        bytes32 reason = keccak256("figures contradict independent satellite data");

        vm.expectEmit(true, true, false, true, address(attestation));
        emit IEmissionsAttestation.VerifierDecision(id, verifier, false, reason);
        vm.prank(verifier);
        attestation.verifierSign(id, false, reason);

        Batch memory b = attestation.getBatch(id);
        assertEq(uint8(b.status), uint8(Status.Rejected));
        assertEq(b.verifier, verifier);
        assertEq(b.reasonHash, reason);
    }

    function test_VerifierSign_OnlyVerifierRole() public {
        uint256 id = _redBatch(250_000);
        address[5] memory callers = [stranger, admin, attestor, exporterB, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, VERIFIER_ROLE, callers[i]));
            vm.prank(callers[i]);
            attestation.verifierSign(id, true, bytes32(0));
        }
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.PendingVerifier));
    }

    function test_VerifierSign_NotPending_OnAttestedBatch() public {
        uint256 id = _greenBatch(5000);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id));
        vm.prank(verifier);
        attestation.verifierSign(id, false, bytes32(0));
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.Attested), "green batch cannot be rejected after the fact");
    }

    function test_VerifierSign_NotPending_AfterDecision() public {
        uint256 id = _redBatch(250_000);
        _verify(id, true);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id));
        vm.prank(verifier);
        attestation.verifierSign(id, false, bytes32(0));

        uint256 id2 = _attest(EAF_ID, 202611, 99, 100);
        _verify(id2, false);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id2));
        vm.prank(verifier);
        attestation.verifierSign(id2, true, bytes32(0));
    }

    function test_VerifierSign_UnknownBatchReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(0)));
        vm.prank(verifier);
        attestation.verifierSign(0, true, bytes32(0));
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(77)));
        vm.prank(verifier);
        attestation.verifierSign(77, true, bytes32(0));
    }

    // --------------------------------------------------------------- views

    function test_GetBatch_UnknownReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(0)));
        attestation.getBatch(0);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(1)));
        attestation.getBatch(1);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, type(uint256).max));
        attestation.getBatch(type(uint256).max);
    }

    function test_AllocInfo_ReflectsStatusAndTonnes() public {
        (Status s0, uint64 t0, bytes32 i0) = attestation.allocInfo(1);
        assertEq(uint8(s0), uint8(Status.None));
        assertEq(t0, 0);
        assertEq(i0, bytes32(0));

        uint256 id = _redBatch(250_000);
        (Status s1, uint64 t1, bytes32 i1) = attestation.allocInfo(id);
        assertEq(uint8(s1), uint8(Status.PendingVerifier));
        assertEq(t1, 250_000);
        assertEq(i1, BOF_ID);

        _verify(id, true);
        (Status s2,,) = attestation.allocInfo(id);
        assertEq(uint8(s2), uint8(Status.Attested));
    }

    function test_BatchOf_UnusedIsZero() public view {
        assertEq(attestation.batchOf(EAF_ID, PERIOD), 0);
    }

    function test_Attest_MaxFieldValuesRoundTrip() public {
        AttestInput memory a = _input(EAF_ID, type(uint32).max, 0, type(uint64).max);
        a.seeDeclared = type(uint32).max;
        vm.prank(attestor);
        uint256 id = attestation.attest(a, CID);
        Batch memory b = attestation.getBatch(id);
        assertEq(b.period, type(uint32).max);
        assertEq(b.seeDeclared, type(uint32).max);
        assertEq(b.verifiedTonnes, type(uint64).max);
        assertEq(b.attestor, attestor);
        assertEq(uint8(b.status), uint8(Status.Attested));
    }

    // ---------------------------------------------------------- admin knobs

    function test_SetRiskThreshold_AdminOnlyAndEmits() public {
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, stranger));
        vm.prank(stranger);
        attestation.setRiskThreshold(10);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, attestor));
        vm.prank(attestor);
        attestation.setRiskThreshold(10);

        vm.expectEmit(false, false, false, true, address(attestation));
        emit IEmissionsAttestation.RiskThresholdSet(10);
        vm.prank(admin);
        attestation.setRiskThreshold(10);
        assertEq(attestation.riskThreshold(), 10);

        // The new threshold drives the status decision.
        assertEq(uint8(attestation.getBatch(_attest(EAF_ID, 202601, 10, 1)).status), uint8(Status.Attested));
        assertEq(uint8(attestation.getBatch(_attest(EAF_ID, 202602, 11, 1)).status), uint8(Status.PendingVerifier));
    }

    function test_SetRiskThreshold_ZeroAndHundredBounds() public {
        vm.startPrank(admin);
        attestation.setRiskThreshold(0);
        attestation.setRiskThreshold(100);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.InvalidRiskScore.selector, uint8(101)));
        attestation.setRiskThreshold(101);
        vm.stopPrank();
        assertEq(attestation.riskThreshold(), 100);
    }

    function test_Initialize_EmitsDefaultThreshold() public {
        address impl = address(new EmissionsAttestation());
        vm.expectEmit(false, false, false, true);
        emit IEmissionsAttestation.RiskThresholdSet(60);
        EmissionsAttestation fresh =
            EmissionsAttestation(address(new ERC1967Proxy(impl, abi.encodeCall(EmissionsAttestation.initialize, (address(registry))))));
        assertEq(fresh.riskThreshold(), 60);
        assertEq(fresh.registry(), address(registry));
    }

    function test_ApproveModel_AdminOnlyEmitsIndexAndIsIdempotent() public {
        bytes32 m2 = sha256("model-two");
        bytes32 m3 = sha256("model-three");
        assertTrue(attestation.isModelApproved(MODEL));
        assertFalse(attestation.isModelApproved(m2));

        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, stranger));
        vm.prank(stranger);
        attestation.approveModel(m2);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, attestor));
        vm.prank(attestor);
        attestation.approveModel(m2);

        vm.expectEmit(true, false, false, true, address(attestation));
        emit IEmissionsAttestation.ModelApproved(m2, 1); // MODEL took index 0 in setUp
        vm.prank(admin);
        attestation.approveModel(m2);
        assertTrue(attestation.isModelApproved(m2));

        vm.recordLogs();
        vm.prank(admin);
        attestation.approveModel(m2); // idempotent: no event, no new index
        assertEq(vm.getRecordedLogs().length, 0);

        vm.expectEmit(true, false, false, true, address(attestation));
        emit IEmissionsAttestation.ModelApproved(m3, 2);
        vm.prank(admin);
        attestation.approveModel(m3);
    }

    function test_ApproveModel_CapAt256() public {
        vm.startPrank(admin);
        for (uint256 i = 1; i < 256; i++) {
            attestation.approveModel(bytes32(i)); // index 0 is MODEL from setUp
        }
        vm.expectRevert(EmissionsAttestation.TooManyModels.selector);
        attestation.approveModel(bytes32(uint256(999)));
        vm.stopPrank();
        assertTrue(attestation.isModelApproved(bytes32(uint256(255))));
        assertFalse(attestation.isModelApproved(bytes32(uint256(999))));
    }

    // ------------------------------------------------------ model revocation

    function test_RevokeModel_AdminOnly() public {
        address[4] memory callers = [stranger, attestor, verifier, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, callers[i]));
            vm.prank(callers[i]);
            attestation.revokeModel(MODEL);
        }
        assertTrue(attestation.isModelApproved(MODEL), "non-admin calls changed nothing");
    }

    function test_RevokeModel_EmitsAndBlocksNewAttestations() public {
        vm.expectEmit(true, false, false, false, address(attestation));
        emit IEmissionsAttestation.ModelRevoked(MODEL);
        vm.prank(admin);
        attestation.revokeModel(MODEL);

        assertFalse(attestation.isModelApproved(MODEL));
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.ModelNotApproved.selector, MODEL));
        vm.prank(attestor);
        attestation.attest(a, CID);
        assertEq(attestation.batchCount(), 0);
        assertEq(attestation.batchOf(EAF_ID, PERIOD), 0, "the period was not consumed");
    }

    function test_RevokeModel_OtherModelsUnaffected() public {
        bytes32 m2 = sha256("model-two");
        vm.startPrank(admin);
        attestation.approveModel(m2);
        attestation.revokeModel(MODEL);
        vm.stopPrank();

        assertTrue(attestation.isModelApproved(m2));
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.modelVersionHash = m2;
        vm.prank(attestor);
        uint256 id = attestation.attest(a, CID);
        assertEq(attestation.getBatch(id).modelVersionHash, m2);
    }

    function test_RevokeModel_ExistingBatchesKeepTheirModelHash() public {
        uint256 green = _greenBatch(5000);
        uint256 pending = _redBatch(250_000);
        vm.prank(admin);
        attestation.revokeModel(MODEL);

        assertEq(attestation.getBatch(green).modelVersionHash, MODEL);
        assertEq(attestation.getBatch(pending).modelVersionHash, MODEL);
        // A verifier can still resolve a batch that was created under the since-revoked model.
        _verify(pending, true);
        assertEq(uint8(attestation.getBatch(pending).status), uint8(Status.Attested));
        assertEq(attestation.getBatch(pending).modelVersionHash, MODEL);
    }

    function test_RevokeModel_NotApprovedOrAlreadyRevokedReverts() public {
        bytes32 unknown = sha256("never-approved");
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.ModelNotApproved.selector, unknown));
        vm.prank(admin);
        attestation.revokeModel(unknown);

        vm.prank(admin);
        attestation.revokeModel(MODEL);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.ModelNotApproved.selector, MODEL));
        vm.prank(admin);
        attestation.revokeModel(MODEL);
    }

    function test_RevokeModel_ReapprovalReusesTheOriginalIndex() public {
        bytes32 m2 = sha256("model-two");
        bytes32 m3 = sha256("model-three");
        vm.startPrank(admin);
        attestation.approveModel(m2); // index 1
        attestation.revokeModel(MODEL); // index 0 stays reserved
        vm.expectEmit(true, false, false, true, address(attestation));
        emit IEmissionsAttestation.ModelApproved(MODEL, 0);
        attestation.approveModel(MODEL);
        vm.expectEmit(true, false, false, true, address(attestation));
        emit IEmissionsAttestation.ModelApproved(m3, 2); // no index was burnt by revoke / re-approve
        attestation.approveModel(m3);
        vm.stopPrank();

        assertTrue(attestation.isModelApproved(MODEL));
        uint256 id = _greenBatch(5000);
        assertEq(attestation.getBatch(id).modelVersionHash, MODEL, "index 0 still resolves to the original hash");
    }

    function test_RevokeModel_RevokedModelCanBeApprovedAgainButApprovedStaysIdempotent() public {
        vm.startPrank(admin);
        attestation.revokeModel(MODEL);
        attestation.approveModel(MODEL);
        vm.recordLogs();
        attestation.approveModel(MODEL); // already approved: no-op
        vm.stopPrank();
        assertEq(vm.getRecordedLogs().length, 0);
        assertTrue(attestation.isModelApproved(MODEL));
    }

    function test_RevokeModel_DoesNotFreeARegistrySlot() public {
        vm.startPrank(admin);
        for (uint256 i = 1; i < 256; i++) {
            attestation.approveModel(bytes32(i)); // MODEL holds index 0 -> 256 registered
        }
        attestation.revokeModel(bytes32(uint256(7)));
        vm.expectRevert(EmissionsAttestation.TooManyModels.selector);
        attestation.approveModel(bytes32(uint256(1000)));
        attestation.approveModel(bytes32(uint256(7))); // re-approving a registered hash still works
        vm.stopPrank();
        assertTrue(attestation.isModelApproved(bytes32(uint256(7))));
    }

    // -------------------------------------------------------------- fuzzing

    function testFuzz_Attest_StatusFollowsThreshold(uint8 risk, uint8 threshold) public {
        risk = uint8(bound(risk, 0, 100));
        threshold = uint8(bound(threshold, 0, 100));
        vm.prank(admin);
        attestation.setRiskThreshold(threshold);
        uint256 id = _attest(EAF_ID, PERIOD, risk, 1);
        Status expected = risk > threshold ? Status.PendingVerifier : Status.Attested;
        assertEq(uint8(attestation.getBatch(id).status), uint8(expected));
    }

    function testFuzz_Attest_AnySignatureBytesCannotImpersonate(bytes calldata junk) public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 1);
        a.ecdsaSig = junk;
        vm.prank(attestor);
        (bool ok,) = address(attestation).call(abi.encodeCall(attestation.attest, (a, CID)));
        // Random bytes never recover to an attestor-role address.
        assertFalse(ok);
        assertEq(attestation.batchCount(), 0);
    }

    // -------------------------------------------------------------- helpers

    function _split(bytes memory sig) internal pure returns (bytes32 r, bytes32 s, uint8 v) {
        assembly {
            r := mload(add(sig, 0x20))
            s := mload(add(sig, 0x40))
            v := byte(0, mload(add(sig, 0x60)))
        }
    }
}
