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
| `forge test` | 185 passed (round 3: per-period cap, revoke-and-resubmit invariants) |
| `forge coverage` (src) | 100% lines, statements, branches, functions |
| Gas, receipt | attest 227,997 ≤ 250k; first allocate 117,952 ≤ 120k |
| `pytest model/tests attestor/tests` | 479 passed (model 51, attestor 428) |
| `pnpm test` / lint / typecheck / `next build` (web) | 70 passed (4 opt-in live skipped) / clean / clean / 9 pages built |
| `make demo` | 5/5 scenarios + 13 hardening/replay probes + rescore PASS, 32.9 s wall |
| Browser click-through | (a) green attest, (b) red → verifier reject with reason, (c) DoubleClaimBlocked panel + importer claim, (d) tamper flips ECDSA + ML-DSA; amber attest; mobile 375 px and tablet nav |
| Fresh clone of round-3 commit b5e2deb | `make setup` exit 0; `make demo` 5/5 + rescore PASS, 90 s cold; model hash `40d9ee13…` byte-identical |

## Round 3 (judge panel 73/100 + PRD audit 95 met / 36 partial / 5 missing)
- Allocation cap keyed by (installation, period): closes revoke-and-resubmit double counting.
- Attestor: capacity bound on attested tonnes; grid-factor and electricity-norm flags (informational);
  assessment carries exact scorer inputs; `attestor.rescore` CLI; hosted demo runner (testnet/local only);
  report JSON Schema; Pinata success-path tests.
- Model: Türkiye slice (n = 4 plants, not powered); artefact unchanged.
- Web: CBAM impact panel (no assumed price), verdict banner, proves / does-not-prove box, reason-hash
  check, sample-certificate links, demo runner UI, role banners, copy pass removing overclaims.
- Docs: STANDARDS.md (honest CBAM field mapping), README impact table, why-blockchain, related work,
  scale arithmetic, PQ key lifecycle, privacy as built.

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
