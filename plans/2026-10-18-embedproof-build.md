# EmbedProof build plan — 2026-10-18

Spec: `EmbedProof_PRD.md`. Interfaces: `docs/INTERFACES.md` (frozen v1). UI look: brag-styles
`glass-layers` (Apple-style frosted glass: #0B1020 base, indigo/teal/rose drifting fields,
white-12% panels, 1 px white-30% edge, Inter + Space Grotesk).

## Phases

1. **Parallel component builds** (4 subagents, background, each owns one directory)
   - A contracts → verify: `forge test` ≥ 12 green, `forge coverage` ≥ 90% lines, gas asserts, deploy script writes `deployments/31337.json` on Anvil
   - B data + model → verify: train script reproduces artefact + prints hash, `eval.json` + model card with MAE/coverage/recall vs baseline, demo reports score green/red
   - C attestor → verify: unit tests for JCS, signing, AES-GCM, CID, rate limit; live `/attest` once A and B land
   - D web → verify: `next build` clean; pages render; wired to chain + attestor per interface
2. **Integration** (orchestrator): Makefile (`setup`, `demo`, `dev`, `test`), `make demo` runs the 4 scenarios headless against Anvil < 60 s; fix drift
3. **End-to-end UI verification** in browser: all four flows clicked through; screenshots
4. **Docs**: README (quickstart, architecture, STRIDE verbatim, addresses, coverage, limitations, licences), LICENSE, Devpost draft, Dockerfile/fly.toml
5. **Review pass**: contracts security review + reality check against PRD acceptance tests

Constraint: correctness across four components built in parallel (interface drift), then wall-clock.
Failure modes: agent returns plausible-but-wrong (e.g. tests that don't assert, leaky model) → caught by
orchestrator re-running every verification command itself; interface drift → caught by `make demo`
end-to-end run; agent timeout/partial → its report lists unverified items, orchestrator finishes them.
Ledger:
- contracts → delegate (Sonnet): bounded, verifiable by forge test/coverage/gas; orchestrator reviews code after
- data+model → delegate (Sonnet): research + training noise would flood context; verifiable by eval.json + rerun
- attestor → delegate (Sonnet): bounded by interface §5–7; verifiable by unit tests + live run
- web → delegate (Sonnet): large, design-heavy but bounded by interface §3, §6, §8; verified in browser by orchestrator
- integration, Makefile, README, final verification → keep: crosses all components; needs whole picture

## Known constraints (cannot be done from this machine without BD's accounts)
- Base Sepolia deploy needs a funded key (faucet) → `make deploy-testnet` provided, addresses TBD
- Pinata pinning needs `PINATA_JWT` → local content-addressed store fallback (PRD cut list #2 permits)
- Vercel / Fly hosting needs BD's accounts → configs provided
- liboqs-python needs a C toolchain (no MSVC here) → PQClean via `pqcrypto` primary on this machine, same API
- Video recording + Devpost submission are BD's
