# Model card: EmbedProof plausibility model v1 (steel, round 2: with lagged Climate TRACE prior)

`modelVersionHash` = `0x40d9ee13d1c9ee9c52d0eb3749353665238712a72e7d8444bdc51be41b1008c8`
(sha256 of `model/artefacts/embedproof_model_v1.json`; also in `model/artefacts/model_version.txt`). All numbers below are copied from
`model/artefacts/eval.json`, produced by `python -m model.train` for exactly this artefact.

## 0. Read this first: what the numbers mean

**With the lagged prior and Climate TRACE ground truth, the headline numbers measure consistency with Climate TRACE's own history of the same plant (persistence). They are not real-world accuracy.**
The truth is Climate TRACE v5.10.0's *modelled* direct+indirect emissions factor, the prior is the same plant's earlier Climate TRACE emissions factor, and the "synthetic underreports" are factor x that same truth,
i.e. relative to the history. A real operator whose true emissions differ from Climate TRACE's estimate in a stable way is invisible to every metric here, and so is an error that Climate TRACE and the model share
(72 % of training rows carry Climate TRACE confidence "low" or "very low" for the emissions factor). The no-prior rows (section 5) are the closer-to-independent case and are much weaker.
A plain no-model persistence rule (predict the plant's own lagged total SEE) is *more accurate* than the model's q50 on the same rows (MAE 0.026 vs 0.042); what the model adds is calibrated intervals, a no-history branch and explanations.

## 1. What it is, and what it is not

A screening model. Given an installation's **route**, **nameplate capacity**, the **national grid carbon intensity** and, when available, a **lagged Climate TRACE prior** for the same plant, it predicts the plausible
interval (5th / 50th / 95th percentile) of **specific embedded emissions, SEE** (tCO2e per tonne of steel, direct + indirect) and turns the position of an operator's declared SEE in that interval into a 0-100 risk
score (formula in `docs/INTERFACES.md` section 7). Red (> 60) sends a batch to a human verifier.

* It does **not measure CO2**. No satellite imagery, plant telemetry or meter data enters the model. The grid input is a national electricity-mix intensity, an **activity proxy for the indirect term**, not a
  measurement of any plant. Climate TRACE's own estimates are partly built from satellite-derived activity (its `satellite` method covers 28 % of the training rows) and are likewise estimates, not measurements.
* It is **not** accredited CBAM verification and not a replacement for it.

## 2. Model

LightGBM 4.7.0 quantile regression, three boosters (q = 0.05, 0.50, 0.95), `num_leaves = 7`, `min_data_in_leaf = 100`, 600 rounds, learning rate 0.05, bagging 0.8, `max_bin = 255`;
`deterministic = True`, `force_row_wise = True`, 1 thread, seed 7. Chosen among 6 configurations by 5-fold plant-grouped CV on the training plants only (`eval.json -> cv_selection_train_plants_only`;
selection score = mean of the with-prior and no-prior pinball loss).

Inputs (4): `route_code`, `capacity_tpy`, `grid_gco2_kwh` (Ember annual national CO2 intensity, last observation carried forward), `ct_prior_ef` (below). LightGBM handles a missing prior natively.

**Lagged prior (FR-M1).** For plant-month m: the mean of the same plant's own Climate TRACE **direct** emissions factor (tCO2e per t steel, the `emissions_factor` column, i.e. Climate TRACE's emissions divided by its activity)
over months **m-14 .. m-3**, required to have >= 6 of those 12 months, otherwise NaN. It never uses months m-2, m-1, m or later (unit-tested). Choices, fixed before any result was seen and not tuned:
* *Lag.* The PRD describes a ~2-month Climate TRACE publication lag and a report for month m is filed after m ends, so the freshest month a verifier can rely on is about m-2; the window ends at m-3 for one month of safety margin.
* *Length 12 months* averages out seasonality and Climate TRACE's monthly grid noise. *Minimum 6 months* = half the window; it makes 2021 rows NaN (prior present on 86.9 % of all rows, 33 % of 2021 rows, 99 % of 2022+ rows).
* *Direct, not total, emissions factor.* This keeps the indirect term tied to the independent Ember grid feature instead of importing Climate TRACE's own electricity (0.83-0.87 MWh/t) and grid assumptions into the prior.
  The alternative (lagged **total** SEE) is better on training-plant CV (below) and was **not** switched to after seeing test results.
