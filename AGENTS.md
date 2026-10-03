# Repository Guidelines

## Project Overview
Power BI (PBIP) report + semantic model that attributes BigQuery on-demand spend to queries and dbt models/snapshots/tests. Runs in two modes via one parameter: committed synthetic CSVs (`sample-data/`) or live BigQuery (`INFORMATION_SCHEMA.JOBS_BY_PROJECT` + `dbt_artifacts` tables). Python scripts only generate sample data and README visuals; there is no app runtime.

## Architecture & Data Flow
```
BigQuery INFORMATION_SCHEMA jobs ─┐                       ┌─ Query Insights pages
dbt_artifacts tables             ─┼─> Power Query (M) ─> TMDL model (DAX) ─> Report (Deneb + HTML Content visuals)
sample-data/*.csv (CSV mode)     ─┘   switch: p_DataSource └─ dbt Jobs pages
```
- **Mode switch**: M parameter `p_DataSource` (SampleCSV vs BigQuery). Related params: `p_SampleDataPath`, `bq_project`, `p_Regions`, `p_DbtMeta_*`, `RangeStart/RangeEnd`. Every source table must keep both branches working.
- **Star-ish model**: `Fact` (grain = `job_id`, parent SCRIPT jobs AND child statements both bill) joins to `dim_Date` (creation_date), `#Data Processed Category` (DataSizeCategoryID), `#Day Usage` (creation_hour_24), `dim_Region_Cost` (region, `usd_per_tib`, one `is_default` row).
- **dbt layer**: `Fact` <-> `dbt_node_executions` (job_id, both-direction filter) -> `dbt_invocations` (command_invocation_id); `dbt_node_executions` <-> `dbt_models` (node_id) <-> `dbt_model_tags` (node_id). One inactive rel: `dbt_node_executions` -> `dim_Date` (run_date).
- **Fact rules** (`Fact.tmdl`): excludes INFORMATION_SCHEMA queries, non-DONE jobs, reserved jobs (on-demand only); strips dbt query comments; normalizes destination (`anon*` -> "(anonymous SELECT result)", drops `__dbt_tmp`); SCRIPT parents inherit destination from largest child; 10 MB billing floor, cache hits free. Incremental refresh: 5-month window, 2-day granularity.
- **Report**: 7 pages (Query & Usage Insights: Performance Analysis/Explore; dbt on BigQuery: Overview/Explore/Nodes Failed; drillthroughs incl. Query Pattern). Custom visuals: Deneb, HTML Content.

## Key Directories
- `powerbi/<name>.SemanticModel/definition/` – `model.tmdl`, `database.tmdl` (compat 1702), `relationships.tmdl`, `tables/*.tmdl`, expressions/parameters.
- `powerbi/<name>.Report/definition/` – `report.json`, pages, visuals (PBIR JSON).
- `sample-data/` – 6 generated CSVs + `summary.json` (June 2026, seed 20260701). Do not hand-edit.
- `scripts/` – Python generators.
- `assets/` – generated SVGs (`*-light.svg`/`*-dark.svg` pairs) and 2 GIF tours; `screenshots/` – cropped PNGs.

## Development Commands
```bash
python scripts/generate_sample_data.py [--out sample-data] [--seed 20260701]   # stdlib only; asserts invariants
python scripts/build_assets.py                                                # stdlib only; reads sample-data/summary.json
python scripts/build_page_assets.py                                           # needs Pillow + NumPy; reads screenshots/_raw/
```
No build/lint/test tooling. Open the report: `powerbi/BigQuery dbt Cost Observability.pbip` in Power BI Desktop (PBIP/TMDL + PBIR preview features enabled).
Run `generate_sample_data.py` twice and diff: output must be byte-identical. Re-run `build_assets.py` after changing sample data (figures come from `summary.json`).

## Code Conventions & Common Patterns
- **TMDL**: tab-indented, one object per file under `tables/`; keep `lineageTag`s intact; never rename a table/column without updating `relationships.tmdl`, measures, and report visual bindings.
- **Naming**: dimensions `dim_*`; helper/bucket tables prefixed `#` (`#Data Processed Category`, `#Day Usage`); dbt tables `dbt_*`; params `p_*`; snake_case columns.
- **Measures**: cost/execution/health measures are DAX-heavy; cost = bytes billed x regional `usd_per_tib` from `dim_Region_Cost`, falling back to the `is_default` row. Avoid double counting SCRIPT parent + children.
- **Python**: stdlib-first, argparse CLI, deterministic `random.Random(seed)`, `pathlib` paths. Generator ends with asserts (unique job_ids, no orphaned executions, full dbt metadata coverage) – extend them when adding fields.
- **SVG assets**: `__TOKEN__` templates filled from `LIGHT`/`DARK` palettes in `build_assets.py`; system fonts only; CSS animations honor `prefers-reduced-motion`. Always produce light+dark pairs.

## Important Files
- `powerbi/BigQuery dbt Cost Observability.pbip` – entry point.
- `.../definition/tables/Fact.tmdl` – core fact + BigQuery SQL / CSV M query.
- `.../definition/relationships.tmdl`, `model.tmdl` – model wiring and parameters.
- `scripts/generate_sample_data.py` – source of truth for sample CSV schemas.
- `README.md` – user-facing setup (prereqs, deployment modes); update with schema/page changes.

## Runtime/Tooling Preferences
- Power BI Desktop (Windows) is required to open/edit the model; Python 3 for scripts. No package manager or lockfile; only `build_page_assets.py` needs `pip install pillow numpy`.
- `.gitignore` excludes `.pbi/cache.abf`, `.pbi/localSettings.json`, `*.pbix`, `~$*`, `render/`, `__pycache__/`, `screenshots/_raw/`. Never commit these.
- `.omp/config.yml` holds local agent model settings only.

## Testing & QA
- No CI or automated test suite. QA = generator asserts + manual checks in Power BI Desktop: refresh in SampleCSV mode, confirm totals match `sample-data/summary.json` (4,894 jobs, 92 invocations, 2,719 node executions, $662.98 total, $470.60 dbt / $192.38 non-dbt).
- After model/report edits: open the `.pbip`, refresh, check every page renders and the relationships are unchanged. Regenerate screenshots via `build_page_assets.py` only when visuals change.
