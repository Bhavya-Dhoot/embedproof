Track 1 — Carbon Markets & Emissions Transparency

# EmbedProof

> Draft Devpost write-up. Items marked **[BD]** need Bhavya's input or confirmation before
> submitting; nothing in them has been verified.

EmbedProof turns a Turkish steel mill's CO₂-per-tonne claim into a tamper-evident, quantum-safe
certificate. An AI model checks the claim against independent evidence before anyone signs it, and
a smart contract makes it impossible to sell the same verified low-carbon tonne to two importers.

## Climate Impact

Since 1 January 2026, EU importers of steel and cement pay for the embedded emissions of what they
import under CBAM's definitive regime. Importers without verified actual data pay default values
plus a mark-up of 10% in 2026, 20% in 2027 and 30% from 2028
([European Commission](https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism/cbam-definitive-regime_en)).
That penalises exactly the producers who decarbonised: an EAF mill running on scrap and a cleaner
grid cannot prove its lower intensity cheaply, so its customers fall back to defaults.

The EU is Türkiye's largest trading partner, and the Turkish ETS pilot (2026–27) covers the same
sectors as CBAM: power, cement, steel, aluminium and fertiliser
([ICAP](https://icapcarbonaction.com/en/ets/turkish-emission-trading-system)). COP31 opens in
Antalya on 9 November 2026. EmbedProof rewards genuinely low-carbon production by making a real
figure cheap to trust and impossible to double-count.

**[BD]** one-shipment € comparison (verified actual vs default + mark-up), labelled as demo data,
once the default-value cost for Turkish steel is confirmed against iklim.gov.tr.

## Innovation & Creativity

- **Double-claiming as double-spending.** No registry tracks supplier-side, per-batch allocation of
  verified tonnes across EU importers. `ConsignmentAllocator` enforces Σ allocated ≤ verified tonnes
  per installation-period on-chain; an over-allocation reverts with
  `DoubleClaimBlocked(batchId, requested, remaining)`.
- **An AI gate that changes on-chain state.** A LightGBM quantile model predicts the plausible SEE
  interval for the plant's route, capacity and grid; a red score forces `PendingVerifier`, and only
  an accredited-verifier key can move it on, with a reason hash on-chain.
- **Hybrid post-quantum signatures where retention makes them matter.** CBAM records are kept for
  years; a 2027 ECDSA signature forged in 2032 would look real. Every report carries ECDSA
  (re-verified on-chain with `ecrecover`) and ML-DSA-44 (FIPS 204), with `algId` for crypto-agility.
- **Capability-URL privacy.** The report bundle is AES-256-GCM encrypted; the certificate QR carries
  the key in the URL fragment, which never reaches a server.

## Technical Execution

Measured on the final build:

| What | Result |
| --- | --- |
| Contract tests | 178 passing (unit, fuzz, stateful invariants, timelocked upgrade), 100% line and branch coverage |
| Gas (receipt) | `attest()` 228,019 (budget 250k) · first `allocate()` 115,243 (budget 120k) |
| Model, 173 held-out plants | q50 MAE 0.079 tCO₂/t vs 0.171 route-median baseline (54% better); 90% interval coverage 93.0% |
| Detection at red | synthetic under-reports at 0.5× / 0.7×: recall 0.86 / 0.59 (targets 0.9 / 0.6 — missed; strong for BF-BOF, weak for EAF) |
| Attestor | report submit → attestation tx hash in ~0.3 s locally; 293 unit tests |
| `make demo` | all four scenarios plus 13 hardening and replay probes, 23 s from a cold start |

- Contracts: Solidity 0.8 + Foundry + OpenZeppelin 5; three UUPS proxies behind a 24-hour
  `TimelockController`; role registry (ADMIN, OPERATOR, VERIFIER, ATTESTOR, REGULATOR); global pause.
- Attestor: FastAPI; RFC 8785 canonical JSON so re-serialisation never changes the commitment;
  salted commitment bound to chain id and contract address; rate limiting.
- Model: quantile LightGBM with SHAP, grouped held-out evaluation against a route-median baseline,
  interval coverage and detection recall on synthetic under-reports; frozen artefact whose SHA-256
  is the on-chain `modelVersionHash`, and only admin-approved model hashes can attest.
- Web: Next.js; the public verify page needs no wallet and checks the commitment, ECDSA and ML-DSA
  in the browser (pure-JS ML-DSA verifying signatures produced by the PQClean C implementation).
- `make demo` runs all four scenarios headless against a local chain.

### Threat model

STRIDE table with eleven threats and mitigations: see the README section "Security, threat model
and privacy". The chain guarantees integrity after data entry, not the truth of the data; the AI
gate, verifier co-signature and dual signatures cover the rest.

## Practical Usefulness

- **Exporter (paying user):** a Turkish CBAM-sector mill, often an SME, gets an attested per-batch
  intensity record its EU buyers can trust, plus a cheap pre-screen before paying for verification.
- **Importer / authorised CBAM declarant:** scans a QR, sees SEE, risk band, verifier, allocation
  holders and both signature checks, without a login; gets an on-chain allocation receipt.
- **Accredited verifier:** site-visit time goes to the riskiest claims; co-signatures are immutable.
- **Regulator:** read-only view of attested intensities per installation for TR-ETS / CBAM cross-checks.
- Production path: a permissioned Hyperledger Besu network (same Solidity) run by exporters,
  verifiers and the Directorate of Climate Change; the Istanbul Chamber of Commerce as an SME
  onboarding channel.

## Presentation & Communication

The video follows the order the criteria ask for: the climate problem (default values and the
mark-up on Turkish steel), how EmbedProof works (AI check → dual signature → chain), then the four
live scenarios and the real-world impact. **[BD]** video link (unlisted, tested in incognito).

## Limitations & what we did not build

- No offsets or credits, no Article 6 / corresponding-adjustment interaction, and **not accredited
  CBAM verification** — EmbedProof is designed to support verification, not replace it.
- Collusion between an exporter and a verifier is made attributable, not impossible.
- Single attestor key (roadmap: 2-of-3 quorum); EOAs, not HSMs.
- ML-DSA is verified off-chain; native on-chain PQ verification is the migration path.
- The model is a plausibility check on modelled public estimates; it does not measure CO₂.
- Roadmap: lattice-based ZK range proof that SEE ≤ benchmark without revealing it; permissioned
  Besu deployment; accredited-verifier and TR-ETS MRV integration; cement and aluminium variants.

## Standards and related work

- Report schema mirrors the CBAM communication template. Event and data formats reference
  IEEE 2418.2-2020 and IEEE 3205-2023 (alignment, not compliance); IEEE P2418.5 is a working group.
- Oracle design follows the secure-oracle framework of
  [Iqbal et al., 2024](https://www.researchgate.net/publication/383150143_Bridging_Two_Worlds_Framework_for_Secure_Implementation_of_Blockchain_Oracles).
- **[BD]** cite once each, without fawning: lattice cryptography for IoT; smart-grid big-data
  security; Turkish energy ML. (Titles not verified here — add the exact references.)

## Links

- Repo: **[BD]** · Hosted demo: **[BD]** · Testnet explorer link to a `DoubleClaimBlocked` transaction: **[BD]**