* *Test plants use their own earlier history*, as a verifier would have it in deployment; plants are never shared between train / calibration / test.
* *No-history case.* In training, 30 % of the training plants (fixed seed 11) have their prior blanked in all months, so the model learns a proper "new plant" branch instead of relying on the 2021 rows that are NaN by construction.

Post-processing: (1) the three predictions are sorted per row (monotone rearrangement) so q05 <= q50 <= q95; (2) **one-sided split-conformal offsets per route and per regime** (prior present / absent) fitted on the **191 calibration
plants** only: q05' = max(0, min(q05 - lower, q50)), q95' = max(q95 + upper, q50), each tail aiming at 5 %. The published `q05`/`q50`/`q95` (= `expectedLow`/`expectedHigh`) are these calibrated values rounded to 4 decimals,
so the risk score can be recomputed from the published numbers. Offsets (tCO2/t, lower / upper): with prior BF-BOF 0.000 / 0.000 (70 plants), EAF 0.012 / 0.100 (107), DRI-EAF 0.299 / 0.373 (12 plants only, noisy);
no prior BF-BOF 0.004 / 0.008, EAF 0.022 / 0.366, DRI-EAF 0.467 / 0.601. Explanations: `shap.TreeExplainer` on the q50 booster, top three contributions by magnitude (now from four features).

## 3. Training data and dates

| Source | Version / date | Licence | Use |
| --- | --- | --- | --- |
| Climate TRACE iron-and-steel plant-month CSV | v5.10.0 (Aug 2026), retrieved 2026-10-18 | CC BY 4.0 | target, route, capacity, lagged prior |
| Ember yearly electricity data, CO2 intensity | release of 2026-06-23, retrieved 2026-10-18 | CC BY 4.0 | grid feature |
| EC CBAM default values workbook | v2 dated 2026-08-06 (Reg. (EU) 2025/2621 as corrected by 2026/1740) | EU, informational | reference only, not a feature |

Details, exact URLs, hashes and the list of what was not available are in `data/SOURCES.md`. The training snapshot hash is in the artefact (`training_data.snapshot_sha256`). The fixtures were not changed in round 2
(the prior is computed from the existing `ct_ef_direct` column in `model/features.py`).

Fixture: **52,869 plant-months, 866 plants, 77 countries, 2021-01 .. 2026-06** (BF-BOF 316 plants, EAF 492, DRI-EAF 58). Dropped: other Climate TRACE route types, 3,744 plant-months with capacity 0, 675 with emissions factor 0.
**Round-2 split (new seed 20261019, stratified by route): train 502 plants, calibration 191, test 173** (BF-BOF 183 / 70 / 63; EAF 286 / 108 / 98; DRI-EAF 33 / 13 / 12). The round-1 split (seed 20261018) had already been scored once,
so round 2 uses a fresh split: the test plants have never influenced any round-2 decision. Test set: 10,590 plant-months; **9,208 of them (172 plants) have a real prior**, 1,382 (early 2021) naturally do not.

## 4. Features and target leakage

Used: route, capacity, grid, **lagged own Climate TRACE direct emissions factor**. This reverses round 1's exclusion of "the Climate TRACE facility estimate", but only in lagged form; it is a persistence feature by design (section 0).

Still **not** used: same-or-recent-month Climate TRACE emissions / activity / emissions quantity (`emissions_quantity = activity x EF` would reconstruct the target), the Climate TRACE direct+indirect factor of recent months,
capacity factor, electricity-use factor (a per-route constant: 99.5 % of EAF rows are 0.87 MWh/t, 99.7 % of BF-BOF rows 0.83), scrap share, methodology, and operator-declared electricity per tonne. Country as a categorical hurt held-out coverage in round 1.

Training-plant CV ablation (5-fold, shipped hyper-parameters; MAE of q50 / mean pinball; `eval.json -> feature_ablation_grouped_cv_train_plants`):

| Feature set | with prior: MAE / pinball | no prior (prior blanked): MAE / pinball |
| --- | --- | --- |
| route + capacity + grid (round-1 features, no prior input) | 0.0815 / 0.02346 | same |
| route + prior | 0.0912 / 0.02648 | 0.1761 / 0.04798 |
| route + grid + prior (no capacity) | **0.0352 / 0.01048** | 0.0833 / 0.02456 |
| **route + capacity + grid + prior (shipped)** | 0.0376 / 0.01100 | 0.0832 / 0.02443 |
| alternative: prior = lagged **total** SEE (not shipped) | 0.0297 / 0.00847 | 0.0862 / 0.02545 |

