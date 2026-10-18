# EmbedProof attestor

FastAPI oracle service: validates an exporter-signed emissions report, scores it with the frozen model,
builds the attestation document, signs it twice (an EIP-712 secp256k1 signature over the `Attestation` struct, and
ML-DSA-44 over the canonical document), encrypts and pins the bundle, and calls `EmissionsAttestation.attest()`.
Spec: `docs/INTERFACES.md` sections 4-6 and the section 9 security amendments.

## Run

From the repository root, with the shared venv (`PYTHONPATH=.` so `attestor` and `model` both import):

```bash
# 1. chain: Anvil + contracts. The attestor's ML-DSA key hash is anchored on-chain at deploy time.
anvil &
cd contracts && DEMO=1 MODEL_VERSION_HASH=$(cat ../model/artefacts/model_version.txt) \
  ATTESTOR_PQ_KEY_HASH=$(cd .. && PYTHONPATH=. python -m attestor.keys) \
  forge script script/Deploy.s.sol --rpc-url http://127.0.0.1:8545 --private-key <anvil key #0> --broadcast && cd ..

# 2. service (fails loudly if the model, deployments or PQ keys cannot be loaded)
PYTHONPATH=. python -m uvicorn attestor.app:app --port 8000

# 3. the four PRD scenarios + hardening probes; exit code != 0 if anything fails
PYTHONPATH=. python -m attestor.demo

# tests (no chain or network needed)
PYTHONPATH=. python -m pytest attestor/tests
```

`python -m attestor.keys` creates the ML-DSA-44 key pair in `<DATA_DIR>/keys` if missing and prints only
`0x` + keccak256(raw public key). `GET /health` reports the same value as `pqPublicKeyHash` plus `pqKeyAnchored`.
The service fails closed: while `registry.attestorPqKey(attestor)` (the current key) differs from that hash (or is unset), `POST /attest`
answers 503 (`attestor ML-DSA key not anchored on-chain: registry has X, attestor has Y - run setAttestorPqKey`);
`/score`, `/verify` and `/health` stay up. A positive check is cached for 30 s, a mismatch is re-read on every request,
so it recovers as soon as the admin anchors the key. The demo re-runs on the same chain: used periods
move to the nearest earlier free month (the attestor refuses periods after the current UTC month) and consignment ids are fresh.

## Configuration (environment or `attestor/.env`, see `.env.example`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `RPC_URL`, `CHAIN_ID` | `http://127.0.0.1:8545`, `31337` | chain to talk to |
| `ATTESTOR_PRIVATE_KEY` | Anvil key #1 **on chain 31337 only** | signer; must hold `ATTESTOR_ROLE`. Required elsewhere |
| `DEPLOYMENTS` | `<repo>/deployments` | folder with `<chainId>.json` and `abi/*.json` |
| `DATA_DIR` | `attestor/var` | sqlite db, `keys/` (ML-DSA), `ipfs/` (local content-addressed store) |
| `INSTALLATIONS_FILE` | `data/installations_demo.json` | admin-maintained plant -> Climate TRACE prior mapping (see below) |
| `DEMO_RUNNER` | unset | `1` enables the hosted demo runner (see below); refuses to start unless `CHAIN_ID` is 31337 or 84532 |
| `DEMO_EXPORTER_KEY`, `DEMO_EXPORTER_B_KEY`, `DEMO_VERIFIER_KEY`, `DEMO_IMPORTER_A`, `DEMO_IMPORTER_B` | Anvil accounts on 31337 only | demo-runner accounts (**demo-only keys**) |
| `PINATA_JWT` | unset | also pin via Pinata v3; the returned CID must equal the local CIDv1/raw/sha2-256 |
| `IPFS_GATEWAY` | unset | optional fallback to fetch bundles missing locally (bytes are re-hashed against the CID) |
| `PQ_BACKEND` | `oqs`, then `pqcrypto`, then `dilithium_py` | force one ML-DSA-44 backend |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | allowed web origins |
| `RATE_BURST`, `RATE_PER_SEC` | `20`, `2` | token bucket per client address and route group (`score`, `attest`, `verify`, `bundle`, `disclosure`, `reasons`); IPv6 clients share one bucket per /64 |
| `CLIENT_IP_HEADER` | unset | real-client-IP header behind a trusted proxy (Fly: `fly-client-ip`) |

