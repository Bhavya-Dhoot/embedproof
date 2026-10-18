# EmbedProof submission video: voiceover script with timings

Voice: Kokoro-82M `af_heart` (offline, narrate skill), speed 1.0. Total length 4:03.33 (243.3 s). Captions are burned in and also in `demo-4min.srt`.

Spoken text is spelled for the voice; the caption is what is on screen. Times are the start and end of each spoken line.

## Chapter map against the PRD section 13 storyboard

| # | Chapter | PRD target | This cut |
|---|---|---|---|
| 1 | The problem | 0:00 to 0:35 | 0:00.00 to 0:30.88 |
| 2 | One sentence | 0:35 to 1:05 | 0:30.43 to 0:57.99 |
| 3 | Demo (a): honest EAF mill | 1:05 to 1:30 | 0:57.54 to 1:26.75 |
| 4 | Demo (b): implausible claim | 1:30 to 1:55 | 1:26.30 to 1:56.78 |
| 5 | Demo (c): double claim | 1:55 to 2:25 | 1:56.33 to 2:19.75 |
| 6 | Demo (d): tamper test | 2:25 to 2:45 | 2:19.30 to 2:41.06 |
| 7 | Impact | 2:45 to 3:25 | 2:40.61 to 3:20.48 |
| 8 | Trust and roadmap | 3:25 to 3:50 | 3:20.03 to 4:03.33 |

Total 4:03.33, inside the 3:45 to 4:15 window. Numbers on screen and in the voice come from README.md, docs/DEVPOST.md and model/MODEL_CARD.md; the shipment example in chapter 7 is arithmetic on the app's reference default and is labelled illustrative demo data (no euro price is quoted).

## 1. The problem  (0:00.00 to 0:30.88)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 0:00.90 to 0:06.28 | 1.1 | Every tonne of Turkish steel that enters the European Union now carries a carbon bill. | Every tonne of Turkish steel entering the EU now carries a carbon bill. |
| 0:06.62 to 0:12.96 | 1.2 | Since the first of January, twenty twenty-six, importers pay for the emissions embedded in what they bring in. | Since 1 January 2026, importers pay for the emissions embedded in what they bring in. |
| 0:13.30 to 0:16.46 | 1.3 | But nobody can see the carbon dioxide in a steel coil. | But nobody can see the CO₂ in a steel coil. |
| 0:16.80 to 0:21.97 | 1.4 | So an importer can trust a number it cannot check, or pay a default value plus a mark-up. | So an importer can trust a number it cannot check, or pay a default value plus a mark-up. |
| 0:22.31 to 0:27.06 | 1.5 | Ten percent this year. Twenty percent next year. Thirty percent from twenty twenty-eight. | 10% in 2026. 20% in 2027. 30% from 2028. |
| 0:27.40 to 0:29.93 | 1.6 | That punishes exactly the mills that cleaned up. | That punishes exactly the mills that cleaned up. |

## 2. One sentence  (0:30.43 to 0:57.99)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 0:30.88 to 0:35.30 | 2.1 | EmbedProof makes the claim checkable, and the tonne impossible to sell twice. | EmbedProof makes the claim checkable, and the tonne impossible to sell twice. |
| 0:35.64 to 0:39.61 | 2.2 | First, an A-I model asks whether the number is plausible for this plant. | First, an AI model asks whether the number is plausible for this plant. |
| 0:39.95 to 0:47.97 | 2.3 | Then the report is signed twice: one classical signature, and one post-quantum signature, because these records are kept for years. | Then it is signed twice: one classical signature, one post-quantum signature, because these records are kept for years. |
| 0:48.31 to 0:53.31 | 2.4 | A smart contract anchors the result, and refuses to hand the same tonne to two buyers. | A smart contract anchors the result, and refuses to hand the same tonne to two buyers. |
| 0:53.66 to 0:57.04 | 2.5 | Anything the model flags goes to a human verifier first. | Anything the model flags goes to a human verifier first. |

## 3. Demo (a): honest EAF mill  (0:57.54 to 1:26.75)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 0:57.99 to 1:07.47 | 3.1 | Marmara Demir is a fictional scrap-fed mill. Its September report claims zero point five three five tonnes of carbon dioxide per tonne of steel. | Marmara Demir, a fictional scrap-fed mill, reports 0.535 tCO₂ per tonne of steel for September. |
| 1:07.81 to 1:13.60 | 3.2 | The model checks that against what this route, size and grid can emit, and against the plant's own history. | The model checks it against what this route, size and grid can emit, and the plant's own history. |
| 1:13.94 to 1:17.47 | 3.3 | It lands inside the expected range: risk twenty, green. | Inside the expected range: risk 20, green. |
| 1:18.06 to 1:25.80 | 3.4 | The mill signs. The attestor adds two signatures and anchors the batch on chain, for about two hundred and twenty-eight thousand gas. | The mill signs. The attestor adds two signatures and anchors the batch on chain: about 228,000 gas. |