Honest reading: the prior roughly halves the error where it exists; **capacity again carries no useful signal** (mean |SHAP| 0.006; removing it improves CV MAE by 6 %) and is kept only because FR-M1 lists it;
a total-SEE prior would be more accurate still, which is another way of saying the best predictor of a Climate TRACE value is Climate TRACE's previous value.

## 5. Evaluation on the 173 held-out plants (test set scored once)

Protocol: the test plants were not used for hyper-parameter choice, feature or window choice, mask fraction, or calibration. The pipeline was executed twice end-to-end (the second run only added the alternative-prior ablation
and per-route persistence rows); the test summary metrics and the hash were identical across the two runs (separate processes). Baseline = median SEE of the training rows of the same route (BF-BOF 2.398, DRI-EAF 1.497, EAF 0.617).
Confidence intervals resample **plants** (2,000 draws), because the months of one plant are strongly correlated. "Without prior" = the prior withheld (NaN) and the no-prior calibrated intervals used.

| Metric (target) | WITH prior, 9,208 rows | WITHOUT prior, same 9,208 rows | WITHOUT prior, all 10,590 rows |
| --- | --- | --- | --- |
| MAE of q50 (tCO2/t) | **0.0422** (CI 0.032-0.055) | 0.0884 (0.064-0.120) | 0.0879 |
| route-median baseline MAE | 0.1877 | 0.1877 | 0.1870 |
| improvement vs baseline (>= 20 %) | **77.5 %** (72.3-82.1) | 52.9 % (42.6-62.8) | 53.0 % |
| 90 % interval coverage (85-95 %) | **92.0 %** (89.2-94.4) | 92.3 % (89.2-95.0) | 92.8 % (90.0-95.3) |
| coverage before conformal | 84.0 % | 82.9 % | 83.4 % |
| mean interval width | 0.246 | 0.596 | 0.595 |
| recall at red (> 60), synthetic 0.5x (>= 0.9) | **96.9 %** (94.1-99.3) | **84.2 %** (78.7-89.2) | 84.2 % |
| recall at red, synthetic 0.7x (>= 0.6) | **89.4 %** (85.2-93.4) | 61.0 % (54.3-66.9) | 61.7 % |
| recall at red, synthetic 0.85x (no target) | 58.8 % (52.0-65.4) | 28.9 % (23.0-35.0) | 30.1 % |
| false red on unmodified values, 1.0x | 1.3 % (0.4-2.4) | 1.3 % (0.5-2.5) | 1.3 % |
| amber-or-red on unmodified values | 2.2 % | 1.8 % | 1.7 % |
| recall 0.5x / 0.7x / 0.85x without the +20 CBAM rule | 95.8 / 85.2 / 54.0 % | 81.8 / 49.8 / 28.1 % | 82.0 / 50.8 / 29.3 % |

**Target scorecard.** With the prior, all four PRD targets are met (MAE 77.5 % better; coverage 92.0 %; recall 0.969 at 0.5x and 0.894 at 0.7x), subject to section 0. Without the prior, the MAE, coverage and 0.7x-recall targets are met
(the 0.7x lower CI edge is 54 %) but **the 0.5x recall target is missed (84.2 % vs >= 90 %)**. No target was met by shrinking intervals below the 85 % coverage floor.

Per route (rows / plants; red rate = recall for 0.5x, 0.7x, 0.85x, then the false-red rate at 1.0x):

| Route | prior | MAE vs baseline | coverage | mean width | red @0.5x / 0.7x / 0.85x / 1.0x |
| --- | --- | --- | --- | --- | --- |
| BF-BOF (62 plants) | with | 0.0265 vs 0.0969 | **86.9 %** | 0.103 | 100 / 99.3 / 98.6 / 3.0 % |
| BF-BOF | without | 0.0271 vs 0.0969 | **86.5 %** | 0.146 | 100 / 100 / 80.5 / 3.5 % |
| EAF (98 plants) | with | 0.0406 vs 0.1964 | 94.9 % | 0.244 | 99.5 / 92.1 / 42.0 / 0.4 % |
| EAF | without | 0.0918 vs 0.1964 | **96.3 %** (above band) | 0.751 | 83.6 / 44.7 / 2.4 / 0.2 % |
| DRI-EAF (12 plants) | with | 0.1482 vs 0.6230 | 91.4 % | 1.089 | **53.1 / 4.9 / 0 / 0 %** |
| DRI-EAF | without | 0.4080 vs 0.6230 | 85.7 % | 1.629 | **0 / 0 / 0 / 0 %** |

