# scripts/

Excel → seed-SQL pipeline and its checks. Driven by `../rebuild.sh`; see [`docs/02_build_and_rebuild.md`](../docs/02_build_and_rebuild.md).

| File | Role |
|---|---|
| `config.py` | Canonical `ACTIVITY_SEQUENCE` (BR-03), `MAJOR_WEIGHTS`/`MAJOR_ORDER` (BR-04), `NAME_MAP`, `KNOWN_UNMAPPED`, `normalize()` (bare `RCC` depends on road type), `road_weightage()` (BR-05 roll-forward) |
| `build_data.py` | `build_dataset()` parses every `.xlsx` in `../Source/` in memory; reports data issues and unmapped names. Run standalone for a summary only (writes nothing) |
| `gen_seed.py` | Calls `build_dataset()` and writes `../db/01_seed.sql` as `INSERT … ON CONFLICT DO NOTHING`. Run from inside `scripts/` (`cd scripts && python3 gen_seed.py`) |
| `verify_seed.py` | Pre-flight check of the dataset and generated SQL (weight totals, major classification, DDL constraints). Exit 1 on failure; `docs/02_build_and_rebuild.md` records it failing on road 10's weight total (not re-run for this doc) |
| `diff_dates.py` | One-off read-only report comparing `Phase- Summary` road dates with each road sheet's first start / last end |
| `__init__.py` | Marks the folder as a package |

Nothing here is imported by the server; `dashboard/` only duplicates the sequence/weights as display constants in `static/dashboard.js`.
