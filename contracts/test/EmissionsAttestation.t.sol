// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
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
        a.ecdsaSig = _signAttestation(attestorPk, a); // the struct is signed, so re-sign after editing
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
        a.ecdsaSig = _signAttestation(roguePk, a); // valid EIP-712 signature, but the key holds no ATTESTOR_ROLE
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, rogue));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_OverDifferentCommitmentReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        // Attestor signed another document; swapping that signature in must not verify.
        AttestInput memory otherDoc = _input(EAF_ID, 202601, 10, 5000);
        bytes memory other = otherDoc.ecdsaSig;
        a.ecdsaSig = other;
        address recovered = ECDSA.recover(_digest(a), other);
        assertTrue(recovered != attestor, "sanity: recovers a different, unauthorised address");
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Attest_BadSignature_TamperedCommitmentReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.commitment = bytes32(uint256(a.commitment) ^ 1); // one flipped bit after signing
        address recovered = ECDSA.recover(_digest(a), a.ecdsaSig);
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
        assertEq(ecrecover(_digest(a), vFlip, r, sHigh), attestor, "twin is a valid ecrecover signature");
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
            attestation.verifierSign(id, true, REASON);
        }
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.PendingVerifier));
    }

    function test_VerifierSign_NotPending_OnAttestedBatch() public {
        uint256 id = _greenBatch(5000);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id));
        vm.prank(verifier);
        attestation.verifierSign(id, false, REASON);
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.Attested), "green batch cannot be rejected after the fact");
    }

    function test_VerifierSign_NotPending_AfterDecision() public {
        uint256 id = _redBatch(250_000);
        _verify(id, true);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id));
        vm.prank(verifier);
        attestation.verifierSign(id, false, REASON);

        uint256 id2 = _attest(EAF_ID, 202611, 99, 100);
        _verify(id2, false);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id2));
        vm.prank(verifier);
        attestation.verifierSign(id2, true, REASON);
    }

    function test_VerifierSign_UnknownBatchReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(0)));
        vm.prank(verifier);
        attestation.verifierSign(0, true, REASON);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(77)));
        vm.prank(verifier);
        attestation.verifierSign(77, true, REASON);
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
        (Status s0, uint64 t0, bytes32 i0, address at0) = attestation.allocInfo(1);
        assertEq(uint8(s0), uint8(Status.None));
        assertEq(t0, 0);
        assertEq(i0, bytes32(0));
        assertEq(at0, address(0));

        uint256 id = _redBatch(250_000);
        (Status s1, uint64 t1, bytes32 i1, address at1) = attestation.allocInfo(id);
        assertEq(uint8(s1), uint8(Status.PendingVerifier));
        assertEq(t1, 250_000);
        assertEq(i1, BOF_ID);
        assertEq(at1, attestor, "allocInfo returns the recovered attestor");

        _verify(id, true);
        (Status s2,,,) = attestation.allocInfo(id);
        assertEq(uint8(s2), uint8(Status.Attested));
    }

    function test_BatchOf_UnusedIsZero() public view {
        assertEq(attestation.batchOf(EAF_ID, PERIOD), 0);
    }

    function test_Attest_MaxFieldValuesRoundTrip() public {
        AttestInput memory a = _input(EAF_ID, type(uint32).max, 0, type(uint64).max);
        a.seeDeclared = type(uint32).max;
        a.ecdsaSig = _signAttestation(attestorPk, a); // the struct is signed, so re-sign after editing
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

    function test_SetRiskThreshold_TimelockOnlyAndEmits() public {
        // Everyone except the timelock - including the admin - is refused.
        address[5] memory callers = [stranger, attestor, admin, verifier, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(IEmissionsAttestation.NotTimelock.selector);
            vm.prank(callers[i]);
            attestation.setRiskThreshold(10);
        }
        assertEq(attestation.riskThreshold(), 60);

        vm.expectEmit(false, false, false, true, address(attestation));
        emit IEmissionsAttestation.RiskThresholdSet(10);
        vm.prank(address(timelock));
        attestation.setRiskThreshold(10);
        assertEq(attestation.riskThreshold(), 10);

        // The new threshold drives the status decision.
        assertEq(uint8(attestation.getBatch(_attest(EAF_ID, 202601, 10, 1)).status), uint8(Status.Attested));
        assertEq(uint8(attestation.getBatch(_attest(EAF_ID, 202602, 11, 1)).status), uint8(Status.PendingVerifier));
    }

    function test_SetRiskThreshold_AdminReachesItOnlyThroughTheTimelockDelay() public {
        bytes memory data = abi.encodeCall(attestation.setRiskThreshold, (25));
        bytes32 salt = keccak256("threshold");
        vm.prank(admin);
        timelock.schedule(address(attestation), 0, data, bytes32(0), salt, TIMELOCK_DELAY);

        uint256 t0 = block.timestamp;
        vm.warp(t0 + TIMELOCK_DELAY - 1);
        vm.expectRevert();
        vm.prank(admin);
        timelock.execute(address(attestation), 0, data, bytes32(0), salt);
        assertEq(attestation.riskThreshold(), 60, "not before 24 h");

        vm.warp(t0 + TIMELOCK_DELAY);
        vm.prank(admin);
        timelock.execute(address(attestation), 0, data, bytes32(0), salt);
        assertEq(attestation.riskThreshold(), 25);
    }

    function test_SetRiskThreshold_ZeroAndHundredBounds() public {
        vm.startPrank(address(timelock));
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
        a.ecdsaSig = _signAttestation(attestorPk, a); // the struct is signed, so re-sign after editing
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

    // ------------------------------------------- EIP-712 signature (section 9.A)

    function _clone(AttestInput memory a) internal pure returns (AttestInput memory c) {
        c = AttestInput({
            installationId: a.installationId,
            period: a.period,
            commitment: a.commitment,
            seeDeclared: a.seeDeclared,
            verifiedTonnes: a.verifiedTonnes,
            riskScore: a.riskScore,
            modelVersionHash: a.modelVersionHash,
            algId: a.algId,
            pqSigHash: a.pqSigHash,
            cidDigest: a.cidDigest,
            ecdsaSig: a.ecdsaSig
        });
    }

    /// @dev Submitting `t` (a tampered copy carrying the ORIGINAL signature) must revert with the address
    ///      that signature recovers to for the tampered fields - never with the attestor.
    function _assertSignatureRejected(AttestInput memory t) internal {
        address recovered = ECDSA.recover(_digest(t), t.ecdsaSig);
        assertTrue(recovered != attestor, "sanity: tampered fields recover to a stranger");
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(attestor);
        attestation.attest(t, CID);
    }

    function test_Eip712_HashAttestationMatchesIndependentImplementation() public view {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        assertEq(attestation.hashAttestation(a), _digest(a));
        assertEq(attestation.domainSeparator(), _domainSeparator(block.chainid, address(attestation)));
        assertEq(
            attestation.hashAttestation(a),
            keccak256(abi.encodePacked("\x19\x01", attestation.domainSeparator(), _structHash(a)))
        );
    }

    function test_Eip712_DigestIgnoresCidDigestAndSignatureButBindsEverySignedField() public view {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        bytes32 base = attestation.hashAttestation(a);

        AttestInput memory c = _clone(a);
        c.cidDigest = bytes32(uint256(1234));
        c.ecdsaSig = hex"00";
        assertEq(attestation.hashAttestation(c), base, "cidDigest and ecdsaSig are not part of the struct");

        // Each of the nine signed fields moves the digest.
        AttestInput memory t = _clone(a);
        t.installationId = BOF_ID;
        assertTrue(attestation.hashAttestation(t) != base, "installationId");
        t = _clone(a);
        t.period = a.period + 1;
        assertTrue(attestation.hashAttestation(t) != base, "period");
        t = _clone(a);
        t.commitment = bytes32(uint256(a.commitment) ^ 1);
        assertTrue(attestation.hashAttestation(t) != base, "commitment");
        t = _clone(a);
        t.seeDeclared = a.seeDeclared + 1;
        assertTrue(attestation.hashAttestation(t) != base, "seeDeclared");
        t = _clone(a);
        t.verifiedTonnes = a.verifiedTonnes + 1;
        assertTrue(attestation.hashAttestation(t) != base, "verifiedTonnes");
        t = _clone(a);
        t.riskScore = a.riskScore + 1;
        assertTrue(attestation.hashAttestation(t) != base, "riskScore");
        t = _clone(a);
        t.modelVersionHash = bytes32(uint256(a.modelVersionHash) ^ 1);
        assertTrue(attestation.hashAttestation(t) != base, "modelVersionHash");
        t = _clone(a);
        t.algId = 2;
        assertTrue(attestation.hashAttestation(t) != base, "algId");
        t = _clone(a);
        t.pqSigHash = bytes32(uint256(a.pqSigHash) ^ 1);
        assertTrue(attestation.hashAttestation(t) != base, "pqSigHash");
    }

    function test_Eip712_CidDigestIsDeliberatelyUnsigned() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        a.cidDigest = bytes32(uint256(0xC1D)); // the bundle carries the signature, so it cannot sign its own digest
        vm.prank(attestor);
        uint256 id = attestation.attest(a, CID);
        assertEq(attestation.getBatch(id).cidDigest, bytes32(uint256(0xC1D)));
    }

    function test_Replay_ValidSignatureWithAlteredFieldsReverts() public {
        bytes32 m2 = sha256("model-two");
        vm.prank(admin);
        attestation.approveModel(m2); // so a model swap is rejected by the signature, not by ModelNotApproved
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);

        AttestInput memory t = _clone(a);
        t.period = 202610;
        _assertSignatureRejected(t); // replay to another period

        t = _clone(a);
        t.riskScore = 11;
        _assertSignatureRejected(t); // altered score

        t = _clone(a);
        t.verifiedTonnes = 5001;
        _assertSignatureRejected(t); // inflated tonnes

        t = _clone(a);
        t.seeDeclared = 1;
        _assertSignatureRejected(t); // altered SEE

        t = _clone(a);
        t.installationId = BOF_ID;
        _assertSignatureRejected(t); // another installation

        t = _clone(a);
        t.commitment = bytes32(uint256(a.commitment) ^ 1);
        _assertSignatureRejected(t); // another document

        t = _clone(a);
        t.modelVersionHash = m2;
        _assertSignatureRejected(t); // another model

        t = _clone(a);
        t.pqSigHash = bytes32(uint256(a.pqSigHash) ^ 1);
        _assertSignatureRejected(t); // another PQ signature

        assertEq(attestation.batchCount(), 0, "none of the replays was recorded");
        // The untouched original is still good.
        vm.prank(attestor);
        assertEq(attestation.attest(a, CID), 1);
    }

    function test_Replay_AfterOriginalSucceeded_ReusedSignatureForNextPeriodReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        vm.prank(attestor);
        attestation.attest(a, CID);

        AttestInput memory t = _clone(a);
        t.period = PERIOD + 1; // the live attack: re-file the same signed report for the next month
        _assertSignatureRejected(t);
        assertEq(attestation.batchOf(EAF_ID, PERIOD + 1), 0);
    }

    function test_Replay_SignatureForAnotherVerifyingContractReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        // Same fields, same chain, but signed for a different contract address.
        address elsewhere = makeAddr("some other attestation contract");
        a.ecdsaSig = _signDigest(attestorPk, _digestFor(a, block.chainid, elsewhere));
        _assertSignatureRejected(a);
    }

    function test_Replay_SignatureFromASecondDeploymentDoesNotCrossOver() public {
        // A second, fully functional attestation proxy on the same chain and the same registry.
        EmissionsAttestation second = EmissionsAttestation(
            address(new ERC1967Proxy(attestationImpl, abi.encodeCall(EmissionsAttestation.initialize, (address(registry)))))
        );
        vm.prank(admin);
        second.approveModel(MODEL);

        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000); // signed for `attestation`
        assertTrue(second.domainSeparator() != attestation.domainSeparator());
        assertTrue(second.hashAttestation(a) != attestation.hashAttestation(a));

        address recovered = ECDSA.recover(second.hashAttestation(a), a.ecdsaSig);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(attestor);
        second.attest(a, CID);

        vm.prank(attestor);
        attestation.attest(a, CID); // the intended contract accepts it
    }

    function test_Replay_SignatureFromAnotherChainOrAfterAForkReverts() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        uint256 original = block.chainid;

        // Signed with a foreign chain id baked in: rejected on this chain.
        AttestInput memory foreign = _clone(a);
        foreign.ecdsaSig = _signDigest(attestorPk, _digestFor(foreign, 1, address(attestation)));
        _assertSignatureRejected(foreign);

        // Signed for this chain, replayed after the chain id changes (e.g. a fork): rejected too.
        vm.chainId(original + 1);
        address recovered = ECDSA.recover(attestation.hashAttestation(a), a.ecdsaSig);
        assertTrue(recovered != attestor);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.BadAttestorSignature.selector, recovered));
        vm.prank(attestor);
        attestation.attest(a, CID);

        vm.chainId(original);
        vm.prank(attestor);
        attestation.attest(a, CID);
    }

    function test_Replay_LegacyEip191SignatureOverCommitmentIsRejected() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        bytes32 eip191 = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", a.commitment));
        a.ecdsaSig = _signDigest(attestorPk, eip191);
        _assertSignatureRejected(a);
    }

    // ------------------------------------------ verifier separation (section 9.D)

    function test_VerifierSign_ZeroReasonHashReverts() public {
        uint256 id = _redBatch(250_000);
        vm.expectRevert(EmissionsAttestation.ZeroReasonHash.selector);
        vm.prank(verifier);
        attestation.verifierSign(id, true, bytes32(0));
        vm.expectRevert(EmissionsAttestation.ZeroReasonHash.selector);
        vm.prank(verifier);
        attestation.verifierSign(id, false, bytes32(0));
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.PendingVerifier));
    }

    function test_VerifierSign_AttestorCannotVerifyItsOwnBatch() public {
        uint256 id = _redBatch(250_000);
        vm.prank(admin);
        registry.grantRole(VERIFIER_ROLE, attestor); // an accredited verifier who is also the attestor key
        vm.expectRevert(abi.encodeWithSelector(EmissionsAttestation.VerifierConflict.selector, id, attestor));
        vm.prank(attestor);
        attestation.verifierSign(id, true, REASON);
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.PendingVerifier));
        assertEq(attestation.getBatch(id).verifier, address(0));
    }

    function test_VerifierSign_InstallationOperatorCannotVerifyItsOwnBatch() public {
        uint256 id = _redBatch(250_000); // BOF, operator exporterB
        vm.prank(admin);
        registry.grantRole(VERIFIER_ROLE, exporterB);
        vm.expectRevert(abi.encodeWithSelector(EmissionsAttestation.VerifierConflict.selector, id, exporterB));
        vm.prank(exporterB);
        attestation.verifierSign(id, true, REASON);

        // exporterA operates a different installation: no conflict for a BOF batch.
        vm.prank(admin);
        registry.grantRole(VERIFIER_ROLE, exporterA);
        vm.prank(exporterA);
        attestation.verifierSign(id, true, REASON);
        assertEq(attestation.getBatch(id).verifier, exporterA);
    }

    function test_VerifierSign_ConflictTracksTheCurrentOperator() public {
        uint256 id = _redBatch(250_000);
        vm.startPrank(admin);
        registry.grantRole(VERIFIER_ROLE, exporterB);
        registry.grantRole(VERIFIER_ROLE, importerA);
        registry.setOperator(BOF_ID, importerA); // exporterB is no longer the operator; importerA is
        vm.stopPrank();

        vm.expectRevert(abi.encodeWithSelector(EmissionsAttestation.VerifierConflict.selector, id, importerA));
        vm.prank(importerA);
        attestation.verifierSign(id, true, REASON);

        vm.prank(exporterB); // the former operator is free to verify
        attestation.verifierSign(id, true, REASON);
    }

    // ------------------------------------------------- revokeBatch (section 9.C)

    function test_RevokeBatch_AdminOnly() public {
        uint256 id = _greenBatch(5000);
        address[5] memory callers = [stranger, attestor, verifier, exporterA, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.MissingRole.selector, ADMIN_ROLE, callers[i]));
            vm.prank(callers[i]);
            attestation.revokeBatch(id, REASON);
        }
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.Attested));
    }

    function test_RevokeBatch_AttestedBatch_EmitsAndRejects() public {
        uint256 id = _greenBatch(5000);
        bytes32 reason = keccak256("figures withdrawn by regulator");
        vm.expectEmit(true, true, false, true, address(attestation));
        emit EmissionsAttestation.BatchRevoked(id, admin, reason);
        vm.prank(admin);
        attestation.revokeBatch(id, reason);

        Batch memory b = attestation.getBatch(id);
        assertEq(uint8(b.status), uint8(Status.Rejected));
        assertEq(b.verifiedTonnes, 5000, "record kept for audit");
        (Status s,,,) = attestation.allocInfo(id);
        assertEq(uint8(s), uint8(Status.Rejected));
    }

    function test_RevokeBatch_PendingBatch_RejectsAndVerifierCanNoLongerDecide() public {
        uint256 id = _redBatch(250_000);
        vm.prank(admin);
        attestation.revokeBatch(id, REASON);
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.Rejected));
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.NotPending.selector, id));
        vm.prank(verifier);
        attestation.verifierSign(id, true, REASON);
    }

    function test_RevokeBatch_KeepsTheOriginalVerifierDecisionOnRecord() public {
        uint256 id = _redBatch(250_000);
        bytes32 verifierReason = keccak256("verifier approved");
        vm.prank(verifier);
        attestation.verifierSign(id, true, verifierReason);
        vm.prank(admin);
        attestation.revokeBatch(id, keccak256("later revoked"));
        Batch memory b = attestation.getBatch(id);
        assertEq(uint8(b.status), uint8(Status.Rejected));
        assertEq(b.verifier, verifier);
        assertEq(b.reasonHash, verifierReason);
    }

    function test_RevokeBatch_FreesThePeriodForResubmission() public {
        uint256 id = _greenBatch(5000);
        vm.prank(admin);
        attestation.revokeBatch(id, REASON);
        uint256 again = _attest(EAF_ID, PERIOD, 10, 4000);
        assertEq(again, 2);
        assertEq(attestation.batchOf(EAF_ID, PERIOD), 2);
    }

    function test_RevokeBatch_Reverts_ZeroReason_Unknown_AlreadyRejected() public {
        uint256 id = _greenBatch(5000);
        vm.expectRevert(EmissionsAttestation.ZeroReasonHash.selector);
        vm.prank(admin);
        attestation.revokeBatch(id, bytes32(0));

        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(0)));
        vm.prank(admin);
        attestation.revokeBatch(0, REASON);
        vm.expectRevert(abi.encodeWithSelector(IEmissionsAttestation.UnknownBatch.selector, uint256(9)));
        vm.prank(admin);
        attestation.revokeBatch(9, REASON);

        vm.prank(admin);
        attestation.revokeBatch(id, REASON);
        vm.expectRevert(abi.encodeWithSelector(EmissionsAttestation.AlreadyRejected.selector, id));
        vm.prank(admin);
        attestation.revokeBatch(id, REASON);

        // A verifier rejection also counts as already rejected.
        uint256 red = _redBatch(250_000);
        _verify(red, false);
        vm.expectRevert(abi.encodeWithSelector(EmissionsAttestation.AlreadyRejected.selector, red));
        vm.prank(admin);
        attestation.revokeBatch(red, REASON);
    }

    function test_RevokeBatch_WorksWhilePaused() public {
        uint256 id = _greenBatch(5000);
        vm.startPrank(admin);
        registry.pause();
        attestation.revokeBatch(id, REASON);
        vm.stopPrank();
        assertEq(uint8(attestation.getBatch(id).status), uint8(Status.Rejected));
    }

    // -------------------------------------------------------------- fuzzing

    function testFuzz_Attest_StatusFollowsThreshold(uint8 risk, uint8 threshold) public {
        risk = uint8(bound(risk, 0, 100));
        threshold = uint8(bound(threshold, 0, 100));
        vm.prank(address(timelock));
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
