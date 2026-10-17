// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {ERC1967Utils} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Utils.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {Base} from "./Base.t.sol";
import {
    InstallationRegistryV2, EmissionsAttestationV2, ConsignmentAllocatorV2, NotUUPS
} from "./mocks/V2.sol";
import {
    IInstallationRegistry,
    IEmissionsAttestation,
    IConsignmentAllocator,
    Batch,
    Status,
    Installation
} from "../src/interfaces/IEmbedProof.sol";

/// @dev FR-C8: UUPS upgrades behind the 24 h TimelockController. Upgrade through the timelock succeeds
///      only after the delay; any direct upgrade reverts NotTimelock.
contract UpgradeTest is Base {
    bytes32 internal constant SALT = keccak256("embedproof-upgrade");
    bytes32 internal constant IMPL_SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    function _implOf(address proxy) internal view returns (address) {
        return address(uint160(uint256(vm.load(proxy, IMPL_SLOT))));
    }

    function _upgradeData(address newImpl) internal pure returns (bytes memory) {
        return abi.encodeCall(UUPSUpgradeable.upgradeToAndCall, (newImpl, ""));
    }

    function _schedule(address proxy, address newImpl) internal returns (bytes32 opId) {
        bytes memory data = _upgradeData(newImpl);
        vm.prank(admin);
        timelock.schedule(proxy, 0, data, bytes32(0), SALT, TIMELOCK_DELAY);
        opId = timelock.hashOperation(proxy, 0, data, bytes32(0), SALT);
    }

    function _execute(address proxy, address newImpl) internal {
        vm.prank(admin);
        timelock.execute(proxy, 0, _upgradeData(newImpl), bytes32(0), SALT);
    }

    function _notReady(bytes32 opId) internal pure returns (bytes memory) {
        // expected state bitmap = 1 << uint8(OperationState.Ready)
        return abi.encodeWithSelector(
            TimelockController.TimelockUnexpectedOperationState.selector,
            opId,
            bytes32(uint256(1) << uint8(TimelockController.OperationState.Ready))
        );
    }

    // ------------------------------------------------------ timelock set-up

    function test_Timelock_IsConfiguredForTwentyFourHours() public view {
        assertEq(timelock.getMinDelay(), 86400);
        assertEq(registry.timelock(), address(timelock));
        assertTrue(timelock.hasRole(timelock.PROPOSER_ROLE(), admin));
        assertTrue(timelock.hasRole(timelock.EXECUTOR_ROLE(), admin));
        assertTrue(timelock.hasRole(timelock.CANCELLER_ROLE(), admin));
        // The deployer keeps no back door: the timelock administers itself.
        assertFalse(timelock.hasRole(timelock.DEFAULT_ADMIN_ROLE(), admin));
        assertTrue(timelock.hasRole(timelock.DEFAULT_ADMIN_ROLE(), address(timelock)));
    }

    function test_Timelock_ScheduleBelowMinDelayReverts() public {
        address newImpl = address(new InstallationRegistryV2());
        vm.expectRevert(abi.encodeWithSelector(TimelockController.TimelockInsufficientDelay.selector, uint256(3600), TIMELOCK_DELAY));
        vm.prank(admin);
        timelock.schedule(address(registry), 0, _upgradeData(newImpl), bytes32(0), SALT, 3600);
    }

    function test_Timelock_OnlyProposerSchedulesOnlyExecutorExecutes() public {
        address newImpl = address(new InstallationRegistryV2());
        // schedule: onlyRole(PROPOSER_ROLE)
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, stranger, timelock.PROPOSER_ROLE())
        );
        vm.prank(stranger);
        timelock.schedule(address(registry), 0, _upgradeData(newImpl), bytes32(0), SALT, TIMELOCK_DELAY);

        _schedule(address(registry), newImpl);
        vm.warp(block.timestamp + TIMELOCK_DELAY);
        // execute: onlyRoleOrOpenRole(EXECUTOR_ROLE); the role is not open (only admin holds it)
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, stranger, timelock.EXECUTOR_ROLE())
        );
        vm.prank(stranger);
        timelock.execute(address(registry), 0, _upgradeData(newImpl), bytes32(0), SALT);
    }

    // ------------------------------------------- FR-C8: timelocked upgrade

    function test_Upgrade_Registry_ThroughTimelockSucceedsAfter24h() public {
        // pre-upgrade state to be preserved (including the anchored PQ key)
        bytes32 pqKey = keccak256("anchored ML-DSA-44 key");
        vm.prank(admin);
        registry.setAttestorPqKey(attestor, pqKey);
        address newImpl = address(new InstallationRegistryV2());
        bytes32 opId = _schedule(address(registry), newImpl);
        assertTrue(timelock.isOperationPending(opId));

        // 1 second early: refused
        uint256 t0 = block.timestamp;
        vm.warp(t0 + TIMELOCK_DELAY - 1);
        vm.expectRevert(_notReady(opId));
        _execute(address(registry), newImpl);
        assertEq(_implOf(address(registry)), registryImpl, "still on v1");

        // exactly at the delay: accepted
        vm.warp(t0 + TIMELOCK_DELAY);
        assertTrue(timelock.isOperationReady(opId));
        _execute(address(registry), newImpl);

        assertEq(_implOf(address(registry)), newImpl);
        assertEq(InstallationRegistryV2(address(registry)).version(), 2);
        // state, roles and wiring survive the upgrade
        assertEq(registry.operatorOf(EAF_ID), exporterA);
        assertEq(registry.installationIds().length, 2);
        assertTrue(registry.hasRole(ATTESTOR_ROLE, attestor));
        assertTrue(registry.hasRole(ADMIN_ROLE, admin));
        assertEq(registry.timelock(), address(timelock));
        assertEq(registry.attestorPqKey(attestor), pqKey, "PQ key anchor survives the upgrade");
        assertTrue(timelock.isOperationDone(opId));
    }

    function test_Upgrade_Attestation_ThroughTimelock_PreservesBatches() public {
        uint256 green = _greenBatch(5000);
        uint256 red = _redBatch(250_000);
        Batch memory before_ = attestation.getBatch(green);

        address newImpl = address(new EmissionsAttestationV2());
        bytes32 opId = _schedule(address(attestation), newImpl);
        uint256 t0 = block.timestamp;
        vm.warp(t0 + TIMELOCK_DELAY - 1);
        vm.expectRevert(_notReady(opId));
        _execute(address(attestation), newImpl);
        vm.warp(t0 + TIMELOCK_DELAY);
        _execute(address(attestation), newImpl);

        assertEq(EmissionsAttestationV2(address(attestation)).version(), 2);
        Batch memory after_ = attestation.getBatch(green);
        assertEq(abi.encode(after_), abi.encode(before_), "batch record byte-identical across upgrade");
        assertEq(uint8(attestation.getBatch(red).status), uint8(Status.PendingVerifier));
        assertEq(attestation.batchOf(EAF_ID, PERIOD), green);
        assertEq(attestation.batchCount(), 2);
        assertEq(attestation.riskThreshold(), 60);
        assertTrue(attestation.isModelApproved(MODEL));
        _verify(red, true); // still operable
        assertEq(uint8(attestation.getBatch(red).status), uint8(Status.Attested));
    }

    function test_Upgrade_Allocator_ThroughTimelock_PreservesAllocations() public {
        uint256 id = _greenBatch(5000);
        _allocate(id, keccak256("c1"), importerA, 1200);
        vm.prank(importerA);
        allocator.claim(keccak256("c1"));

        address newImpl = address(new ConsignmentAllocatorV2());
        bytes32 opId = _schedule(address(allocator), newImpl);
        uint256 t0 = block.timestamp;
        vm.warp(t0 + TIMELOCK_DELAY - 1);
        vm.expectRevert(_notReady(opId));
        _execute(address(allocator), newImpl);
        vm.warp(t0 + TIMELOCK_DELAY);
        _execute(address(allocator), newImpl);

        assertEq(ConsignmentAllocatorV2(address(allocator)).version(), 2);
        assertEq(allocator.allocatedTonnes(id), 1200);
        assertTrue(allocator.getAllocation(keccak256("c1")).claimed);
        // the cap is still enforced after the upgrade
        vm.expectRevert(abi.encodeWithSelector(IConsignmentAllocator.DoubleClaimBlocked.selector, id, uint64(3801), uint64(3800)));
        vm.prank(exporterA);
        allocator.allocate(id, keccak256("c2"), importerB, 1, 3801);
        assertEq(allocator.registry(), address(registry));
        assertEq(allocator.attestation(), address(attestation));
    }

    function test_Upgrade_CanceledOperationCannotRun() public {
        address newImpl = address(new InstallationRegistryV2());
        bytes32 opId = _schedule(address(registry), newImpl);
        vm.prank(admin);
        timelock.cancel(opId);
        vm.warp(block.timestamp + TIMELOCK_DELAY);
        vm.expectRevert(_notReady(opId));
        _execute(address(registry), newImpl);
        assertEq(_implOf(address(registry)), registryImpl);
    }

    function test_Upgrade_ToNonUUPSImplementationThroughTimelockFails() public {
        address bad = address(new NotUUPS());
        _schedule(address(registry), bad);
        vm.warp(block.timestamp + TIMELOCK_DELAY);
        vm.expectRevert(abi.encodeWithSelector(ERC1967Utils.ERC1967InvalidImplementation.selector, bad));
        _execute(address(registry), bad);
        assertEq(_implOf(address(registry)), registryImpl, "proxy not bricked");
    }

    // ----------------------------------------- FR-C8: direct upgrade reverts

    function test_Upgrade_DirectByAdminRevertsNotTimelock() public {
        address r2 = address(new InstallationRegistryV2());
        address a2 = address(new EmissionsAttestationV2());
        address c2 = address(new ConsignmentAllocatorV2());

        vm.startPrank(admin);
        vm.expectRevert(IInstallationRegistry.NotTimelock.selector);
        registry.upgradeToAndCall(r2, "");
        vm.expectRevert(IEmissionsAttestation.NotTimelock.selector);
        attestation.upgradeToAndCall(a2, "");
        vm.expectRevert(IConsignmentAllocator.NotTimelock.selector);
        allocator.upgradeToAndCall(c2, "");
        vm.stopPrank();

        assertEq(_implOf(address(registry)), registryImpl);
        assertEq(_implOf(address(attestation)), attestationImpl);
        assertEq(_implOf(address(allocator)), allocatorImpl);
    }

    function test_Upgrade_DirectByOtherRolesRevertsNotTimelock() public {
        address r2 = address(new InstallationRegistryV2());
        address a2 = address(new EmissionsAttestationV2());
        address c2 = address(new ConsignmentAllocatorV2());
        address[5] memory callers = [stranger, attestor, verifier, exporterA, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.startPrank(callers[i]);
            vm.expectRevert(IInstallationRegistry.NotTimelock.selector);
            registry.upgradeToAndCall(r2, "");
            vm.expectRevert(IEmissionsAttestation.NotTimelock.selector);
            attestation.upgradeToAndCall(a2, "");
            vm.expectRevert(IConsignmentAllocator.NotTimelock.selector);
            allocator.upgradeToAndCall(c2, "");
            vm.stopPrank();
        }
    }

    function test_Upgrade_ImplementationContractsCannotBeUpgradedOrInitialised() public {
        address r2 = address(new InstallationRegistryV2());
        // Calling upgradeToAndCall on the bare implementation (not through a proxy) is blocked by UUPS' own guard.
        vm.expectRevert(UUPSUpgradeable.UUPSUnauthorizedCallContext.selector);
        UUPSUpgradeable(registryImpl).upgradeToAndCall(r2, "");
        vm.expectRevert(UUPSUpgradeable.UUPSUnauthorizedCallContext.selector);
        UUPSUpgradeable(attestationImpl).upgradeToAndCall(r2, "");
        vm.expectRevert(UUPSUpgradeable.UUPSUnauthorizedCallContext.selector);
        UUPSUpgradeable(allocatorImpl).upgradeToAndCall(r2, "");
    }

    function test_Upgrade_TimelockedUpgradeWorksWhilePaused() public {
        // Incident response: pause, then ship a fix through the timelock; pausing never blocks governance.
        vm.prank(admin);
        registry.pause();
        address newImpl = address(new EmissionsAttestationV2());
        _schedule(address(attestation), newImpl);
        vm.warp(block.timestamp + TIMELOCK_DELAY);
        _execute(address(attestation), newImpl);
        assertEq(_implOf(address(attestation)), newImpl);
    }
}
