// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";

import {Deploy} from "../script/Deploy.s.sol";
import {Route, Sector, Installation} from "../src/interfaces/IEmbedProof.sol";

/// @dev INTERFACES section 9.F: on any chain other than 31337 the deploy script refuses to fall back to the
///      public Anvil role holders. Also exercises the optional environment wiring of the script.
contract DeployScriptTest is Test {
    bytes32 internal constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");
    bytes32 internal constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 internal constant REGULATOR_ROLE = keccak256("REGULATOR_ROLE");
    address internal constant ANVIL_ATTESTOR = 0x70997970C51812dc3A010C7d01b50e0d17dc79C8;
    address internal constant ANVIL_VERIFIER = 0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65;
    address internal constant ANVIL_REGULATOR = 0x14dC79964da2C08b23698B3D3cc7Ca32193d9955;

    Deploy internal script;

    function setUp() public {
        script = new Deploy();
    }

    function _skipIfEnvPreset() internal {
        // vm.setEnv is process-wide; these tests only make sense on a clean environment.
        if (vm.envExists("ATTESTOR") || vm.envExists("VERIFIER") || vm.envExists("REGULATOR")) vm.skip(true);
    }

    /// @dev ONE sequential test: forge runs the test functions of a contract in parallel and the
    ///      environment is process-global, so the two scenarios must not be separate tests.
    function test_Deploy_Script_AnvilDefaultsThenMandatoryEnvOffAnvil() public {
        _skipIfEnvPreset();
        _anvilChainDefaultsAndOptionalWiring();
        _nonAnvilChainRequiresExplicitRoleHolders();
    }

    function _nonAnvilChainRequiresExplicitRoleHolders() internal {
        vm.chainId(84532);

        vm.expectRevert(abi.encodeWithSelector(Deploy.MissingEnv.selector, "ATTESTOR"));
        script.run();

        vm.setEnv("ATTESTOR", vm.toString(makeAddr("prod attestor")));
        vm.expectRevert(abi.encodeWithSelector(Deploy.MissingEnv.selector, "VERIFIER"));
        script.run();

        vm.setEnv("VERIFIER", vm.toString(makeAddr("prod verifier")));
        vm.expectRevert(abi.encodeWithSelector(Deploy.MissingEnv.selector, "REGULATOR"));
        script.run();

        // With all three set the same chain deploys, and the public Anvil defaults hold no role.
        address a = makeAddr("prod attestor");
        address v = makeAddr("prod verifier");
        address r = makeAddr("prod regulator");
        vm.setEnv("REGULATOR", vm.toString(r));
        Deploy.Deployed memory d = script.run();
        assertTrue(d.registry.hasRole(ATTESTOR_ROLE, a));
        assertTrue(d.registry.hasRole(VERIFIER_ROLE, v));
        assertTrue(d.registry.hasRole(REGULATOR_ROLE, r));
        assertFalse(d.registry.hasRole(ATTESTOR_ROLE, ANVIL_ATTESTOR), "no public Anvil attestor on a real chain");
        assertFalse(d.registry.hasRole(VERIFIER_ROLE, ANVIL_VERIFIER));
        assertFalse(d.registry.hasRole(REGULATOR_ROLE, ANVIL_REGULATOR));
        assertEq(d.timelock.getMinDelay(), 86400);
        assertEq(d.registry.timelock(), address(d.timelock));
        assertEq(d.attestation.registry(), address(d.registry));
        assertEq(d.allocator.attestation(), address(d.attestation));
    }

    function _anvilChainDefaultsAndOptionalWiring() internal {
        vm.chainId(31337);
        address pqAttestor = ANVIL_ATTESTOR;
        bytes32 pq = keccak256("dummy ml-dsa-44 public key");
        bytes32 model = keccak256("dummy model");
        vm.setEnv("DEMO", "1");
        vm.setEnv("ATTESTOR_PQ_KEY_HASH", vm.toString(pq));
        vm.setEnv("MODEL_VERSION_HASH", vm.toString(model));

        Deploy.Deployed memory d = script.run();

        // Anvil defaults of INTERFACES section 1
        assertTrue(d.registry.hasRole(ATTESTOR_ROLE, ANVIL_ATTESTOR));
        assertTrue(d.registry.hasRole(VERIFIER_ROLE, ANVIL_VERIFIER));
        assertTrue(d.registry.hasRole(REGULATOR_ROLE, ANVIL_REGULATOR));
        // optional wiring
        assertEq(d.registry.attestorPqKey(pqAttestor), pq);
        assertTrue(d.registry.isAttestorPqKey(pqAttestor, pq));
        assertTrue(d.attestation.isModelApproved(model));
        // DEMO installations (INTERFACES section 2), TR-EAF-0001 at 300,000 t/yr
        Installation memory eaf = d.registry.getInstallation(bytes32("TR-EAF-0001"));
        assertEq(uint8(eaf.route), uint8(Route.EAF));
        assertEq(uint8(eaf.sector), uint8(Sector.STEEL));
        assertEq(eaf.capacityTpy, 300_000);
        assertEq(d.registry.getInstallation(bytes32("TR-BOF-0002")).capacityTpy, 3_500_000);
    }
}
