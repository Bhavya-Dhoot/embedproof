# Model card: EmbedProof plausibility model v1 (steel)

`modelVersionHash` = `0x20b809be2fbaaa91ca00a36a48825647b0259ef6dfd049d04817d1b023f89b3d`
(sha256 of `model/artefacts/embedproof_model_v1.json`; also in `model/artefacts/model_version.txt`). All numbers below are copied from
`model/artefacts/eval.json`, produced by `python -m model.train` for exactly this artefact.

## 1. What it is, and what it is not

A screening model. Given an installation's **route**, **nameplate capacity** and the **national grid carbon intensity**, it predicts the plausible
interval (5th / 50th / 95th percentile) of **specific embedded emissions, SEE** (tCO2e per tonne of steel, direct + indirect) and turns the position of
an operator's declared SEE inside that interval into a 0-100 risk score (formula in `docs/INTERFACES.md` section 7). Red (> 60) sends a batch to a human verifier.

* It does **not measure CO2**. No satellite imagery, plant telemetry or fuel/electricity meter data enters the model. The grid input is a national
  electricity-mix intensity, an **activity proxy for the indirect term**, not a measurement of any plant's emissions. Climate TRACE's own estimates are
  partly built from satellite-derived activity (its `satellite` method covers 28 % of the training rows) and are likewise estimates, not measurements.
* It is **not** accredited CBAM verification and not a replacement for it.
* **The "truth" is Climate TRACE's modelled estimate.** The training target is Climate TRACE v5.10.0's direct+indirect emissions factor (`other1`). For 72 % of
  training rows Climate TRACE rates that emissions factor "low" (62 %) or "very low" (10 %) confidence. Every accuracy figure here is agreement with that model;
  an error that Climate TRACE and this model share cannot be seen by any metric below.

## 2. Model

LightGBM 4.7.0 quantile regression, three boosters (q = 0.05, 0.50, 0.95), `num_leaves = 4`, `min_data_in_leaf = 400`, 500 rounds, learning rate 0.05, bagging 0.8,
`max_bin = 32`; `deterministic = True`, `force_row_wise = True`, 1 thread, seed 7. Chosen by 5-fold plant-grouped CV on the training plants only (12 configurations,
`eval.json -> cv_selection_train_plants_only`).

Post-processing: (1) the three predictions are sorted per row (monotone rearrangement) so q05 <= q50 <= q95 always; (2) one-sided split-conformal offsets
fitted on 139 calibration plants widen the tails (EAF upper tail +0.086, BF-BOF +0.010, pooled +0.040; lower offsets are about 0.00-0.01): q05' = max(0, min(q05 - lower, q50)),
q95' = max(q95 + upper, q50). The published `q05`/`q50`/`q95` (= `expectedLow`/`expectedHigh`) are these calibrated values rounded to 4 decimals, so the risk score can be recomputed from the published numbers.
Explanations: `shap.TreeExplainer` on the q50 booster; top three contributions by magnitude.

## 3. Training data and dates

| Source | Version / date | Licence | Use |
| --- | --- | --- | --- |
| Climate TRACE iron-and-steel plant-month CSV | v5.10.0 (Aug 2026), retrieved 2026-10-18 | CC BY 4.0 | target, route, capacity |
| Ember yearly electricity data, CO2 intensity | release of 2026-06-23, retrieved 2026-10-18 | CC BY 4.0 | grid feature |
| EC CBAM default values workbook | v2 dated 2026-08-06 (Reg. (EU) 2025/2621 as corrected by 2026/1740) | EU, informational | reference only, not a feature |

Details, exact URLs, hashes and the list of what was not available are in `data/SOURCES.md`. The training snapshot hash is recorded in the artefact
(`training_data.snapshot_sha256` = sha256 of `data/fixtures/steel_facility_months.csv`).

Fixture: **52,869 plant-months, 866 plants, 77 countries, 2021-01 .. 2026-06** (BF-BOF 316 plants, EAF 492, DRI-EAF 58). Dropped: other CT route types (induction furnace, DRI-IF,
BOF-only, mixed), 3,744 plant-months with capacity 0, 675 with emissions factor exactly 0 (zero-activity months). Split by plant, stratified by route, seed 20261018:
train 554 plants / 8,392-row calibration set from 139 plants / **test 173 plants (10,699 plant-months)**. No plant appears in more than one split.

