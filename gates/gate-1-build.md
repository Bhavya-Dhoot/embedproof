# Gate 1: end-to-end build (2026-10-18)

## Decisions
- Interfaces frozen in `docs/INTERFACES.md` before parallel builds; v1.1 amendments (§9) after an
  adversarial review: EIP-712 attestor signature over every on-chain field, live operator/attestor
  role checks, admin batch revocation, verifier separation of duties, PQ key history, deploy guard.
- ML-DSA-44 backend: PQClean via `pqcrypto` on Windows (liboqs-python needs a C toolchain); liboqs in
  the Docker image (`WITH_LIBOQS=1`); browser verification with `@noble/post-quantum`. Cross-checked.
- Report bundle AES-256-GCM encrypted; key travels in the certificate URL fragment.
- Model (round 2, FR-M1): route, capacity, Ember grid intensity and a LAGGED Climate TRACE facility
  prior (m-14..m-3). New plant split; test scored once; no-history case reported separately.
- Demo EAF capacity moved to 300,000 t/yr (inside the training range); real plant names removed
  from demo fixtures.
- UI: brag-styles `glass-layers` (frosted panels, drifting indigo/teal/rose fields, Inter + Space Grotesk).

## Verified (by the orchestrator, not only by agents)
| Check | Result |
| --- | --- |
| `forge test` | 178 passed |
| `forge coverage` (src) | 100% lines, statements, branches, functions |
| Gas, receipt | attest 228,019 ≤ 250k; first allocate 115,243 ≤ 120k |
| `pytest model/tests attestor/tests` | 341 passed |
| `pnpm test` / lint (web) | 33 passed (4 opt-in live skipped) / clean |
| `make demo` | 5/5 scenarios + 13 hardening/replay probes, 23 s |
| Browser click-through | (a) green attest, (b) red → verifier reject with reason, (c) DoubleClaimBlocked panel + importer claim, (d) tamper flips ECDSA + ML-DSA; amber attest; mobile 375 px and tablet nav |
| Fresh clone keeps model hash | `20b809be…` byte-identical (`.gitattributes`) |

## Deltas from the PRD
- Detection targets met with plant history (0.97 at 0.5×, 0.89 at 0.7×); without history 0.84 at 0.5× misses; DRI-EAF weak. Metrics measure consistency with Climate TRACE, stated in the model card.
- +20 CBAM-default rule fires for most honest EAF mills (EC steel defaults are BF-BOF-scale, direct-only); implemented as specified, documented in the model card.
- Sentinel-2 feature and 2-of-3 attestor quorum not built (cut list / roadmap).
- Privacy as built differs from the PRD text (tonnes public; key also to operator and allocation holders; no per-field disclosure) — documented in README.

## Open risks / needs BD
- Base Sepolia deploy (funded key, real role holders), Pinata JWT, Vercel + Fly hosting, the 4-minute narrated video, Devpost submission, confirming the €/t default-value figure.
- Injected-wallet path and in-browser PDF download not exercised (PDF generation is unit-tested).

## Ledger
- contracts, model, attestor, web: delegated (Sonnet), each owning one directory; every claim re-run by the orchestrator.
- security review: delegated read-only (Sonnet); 13 findings, fixes dispatched to the owning agents.
- integration, Makefile/scripts, README/Devpost, browser verification: kept.
- launch video: delegated (Sonnet) with the brag-slim workflow and fast-render.