## Report schema and checks

`attestor/schemas/report.schema.json` (JSON Schema draft 2020-12) describes the report structure and ranges; every file in
`data/demo_reports/` validates against it and the tests compare it with `validate_report` on invalid cases. Rules that need other
data stay in the attestor and are listed in the schema description: the 1% tolerances (`indirect` vs electricity x grid factor,
`see` vs (direct + indirect) / production), route equal to the registry route, no period later than the current UTC month, and
**capacity**: because `verifiedTonnes` is the exporter's own `productionTonnes` and caps every allocation, a report is refused
with 422 when `productionTonnes` > on-chain nameplate capacity (t/yr) / 12 x 1.2 (exactly nameplate / 10).

## Assessment: `hasHistory`, `flags`, `inputs`

The signed document's `assessment` (and `/score`, `AttestResult.score`) carries, next to the model output:

* `hasHistory` - false when the installation has no Climate TRACE lagged prior and the no-history branch of the model scored it.
* `flags` - informational `[{code, message}]`, never changing the score:
  * `GRID_FACTOR_BELOW_EMBER`: declared grid factor < 0.8 x the Ember country-year value the model uses.
  * `ELECTRICITY_OUTSIDE_ROUTE_NORM`: declared MWh per tonne < 0.5x or > 2x of Climate TRACE's per-route modelling assumption
    (field `other3` of the v5.10.0 iron-and-steel data: EAF 0.87, DRI-EAF 0.87, BF-BOF 0.83 MWh/t; the constant lives in
    `attestor/assess.py`). **The 0.5x-2x band is a coarse sanity band chosen by EmbedProof, not a regulatory norm**, and Climate
    TRACE does not cite a source for its own figures. This is the PRD's "electricity consumed per tonne ... checked against route norms".
* `inputs` - the exact inputs the scorer used: `installation`, `features` (route, capacityTpy, gridGco2PerKwh, ctLaggedPriorEf),
  `prior` (value, source text, Climate TRACE source asset id), `grid` (value, source, Ember observation year, country),
  `dataVersion` and `artefactSha256` (`0x` + sha256 of the model artefact file = `modelVersionHash`).

### Plant-to-prior mapping (`INSTALLATIONS_FILE`)

The mapping from an installation id to its Climate TRACE prior is an **admin-maintained JSON file** (default
`data/installations_demo.json`, override with `INSTALLATIONS_FILE`). The service reads it at startup (restart after editing).
Entry shape: `priors.ctLaggedPriorEf`, optional `priors.gridGco2PerKwh`, `priors.capacityTpy`, `priors.ctLaggedPriorSource` and
`climateTraceReference.climateTraceSourceId`. **A plant without an entry (or an entry without `ctLaggedPriorEf`) is scored on the
no-history branch (`hasHistory` false)** with route, on-chain capacity and Ember grid only, which uses wider calibrated intervals.

## Rescore a certificate

```bash
PYTHONPATH=. python -m attestor.rescore "http://localhost:3000/verify/3#k=<base64url key>" [--attestor URL] [--artefact PATH]
```

It reads the batch from the chain (`RPC_URL`, deployments), fetches the bundle by the CID derived from the on-chain `cidDigest`
(from `GET <attestor>/bundle/<cid>`), decrypts it with the key in the URL fragment and re-runs the frozen model on the recorded
`assessment.inputs` and report. Checks, each PASS/FAIL, exit code 1 on any FAIL: (a) sha256(model artefact file) == on-chain
`modelVersionHash`; (b) recomputed risk score == on-chain `riskScore`; (c) the commitment recomputes; plus (d) sha256(bundle) ==
on-chain `cidDigest` and (e) the recomputed interval and band equal the signed assessment. `make demo` runs it on the green certificate.

## Hosted demo runner (judges without a wallet)

