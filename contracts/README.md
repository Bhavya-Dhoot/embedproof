# EmbedProof contracts

Solidity 0.8.28 (cancun), OpenZeppelin 5.7.0, Foundry 1.8. Three UUPS proxies behind a 24 h OZ
`TimelockController`; the external interface is `src/interfaces/IEmbedProof.sol` (`docs/INTERFACES.md` section 3 as
amended by section 9, v1.1 security amendments A-F).

| Contract | Role |
| --- | --- |
| `InstallationRegistry` | installation master data, AccessControl role hub, global pause, upgrade timelock pointer |
| `EmissionsAttestation` | one attestation per (installation, period): commitment, SEE, tonnes, risk score, model/PQ/CID hashes, verifier co-sign above the risk threshold |
| `ConsignmentAllocator` | splits an Attested batch over consignments; `sum(allocations) <= verifiedTonnes`, one batch per consignment id |

## Setup

```bash
git submodule update --init --recursive      # forge-std, openzeppelin-contracts(-upgradeable) in lib/
export PATH="/c/Users/DELL/.foundry/bin:$PATH"   # Git Bash on this machine; Foundry is not on PATH
cd contracts
```

## Build, test, gas, coverage

```bash
forge build
forge test                      # 140 tests: unit, fuzz (512 runs), stateful invariants, gas budgets
forge test --match-path test/Gas.t.sol -vv      # prints measured gas
forge coverage --report summary --no-match-test "test_Gas_" --no-match-coverage '(^|/)(test|script)/'
```

`forge coverage` compiles without the optimizer, so the gas-budget tests are excluded from it (they
assert optimized-build numbers). The exclusion regex is anchored on purpose: an unanchored `(test|script)`
also matches `src/EmissionsAttestation.sol` ("Attes**test**ation") and silently drops it from the report.

Gas (optimizer on, `isolate = true`: each test call is its own transaction, every account and slot cold).
The budgets are asserted on the receipt basis (`gasUsed` = 21,000 intrinsic + calldata + execution):

| Call | Receipt `gasUsed` (forge / Anvil) | Budget |
| --- | --- | --- |
| `attest()` green, fresh period (EIP-712) | 228,019 / 228,031 | 250,000 |
| `allocate()` first allocation on a batch | 115,471 / 115,471 | 120,000 |

## Deploy

Environment. On chain 31337 everything is optional (defaults: the Anvil accounts of INTERFACES section 1). On any
other chain `ATTESTOR`, `VERIFIER` and `REGULATOR` are mandatory: the script reverts `MissingEnv` rather than grant
roles to the public Anvil keys.
`ADMIN` (defaults to the broadcaster), `ATTESTOR`, `VERIFIER`, `REGULATOR`, `EXPORTER_A`, `EXPORTER_B`,
`IMPORTER_A`, `IMPORTER_B`, `MODEL_VERSION_HASH` (0x + 64 hex; approved when set), `ATTESTOR_PQ_KEY_HASH` (0x + 64 hex; anchored for `ATTESTOR` when set), `DEMO=1` (registers
`TR-EAF-0001` and `TR-BOF-0002`), `RPC_URL` (recorded in the JSON).

Local Anvil (`anvil` in another terminal; `PRIVATE_KEY` = Anvil account 0):

```bash
DEMO=1 MODEL_VERSION_HASH=0x<64 hex> \
  forge script script/Deploy.s.sol --rpc-url http://127.0.0.1:8545 --private-key $PRIVATE_KEY --broadcast
python script/export_abi.py                          # deployments/abi/<Name>.json
python script/export_abi.py --fix-deploy-block 31337 # exact deployBlock from the broadcast receipts
```

Base Sepolia (chain 84532; needs a funded key; never reuse the public Anvil keys):

```bash
export RPC_URL=https://sepolia.base.org PRIVATE_KEY=<funded deployer key>
export ADMIN=<admin address> ATTESTOR=<attestor service address> VERIFIER=<verifier address> REGULATOR=<regulator address>
export MODEL_VERSION_HASH=0x<64 hex>
forge script script/Deploy.s.sol --rpc-url $RPC_URL --private-key $PRIVATE_KEY --broadcast
python script/export_abi.py && python script/export_abi.py --fix-deploy-block 84532
```

The script writes `../deployments/<chainId>.json` only when broadcasting (dry runs write nothing). The
deployer is the registry admin during setup and hands `DEFAULT_ADMIN_ROLE` to `ADMIN` at the end when they differ.
The timelock has no admin: `ADMIN` is its proposer and executor, and upgrades take 24 h.

