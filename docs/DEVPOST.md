Track 1 — Carbon Markets & Emissions Transparency

# EmbedProof

> Devpost write-up. The "About the project" field on Devpost is this file from "## Inspiration" down.

EmbedProof turns a Turkish steel mill's CO₂-per-tonne claim into a tamper-evident certificate with a
post-quantum signature. An AI model checks the claim for plausibility against Climate TRACE's
estimates before anyone signs it. A smart contract stops the same attested tonne being allocated to
two EU importers.

## Inspiration

Since 1 January 2026, EU importers of steel pay for the embedded emissions of what they import under
CBAM's definitive regime. If an importer has no trusted actual data, it declares the EC's default
value plus a mark-up: 10% in 2026, 20% in 2027 and 30% from 2028. That penalises exactly the
producers who decarbonised. A Turkish EAF mill running on scrap cannot cheaply prove its lower
intensity, so its customers fall back to defaults. The EU is Türkiye's largest trading partner, the
Turkish ETS pilot covers the same sectors, and COP31 opens in Antalya on 9 November 2026.

## Climate Impact: one shipment

For iron and steel only direct emissions count. The EC default for Turkish EAF rebar
(CN 7214 20 00) is 2.310 tCO₂e per tonne before the mark-up. Take our honest demo mill's
5,000 t batch, which is demo data with direct emissions of 1,200 t (0.24 tCO₂e/t):

| 5,000 t of EAF rebar | 2026 (+10%) | 2028 onward (+30%) |
| --- | --- | --- |
| Counted at the default value | 12,705 tCO₂e | 15,015 tCO₂e |
| Counted with the attested actual value | 1,200 tCO₂e | 1,200 tCO₂e |
| Over-counted under the default | 11,505 tCO₂e | 13,815 tCO₂e |

The video uses total SEE (0.535 t/t, which includes indirect emissions) instead of direct, the
conservative variant: 10,030 tCO₂e for 2026. The web app's impact panel multiplies the gap by a
carbon price only if you enter one; it never assumes a price.

The figure is simplified: it ignores the free-allocation adjustment and any carbon price paid in
Türkiye. That gap is the price signal CBAM intends, and it only reaches a mill that has decarbonised
if its buyer can trust the mill's real figure and claim it exclusively. EmbedProof makes the figure
cheap to check and stops it being allocated twice.

