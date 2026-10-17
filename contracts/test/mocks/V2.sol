// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {InstallationRegistry} from "../../src/InstallationRegistry.sol";
import {EmissionsAttestation} from "../../src/EmissionsAttestation.sol";
import {ConsignmentAllocator} from "../../src/ConsignmentAllocator.sol";

/// @dev Upgrade targets used only by test/Upgrade.t.sol: same storage layout plus a `version()` marker.
contract InstallationRegistryV2 is InstallationRegistry {
    function version() external pure returns (uint256) {
        return 2;
    }
}

contract EmissionsAttestationV2 is EmissionsAttestation {
    function version() external pure returns (uint256) {
        return 2;
    }
}

contract ConsignmentAllocatorV2 is ConsignmentAllocator {
    function version() external pure returns (uint256) {
        return 2;
    }
}

/// @dev Not UUPS-compatible (no proxiableUUID): an upgrade to this must fail.
contract NotUUPS {
    function hello() external pure returns (uint256) {
        return 1;
    }
}
