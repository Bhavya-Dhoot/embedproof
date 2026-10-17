# Data sources, licences and honesty notes

Retrieved **2026-10-18** (local time; 2026-10-17 22:5x UTC) by `data/fetch_raw.py`. Raw snapshots are in
`data/raw/` (hashes and exact URLs in `data/raw/MANIFEST.json`); derived training/lookup files are in
`data/fixtures/` and are rebuilt by `data/build_fixtures.py`. Nothing in `fixtures/` is synthesised: every number is copied or
arithmetically derived from a raw file. The only synthetic things in this repo are (a) the fictional demo installations and the
three demo reports in `data/demo_reports/`, and (b) the "synthetic underreports" used in `model/artefacts/eval.json`
(declared = factor x Climate TRACE value); both are labelled as such wherever they appear.

## 1. Climate TRACE iron & steel (CC BY 4.0)

| | |
| --- | --- |
| Bulk file used | `https://downloads.climatetrace.org/latest/sector_packages/co2e_100yr/manufacturing.zip` (45,852,775 B, S3 Last-Modified 2026-08-26, sha256 `51dd3003...e3186`); contents are release **v5.10.0 (Aug 2026)** |
| Kept from the zip | `iron-and-steel_emissions_sources_v5_10_0.csv` (gz), `..._sources_confidence_v5_10_0.csv` (gz), `detailed_data_schema_v5_10_0.csv` -> `data/raw/climate_trace/` |
| API checked | `https://api.climatetrace.org/v6` (OpenAPI 6.8.2 at `/v6/swagger/openapi.json`). Kept: `definitions/countries` and a Turkiye sample `assets?subsectors=iron-and-steel&countries=TUR&limit=100&year=2024` (map-tile token field `Thumbnail` stripped) |
| Licence | "All Climate TRACE data is freely available under the Creative Commons Attribution 4.0 International Public License, unless otherwise noted" (Data Licensing, Schema, and Citation Guide v5.10.0, in the zip). Cite: Climate TRACE (2026), *Climate TRACE Emissions Inventory v5.10.0*, https://climatetrace.org; and Crane, V. and Ebri, G. (2025), *Manufacturing and Industrial Processes sector: Iron & Steel Manufacturing Emissions*, TransitionZero, UK, Climate TRACE Emissions Inventory |

What the file contains: 67,122 plant-month rows, **1,017 plants, 82 countries, 66 months (2021-01 .. 2026-06)**, gas `co2e_100yr`.
Per row: `source_type` (route: EAF 32,472 rows, BF/BOF 20,988, DRI-EAF 3,828, plus mixed/other types), capacity (t/month, scaled by
days-in-month), activity, `emissions_factor` (direct), `emissions_quantity`, capacity factor, and `other1..other8`
(`other1` = **direct+indirect emissions factor**, `other3` electricity-use factor MWh/t, `other5` grid intensity t/MWh,
`other6` Climate TRACE model methodology, `other7` scrap share, `other8` IPPU share). A confidence file gives a per-field
rating (emissions factor: 55 % "low", 29 % "medium", 16 % "very low" over all steel rows).

Availability / restrictions, stated plainly:
* **Facility-level, monthly, per-route data are available** for iron & steel in the bulk CSV. The API returns annual figures per asset and caps
  `offset` at 100 (so a full listing needs per-country queries); the monthly series exists only in the bulk files, hence the bulk file is used.
* The guide says activity data are "not available for some subsectors due to licensing restrictions". For iron & steel, activity is populated
  in every row and no iron-and-steel field is flagged as restricted. `geometry_ref` is empty and `other9`/`other10` are `field_not_included`.