Reading: BF-BOF and EAF detection is strong with a prior; **DRI-EAF is not detectable at 0.7x and below in either regime** (12 test and 33 training plants, very wide intervals), and BF-BOF has a 3 % false-red rate and
per-route coverage at the low edge of the band (the per-route conformal offsets calibrated on 70 plants did not fully transfer to the 62 test plants). Without the prior, EAF coverage is conservative (96.3 %).

Reference (no model): persisting the plant's own lagged total SEE gives MAE **0.0262** on the 9,208 with-prior rows (BF-BOF 0.0265, EAF 0.0235, DRI-EAF 0.0522), i.e. better than the model's 0.0422; it has no interval and no no-history case.

**SHAP stability across 5 seeds** (3,000 held-out rows): mean |SHAP| route 0.612, lagged prior 0.185, grid 0.140, capacity 0.006. The top-3 set (route, prior, grid) and the full rank order were identical in 5/5 seeds.

**The +20 rule is not discriminating for steel.** EC defaults are 2.3-4.0 tCO2/t, so SEE < 0.5 x default holds for essentially every EAF and DRI plant; it adds 20 points to every EAF mill (the honest demo EAF scores 20, green) without separating honest
from dishonest claims. The EC steel default is also direct-only while SEE is direct+indirect.

## 6. Known failure modes

1. **New plant with no Climate TRACE history.** The prior is NaN (also whenever < 6 of the window months exist). The model falls back to route + capacity + grid: intervals are 2.4x wider, 0.5x recall is 84 %, DRI-EAF detection is zero.
2. **Persistence cuts both ways.** A claim that equals Climate TRACE's (possibly wrong) history scores green; a genuine step change in a plant's emissions (new furnace, fuel switch, shutdown) scores as an anomaly.
   An operator who drifts slowly can drag the prior in later periods if attested values ever feed Climate TRACE; here they do not.
3. **Unseen countries.** Leave-country-out CV on the training plants: with prior MAE 0.064 vs 0.239 baseline but raw (pre-calibration) coverage only 65.6 %; without prior MAE 0.129 vs 0.240 and 72.4 %. Intervals for plants from
   countries absent from training are too narrow.
4. **Plants near grid boundaries / sub-national grids / captive or contracted power.** Ember's factor is national production-mix (imports excluded); Climate TRACE itself uses regional intensities for some countries
   (standard deviation across plants of one country-month up to 0.12 t/MWh), so a plant whose real supply differs from the national mix is mis-predicted in the indirect term.
5. **Capacity outside the training range** (EAF 200,000-6,022,000 t/yr; BF-BOF 380,000-22,950,000; DRI-EAF 200,000-7,400,000; ranges are for the train split and are in the artefact): trees extrapolate flat and the explanation says so.
6. **DRI-EAF**: 33 training, 13 calibration and 12 test plants; offsets and metrics are noisy and weak (above).
7. **Missing grid data**: Albania (44 plant-months) has no Ember row; unknown countries score with a missing grid input and the "other countries" CBAM default.
8. **Time**: Climate TRACE data end 2026-06; for the demo periods (2026-09, 2026-10) the prior windows end 2026-06 and the grid value is carried forward.
9. **Gaming**: a claim just above the calibrated q05 scores 0 (plus 20). No cross-installation or cross-period consistency checks.
10. **Excluded routes**: induction furnace, DRI-IF, BOF-only and mixed-route plants are not modelled; cement raises `ValueError`.
11. **One split, one seed** per round; variability across splits is represented only by the plant bootstrap.

## 7. Inference

`Scorer.load()` about 0.8 s (plus a cold-start import of shap/numba of up to ~14 s on this machine); warm calls (300 calls, feature build + 3 boosters + SHAP + explanation): mean 4.6 ms, p50 4.6 ms, p95 5.1 ms, max 5.6 ms.
Requirement < 500 ms met. The prior is read from `installation["ctLaggedPriorEf"]` or `data/installations_demo.json -> priors.ctLaggedPriorEf`; absent -> the no-prior branch (explanation says so).

