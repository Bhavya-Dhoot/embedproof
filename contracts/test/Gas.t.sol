// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Base} from "./Base.t.sol";
import {AttestInput} from "../src/interfaces/IEmbedProof.sol";

/// @dev Gas budgets from INTERFACES.md section 3 / PRD section 10: attest() <= 250,000 and
///      allocate() <= 120,000 (first allocation on a batch, cold).
///
///      Measurement. With `isolate = true` (foundry.toml) every top-level call from a test runs as its
///      own transaction, so `vm.lastCallGas().gasTotalUsed` is the figure a receipt shows:
///        gasUsed = 21,000 intrinsic + calldata (4 gas/zero byte, 16 gas/non-zero byte) + execution,
///      with every account and storage slot cold except the transaction target.
///      The budgets are asserted on that RECEIPT basis. (Execution-only, i.e. minus intrinsic and
///      calldata, is logged for information.) These tests need the optimizer; run them with the default
///      profile, not under `forge coverage`, which compiles unoptimised - see README.
contract GasTest is Base {
    uint256 internal constant ATTEST_BUDGET = 250_000;
    uint256 internal constant ALLOCATE_BUDGET = 120_000;
    uint256 internal constant INTRINSIC = 21_000;

    function _calldataGas(bytes memory data) internal pure returns (uint256 g) {
        for (uint256 i; i < data.length; i++) {
            g += data[i] == 0 ? 4 : 16;
        }
    }

    /// @return receipt full-transaction gasUsed; exec execution gas without intrinsic and calldata.
    function _measure(address from, address target, bytes memory data) internal returns (uint256 receipt, uint256 exec) {
        vm.prank(from);
        (bool ok,) = target.call(data);
        assertTrue(ok, "call under measurement must succeed");
        receipt = vm.lastCallGas().gasTotalUsed;
        exec = receipt - INTRINSIC - _calldataGas(data);
    }

    function test_Gas_Attest_Within250k() public {
        AttestInput memory a = _input(EAF_ID, PERIOD, 10, 5000);
        (uint256 receipt, uint256 exec) =
            _measure(attestor, address(attestation), abi.encodeCall(attestation.attest, (a, CID)));
        emit log_named_uint("attest() receipt gasUsed (green, fresh period, cold)", receipt);
        emit log_named_uint("attest() execution gas (excl. 21000 + calldata)", exec);
        assertLe(receipt, ATTEST_BUDGET, "attest receipt gas");
    }

    function test_Gas_Attest_PendingVerifierPath_Within250k() public {
        AttestInput memory a = _input(BOF_ID, PERIOD, 90, 250_000);
        (uint256 receipt,) = _measure(attestor, address(attestation), abi.encodeCall(attestation.attest, (a, CID)));
        emit log_named_uint("attest() PendingVerifier receipt gasUsed", receipt);
        assertLe(receipt, ATTEST_BUDGET);
    }

    function test_Gas_Attest_ResubmissionAfterRejected_Within250k() public {
        uint256 first = _redBatch(250_000);
        _verify(first, false);
        AttestInput memory a = _input(BOF_ID, PERIOD, 20, 4_000);
        (uint256 receipt,) = _measure(attestor, address(attestation), abi.encodeCall(attestation.attest, (a, CID)));
        emit log_named_uint("attest() resubmission receipt gasUsed", receipt);
        assertLe(receipt, ATTEST_BUDGET);
    }

    /// @dev First allocation on a batch writes two cold words (the packed one-word allocation record and the
    ///      batch running total, 2 x 22,100) and crosses three proxies (allocator, registry, attestation).
    function test_Gas_Allocate_FirstAllocationOnBatch_Within120k() public {
        uint256 id = _greenBatch(5000);
        bytes memory data = abi.encodeCall(allocator.allocate, (id, keccak256("c1"), importerA, 72083900, 3000));
        (uint256 receipt, uint256 exec) = _measure(exporterA, address(allocator), data);
        emit log_named_uint("allocate() receipt gasUsed (first allocation on batch, cold)", receipt);
        emit log_named_uint("allocate() execution gas (excl. 21000 + calldata)", exec);
        assertLe(receipt, ALLOCATE_BUDGET, "allocate receipt gas");
    }

    function test_Gas_Allocate_SecondAllocationIsCheaper() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, keccak256("c1"), importerA, 1000);
        bytes memory data = abi.encodeCall(allocator.allocate, (id, keccak256("c2"), importerB, 72083900, 1000));
        (uint256 receipt,) = _measure(exporterA, address(allocator), data);
        emit log_named_uint("allocate() second allocation receipt gasUsed", receipt);
        assertLt(receipt, ALLOCATE_BUDGET - 15_000, "no 0->nonzero write for the running total");
    }

    function test_Gas_VerifierSign_And_Claim_AreModest() public {
        uint256 pending = _redBatch(250_000);
        (uint256 vReceipt,) =
            _measure(verifier, address(attestation), abi.encodeCall(attestation.verifierSign, (pending, true, keccak256("r"))));
        emit log_named_uint("verifierSign() receipt gasUsed", vReceipt);
        assertLt(vReceipt, 120_000);

        uint256 green = _greenBatch(5000);
        _allocate(green, keccak256("c1"), importerA, 10);
        (uint256 cReceipt,) =
            _measure(importerA, address(allocator), abi.encodeCall(allocator.claim, (keccak256("c1"))));
        emit log_named_uint("claim() receipt gasUsed", cReceipt);
        assertLt(cReceipt, 80_000);
    }
}
