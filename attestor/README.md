# EmbedProof attestor

FastAPI oracle service: validates an exporter-signed emissions report, scores it with the frozen model,
builds the attestation document, signs it twice (secp256k1 ECDSA and ML-DSA-44), encrypts and pins the bundle,
and calls `EmissionsAttestation.attest()`. Spec: `docs/INTERFACES.md` sections 4-6.

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
The service fails closed: while `registry.attestorPqKey(attestor)` differs from that hash (or is unset), `POST /attest`
answers 503 (`attestor ML-DSA key not anchored on-chain: registry has X, attestor has Y - run setAttestorPqKey`);
`/score`, `/verify` and `/health` stay up. A positive check is cached for 30 s, a mismatch is re-read on every request,
so it recovers as soon as the admin anchors the key. The demo re-runs on the same chain: used periods
advance to the next free month and consignment ids are fresh.

## Configuration (environment or `attestor/.env`, see `.env.example`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `RPC_URL`, `CHAIN_ID` | `http://127.0.0.1:8545`, `31337` | chain to talk to |
| `ATTESTOR_PRIVATE_KEY` | Anvil key #1 **on chain 31337 only** | signer; must hold `ATTESTOR_ROLE`. Required elsewhere |
| `DEPLOYMENTS` | `<repo>/deployments` | folder with `<chainId>.json` and `abi/*.json` |
| `DATA_DIR` | `attestor/var` | sqlite db, `keys/` (ML-DSA), `ipfs/` (local content-addressed store) |
| `PINATA_JWT` | unset | also pin via Pinata v3; the returned CID must equal the local CIDv1/raw/sha2-256 |
| `IPFS_GATEWAY` | unset | optional fallback to fetch bundles missing locally (bytes are re-hashed against the CID) |
| `PQ_BACKEND` | `oqs`, then `pqcrypto`, then `dilithium_py` | force one ML-DSA-44 backend |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | allowed web origins |
| `RATE_BURST`, `RATE_PER_SEC` | `20`, `2` | token bucket per client IP on `/score`, `/attest`, `/reasons` |
| `CLIENT_IP_HEADER` | unset | real-client-IP header behind a trusted proxy (Fly: `fly-client-ip`) |

## Packaging

`docker build -f attestor/Dockerfile -t embedproof-attestor .` from the repo root (add `--build-arg WITH_LIBOQS=1` to
compile liboqs so it becomes the ML-DSA backend). `fly deploy --config attestor/fly.toml` (single machine; sqlite and
keys live on the `/data` volume; set `ATTESTOR_PRIVATE_KEY` and `PINATA_JWT` with `fly secrets set`).

## Security notes

The attestor is a single trusted oracle: it can attest anything the model scores, so the on-chain controls (role
separation, approved model hash, verifier co-signature above the risk threshold, pause, timelocked upgrades) carry the
trust, and the service narrows what it will sign. Every record is hybrid-signed: the ECDSA signature covers the
commitment `keccak256(JCS(document))`, the ML-DSA-44 signature covers the canonical document bytes, and the chain
stores `keccak256(ML-DSA signature)`, the bundle's sha2-256 digest and, through `registry.attestorPqKey`, the hash of the
attestor's ML-DSA public key, so `/verify` can check that the post-quantum key in a bundle is the one the registry
anchors for the on-chain attestor (`pqKeyMatches`) and that the signed document agrees with the on-chain numbers
(`fieldsMatch`). Known limitations of this prototype: (1) a disclosure signature can be replayed by anyone who captures
it until its +-300 s timestamp window closes (no nonce; rely on TLS); (2) the per-batch AES keys (sqlite) and the
ML-DSA secret key (`DATA_DIR/keys`) are stored unwrapped on disk, so anyone with read access to `DATA_DIR` can decrypt
every bundle and forge ML-DSA signatures, and the attestor ECDSA key is a plain environment variable (no HSM/KMS); (3) the
rate limiter is in-memory and per-process, so it resets on restart, does not coordinate across instances, and only
trusts `CLIENT_IP_HEADER` if you set it behind a proxy you control; (4) the exporter's report signature is checked
and stored only in sqlite, not committed to the bundle or the chain, so exporter non-repudiation depends on this
database; (5) `/reasons` is unauthenticated by design (rate-limited, size-capped, content-addressed by hash); (6) the
first-run ML-DSA key is generated by whichever backend is available, and `dilithium_py`, if it is the only backend,
is a pure-Python reference implementation that is not constant-time.