## 4. Demo (b): implausible claim  (1:26.30 to 1:56.78)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 1:26.75 to 1:35.35 | 4.1 | Now Anadolu Entegre, a fictional blast-furnace mill, reports zero point five six: a figure only an electric-arc mill could reach. | Anadolu Entegre, a fictional blast-furnace mill, reports 0.56: a figure only an electric-arc mill could reach. |
| 1:35.69 to 1:42.39 | 4.2 | The model expected about two point three. Its reasons are listed: the production route, and the plant's own history. | The model expected about 2.3. Its reasons are listed: the production route, and the plant's own history. |
| 1:42.73 to 1:48.50 | 4.3 | Risk one hundred. Red. The batch is held for a verifier, and its tonnes cannot be allocated. | Risk 100. Red. The batch is held for a verifier, and its tonnes cannot be allocated. |
| 1:49.03 to 1:55.83 | 4.4 | The verifier unseals the report, reads those reasons, and overrides it, with a written reason pinned to the batch. | The verifier unseals the report, reads those reasons, and overrides it, with a written reason pinned to the batch. |

## 5. Demo (c): double claim  (1:56.33 to 2:19.75)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 1:56.78 to 2:03.19 | 5.1 | Back to the honest batch: five thousand verified tonnes. The exporter allocates all of them to Importer A. | Back to the honest batch: 5,000 verified tonnes, all allocated to Importer A. |
| 2:03.53 to 2:06.48 | 5.2 | Now the same five thousand tonnes, to Importer B. | Now the same 5,000 tonnes, to Importer B. |
| 2:07.67 to 2:12.98 | 5.3 | The contract reverts. Double claim blocked: five thousand requested, zero remaining. | The contract reverts. Double claim blocked: 5,000 requested, 0 remaining. |
| 2:13.32 to 2:18.80 | 5.4 | Double claiming is treated like double spending, and the cap is enforced on chain for every batch. | Double claiming is treated like double spending: the cap is enforced on chain. |

## 6. Demo (d): tamper test  (2:19.30 to 2:41.06)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 2:19.75 to 2:26.32 | 6.1 | Importer A scans the certificate's Q-R code. No wallet, no login: every check runs in the browser. | Importer A scans the certificate's QR code. No wallet, no login: every check runs in the browser. |
| 2:26.66 to 2:29.88 | 6.2 | Both signatures verify, including the post-quantum one. | Both signatures verify, including the post-quantum one. |
| 2:30.22 to 2:35.06 | 6.3 | Now change one digit of the report: five three five becomes five three four. | Now change one digit: 0.535 becomes 0.534. |
| 2:35.90 to 2:40.11 | 6.4 | The fingerprint no longer matches the chain, and neither signature validates. | The fingerprint no longer matches the chain, and neither signature validates. |

## 7. Impact  (2:40.61 to 3:20.48)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 2:41.06 to 2:51.26 | 7.1 | Why now. The definitive phase of see-bam has begun, Turkiyeh's emissions trading pilot covers the same sectors, and the European Union is its largest trading partner. | Why now: CBAM's definitive phase has begun, Türkiye's emissions trading pilot covers the same sectors, and the EU is its largest trading partner. |
| 2:51.60 to 2:57.28 | 7.2 | Without verified data, a cleaner mill pays the same default plus mark-up as everyone else. | Without verified data, a cleaner mill pays the same default plus mark-up as everyone else. |
| 2:57.62 to 3:07.00 | 7.3 | For one shipment, the gap is the tonnes, times default plus mark-up minus the verified actual, times the carbon price. Illustrative, with demo data. | For one shipment: tonnes × (default + mark-up − verified actual) × EU carbon price. Illustrative, demo data. |
| 3:07.34 to 3:12.11 | 7.4 | EmbedProof makes the real figure cheap to trust, and impossible to count twice. | EmbedProof makes the real figure cheap to trust, and impossible to count twice. |
| 3:12.45 to 3:19.53 | 7.5 | And with COP thirty-one opening in Antalya on the ninth of November, this is implementation, not another pledge. | And with COP31 opening in Antalya on 9 November, this is implementation, not another pledge. |

## 8. Trust and roadmap  (3:20.03 to 4:03.33)

| Time | Line | Spoken | On-screen caption |
|---|---|---|---|
| 3:20.48 to 3:27.90 | 8.1 | What this is not: it is not accredited see-bam verification, it issues no offsets, and it does not touch Article six. | Not accredited CBAM verification. No offsets. No Article 6. |
| 3:28.24 to 3:32.65 | 8.2 | The chain guarantees integrity after data entry, not the truth of the data. | The chain guarantees integrity after data entry, not the truth of the data. |
| 3:32.99 to 3:37.21 | 8.3 | The model measures consistency with Climate TRACE, not real emissions. | The model measures consistency with Climate TRACE, not real emissions. |
| 3:37.55 to 3:46.83 | 8.4 | With a plant's history it catches ninety-seven percent of half-size under-reports. With no history, eighty-four percent, which misses our ninety percent target. | With plant history it catches 97% of half-size under-reports. With no history, 84%: that misses our 90% target. |
| 3:47.17 to 3:56.58 | 8.5 | One hundred seventy-eight contract tests, full coverage. Next: a two-of-three attestor quorum, zero-knowledge proofs, and a permissioned Besu network. | 178 contract tests, 100% coverage. Next: a 2-of-3 attestor quorum, zero-knowledge range proofs, a permissioned Besu network. |
| 3:56.92 to 3:59.93 | 8.6 | EmbedProof. Check the claim. Block the double sale. | EmbedProof. Check the claim. Block the double sale. |