## 4. Features and target leakage

Inputs (3): `route_code` (BF-BOF / EAF / DRI-EAF), `capacity_tpy` (Climate TRACE annual nameplate), `grid_gco2_kwh` (Ember annual national CO2 intensity, last observation carried forward
because Ember's 2025 file covers only 91 countries and has no 2026).

Deliberately **not** used, because the target is Climate TRACE's own emissions factor:
* Climate TRACE emissions, activity, direct emissions factor, electricity-use factor, grid intensity, scrap share, methodology: these are inputs to or outputs of the target's own calculation (`emissions_quantity = activity x EF`).
  This also means the PRD feature "facility-level monthly CO2 estimate from Climate TRACE, used as a prior" is **not implemented**: with Climate TRACE as ground truth it would be predicting the answer from itself.
  In production, where the truth is an operator's claim, that estimate is a legitimate independent prior; it simply cannot be evaluated honestly with this ground truth.
* Operator-declared electricity per tonne (PRD feature): in Climate TRACE it is essentially a per-route constant (99.5 % of EAF rows are exactly 0.87 MWh/t, 99.7 % of BF-BOF rows 0.83, all DRI-EAF rows 0.87), so it carries no plant information and would only let the model reconstruct the indirect term. (It is also above typical real EAF electricity use; Climate TRACE's indirect term is itself a coarse estimate.)
* Climate TRACE capacity factor: excluded a priori as a proxy for which Climate TRACE methodology branch produced the row. Ablation (below) shows it adds about 1.5 % MAE improvement even with larger trees once zero-activity months are removed.
* Country as a categorical: made held-out coverage worse in CV (below), and fails on unseen countries.

Feature ablation, 5-fold plant-grouped CV on the 554 training plants (shipped hyper-parameters; `eval.json -> feature_ablation_*`):

| Feature set | CV MAE of q50 | CV pinball | raw 90 % coverage |
| --- | --- | --- | --- |
| route only | 0.1722 | 0.04587 | 89.4 % |
| route + grid | **0.0895** | **0.02533** | 89.1 % |
| route + capacity | 0.1770 | 0.04877 | 84.9 % |
| **route + capacity + grid (shipped)** | 0.0909 | 0.02595 | 87.0 % |
| + country categorical | 0.0880 | 0.02751 | 83.0 % |
| + Climate TRACE capacity factor (excluded) | 0.0909 | 0.02592 | 86.1 % |

Honest reading: **the grid feature does almost all the work** (it mirrors the indirect-emissions term of the target) next to route; **capacity carries no measurable signal** (mean |SHAP| 0.007 against 0.734 for route
and 0.166 for grid). Capacity is kept because it is part of the installation contract and gives the SHAP list its third entry; the cost is about 1.5 % CV MAE versus the route + grid model.

## 5. Evaluation on the 173 held-out plants

Protocol: the test plants were not used for hyper-parameter choice, feature choice or calibration. Baseline = median SEE of the training rows of the same route
(BF-BOF 2.401, DRI-EAF 1.486, EAF 0.634). Confidence intervals resample **plants** (2,000 bootstrap draws), because the 66 months of one plant are strongly correlated.

| Check | Result | Target | Met |
| --- | --- | --- | --- |
| MAE of q50 vs Climate TRACE | **0.0790** tCO2/t (95 % CI 0.060-0.103); baseline 0.1705 | beat baseline by >= 20 % | **yes: 53.7 % better** (CI 45.2-61.6 %; 52.9 % with equal weight per plant) |
| 90 % interval coverage | **93.0 %** (CI 90.5-95.2 %); 88.8 % before conformal calibration; mean width 0.434 | 85-95 % | yes (upper CI edge 95.2 %) |
| Recall at red (> 60), synthetic 0.5x underreport | **86.2 %** (CI 81.1-90.7 %) | >= 0.90 | **no** |
| Recall at red, synthetic 0.7x | **59.4 %** (CI 53.1-66.0 %) | >= 0.60 | **no (0.6 pp short)** |
| Recall at red, synthetic 0.85x | 38.0 % (CI 31.0-45.2 %) | none set | n/a |
| False alert on unmodified held-out values (1.0x, not synthetic) | red 0.5 % (CI 0.1-1.0 %); amber-or-red 1.6 % | none set | n/a |
| Top-3 SHAP stable across 5 seeds | rank order identical in 5/5 seeds; mean abs SHAP 0.734 / 0.166 / 0.007 (route / grid / capacity) | qualitative | yes, but see note |

Note on SHAP: with three features the top-3 *set* is trivially the same; the informative result is that the *rank order* and the magnitudes did not move across seeds.

Detection detail. "Synthetic" = declared SEE set to factor x the Climate TRACE value of the same plant-month (labelled synthetic in `eval.json`); it tests the score's sensitivity, not real misreporting.

| Red rate (full shipped rule incl. +20) | BF-BOF (63 plants) | EAF (98) | DRI-EAF (12) | all rows |
| --- | --- | --- | --- | --- |
| 0.5x | 98.9 % | 85.1 % | 31.6 % | 86.2 % |
| 0.7x | 98.9 % | 42.2 % | 3.4 % | 59.4 % |
| 0.85x | 98.9 % | 5.7 % | 0.0 % | 38.0 % |
| 1.0x (honest) | 1.0 % | 0.2 % | 0.0 % | 0.5 % |

Without the +20 CBAM rule the all-row red rates are 80.5 % / 52.2 % / 36.1 % at 0.5x / 0.7x / 0.85x. The targets are met for BF-BOF (tight, well-determined interval: width 0.11) and missed for EAF and especially DRI-EAF,
whose Climate TRACE emissions factors vary a lot between plants that look identical to this model (EAF interval width 0.54, DRI-EAF 1.21). The model cannot do better with these inputs; a plant-specific prior would be needed (section 4).

Accuracy by route (test): BF-BOF MAE 0.028 vs 0.062 baseline, coverage 90.8 %; EAF 0.090 vs 0.212, 94.2 %; DRI-EAF (12 plants only) 0.244 vs 0.372, 94.9 %.
By Climate TRACE method: satellite 90.5 % coverage / MAE 0.046; national extrapolation 95.8 % / 0.081; plant extrapolation 93.2 % / 0.044; **global extrapolation 85.0 % / 0.163** (worst).

**The +20 rule is not discriminating for steel.** EC defaults are 2.3-4.0 tCO2/t, so SEE < 0.5 x default is true for 60.6 % of *honest* held-out rows (97 % of the 0.5x claims) and for essentially every EAF plant.
It lifts every EAF mill's score by 20 points (the honest demo EAF scores 20, green) without distinguishing honest from dishonest claims. Also, the EC steel default is direct-only while SEE is direct+indirect.

## 6. Known failure modes

1. **New plant with no Climate TRACE history / new country.** The model uses no plant history, so it still produces an interval, but it then reflects only route + grid + capacity. Leave-country-out CV on the training plants:
   MAE 0.130 vs 0.235 baseline (44 % better) but raw coverage drops to **78.4 %** (vs 88.8 % raw on the held-out test plants, which share countries with the training plants). Intervals for unseen countries are too narrow.
2. **Plants near grid boundaries / sub-national grids / captive or contracted power.** Ember's factor is national production-mix (imports excluded). Climate TRACE itself uses regional grid intensities for some countries
   (standard deviation across the plants of one country-month up to 0.12 t/MWh), so a plant whose real supply differs from the national mix is mis-predicted in the indirect term.
3. **Capacity outside the training range** (EAF 200,000-6,022,000 t/yr; BF-BOF 380,000-22,950,000; DRI-EAF 200,000-7,400,000). Trees extrapolate flat outside it and the explanation says so; the demo EAF (300,000 t/yr) is inside it.
4. **DRI-EAF**: 37 training plants, interval width 1.2, detection near zero. Treat its scores as weak.
5. **Missing grid data**: Albania (44 plant-months) has no Ember row and is trained with a missing grid value; unknown countries score with a missing grid input and the "other countries" CBAM default.
6. **Time**: Climate TRACE data end 2026-06 and Ember's 2025 file covers 91 countries; scoring a 2026-09 report extrapolates in time (grid value carried forward).
7. **Gaming**: the score is a screen. A claim placed just above the calibrated q05 scores 0 (plus 20). Cross-installation and cross-period consistency checks are not in this model.
8. **Excluded routes**: induction furnace, DRI-IF, BOF-only and mixed-route plants are not modelled; cement raises `ValueError`.
9. **One split, one seed.** Hyper-parameters and features were chosen on training-plant CV, and the test plants were scored once, but only one plant split (seed 20261018) was run; variability across splits is represented only by the plant bootstrap.

## 7. Inference

`Scorer.load()` about 0.8 s (plus about 14 s cold-start import of shap/numba on this machine); first call about 65 ms; warm calls (300 calls, includes feature build, 3 boosters, SHAP and explanation):
mean 8.2 ms, p50 7.5 ms, p95 12.5 ms, max 29 ms. Requirement < 500 ms met.

## 8. Reproduce

```
PYTHONPATH=. .venv/Scripts/python.exe data/fetch_raw.py        # raw snapshots (needs network)
PYTHONPATH=. .venv/Scripts/python.exe data/build_fixtures.py   # fixtures (byte-identical on re-run)
PYTHONPATH=. .venv/Scripts/python.exe -m model.train           # ~4 min: CV, fit twice (asserts identical bytes), eval.json, hash
PYTHONPATH=. .venv/Scripts/python.exe -m pytest model/tests -q
```
`model/tests/test_scorer.py` also re-fits the artefact in a fresh process and asserts the sha256 is identical to the committed one.

## 9. Deviations from the PRD (section 8) and open items

* Not implemented: Climate TRACE facility estimate as a feature (leakage, section 4); operator electricity per tonne as a feature; Global Energy Monitor route/capacity (download is gated behind a form); Sentinel-2 thermal anomalies (stretch); monthly Ember grid data (annual used); cement.
* Evaluation targets not met: detection recall 0.862 (< 0.9) at 0.5x and 0.594 (< 0.6) at 0.7x.
* `eval.json` contains the full selection table, the ablations, per-route and per-method breakdowns, the SHAP seeds and the latency run.

## 10. Demo reports (synthetic, fictional installations)

`data/demo_reports/` holds three reports for the fictional installations (arithmetic consistent to < 0.1 %: indirect = electricity x grid factor, SEE = (direct + indirect) / production).
Grid factor 0.476 tCO2/MWh = Ember Turkiye 2025 (475.73 gCO2/kWh) rounded.

| File | Installation / period | Inputs | SEE | Score (frozen model) |
| --- | --- | --- | --- | --- |
| `honest_eaf.json` | TR-EAF-0001, 2026-09, 5,000 t | direct 0.24 t/t, electricity 0.62 MWh/t | 0.535 | **20, green** (interval part 0, +20 CBAM rule) |
| `amber_eaf.json` | TR-EAF-0001, 2026-10, 4,800 t | direct 0.19 t/t, electricity 0.45 MWh/t | 0.404 | **40, amber** (20 from the interval + 20 CBAM rule) |
| `implausible_bof.json` | TR-BOF-0002, 2026-09, 250,000 t | direct 0.36 t/t, electricity 0.42 MWh/t | 0.560 (EAF-level for a BF-BOF mill) | **100, red** (q05 2.171, q50 2.275, CBAM default 2.428) |

The honest EAF values were set from typical mini-mill figures and then checked to sit inside Climate TRACE's range for Turkish EAFs (0.49-0.90); they were not tuned to the model. The amber claim **was** chosen using the frozen model's interval
(about 20 % of the interval width below q05 = 0.432) so that it lands in the amber band. If the model is retrained, these three scores must be re-checked (`model/tests/test_scorer.py` does so).