* Cross-check: for the 26 Turkish plants present in both the API sample (year 2024) and the bulk CSV, the direct emissions factors agree exactly and capacity agrees within 0.2 % for 25 (one plant's capacity changed within the year).
* `?sectors=steel` on the API is silently ignored; the working filter is `subsectors=iron-and-steel`.
  The data page also advertises a `/v7` API; `https://api.climatetrace.org/v7/...` returned 404 on 2026-10-18.
* **These are modelled estimates, not measurements.** Within a route, the emissions factor is largely a function of route, country, year and
  Climate TRACE's methodology branch (e.g. 256 of the 318 BF/BOF plants have a mean direct EF of exactly 1.9 t/t). Every accuracy number in this repo is agreement with that model.
* Route and capacity come from Climate TRACE's own `source_type` / `capacity`. Global Energy Monitor's Global Iron and Steel Tracker was **not used**:
  its download is gated behind a request form (checked 2026-10-18), so it is not openly downloadable. Whether Climate TRACE's route/capacity
  originate from GEM is not stated in the guide and is unverified here.
* Cement (same package) was not downloaded into the repo; the model is steel-only.

Filters applied (`data/build_fixtures.py`, counts printed by the script): routes limited to `BF/BOF`, `EAF`, `DRI-EAF` (57,288 rows; induction-furnace,
DRI-IF, BOF-only and mixed-route plants have no contract route and are excluded); 3,744 rows with capacity 0 (plant closed in Climate TRACE) and
675 rows with emissions factor exactly 0 (zero-activity months where intensity is undefined) dropped -> **52,869 plant-months, 866 plants, 77 countries**
in `steel_facility_months.csv`. Annual capacity = monthly capacity x days-in-year / days-in-month (verified: gives round nameplates such as 700,000 and 1,300,000 t/yr).

## 2. Ember grid carbon intensity (CC BY 4.0)

| | |
| --- | --- |
| File | `https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/yearly_full_release_long_format.csv` (49,079,981 B, Last-Modified 2026-06-23, sha256 `259e1095...4c3b`) |
| Kept | rows with `Variable == "CO2 intensity"` (unit gCO2/kWh, 5,726 rows incl. regional aggregates) -> `data/raw/ember/ember_yearly_co2_intensity_gco2_per_kwh.csv`; country rows from 2018 on -> `fixtures/grid_intensity_ember_annual.csv` |
| Licence | Ember states "All content is released under a Creative Commons Attribution Licence (CC-BY-4.0)" (ember-energy.org/data/yearly-electricity-data/, checked 2026-10-18). Some third-party republishers label the intensity series CC BY-SA; check Ember's current notice before redistributing. Cite Ember, *Yearly electricity data*. |
| Coverage | 212 countries 2018-2022, 195 in 2024, **only 91 in 2025**; no 2026. Lookup rule (training and scoring): latest value with data year <= the row/report year. Albania has no row (NaN feature). |

The monthly file (`.../monthly_full_release_long_format.csv`, 87 countries, to 2026-06) exists but is not used: the demo period (2026-09) is beyond it and mixing monthly and annual features would create train/serve skew.
Ember's intensity is the national **production-mix** factor (excludes imports); Climate TRACE uses its own, sometimes regional, grid intensities (country-year means, 386 pairs in 81 countries: correlation 0.89, median Climate TRACE / Ember ratio 1.02, p10-p90 0.85-1.19), so this is an independent but not identical grid source.

## 3. CBAM default values (European Commission)

| | |
| --- | --- |
| Document | "Default values definitive period (Excel format)" on the EC page https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism/cbam-definitive-regime_en , file `DVs as adopted_v20260204 .xlsx` (serves workbook `DV correcting act_final update_06.08.xlsx`, 642,541 B, sha256 `90058381...9f35`) -> `data/raw/cbam/cbam_default_values_definitive_period_v2_2026-08-06.xlsx` |
| Legal basis | Annex I of Commission Implementing Regulation (EU) 2025/2621 (adopted 16 Dec 2025), as corrected by (EU) 2026/1740 (adopted 20 July 2026). Workbook "Version History": v1 2026-02-04, **v2 2026-08-06**. The workbook's own disclaimer: informational, **not legally binding**; only the Official Journal text is. It excludes the indirect-emission and electricity defaults (Annexes II, III). |
| Structure | One sheet per exporting country (121) plus `_Other Countries and Territories` and `Annex IV` (highest values). Rows are **per CN code**; columns direct / indirect / total (tCO2eq per t of good) and an "underlying production route determining CBAM BM" letter. |
| What was used | The "Iron and steel" section, per-country sheet: **direct = total** (indirect is `N/A` for steel). One representative CN code per route (**assumption A-CBAM-1**, because defaults are per product and country, not per route): BF-BOF -> `7208`, EAF -> `7214 20 00` (rebar), DRI-EAF -> `7207 11 14`. Result: `fixtures/cbam_defaults_steel.csv` (99 rows; 32 country sheets carry these steel codes) |
| Turkiye (sheet "Türkiye") | 7208 = **2.428**, 7214 20 00 = **2.310**, 7207 11 14 = **2.310** tCO2eq/t, all with route letter (C) |
| Fallback | Countries whose sheet has no steel default (and EU members, which have no sheet) use `_Other Countries and Territories`: 4.049 / 3.996 / 3.996 |

Not verified / caveats: (1) the workbook does not say whether these figures already include the year-dependent mark-up (10 % in 2026, per secondary sources
and the PRD); they are used as published, **no mark-up added**. (2) The route letters (C), (E) are not explained inside the workbook and are not interpreted here
(note: every steel row for Turkiye carries (C), so the EC default for a Turkish EAF mill is an integrated-route-based number, 2.31 vs a Climate TRACE EAF estimate of ~0.5-0.7).
(3) Because EC defaults for steel are direct-only while the report's SEE is direct+indirect, the "SEE < 0.5 x default" rule compares unlike scopes (conservative for the exporter).
The transitional-period (Dec 2023) default-value document was not used.

## 4. Demo installations and reports (synthetic, fictional)

`data/installations_demo.json` holds the grid and capacity priors of the two **fictional** installations (INTERFACES section 2). Each block records the real Climate TRACE asset
id used only as route/capacity-class provenance (TR-EAF-0001: 1567079; TR-BOF-0002: 1567073); these are not model inputs and nothing is stated or implied about those companies.
The demo EAF's 300,000 t/yr is inside the training range (smallest EAF in the training data: 200,000 t/yr).
`data/demo_reports/*.json` are synthetic reports for the fictional installations (see `model/MODEL_CARD.md` for how each number was chosen).
