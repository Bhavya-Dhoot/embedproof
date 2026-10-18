// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {VmSafe} from "forge-std/Vm.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {InstallationRegistry} from "../src/InstallationRegistry.sol";
import {EmissionsAttestation} from "../src/EmissionsAttestation.sol";
import {ConsignmentAllocator} from "../src/ConsignmentAllocator.sol";
import {Sector, Route} from "../src/interfaces/IEmbedProof.sol";

/// @notice Deploys the EmbedProof system and writes `deployments/<chainId>.json` (INTERFACES.md section 3).
///
/// Deployed: OZ TimelockController (minDelay 86400; admin is proposer + executor, no timelock admin),
/// then InstallationRegistry, EmissionsAttestation and ConsignmentAllocator, each behind an ERC1967
/// proxy (UUPS), wired registry -> attestation -> allocator.
///
/// Environment. On chain 31337 everything is optional and defaults to the Anvil accounts of INTERFACES.md
/// section 1. On ANY OTHER chain ATTESTOR, VERIFIER and REGULATOR (and, with DEMO=1, EXPORTER_A and EXPORTER_B)
/// are mandatory (`MissingEnv`): the public
/// Anvil keys must never receive roles on a real network (INTERFACES.md section 9.F).
///   ADMIN, ATTESTOR, VERIFIER, REGULATOR           role holders (ADMIN defaults to the broadcaster)
///   EXPORTER_A, EXPORTER_B, IMPORTER_A, IMPORTER_B demo personas (recorded in the JSON; exporters
///                                                  become installation operators when DEMO=1)
///   MODEL_VERSION_HASH   0x + 64 hex; approved on EmissionsAttestation when set (skipped when unset)
///   ATTESTOR_PQ_KEY_HASH 0x + 64 hex; keccak256 of the attestor's raw ML-DSA-44 public key. When set,
///                        the deploy calls registry.setAttestorPqKey(ATTESTOR, hash)
///   DEMO                 1 registers TR-EAF-0001 and TR-BOF-0002 (INTERFACES.md section 2)
///   RPC_URL              recorded as `rpcUrl` in the JSON (default http://127.0.0.1:8545)
///
/// Run: `forge script script/Deploy.s.sol --rpc-url $RPC_URL --private-key $PRIVATE_KEY --broadcast`
/// The JSON is only written when broadcasting. `deployBlock` is the chain head at script start (a safe
/// lower bound for log scans); `python script/export_abi.py --fix-deploy-block <chainId>` replaces it
/// with the exact block of the first deployment transaction once the broadcast receipts exist.
contract Deploy is Script {
    uint256 internal constant TIMELOCK_DELAY = 86400;
    uint256 internal constant ANVIL_CHAIN_ID = 31337;

    /// @dev A mandatory environment variable is not set (chains other than 31337).
    error MissingEnv(string name);

    // INTERFACES.md section 1 (Anvil default mnemonic accounts)
    address internal constant D_ADMIN = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266;
    address internal constant D_ATTESTOR = 0x70997970C51812dc3A010C7d01b50e0d17dc79C8;
    address internal constant D_EXPORTER_A = 0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC;
    address internal constant D_EXPORTER_B = 0x90F79bf6EB2c4f870365E785982E1f101E93b906;
    address internal constant D_VERIFIER = 0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65;
    address internal constant D_IMPORTER_A = 0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc;
    address internal constant D_IMPORTER_B = 0x976EA74026E726554dB657fA54763abd0C3a0aa9;
    address internal constant D_REGULATOR = 0x14dC79964da2C08b23698B3D3cc7Ca32193d9955;

    bytes32 internal constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");
    bytes32 internal constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 internal constant REGULATOR_ROLE = keccak256("REGULATOR_ROLE");

    struct Accounts {
        address admin;
        address attestor;
        address exporterA;
        address exporterB;
        address verifier;
        address importerA;
        address importerB;
        address regulator;
    }

    struct Deployed {
        TimelockController timelock;
        InstallationRegistry registry;
        EmissionsAttestation attestation;
        ConsignmentAllocator allocator;
    }

    function run() external returns (Deployed memory d) {
        _requireExplicitRoleHolders();
        uint256 headBlock = block.number;
        bytes32 modelHash = vm.envOr("MODEL_VERSION_HASH", bytes32(0));
        bytes32 pqKeyHash = vm.envOr("ATTESTOR_PQ_KEY_HASH", bytes32(0));
        bool demo = _isDemo();

        vm.startBroadcast();
        (, address deployer,) = vm.readCallers();
        Accounts memory acc = _accounts(deployer);

        d = _deployStack(deployer, acc.admin);
        _wire(d, acc, deployer, modelHash, pqKeyHash, demo);
        vm.stopBroadcast();

        console2.log("chainId     ", block.chainid);
        console2.log("timelock    ", address(d.timelock));
        console2.log("registry    ", address(d.registry));
        console2.log("attestation ", address(d.attestation));
        console2.log("allocator   ", address(d.allocator));

        if (vm.isContext(VmSafe.ForgeContext.ScriptBroadcast) || vm.isContext(VmSafe.ForgeContext.ScriptResume)) {
            _writeDeployment(d, acc, headBlock, modelHash);
        } else {
            console2.log("dry run: deployments json not written");
        }
    }

    // ----------------------------------------------------------------- steps

    function _deployStack(address deployer, address admin) internal returns (Deployed memory d) {
        address[] memory governors = new address[](1);
        governors[0] = admin;
        // admin = address(0): the timelock administers itself; nobody keeps a back door.
        d.timelock = new TimelockController(TIMELOCK_DELAY, governors, governors, address(0));

        // The deployer is the registry admin during setup (it must grant roles), then hands over.
        d.registry = InstallationRegistry(
            address(
                new ERC1967Proxy(
                    address(new InstallationRegistry()),
                    abi.encodeCall(InstallationRegistry.initialize, (deployer, address(d.timelock)))
                )
            )
        );
        d.attestation = EmissionsAttestation(
            address(
                new ERC1967Proxy(
                    address(new EmissionsAttestation()),
                    abi.encodeCall(EmissionsAttestation.initialize, (address(d.registry)))
                )
            )
        );
        d.allocator = ConsignmentAllocator(
            address(
                new ERC1967Proxy(
                    address(new ConsignmentAllocator()),
                    abi.encodeCall(ConsignmentAllocator.initialize, (address(d.registry), address(d.attestation)))
                )
            )
        );
    }

    function _wire(
        Deployed memory d,
        Accounts memory acc,
        address deployer,
        bytes32 modelHash,
        bytes32 pqKeyHash,
        bool demo
    ) internal {
        d.registry.grantRole(ATTESTOR_ROLE, acc.attestor);
        d.registry.grantRole(VERIFIER_ROLE, acc.verifier);
        d.registry.grantRole(REGULATOR_ROLE, acc.regulator);

        if (pqKeyHash != bytes32(0)) {
            d.registry.setAttestorPqKey(acc.attestor, pqKeyHash);
        }

        if (modelHash != bytes32(0)) {
            d.attestation.approveModel(modelHash);
        }

        if (demo) {
            d.registry.registerInstallation(
                bytes32("TR-EAF-0001"), "TR", Sector.STEEL, Route.EAF, 300_000, acc.exporterA
            );
            d.registry.registerInstallation(
                bytes32("TR-BOF-0002"), "TR", Sector.STEEL, Route.BF_BOF, 3_500_000, acc.exporterB
            );
        }

        if (acc.admin != deployer) {
            // Hand the registry (and with it every admin function of all three contracts) to ADMIN.
            d.registry.grantRole(d.registry.DEFAULT_ADMIN_ROLE(), acc.admin);
            d.registry.renounceRole(d.registry.DEFAULT_ADMIN_ROLE(), deployer);
        }
    }

    // ------------------------------------------------------------------ io

    function _requireExplicitRoleHolders() internal view {
        if (block.chainid == ANVIL_CHAIN_ID) return;
        string[3] memory names = ["ATTESTOR", "VERIFIER", "REGULATOR"];
        for (uint256 i; i < names.length; i++) {
            if (!vm.envExists(names[i])) revert MissingEnv(names[i]);
        }
        // DEMO installations get operators: never the public Anvil exporters on a real chain.
        if (_isDemo()) {
            string[2] memory ops = ["EXPORTER_A", "EXPORTER_B"];
            for (uint256 i; i < ops.length; i++) {
                if (!vm.envExists(ops[i])) revert MissingEnv(ops[i]);
            }
        }
    }

    function _accounts(address deployer) internal view returns (Accounts memory a) {
        a.admin = vm.envOr("ADMIN", deployer);
        a.attestor = vm.envOr("ATTESTOR", D_ATTESTOR);
        a.exporterA = vm.envOr("EXPORTER_A", D_EXPORTER_A);
        a.exporterB = vm.envOr("EXPORTER_B", D_EXPORTER_B);
        a.verifier = vm.envOr("VERIFIER", D_VERIFIER);
        a.importerA = vm.envOr("IMPORTER_A", D_IMPORTER_A);
        a.importerB = vm.envOr("IMPORTER_B", D_IMPORTER_B);
        a.regulator = vm.envOr("REGULATOR", D_REGULATOR);
    }

    function _isDemo() internal view returns (bool) {
        string memory v = vm.envOr("DEMO", string(""));
        return keccak256(bytes(v)) == keccak256("1") || keccak256(bytes(v)) == keccak256("true");
    }

    function _writeDeployment(Deployed memory d, Accounts memory acc, uint256 deployBlock, bytes32 modelHash)
        internal
    {
        string memory rpcUrl = vm.envOr("RPC_URL", string("http://127.0.0.1:8545"));
        string memory head = string.concat(
            "{\n",
            '  "chainId": ', vm.toString(block.chainid), ",\n",
            '  "rpcUrl": "', rpcUrl, '",\n',
            '  "deployBlock": ', vm.toString(deployBlock), ",\n",
            '  "timelock": "', vm.toString(address(d.timelock)), '",\n',
            '  "registry": "', vm.toString(address(d.registry)), '",\n',
            '  "attestation": "', vm.toString(address(d.attestation)), '",\n',
            '  "allocator": "', vm.toString(address(d.allocator)), '",\n',
            '  "modelVersionHash": "', vm.toString(modelHash), '",\n'
        );
        string memory accounts = string.concat(
            '  "accounts": {\n',
            '    "admin": "', vm.toString(acc.admin), '",\n',
            '    "attestor": "', vm.toString(acc.attestor), '",\n',
            '    "exporterA": "', vm.toString(acc.exporterA), '",\n',
            '    "exporterB": "', vm.toString(acc.exporterB), '",\n',
            '    "verifier": "', vm.toString(acc.verifier), '",\n',
            '    "importerA": "', vm.toString(acc.importerA), '",\n',
            '    "importerB": "', vm.toString(acc.importerB), '",\n',
            '    "regulator": "', vm.toString(acc.regulator), '"\n',
            "  }\n}\n"
        );
        string memory path = string.concat("../deployments/", vm.toString(block.chainid), ".json");
        vm.writeFile(path, string.concat(head, accounts));
        console2.log("wrote", path);
    }
}
