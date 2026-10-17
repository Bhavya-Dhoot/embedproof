// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {AccessControlUpgradeable} from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import {PausableUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";

import {IInstallationRegistry, Installation, Sector, Route} from "./interfaces/IEmbedProof.sol";
import {ZeroAddress} from "./interfaces/IRegistryView.sol";

/// @title InstallationRegistry
/// @notice Installation master data, the AccessControl role hub and the global pause switch of the
///         EmbedProof system. `EmissionsAttestation` and `ConsignmentAllocator` read roles, the
///         pause flag, installation operators and the upgrade timelock from this contract.
/// @dev UUPS upgradeable. Upgrades are only authorised for the OZ TimelockController whose address
///      is fixed at initialisation (`timelock()`); the admin cannot upgrade directly.
///      Storage: one packed word per installation; an installation exists iff its operator is
///      non-zero (registration rejects the zero operator), so `exists` costs no storage.
contract InstallationRegistry is
    Initializable,
    AccessControlUpgradeable,
    PausableUpgradeable,
    UUPSUpgradeable,
    IInstallationRegistry
{
    // ---------------------------------------------------------------- roles
    // ADMIN is OZ DEFAULT_ADMIN_ROLE (0x00).
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 public constant ATTESTOR_ROLE = keccak256("ATTESTOR_ROLE");
    bytes32 public constant REGULATOR_ROLE = keccak256("REGULATOR_ROLE");

    // -------------------------------------------------------------- storage
    /// @dev country(2) + sector(1) + route(1) + capacityTpy(8) + operator(20) = 32 bytes.
    struct InstallationStore {
        bytes2 country;
        Sector sector;
        Route route;
        uint64 capacityTpy;
        address operator;
    }

    /// @dev `setAttestorPqKey` target does not hold ATTESTOR_ROLE.
    error NotAnAttestor(address account);
    /// @dev A zero hash would read back as "no key anchored".
    error ZeroPqKeyHash();

    address private _timelock;
    mapping(bytes32 id => InstallationStore) private _installations;
    bytes32[] private _ids;
    /// @dev Appended after the original state so upgrades from the first layout stay storage-compatible.
    mapping(address attestor => bytes32 pqKeyHash) private _attestorPqKey;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    // ----------------------------------------------------------- initialiser

    /// @notice Initialise the proxy: grant `admin` the DEFAULT_ADMIN_ROLE and fix the upgrade timelock.
    /// @param admin Receives DEFAULT_ADMIN_ROLE. Must be non-zero.
    /// @param timelock_ The OZ TimelockController allowed to authorise upgrades of all three contracts.
    function initialize(address admin, address timelock_) external initializer {
        if (admin == address(0) || timelock_ == address(0)) revert ZeroAddress();
        __AccessControl_init();
        __Pausable_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _timelock = timelock_;
    }

    // ------------------------------------------------------- installations

    /// @notice Announce a wish to register an installation. Anyone may call; nothing is stored, the
    ///         admin acts on the event off-chain after checking the requester.
    /// @param id Installation identifier (right-padded ASCII).
    /// @param country ISO 3166-1 alpha-2 code.
    /// @param sector Industrial sector.
    /// @param route Production route.
    /// @param capacityTpy Nameplate capacity in tonnes of product per year.
    function requestInstallation(bytes32 id, bytes2 country, Sector sector, Route route, uint64 capacityTpy)
        external
    {
        emit InstallationRequested(id, msg.sender, country, sector, route, capacityTpy);
    }

    /// @notice Register an installation and grant its operator the OPERATOR_ROLE. ADMIN only.
    /// @param id Installation identifier; must not already be registered.
    /// @param country ISO 3166-1 alpha-2 code.
    /// @param sector Industrial sector.
    /// @param route Production route.
    /// @param capacityTpy Nameplate capacity in tonnes of product per year.
    /// @param operator Exporter wallet allowed to allocate this installation's batches. Non-zero.
    function registerInstallation(
        bytes32 id,
        bytes2 country,
        Sector sector,
        Route route,
        uint64 capacityTpy,
        address operator
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (operator == address(0)) revert ZeroAddress();
        InstallationStore storage s = _installations[id];
        if (s.operator != address(0)) revert InstallationExists(id);

        s.country = country;
        s.sector = sector;
        s.route = route;
        s.capacityTpy = capacityTpy;
        s.operator = operator;
        _ids.push(id);
        _grantRole(OPERATOR_ROLE, operator);

        // _grantRole only touches this contract's own storage.
        // forge-lint: disable-next-line(reentrancy-events)
        emit InstallationRegistered(id, operator, country, sector, route, capacityTpy);
    }

    /// @notice Read an installation's master data.
    /// @param id Installation identifier.
    /// @return inst The installation record (`exists` is always true on success).
    function getInstallation(bytes32 id) external view returns (Installation memory inst) {
        InstallationStore memory s = _installations[id];
        if (s.operator == address(0)) revert UnknownInstallation(id);
        inst = Installation({
            id: id,
            country: s.country,
            sector: s.sector,
            route: s.route,
            capacityTpy: s.capacityTpy,
            operator: s.operator,
            exists: true
        });
    }

    /// @notice The wallet allowed to allocate batches of an installation.
    /// @param id Installation identifier.
    /// @return The operator, or address(0) if the installation is unknown.
    function operatorOf(bytes32 id) external view returns (address) {
        return _installations[id].operator;
    }

    /// @notice All registered installation identifiers, in registration order.
    /// @return ids The identifier list.
    function installationIds() external view returns (bytes32[] memory ids) {
        return _ids;
    }

    // ------------------------------------------------- attestor PQ key anchor

    /// @notice Anchor the attestor's ML-DSA-44 public key on-chain as keccak256(raw public key). ADMIN only.
    /// @dev The target must currently hold ATTESTOR_ROLE and the hash must be non-zero. Calling again
    ///      replaces the anchored hash (key rotation); the event log keeps the history. Verifiers compare
    ///      the key inside an IPFS bundle against this value (`attestorPqKey(batch.attestor)`).
    /// @param attestor An address holding ATTESTOR_ROLE.
    /// @param pqKeyHash keccak256 of the raw ML-DSA-44 public key (1,312 bytes). Must be non-zero.
    function setAttestorPqKey(address attestor, bytes32 pqKeyHash) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (pqKeyHash == bytes32(0)) revert ZeroPqKeyHash();
        if (!hasRole(ATTESTOR_ROLE, attestor)) revert NotAnAttestor(attestor);
        _attestorPqKey[attestor] = pqKeyHash;
        emit AttestorPqKeySet(attestor, pqKeyHash);
    }

    /// @notice The anchored ML-DSA-44 public-key hash of an attestor.
    /// @param attestor The attestor address.
    /// @return The keccak256 of the raw public key, or bytes32(0) if none is anchored.
    function attestorPqKey(address attestor) external view returns (bytes32) {
        return _attestorPqKey[attestor];
    }

    // ------------------------------------------------------- admin controls

    /// @notice The OZ TimelockController that authorises upgrades of every EmbedProof contract.
    /// @return The timelock address.
    function timelock() external view returns (address) {
        return _timelock;
    }

    /// @notice Pause attest, verifierSign, allocate and claim system-wide. ADMIN only.
    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    /// @notice Lift the system-wide pause. ADMIN only.
    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    // -------------------------------------------------------------- upgrades

    /// @dev UUPS authorisation hook: only the timelock may upgrade. The admin calling directly reverts.
    function _authorizeUpgrade(address) internal view override {
        if (msg.sender != _timelock) revert NotTimelock();
    }
}