`DEMO_RUNNER=1` (only on chain 31337 or 84532; the service refuses to start elsewhere) exposes `GET /demo/status` ->
`{enabled, chainId}` and `POST /demo/run/{green|red|double-claim}` -> `{scenario, chainId, batchId, certificatePath, steps:[{label, ok,
expected, txHash, detail}]}`. It answers 404 when disabled, 409 while another run is in progress (one global lock) and 429 with
`Retry-After` for a 30 s per-client cooldown (IPv6 by /64). `double-claim` sends the final over-allocation with a fixed gas limit so it is
mined with status 0 (`ok=false, expected=true`, `DoubleClaimBlocked(batchId, requested, remaining)` decoded), giving a real
explorer link. Each run uses an unused (installation, period): periods are searched backwards from the demo report's month (never into the
future), which does not move the risk band (tests score the real model across the window). **These are demo-only keys**: anyone who can
reach the endpoints can make the service spend gas from `DEMO_EXPORTER_KEY` / `DEMO_VERIFIER_KEY` (on 31337 they default to the public Anvil
accounts of docs/INTERFACES.md section 1), so use throwaway accounts holding only testnet dust; the service logs a warning at startup.

## Packaging

`docker build -f attestor/Dockerfile -t embedproof-attestor .` from the repo root (add `--build-arg WITH_LIBOQS=1` to
compile liboqs so it becomes the ML-DSA backend). `fly deploy --config attestor/fly.toml` (single machine; sqlite and
keys live on the `/data` volume; set `ATTESTOR_PRIVATE_KEY` and `PINATA_JWT` with `fly secrets set`).

## Security notes

The attestor is a single trusted oracle: it can attest anything the model scores, so the on-chain controls (role
separation, approved model hash, verifier co-signature above the risk threshold, pause, timelocked upgrades) carry the
trust, and the service narrows what it will sign. Every record is hybrid-signed. The ECDSA signature is EIP-712 over
`Attestation(installationId, period, commitment, seeDeclared, verifiedTonnes, riskScore, modelVersionHash, algId,
pqSigHash)` under the domain `{EmbedProof, 1, chainId, EmissionsAttestation proxy}`, so it cannot be replayed to another
period, installation, score or chain (`cidDigest` is left out because the bundle carries the signature). The ML-DSA-44
signature covers the canonical document bytes, and the chain stores `keccak256(ML-DSA signature)` and the bundle's
sha2-256 digest. `/verify` rebuilds the typed data from the on-chain batch fields and the recomputed commitment
(`ecdsaValid`), checks that the bundle's ML-DSA key is one the registry has anchored for the on-chain attestor
(`pqKeyMatches`, via `isAttestorPqKey`, so key rotation keeps history valid until revoked) and that the signed document
agrees with the on-chain numbers under strict typing (`fieldsMatch`). `/attest` additionally requires the exporter
signer to hold `OPERATOR_ROLE` as well as being the registered operator, rejects periods later than the current UTC month,
and refuses to run at all while its own ML-DSA key is not the registry's current `attestorPqKey`. Inputs are bounded:
request bodies are capped while streaming (chunked too), numbers beyond 2^53 are rejected before any float conversion,
RPC and storage errors reach clients only as generic 502s (details stay in the log), revert data is decoded only from
the node's structured `data` field, `/disclosure`, `/verify`, `/bundle` and `/reasons` are rate limited like `/attest`,
and `importers_of` scans are cached incrementally. Known limitations of this prototype: (1) a disclosure signature can be
replayed by anyone who captures it until its +-300 s timestamp window closes (no nonce; rely on TLS); (2) the per-batch
AES keys (sqlite) and the ML-DSA secret key (`DATA_DIR/keys`) are stored unwrapped on disk, so anyone with read access to
`DATA_DIR` can decrypt every bundle and forge ML-DSA signatures, and the attestor ECDSA key is a plain environment
variable (no HSM/KMS); (3) the rate limiter is in-memory and per-process, so it resets on restart and does not coordinate
across instances, and it only trusts `CLIENT_IP_HEADER` if you set it behind a proxy you control; (4) the exporter's
report signature is checked and stored only in sqlite, not committed to the bundle or the chain, so exporter
non-repudiation depends on this database; (5) `/reasons` is unauthenticated by design (rate-limited, size-capped,
content-addressed by hash); (6) `dilithium_py`, if it is the only ML-DSA backend, is a pure-Python reference
implementation that is not constant-time; (7) the fail-closed gate reads only `attestorPqKey` (the current key) and caches a positive result for 30 s, so
it notices a replaced key up to 30 s late and does not consult the revocation list; `/verify` (`pqKeyMatches`) does.
