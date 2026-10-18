# Standards: what EmbedProof maps to, and what it does not

This page replaces the earlier wording "report fields mirror the CBAM communication template". That
claim was too strong. Below is the field-by-field position.

## 1. CBAM operator data (definitive period)

For 2026 imports the operator-to-declarant data is the operator's summary emissions report, whose
numbered items are in point 1.2 of Annex IV to Commission Implementing Regulation (EU) 2025/2547.
The list of 16 items below is taken from a secondary summary
([cbamguide.com](https://cbamguide.com/compliance/data-requirements/)). It has not been checked
against the Official Journal text.

EmbedProof's report (`attestor/schemas/report.schema.json`, `docs/INTERFACES.md` §4) against those
items, for iron and steel:

| # | Annex IV item (summary wording) | EmbedProof field | Status |
| --- | --- | --- | --- |
| 1 | Operator identity | operator address in `InstallationRegistry` | Partial: a wallet address, no legal name or contact |
| 2 | Installation identity and location | `installationId`, country (`bytes2`) on-chain | Partial: no address, UN/LOCODE or coordinates |
| 3 | Production processes and routes | `route` (BF-BOF, EAF, DRI-EAF), `cnCode` | Covered at route level; no per-process split |
| 4 | Per good: specific embedded emissions and free allocation | `seeTco2PerT` (direct + indirect), `directEmissionsTco2 / productionTonnes` | Partial: SEE yes; free allocation not recorded |
| 5 | Total direct emissions | `directEmissionsTco2` | Covered |
| 6 | Indirect emissions (goods outside Annex II) | `electricityMwh`, `gridFactorTco2PerMwh`, `indirectEmissionsTco2` | Not required for steel (an Annex II good); recorded anyway for the plausibility check |
| 7 | Measurable heat flows | none | Not covered |
| 8 | Zero-rated fuels | none | Not covered |
| 9 | Waste gases | none | Not covered; this matters for BF-BOF plants |
| 10 | CO₂ capture | none | Not covered |
| 11 | On-site electricity generation (goods outside Annex II) | none | Not required for steel |
| 12–16 | Precursors: default or actual values, averages, origin | none | Not covered; steel goods have precursors (pig iron, DRI, crude steel) |

So the prototype covers the items that drive the headline intensity (route, production, direct
emissions, SEE) and leaves out heat, waste gases, capture and precursors. A production version
would import the operator's filled-in summary report rather than ask for a parallel form.

Iron and steel count direct emissions only under CBAM, so the impact panel in the web app compares
the report's direct intensity with the direct-emission default value.

## 2. CBAM default values used

These come from the EC's informational workbook v2 (6 Aug 2026) for Annex I to Implementing Regulation
(EU) 2025/2621, as corrected by (EU) 2026/1740
([EUR-Lex](https://eur-lex.europa.eu/eli/reg_impl/2025/2621/oj)). Only the Official Journal text is
legally binding. The workbook prints values before the mark-up. The mark-up is 10% in 2026, 20% in
2027 and 30% from 2028 for iron and steel, set in the introduction to Annex I (secondary summary:
[cbamguide.com](https://cbamguide.com/compliance/default-values/)). One CN code represents each route:
BF-BOF `7208`, EAF `7214 20 00`, DRI-EAF `7207 11 14`. See `data/SOURCES.md` §3.

## 3. IEEE blockchain standards

IEEE 2418.2-2020 (blockchain data format) and IEEE 3205-2023 (blockchain interoperability) informed
the design: typed, versioned records (`schema: embedproof.report.v1`), a canonical encoding (RFC
8785), explicit chain id and contract address in every signed document, and an algorithm id for
crypto-agility. We have **not** mapped our formats field by field to either standard, and we claim
no conformance. IEEE P2418.5 is a working group, not a published standard.

## 4. Other formats

- Signatures: EIP-712 typed data (ECDSA, verified on-chain with `ecrecover`) and ML-DSA-44
  (FIPS 204) over the RFC 8785 canonical bytes.
- Content addressing: CIDv1, raw codec, sha2-256; the digest is stored on-chain.
- A certificate is a natural payload for a W3C Verifiable Credential or a future product-passport
  record: issuer = attestor key, subject = batch, evidence = commitment and signatures. We have not
  built either.
