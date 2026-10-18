// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {Base} from "./Base.t.sol";
import {InstallationRegistry} from "../src/InstallationRegistry.sol";
import {EmissionsAttestation} from "../src/EmissionsAttestation.sol";
import {ConsignmentAllocator} from "../src/ConsignmentAllocator.sol";
import {IInstallationRegistry, Installation, Sector, Route} from "../src/interfaces/IEmbedProof.sol";
import {ZeroAddress} from "../src/interfaces/IRegistryView.sol";

/// @dev FR-C1 (register, read back, non-admin reverts), FR-C2 (roles) and the registry half of FR-C7.
contract InstallationRegistryTest is Base {
    bytes32 internal constant NEW_ID = bytes32("TR-CEM-0003");

    function _unauthorized(address who) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, who, bytes32(0));
    }

    // ------------------------------------------------------------ FR-C1

    function test_Register_ReadBack() public {
        vm.expectEmit(true, true, false, true, address(registry));
        emit IInstallationRegistry.InstallationRegistered(NEW_ID, exporterB, "TR", Sector.CEMENT, Route.CEMENT_DRY, 900_000);
        vm.prank(admin);
        registry.registerInstallation(NEW_ID, "TR", Sector.CEMENT, Route.CEMENT_DRY, 900_000, exporterB);

        Installation memory i = registry.getInstallation(NEW_ID);
        assertEq(i.id, NEW_ID);
        assertEq(i.country, bytes2("TR"));
        assertEq(uint8(i.sector), uint8(Sector.CEMENT));
        assertEq(uint8(i.route), uint8(Route.CEMENT_DRY));
        assertEq(i.capacityTpy, 900_000);
        assertEq(i.operator, exporterB);
        assertTrue(i.exists);
        assertEq(registry.operatorOf(NEW_ID), exporterB);
    }

    function test_Register_DemoInstallationsFromFixture() public view {
        Installation memory eaf = registry.getInstallation(EAF_ID);
        assertEq(uint8(eaf.route), uint8(Route.EAF));
        assertEq(eaf.capacityTpy, 300_000);
        assertEq(eaf.operator, exporterA);
        Installation memory bof = registry.getInstallation(BOF_ID);
        assertEq(uint8(bof.route), uint8(Route.BF_BOF));
        assertEq(bof.capacityTpy, 3_500_000);
        assertEq(bof.operator, exporterB);
    }

    function test_Register_NonAdminReverts() public {
        address[5] memory callers = [stranger, attestor, verifier, exporterA, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(_unauthorized(callers[i]));
            vm.prank(callers[i]);
            registry.registerInstallation(NEW_ID, "TR", Sector.CEMENT, Route.CEMENT_DRY, 1, exporterB);
        }
        vm.expectRevert(abi.encodeWithSelector(IInstallationRegistry.UnknownInstallation.selector, NEW_ID));
        registry.getInstallation(NEW_ID);
    }

    function test_Register_GrantsOperatorRole() public {
        assertFalse(registry.hasRole(OPERATOR_ROLE, importerA));
        vm.prank(admin);
        registry.registerInstallation(NEW_ID, "TR", Sector.CEMENT, Route.CEMENT_WET, 5, importerA);
        assertTrue(registry.hasRole(OPERATOR_ROLE, importerA));
        // The two demo operators received it at registration.
        assertTrue(registry.hasRole(OPERATOR_ROLE, exporterA));
        assertTrue(registry.hasRole(OPERATOR_ROLE, exporterB));
    }

    function test_Register_DuplicateReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IInstallationRegistry.InstallationExists.selector, EAF_ID));
        vm.prank(admin);
        registry.registerInstallation(EAF_ID, "TR", Sector.STEEL, Route.EAF, 1, exporterB);
        // Original record untouched.
        assertEq(registry.operatorOf(EAF_ID), exporterA);
    }

    function test_Register_ZeroOperatorReverts() public {
        vm.expectRevert(ZeroAddress.selector);
        vm.prank(admin);
        registry.registerInstallation(NEW_ID, "TR", Sector.CEMENT, Route.CEMENT_DRY, 1, address(0));
    }

    function test_GetInstallation_UnknownReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IInstallationRegistry.UnknownInstallation.selector, NEW_ID));
        registry.getInstallation(NEW_ID);
    }

    function test_OperatorOf_UnknownIsZero() public view {
        assertEq(registry.operatorOf(NEW_ID), address(0));
    }

    function test_InstallationIds_InRegistrationOrder() public {
        vm.prank(admin);
        registry.registerInstallation(NEW_ID, "TR", Sector.CEMENT, Route.CEMENT_DRY, 1, exporterB);
        bytes32[] memory ids = registry.installationIds();
        assertEq(ids.length, 3);
        assertEq(ids[0], EAF_ID);
        assertEq(ids[1], BOF_ID);
        assertEq(ids[2], NEW_ID);
    }

    function test_RequestInstallation_EventOnlyNoStorage() public {
        vm.expectEmit(true, true, false, true, address(registry));
        emit IInstallationRegistry.InstallationRequested(NEW_ID, stranger, "DE", Sector.STEEL, Route.DRI_EAF, 42);
        vm.prank(stranger);
        registry.requestInstallation(NEW_ID, "DE", Sector.STEEL, Route.DRI_EAF, 42);

        assertEq(registry.installationIds().length, 2, "no id stored");
        assertEq(registry.operatorOf(NEW_ID), address(0), "no record stored");
        vm.expectRevert(abi.encodeWithSelector(IInstallationRegistry.UnknownInstallation.selector, NEW_ID));
        registry.getInstallation(NEW_ID);
    }

    // ------------------------------------------------------------ FR-C2

    function test_Roles_ConstantsAreKeccakOfNames() public view {
        assertEq(registry.OPERATOR_ROLE(), keccak256("OPERATOR_ROLE"));
        assertEq(registry.VERIFIER_ROLE(), keccak256("VERIFIER_ROLE"));
        assertEq(registry.ATTESTOR_ROLE(), keccak256("ATTESTOR_ROLE"));
        assertEq(registry.REGULATOR_ROLE(), keccak256("REGULATOR_ROLE"));
        assertEq(registry.DEFAULT_ADMIN_ROLE(), bytes32(0));
    }

    function test_Roles_AdminIsOnlyRoleAdmin() public {
        assertEq(registry.getRoleAdmin(ATTESTOR_ROLE), ADMIN_ROLE);
        assertEq(registry.getRoleAdmin(VERIFIER_ROLE), ADMIN_ROLE);
        assertEq(registry.getRoleAdmin(REGULATOR_ROLE), ADMIN_ROLE);
        assertEq(registry.getRoleAdmin(OPERATOR_ROLE), ADMIN_ROLE);

        // grantRole needs the role's admin: attestor/verifier cannot mint roles.
        vm.expectRevert(_unauthorized(attestor));
        vm.prank(attestor);
        registry.grantRole(VERIFIER_ROLE, stranger);

        vm.expectRevert(_unauthorized(verifier));
        vm.prank(verifier);
        registry.revokeRole(ATTESTOR_ROLE, attestor);

        assertTrue(registry.hasRole(ATTESTOR_ROLE, attestor), "revoke by non-admin had no effect");
    }

    function test_Roles_AdminCanRevoke() public {
        vm.prank(admin);
        registry.revokeRole(VERIFIER_ROLE, verifier);
        assertFalse(registry.hasRole(VERIFIER_ROLE, verifier));
    }

    function test_Roles_AdminHoldsNoOperationalRole() public view {
        // Separation of duties: the admin is not implicitly attestor / verifier / operator.
        assertFalse(registry.hasRole(ATTESTOR_ROLE, admin));
        assertFalse(registry.hasRole(VERIFIER_ROLE, admin));
        assertFalse(registry.hasRole(OPERATOR_ROLE, admin));
    }

    // ------------------------------------------- attestor ML-DSA-44 key anchor

    bytes32 internal constant PQ_KEY_HASH = keccak256("raw ML-DSA-44 public key bytes (1312 B)");

    function test_AttestorPqKey_DefaultsToZero() public view {
        assertEq(registry.attestorPqKey(attestor), bytes32(0));
        assertEq(registry.attestorPqKey(stranger), bytes32(0));
        assertEq(registry.attestorPqKey(address(0)), bytes32(0));
    }

    function test_AttestorPqKey_SetReadBackAndEmits() public {
        vm.expectEmit(true, false, false, true, address(registry));
        emit IInstallationRegistry.AttestorPqKeySet(attestor, PQ_KEY_HASH);
        vm.prank(admin);
        registry.setAttestorPqKey(attestor, PQ_KEY_HASH);

        assertEq(registry.attestorPqKey(attestor), PQ_KEY_HASH);
        assertEq(registry.attestorPqKey(stranger), bytes32(0), "other addresses unaffected");
    }

    function test_AttestorPqKey_AdminOnly() public {
        address[5] memory callers = [stranger, attestor, verifier, exporterA, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(_unauthorized(callers[i]));
            vm.prank(callers[i]);
            registry.setAttestorPqKey(attestor, PQ_KEY_HASH);
        }
        assertEq(registry.attestorPqKey(attestor), bytes32(0), "an attestor cannot anchor its own key");
    }

    function test_AttestorPqKey_ZeroHashReverts() public {
        vm.expectRevert(InstallationRegistry.ZeroPqKeyHash.selector);
        vm.prank(admin);
        registry.setAttestorPqKey(attestor, bytes32(0));

        // A zero hash is refused even when it would "clear" an existing anchor.
        vm.prank(admin);
        registry.setAttestorPqKey(attestor, PQ_KEY_HASH);
        vm.expectRevert(InstallationRegistry.ZeroPqKeyHash.selector);
        vm.prank(admin);
        registry.setAttestorPqKey(attestor, bytes32(0));
        assertEq(registry.attestorPqKey(attestor), PQ_KEY_HASH);
    }

    function test_AttestorPqKey_TargetMustHoldAttestorRole() public {
        address[5] memory targets = [stranger, verifier, exporterA, regulator, address(0)];
        for (uint256 i; i < targets.length; i++) {
            vm.expectRevert(abi.encodeWithSelector(InstallationRegistry.NotAnAttestor.selector, targets[i]));
            vm.prank(admin);
            registry.setAttestorPqKey(targets[i], PQ_KEY_HASH);
            assertEq(registry.attestorPqKey(targets[i]), bytes32(0));
        }
        // The admin is not implicitly an attestor either.
        vm.expectRevert(abi.encodeWithSelector(InstallationRegistry.NotAnAttestor.selector, admin));
        vm.prank(admin);
        registry.setAttestorPqKey(admin, PQ_KEY_HASH);
    }

    function test_AttestorPqKey_RevokedAttestorCannotBeAnchoredButKeepsExistingAnchor() public {
        vm.startPrank(admin);
        registry.setAttestorPqKey(attestor, PQ_KEY_HASH);
        registry.revokeRole(ATTESTOR_ROLE, attestor);
        vm.expectRevert(abi.encodeWithSelector(InstallationRegistry.NotAnAttestor.selector, attestor));
        registry.setAttestorPqKey(attestor, keccak256("another key"));
        vm.stopPrank();
        assertEq(registry.attestorPqKey(attestor), PQ_KEY_HASH, "history is not erased by a role change");
    }

    function test_AttestorPqKey_RotationOverwritesAndIsLogged() public {
        bytes32 rotated = keccak256("rotated ML-DSA-44 public key");
        vm.startPrank(admin);
        registry.setAttestorPqKey(attestor, PQ_KEY_HASH);
        vm.expectEmit(true, false, false, true, address(registry));
        emit IInstallationRegistry.AttestorPqKeySet(attestor, rotated);
        registry.setAttestorPqKey(attestor, rotated);
        vm.stopPrank();
        assertEq(registry.attestorPqKey(attestor), rotated);
    }

    function test_AttestorPqKey_IndependentPerAttestor() public {
        address attestor2 = makeAddr("attestor2");
        bytes32 key2 = keccak256("second attestor key");
        vm.startPrank(admin);
        registry.grantRole(ATTESTOR_ROLE, attestor2);
        registry.setAttestorPqKey(attestor, PQ_KEY_HASH);
        registry.setAttestorPqKey(attestor2, key2);
        vm.stopPrank();
        assertEq(registry.attestorPqKey(attestor), PQ_KEY_HASH);
        assertEq(registry.attestorPqKey(attestor2), key2);
    }

    function test_AttestorPqKey_WorksWhilePausedAndDoesNotAffectAttest() public {
        vm.startPrank(admin);
        registry.pause();
        registry.setAttestorPqKey(attestor, PQ_KEY_HASH);
        registry.unpause();
        vm.stopPrank();
        // The anchor is metadata for verifiers; attest() behaviour is unchanged by it.
        assertEq(_greenBatch(5000), 1);
    }

    // ------------------------------------------------ setOperator (section 9.B)

    function test_SetOperator_ChangesOperatorEmitsAndGrantsRole() public {
        address newOp = makeAddr("newOperator");
        assertFalse(registry.hasRole(OPERATOR_ROLE, newOp));
        vm.expectEmit(true, true, true, false, address(registry));
        emit InstallationRegistry.OperatorChanged(EAF_ID, exporterA, newOp);
        vm.prank(admin);
        registry.setOperator(EAF_ID, newOp);

        assertEq(registry.operatorOf(EAF_ID), newOp);
        assertEq(registry.getInstallation(EAF_ID).operator, newOp);
        assertEq(registry.getInstallation(EAF_ID).capacityTpy, 300_000, "other fields untouched");
        assertTrue(registry.hasRole(OPERATOR_ROLE, newOp));
        assertEq(registry.operatorOf(BOF_ID), exporterB, "other installations untouched");
    }

    function test_SetOperator_AdminOnly() public {
        address[5] memory callers = [stranger, attestor, verifier, exporterA, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(_unauthorized(callers[i]));
            vm.prank(callers[i]);
            registry.setOperator(EAF_ID, callers[i]);
        }
        assertEq(registry.operatorOf(EAF_ID), exporterA, "an operator cannot reassign itself");
    }

    function test_SetOperator_UnknownInstallationAndZeroOperatorRevert() public {
        vm.expectRevert(abi.encodeWithSelector(IInstallationRegistry.UnknownInstallation.selector, NEW_ID));
        vm.prank(admin);
        registry.setOperator(NEW_ID, exporterB);

        vm.expectRevert(ZeroAddress.selector);
        vm.prank(admin);
        registry.setOperator(EAF_ID, address(0));
        assertEq(registry.operatorOf(EAF_ID), exporterA);
    }

    function test_SetOperator_PreviousOperatorKeepsRoleButIsNoLongerOperatorOf() public {
        vm.prank(admin);
        registry.setOperator(EAF_ID, exporterB);
        assertEq(registry.operatorOf(EAF_ID), exporterB);
        assertTrue(registry.hasRole(OPERATOR_ROLE, exporterA), "role is separate; admin revokes it explicitly");
        assertTrue(registry.operatorOf(EAF_ID) != exporterA);
    }

    function test_SetOperator_SameOperatorIsAllowedAndReEmits() public {
        vm.expectEmit(true, true, true, false, address(registry));
        emit InstallationRegistry.OperatorChanged(EAF_ID, exporterA, exporterA);
        vm.prank(admin);
        registry.setOperator(EAF_ID, exporterA);
        assertEq(registry.operatorOf(EAF_ID), exporterA);
    }

    // ------------------------------------- PQ key history (section 9.E)

    function test_PqKeyHistory_RotationKeepsOldKeyAnchored() public {
        bytes32 k1 = keccak256("key one");
        bytes32 k2 = keccak256("key two");
        assertFalse(registry.isAttestorPqKey(attestor, k1));
        assertFalse(registry.isAttestorPqKey(attestor, bytes32(0)));

        vm.startPrank(admin);
        registry.setAttestorPqKey(attestor, k1);
        registry.setAttestorPqKey(attestor, k2);
        vm.stopPrank();

        assertEq(registry.attestorPqKey(attestor), k2, "current key is the latest");
        assertTrue(registry.isAttestorPqKey(attestor, k1), "history survives rotation");
        assertTrue(registry.isAttestorPqKey(attestor, k2));
        assertFalse(registry.isAttestorPqKey(stranger, k1), "per-attestor");
    }

    function test_PqKeyRevoke_AdminOnlyAndNotAnchoredReverts() public {
        bytes32 k1 = keccak256("key one");
        vm.prank(admin);
        registry.setAttestorPqKey(attestor, k1);

        address[4] memory callers = [stranger, attestor, verifier, regulator];
        for (uint256 i; i < callers.length; i++) {
            vm.expectRevert(_unauthorized(callers[i]));
            vm.prank(callers[i]);
            registry.revokeAttestorPqKey(attestor, k1);
        }
        assertTrue(registry.isAttestorPqKey(attestor, k1));

        bytes32 never = keccak256("never anchored");
        vm.expectRevert(abi.encodeWithSelector(InstallationRegistry.PqKeyNotAnchored.selector, attestor, never));
        vm.prank(admin);
        registry.revokeAttestorPqKey(attestor, never);
        vm.expectRevert(abi.encodeWithSelector(InstallationRegistry.PqKeyNotAnchored.selector, stranger, k1));
        vm.prank(admin);
        registry.revokeAttestorPqKey(stranger, k1);
    }

    function test_PqKeyRevoke_EmitsClearsCurrentOnlyWhenCurrentAndAllowsReAnchor() public {
        bytes32 k1 = keccak256("key one");
        bytes32 k2 = keccak256("key two");
        vm.startPrank(admin);
        registry.setAttestorPqKey(attestor, k1);
        registry.setAttestorPqKey(attestor, k2);

        // Revoking a superseded key leaves the current one alone.
        vm.expectEmit(true, false, false, true, address(registry));
        emit InstallationRegistry.AttestorPqKeyRevoked(attestor, k1);
        registry.revokeAttestorPqKey(attestor, k1);
        assertFalse(registry.isAttestorPqKey(attestor, k1));
        assertTrue(registry.isAttestorPqKey(attestor, k2));
        assertEq(registry.attestorPqKey(attestor), k2);

        // Revoking the current key leaves no current key.
        registry.revokeAttestorPqKey(attestor, k2);
        assertFalse(registry.isAttestorPqKey(attestor, k2));
        assertEq(registry.attestorPqKey(attestor), bytes32(0));

        // A revoked hash cannot be revoked twice, but can be anchored again.
        vm.expectRevert(abi.encodeWithSelector(InstallationRegistry.PqKeyNotAnchored.selector, attestor, k2));
        registry.revokeAttestorPqKey(attestor, k2);
        registry.setAttestorPqKey(attestor, k1);
        vm.stopPrank();
        assertTrue(registry.isAttestorPqKey(attestor, k1));
        assertEq(registry.attestorPqKey(attestor), k1);
    }

    // ------------------------------------------------------------ pause

    function test_Pause_AdminOnly() public {
        vm.expectRevert(_unauthorized(stranger));
        vm.prank(stranger);
        registry.pause();
        assertFalse(registry.paused());

        vm.prank(admin);
        registry.pause();
        assertTrue(registry.paused());

        vm.expectRevert(_unauthorized(attestor));
        vm.prank(attestor);
        registry.unpause();
        assertTrue(registry.paused());

        vm.prank(admin);
        registry.unpause();
        assertFalse(registry.paused());
    }

    function test_Pause_Twice_Reverts() public {
        vm.startPrank(admin);
        registry.pause();
        vm.expectRevert(PausableUpgradeable.EnforcedPause.selector);
        registry.pause();
        registry.unpause();
        vm.expectRevert(PausableUpgradeable.ExpectedPause.selector);
        registry.unpause();
        vm.stopPrank();
    }

    function test_Pause_DoesNotBlockRegistryAdministration() public {
        vm.startPrank(admin);
        registry.pause();
        registry.registerInstallation(NEW_ID, "TR", Sector.CEMENT, Route.CEMENT_DRY, 1, exporterB);
        registry.grantRole(VERIFIER_ROLE, stranger);
        vm.stopPrank();
        assertEq(registry.operatorOf(NEW_ID), exporterB);
    }

    // ------------------------------------------------------- initialisation

    function test_Initialize_CannotRunTwice() public {
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        registry.initialize(stranger, stranger);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        attestation.initialize(stranger);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        allocator.initialize(stranger, stranger);
        assertTrue(registry.hasRole(ADMIN_ROLE, admin));
        assertFalse(registry.hasRole(ADMIN_ROLE, stranger));
    }

    function test_Initialize_ImplementationsAreLocked() public {
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        InstallationRegistry(registryImpl).initialize(stranger, address(timelock));
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        EmissionsAttestation(attestationImpl).initialize(address(registry));
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        ConsignmentAllocator(allocatorImpl).initialize(address(registry), address(attestation));
    }

    function test_Initialize_ZeroAddressesRevert() public {
        vm.expectRevert(ZeroAddress.selector);
        new ERC1967Proxy(registryImpl, abi.encodeCall(InstallationRegistry.initialize, (address(0), address(timelock))));
        vm.expectRevert(ZeroAddress.selector);
        new ERC1967Proxy(registryImpl, abi.encodeCall(InstallationRegistry.initialize, (admin, address(0))));
        vm.expectRevert(ZeroAddress.selector);
        new ERC1967Proxy(attestationImpl, abi.encodeCall(EmissionsAttestation.initialize, (address(0))));
        vm.expectRevert(ZeroAddress.selector);
        new ERC1967Proxy(allocatorImpl, abi.encodeCall(ConsignmentAllocator.initialize, (address(0), address(attestation))));
        vm.expectRevert(ZeroAddress.selector);
        new ERC1967Proxy(allocatorImpl, abi.encodeCall(ConsignmentAllocator.initialize, (address(registry), address(0))));
    }

    function test_Wiring_ProxiesPointAtEachOther() public view {
        assertEq(registry.timelock(), address(timelock));
        assertEq(attestation.registry(), address(registry));
        assertEq(allocator.registry(), address(registry));
        assertEq(allocator.attestation(), address(attestation));
    }

    function test_NoEtherAccepted() public {
        vm.deal(stranger, 1 ether);
        vm.prank(stranger);
        (bool ok,) = address(registry).call{value: 1}("");
        assertFalse(ok);
        vm.prank(stranger);
        (ok,) = address(attestation).call{value: 1}("");
        assertFalse(ok);
        vm.prank(stranger);
        (ok,) = address(allocator).call{value: 1}("");
        assertFalse(ok);
    }
}