Sources: the EC's default-value workbook v2 for Annex I to Implementing Regulation (EU) 2025/2621,
as corrected by (EU) 2026/1740 ([EUR-Lex](https://eur-lex.europa.eu/eli/reg_impl/2025/2621/oj)). The
mark-up schedule comes from Annex I's introduction (secondary summary:
[cbamguide.com](https://cbamguide.com/compliance/default-values/)). Details:
[`docs/STANDARDS.md`](https://github.com/Bhavya-Dhoot/embedproof/blob/main/docs/STANDARDS.md).

## What it does: the four demo flows

| # | Scenario | What you see |
| --- | --- | --- |
| a | Honest EAF mill | Report → AI band **green** → dual-signed, pinned, `attest()` → `Attested` |
| b | Implausible claim | BF-BOF mill declares EAF-level intensity → **red** with SHAP reasons → `PendingVerifier` → verifier rejects with an on-chain reason hash |
| c | Double claim | Exporter allocates tonnes to importer A, then tries them again for importer B → reverts `DoubleClaimBlocked(batchId, requested, remaining)` |
| d | Tamper test | Change one digit of the off-chain report → commitment, ECDSA and ML-DSA checks all fail in the browser |

## Innovation & Creativity

- **Double-claiming treated as double-spending.** `ConsignmentAllocator` enforces Σ allocated ≤
  attested tonnes per installation-period on-chain, including across a batch revocation and
  resubmission.
- **An AI gate that changes on-chain state.** A LightGBM quantile model predicts the plausible
  intensity interval for the plant's route, capacity and grid, using the plant's own lagged
  Climate TRACE history. A red score forces `PendingVerifier`, and only an accredited-verifier key
  can move it on.
- **An AI score anyone can re-check.** `python -m attestor.rescore "<certificate URL>"` re-runs the
  frozen model on the inputs stored in the certificate. It checks that the model file's SHA-256
  equals the on-chain `modelVersionHash` and that the recomputed score equals the on-chain score.
- **Hybrid post-quantum signatures, where retention makes them matter.** CBAM records are kept for
  years, and a 2027 ECDSA signature forged in 2032 would look real. The attestor signs every
  certificate with ECDSA (EIP-712, re-verified on-chain with `ecrecover`) and ML-DSA-44 (FIPS 204,
  verified in the browser), with an `algId` for crypto-agility and an on-chain key history.
- **Capability-URL privacy.** The report is AES-256-GCM encrypted, and the certificate QR carries
  the key in the URL fragment, which browsers never send to a server.

## Why a blockchain, not a database

A Turkish mill, its EU importers, an accredited verifier and a regulator don't share an IT system,
and none of them should have to trust another to run the allocation ledger. The contract enforces
the cap for everyone at once, every revocation and verifier decision is attributable to a key, and
the record outlives any one company's server. In production the same Solidity runs on a
permissioned Besu network operated by the participants.

## How we built it: Technical Execution

| What | Result (final build) |
| --- | --- |
| Contract tests | 185 passing (unit, fuzz, stateful invariants incl. revoke-and-resubmit, EIP-712 replay matrix, timelocked upgrade); 100% line, branch and function coverage |
| Gas (receipt) | `attest()` 227,997 (budget 250k) · first `allocate()` 117,952 (budget 120k) |
| Python tests | 479 passing (model 51, attestor 428) |
| Model, 173 held-out plants | q50 MAE 0.042 tCO₂/t vs 0.188 for a route-median baseline (78% better); 90% interval coverage 92.0% |
| Detection at red | synthetic under-reports at 0.5× / 0.7×: recall 0.97 / 0.89 with plant history (targets 0.9 / 0.6 met); 0.84 / 0.61 for a plant with no history (0.5× target missed); DRI-EAF weak |
| Honest caveat | A no-model persistence forecast is closer on point error (MAE 0.026). The model's value is the calibrated interval, the no-history branch and the explanations. The metrics measure consistency with Climate TRACE, not real emissions. |
| Türkiye slice | 4 held-out plants, not statistically powered: MAE 0.049 vs 0.104, coverage 86.5%, recall 100% at 0.5× |
| `make demo` | Fresh chain, contracts, attestor, then all four scenarios, 13 hardening and replay probes and the rescore check, headless |

The video was recorded before the final hardening round; this table has the current numbers.

- **Contracts:** Solidity 0.8.24, Foundry and OpenZeppelin 5.
  - Three UUPS proxies sit behind a 24-hour `TimelockController`.
  - A role registry holds ADMIN, OPERATOR, VERIFIER, ATTESTOR and REGULATOR, with a global pause.
  - Only admin-approved model hashes can attest.
- **Attestor:** FastAPI.
  - RFC 8785 canonical JSON, so re-serialising never changes the commitment.
  - A salted commitment bound to the chain id and contract address.
  - Attested tonnes capped at nameplate capacity; informational grid-factor and electricity flags.
  - Rate limiting and a streaming body cap.
- **Model:** quantile LightGBM with SHAP.
  - Grouped held-out evaluation against route-median and persistence baselines.
  - Per-route conformal calibration.
  - A frozen artefact whose SHA-256 is the on-chain `modelVersionHash`; `make train` reproduces it
    byte for byte.
- **Web:** Next.js 15 with the Apple-style glass look.
  - The public verify page needs no wallet. It checks the commitment, ECDSA, ML-DSA and the
    verifier's reason hash in the browser.
  - It shows a plain-language verdict and a "what this proves / does not prove" box.

### Threat model (STRIDE, abridged)

| Threat | Mitigation | Residual risk |
| --- | --- | --- |
| Falsified report | AI plausibility gate, mandatory verifier co-sign on red, tonnes capped at capacity | Collusion; plausible-but-false figures |
| Oracle manipulation | Separate attestor key, model hash on-chain, verifier co-sign above threshold ([Iqbal et al., 2024](https://www.researchgate.net/publication/383150143_Bridging_Two_Worlds_Framework_for_Secure_Implementation_of_Blockchain_Oracles)) | Single attestor in the prototype |
| Double-claiming | Cap per installation-period that survives revoke and resubmit, unique consignment ids | Claims made outside EmbedProof |
| Replay | EIP-712 over every on-chain field, with chain id and contract in the domain | None in the EIP-712 replay test matrix |
| Harvest-now, forge-later | ECDSA + ML-DSA-44, PQ key hash anchored on-chain with rotation and revocation | On-chain transaction signatures are still ECDSA |
| Data leakage | Report, electricity and SHAP output encrypted off-chain | Attested tonnes and SEE are public (the cap needs them) |

The full 11-row table is in the README.

## Practical Usefulness

- **Exporter (the paying user):** a Turkish CBAM-sector mill, often an SME, gets an attested
  per-batch intensity record its EU buyers can trust, and a cheap pre-screen before paying for
  verification.
- **Importer / CBAM declarant:** scans a QR code and, with no login, sees the intensity, the risk
  band, the verifier decision, allocation holders, both signature checks and the impact panel. It
  also gets an on-chain allocation receipt.
- **Accredited verifier:** site-visit time goes to the riskiest claims, and co-signatures are
  immutable.
- **Regulator:** a read-only view of attested intensities per installation for TR-ETS and CBAM
  cross-checks.
- **Scale:** all 866 steel plants in our Climate TRACE dataset attesting monthly is 10,392
  `attest()` calls a year (2.37 × 10⁹ gas). At the measured 1.3% false-red rate, a fully honest
  population would send about 135 reports a year to a verifier unnecessarily.
- **Production path:** a permissioned Hyperledger Besu network run by exporters, verifiers and the
  Directorate of Climate Change.

## Challenges we ran into

- The first model ignored the plant's own history and missed the detection targets. Adding the
  history naively would leak the same-month estimate, so we use a lagged prior (m-14 to m-3), hold
  out whole plants, and report the no-history case and a persistence baseline even where they make
  us look worse.
- Keeping `allocate()` under 120k gas while adding the per-period cap meant packing an allocation
  into one storage slot.
- Getting a pure-JS ML-DSA verifier in the browser to accept signatures from the PQClean C
  implementation byte for byte.
- An adversarial security review found 13 issues; signature replay across periods was the most
  serious. A later audit found that revoking and resubmitting a batch reset the allocation cap. All
  of these are fixed and tested.

## What we learned

The chain guarantees integrity after data entry, not the truth of the data. The AI gate, the verifier
co-signature and the capacity bound are what make the data worth anchoring, and honest metrics are
worth more than flattering ones.

## Limitations

- No offsets or credits, no Article 6, and **not accredited CBAM verification**. EmbedProof is
  designed to support verification, not replace it.
- The model covers steel only and is a plausibility check on modelled public estimates. It does not
  measure CO₂.
- There is a single attestor key (roadmap: a 2-of-3 quorum), and keys are EOAs, not HSMs.
- ML-DSA is verified off-chain.
- Not yet deployed to Base Sepolia (the deploy runbook is ready; it needs a funded key). Today it
  runs locally with `make setup && make demo`.
- Our report covers the core CBAM operator data items (route, production, direct emissions, SEE),
  not heat flows, waste gases or precursors (see `docs/STANDARDS.md`).

## What's next

A lattice-based ZK range proof that SEE ≤ benchmark without revealing it; a permissioned Besu
deployment; accredited-verifier and TR-ETS MRV integration; importing the operator's own CBAM
summary report; cement and aluminium models.

## Standards and related work

- CBAM: a field-by-field table of our report against the operator's summary emissions report
  (Annex IV, Implementing Regulation (EU) 2025/2547) is in `docs/STANDARDS.md`.
- IEEE 2418.2-2020 and IEEE 3205-2023 informed the design. We claim no conformance.
- The oracle design follows [Iqbal et al., 2024](https://www.researchgate.net/publication/383150143_Bridging_Two_Worlds_Framework_for_Secure_Implementation_of_Blockchain_Oracles).

## Links

- Code: https://github.com/Bhavya-Dhoot/embedproof
- Video: https://youtu.be/-JmX905GWOQ
