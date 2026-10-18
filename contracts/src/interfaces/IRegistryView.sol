// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Status} from "./IEmbedProof.sol";

/// @dev Reverted by `initialize` when a required address argument is the zero address, and by
///      `InstallationRegistry.registerInstallation` when the operator is the zero address.
///      Additive to the INTERFACES.md error set (it never shadows a spec'd error).
error ZeroAddress();

/// @notice Minimal view of the InstallationRegistry hub that the attestation and allocator
///         contracts call into. All members are part of the registry's public ABI
///         (OZ AccessControl `hasRole`, OZ Pausable `paused`, plus `operatorOf` / `timelock`
///         from IInstallationRegistry).
interface IRegistryView {
    function hasRole(bytes32 role, address account) external view returns (bool);
    function paused() external view returns (bool);
    function operatorOf(bytes32 id) external view returns (address);
    function timelock() external view returns (address);
}

/// @notice Minimal view of EmissionsAttestation that the allocator calls into.
interface IAttestationView {
    /// @dev `allocInfo` plus the batch's period, so the allocator can key its running total by
    ///      (installationId, period) instead of by batch id.
    function allocData(uint256 batchId)
        external
        view
        returns (Status status, uint64 verifiedTonnes, bytes32 installationId, address attestor, uint32 period);
}