## Design notes

- Roles, pause and the timelock live in the registry; the other two contracts query it. `attest`,
  `verifierSign`, `allocate` and `claim` revert with OZ `EnforcedPause()` while paused; `_authorizeUpgrade`
  requires `msg.sender == registry.timelock()` (otherwise `NotTimelock()`).
- `attest` verifies an EIP-712 signature over the nine-field `Attestation` struct (everything in `AttestInput` except
  `cidDigest`) under the domain `{EmbedProof, 1, block.chainid, address(this)}`, with OZ `ECDSA` (canonical low-s,
  65 bytes). The domain separator is built from constant hashes, `block.chainid` and `address(this)` (no storage
  reads), so it is correct behind the proxy and across forks. Installation, period, tonnes, SEE, score, model and PQ
  signature hash are all signed: a valid signature cannot be replayed for another period, with altered numbers, on
  another chain or against another contract. `hashAttestation(AttestInput)` and `domainSeparator()` expose the
  digest; `test/vectors/eip712_vector.json` is a golden vector (Python `eth_account` == contract == forge test).
  Both `msg.sender` and the recovered signer need `ATTESTOR_ROLE`; the signer is stored as `attestor`.
- Separation of duties (v1.1): `allocate` needs OPERATOR_ROLE **and** `operatorOf(id) == msg.sender` (admin can
  re-point an installation with `setOperator`, emitting `OperatorChanged`) and reverts `AttestorRevoked` if the batch's
  attestor lost ATTESTOR_ROLE; `verifierSign` reverts `VerifierConflict` for the batch attestor or the installation
  operator and `ZeroReasonHash` for an empty reason; `revokeBatch(batchId, reasonHash)` (ADMIN) turns a
  PendingVerifier/Attested batch into Rejected (`BatchRevoked`; allocations already made are not unwound);
  `setRiskThreshold` is callable only by the timelock.
- Gas packing: a batch is 7 storage words (word 1 holds attestor, tonnes, score, status so `allocInfo` reads two
  words; `verifier` and `reasonHash` are written only by `verifierSign`);
  the model hash is stored once per approval and batches keep a 1-byte index (hence `ModelApproved(hash, index)`).
  An installation exists iff its operator is non-zero, so registration rejects the zero operator.
  An allocation is ONE word (`importer | cnCode | tonnes(uint32) | batchId(uint32)`); `claimed` lives in a separate
  mapping written by `claim`. Hence `TonnesTooLarge` (> 4,294,967,295 t in one consignment; split it) and
  `BatchIdTooLarge`. The per-batch running total and the cap check stay uint64, and the cap check runs first, so an
  over-cap request always reports `DoubleClaimBlocked`.
- Additions beyond INTERFACES section 3 (none change a specified signature): errors `ZeroAddress`,
  `TooManyModels` (more than 256 models), `TonnesTooLarge`, `BatchIdTooLarge`, `AttestorRevoked`, `NotAnAttestor`,
  `ZeroPqKeyHash`, `PqKeyNotAnchored`, `VerifierConflict`, `ZeroReasonHash`, `AlreadyRejected` and, on
  `EmissionsAttestation`, `UnknownInstallation`; views `registry()` / `attestation()` on the allocator and the
  role/algorithm constants. `allocInfo` returns a fourth value, `attestor` (section 9.C).
- Attestor PQ key anchor: `setAttestorPqKey(attestor, keccak256(raw ML-DSA-44 public key))` (ADMIN; target must hold
  ATTESTOR_ROLE; non-zero hash; emits `AttestorPqKeySet`; re-calling rotates) and `attestorPqKey(attestor)` = the
  current key (0 if none). Every anchored key stays valid history: `isAttestorPqKey(attestor, hash)` is true until
  `revokeAttestorPqKey` (ADMIN; `AttestorPqKeyRevoked`; also clears the current key if it was current). Storage is
  appended after the original state. `Deploy.s.sol` calls `setAttestorPqKey` when `ATTESTOR_PQ_KEY_HASH` is set.
- `remainingTonnes` is `verifiedTonnes - allocatedTonnes` regardless of status (0 for an unknown batch).
  `attestedAt` is the submission time. `approveModel` is idempotent. `revokeModel` blocks new attestations
  (`ModelNotApproved`) but leaves existing batches and their recorded model hash intact; revoking a hash that is not
  currently approved reverts `ModelNotApproved`; re-approving reuses the original index (revocation frees no slot).