## 8. Reproduce

```
PYTHONPATH=. .venv/Scripts/python.exe data/fetch_raw.py        # raw snapshots (needs network)
PYTHONPATH=. .venv/Scripts/python.exe data/build_fixtures.py   # fixtures (byte-identical on re-run)
PYTHONPATH=. .venv/Scripts/python.exe -m model.train           # ~2 min: CV, fit twice (asserts identical bytes), eval.json, hash
PYTHONPATH=. .venv/Scripts/python.exe -m pytest model/tests -q
```
`model/tests/test_scorer.py` also re-fits the artefact in the pytest process and asserts the sha256 equals the committed one (the full `train` pipeline was additionally run twice in separate processes with the same hash).

## 9. Deviations from the PRD (section 8) and open items

* Not implemented: Global Energy Monitor route/capacity (download gated behind a form); Sentinel-2 thermal anomalies (stretch); monthly Ember grid data (annual used); cement; operator-declared electricity per tonne as a feature.
* The Climate TRACE facility estimate is implemented as a lagged direct-emissions-factor prior (FR-M1), not as the monthly CO2 estimate itself, and not as the total SEE (alternative reported in section 4).
* Detection targets with a prior are met overall but not for DRI-EAF; without a prior the 0.5x target (>= 0.9) is missed at 0.842.
* `eval.json` holds the full selection table, ablations, per-route breakdowns, calibration offsets, SHAP seeds, leave-country-out CV and the latency run.

## 10. Demo reports (synthetic, fictional installations)

`data/demo_reports/` holds three reports for the fictional installations (indirect = electricity x grid factor and SEE = (direct + indirect) / production hold to < 0.1 %).
Grid factor 0.476 tCO2/MWh = Ember Turkiye 2025 (475.73 gCO2/kWh) rounded. Both installations carry a lagged prior borrowed from their reference Climate TRACE asset (`data/installations_demo.json`):
TR-EAF-0001 (300,000 t/yr) 0.08 tCO2e/t from asset 1567079; TR-BOF-0002 (3,500,000 t/yr) 1.90 tCO2e/t from asset 1567073.

| File | Installation / period | Inputs | SEE | q05 / q50 / q95 | Score (frozen model) |
| --- | --- | --- | --- | --- | --- |
| `honest_eaf.json` | TR-EAF-0001, 2026-09, 5,000 t | direct 0.24 t/t, electricity 0.62 MWh/t | 0.535 | 0.403 / 0.488 / 0.637 | **20, green** (interval part 0, +20 CBAM rule) |
| `amber_eaf.json` | TR-EAF-0001, 2026-10, 5,000 t | direct 0.17 t/t, electricity 0.45 MWh/t | 0.384 | 0.403 / 0.488 / 0.637 | **43, amber** (23 from the interval + 20 CBAM rule) |
| `implausible_bof.json` | TR-BOF-0002, 2026-09, 250,000 t | direct 0.36 t/t, electricity 0.42 MWh/t | 0.560 (EAF-level for a BF-BOF mill) | 2.196 / 2.270 / 2.326 | **100, red** |

The honest EAF values were set from typical mini-mill figures in round 1 and are unchanged; the new tight interval happens to contain them. The amber report was changed in round 2 (production 4,800 -> 5,000 t, SEE 0.404 -> 0.384)
because the prior narrowed the interval and the old 0.404 now sat exactly on q05; it **was** chosen using the frozen model's interval (about 23 % of the interval width below q05) so that it lands in the amber band.
If the model is retrained these three scores must be re-checked (`model/tests/test_scorer.py` does so). Without the prior the honest and amber EAF reports both score 20 (green).

## 11. History

Round 1 (3 features, no prior, split seed 20261018, hash `0x20b809be...`): MAE 0.0790 vs 0.1705 baseline (53.7 % better), coverage 93.0 %, red recall 0.862 / 0.594 / 0.380 at 0.5x / 0.7x / 0.85x, false red 0.5 %.
The round-2 "without prior" column (new split) is consistent with it (52.9 %, 92.3 %, 0.842 / 0.610 / 0.289).
