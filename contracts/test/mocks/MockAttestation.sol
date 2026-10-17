// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Status} from "../../src/interfaces/IEmbedProof.sol";

/// @dev Stand-in for EmissionsAttestation used to drive the allocator with batch ids the real contract
///      cannot reach in a test (> type(uint32).max). Returns the same tuple for every batch id.
contract MockAttestation {
    Status internal _status;
    uint64 internal _verified;
    bytes32 internal _installation;

    function set(Status status_, uint64 verified_, bytes32 installation_) external {
        _status = status_;
        _verified = verified_;
        _installation = installation_;
    }

    function allocInfo(uint256) external view returns (Status, uint64, bytes32) {
        return (_status, _verified, _installation);
    }
}
