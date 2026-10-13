# Tata Steel Road Programme Dashboard — Project Documentation

## 1. Overview

A progress-tracking dashboard for the Tata Steel road-works programme (Phase 1 = 17 roads, ~5,065 m). The source of truth for the plan is an Excel workbook in `Source/` (`Final Sheet for Claude Project-Phase-1.xlsx`: a `Phase- Summary` sheet plus one sheet per road). The app loads that plan into PostgreSQL, lets site staff enter weekly actual quantities per activity, and shows computed progress: per-road %, length-weighted overall %, planned vs actual, vendor / line-manager performance, bottlenecks, critical path, milestones and data-quality issues.

The business rules are written down in `docs/01_business_rules.md` (confirmed rules `BR-01..BR-16`, undecided items `OPEN-01..OPEN-06`). Code comments cite these IDs, so that doc is the spec.

> The repo root also holds an **earlier, separate app** (stdlib `server.py` + SQLite `roadworks.db` + single-file `index.html` + `calc.py`) and a legacy Tkinter desktop app (`roadworks.py`). See §8. Everything below describes the current `dashboard/` app unless stated.

## 2. Architecture

```
Source/*.xlsx
   │  scripts/build_data.py   (parse sheets, normalise names via scripts/config.py,
   │                           collect data issues — in memory, no files)
   ▼
scripts/gen_seed.py ──► db/01_seed.sql ──► PostgreSQL schema `tata_road`
   ▲ scripts/verify_seed.py (pre-flight gate)            ▲
   │                                                      │ psycopg2 (dashboard/db.py)
rebuild.sh orchestrates all of the above            dashboard/db_routes.py  (/api/db/*)
                                                     dashboard/calculations.py
                                                              ▲
                                       dashboard/main.py (FastAPI) ── serves ──► dashboard/static/
                                                                          (index.html, dashboard.js, style.css)
```

- **Data tier.** Six tables in schema `tata_road` (`db/00_schema.sql`, idempotent `IF NOT EXISTS`): `road`, `activity`, `weekly_entry`, `data_issue`, `edit_log`, `manual_issue` (hand-logged bottlenecks). `road`/`activity` come from the workbook (or the "Add Road" form, stamped `source_file = 'manual-entry'`); `weekly_entry` is entered through the UI and starts empty on purpose, so every percentage reads 0% until real actuals exist.
- **API tier.** `dashboard/main.py` creates the FastAPI app, includes the `/api/db` router from `dashboard/db_routes.py`, and mounts `dashboard/static/` at `/static` and at `/` (`html=True`) through `NoCacheStaticFiles` (forces revalidation so rebuilt JS/CSS is never stale). There is no JSON/static data tier any more.
- **Calculation tier.** `dashboard/calculations.py` is the only place metrics are computed (road %, overall %, planned %, performance, bottlenecks, CPM, milestones, chart series). The browser renders what the API returns. The one exception is the "Canonical Activity Sequence" panel, which renders fixed `ACTIVITY_SEQUENCE` / `MAJOR_WEIGHTS` constants in `dashboard.js` mirrored from `scripts/config.py`.
- **DB access.** `dashboard/db.py` is plain psycopg2 (no ORM): `get_conn()`, `query()` (→ list of dicts), `execute()`, `execute_returning()`. Each call opens a new connection. `search_path` is set to `tata_road,public` on every connection via a `SET` statement (`_with_search_path`, `SEARCH_PATH_SQL`) because the code uses unqualified table names; a startup `options` parameter was dropped because Neon's pooled PgBouncer endpoint rejects it. The module docstring in `db.py` still refers to the old `SEARCH_PATH_OPTIONS`.

## 3. Directory structure

| Path | Responsibility |
|---|---|
| `dashboard/` | The FastAPI app: `main.py`, `db.py`, `db_routes.py`, `calculations.py`, `static/` (see `dashboard/README.md`) |
| `scripts/` | Excel → seed pipeline and checks: `config.py`, `build_data.py`, `gen_seed.py`, `verify_seed.py`, `diff_dates.py` (see `scripts/README.md`) |
| `db/` | SQL: schema, generated seed, verify, backup, restore (see `db/README.md`) |
| `docs/` | `01_business_rules.md` (rules/spec), `02_build_and_rebuild.md` (build mechanics), `DEPLOY_VERCEL.md` (Vercel + Neon), this file |
| `Source/` | Input workbook(s). Every `.xlsx` here is read and combined (BR-13) |
| `rebuild.sh`, `run.bat`, `requirements.txt` | Incremental build script, Windows launcher, Python deps |
| `api/index.py`, `vercel.json` | Vercel serverless entry (re-exports `dashboard.main.app`) and catch-all rewrite; see `docs/DEPLOY_VERCEL.md` |
| `session-logs/` | Notes on past Claude Code sessions (not part of the app) |
| root `server.py`, `index.html`, `calc.py`, `plan.csv`, `bottlenecks.csv`, `weekly_*.csv`, `roadworks.db*` | Earlier SQLite app (§8) |
| root `roadworks.py`, `build_exe.bat` | Legacy Tkinter desktop app |
| root `clipboard_image_*.jpg`, `_write_test.md`, `_diag_gsb.py`, `__pycache__/` | Scratch files; `_diag_gsb.py` is a throwaway query script for GSB activities |

## 4. Setup & running

Prerequisites: Python 3, a reachable PostgreSQL (database `tata_steel` by default), `psql` on PATH for `rebuild.sh`.

```bash
pip install -r requirements.txt        # fastapi, uvicorn, openpyxl, psycopg2-binary (global; no venv)
./rebuild.sh                           # build/rebuild the DB; does NOT start the server
python3 -m uvicorn dashboard.main:app --reload --host 0.0.0.0 --port 8000
```

`rebuild.sh` is **incremental** (7 steps): stop a running `uvicorn dashboard.main:app` → check Python deps → check static assets exist → (prompt once for the DB password, saved to a gitignored `./.env`, mode 600, unless `PGPASSWORD` or `~/.pgpass` covers it) → `build_data.py` → `gen_seed.py` (emits `INSERT … ON CONFLICT DO NOTHING`) → `verify_seed.py` (exits on failure unless `FORCE=1`) → `db/00_schema.sql` (idempotent, no `DROP`) → `db/01_seed.sql` → `db/02_verify.sql`. Existing rows, weekly actuals, edits and UI-added roads are never touched; the flip side is that a changed workbook value does not update an already-loaded row. Details and the Kerberos/GSSAPI explanation are in `docs/02_build_and_rebuild.md`.

`run.bat` (Windows) re-runs only `scripts/build_data.py` (prints a summary) and then starts uvicorn; it does not touch the database.

There are no tests and no linter.

## 5. Configuration

| Variable / file | Used by | Meaning |
|---|---|---|
| `DATABASE_URL` | `dashboard/db.py`, `rebuild.sh` | Full connection string (deployment, e.g. Render + Neon). Wins over `PG*` |
| `PGHOST` | both | Deliberately **no default**: unset → Unix socket, which skips the GSSAPI failure (`Cannot find KDC for realm "APPLECONNECT.APPLE.COM"`) seen over TCP on this machine |
| `PGPORT` / `PGUSER` / `PGDATABASE` | both | Defaults `5432` / `postgres` / `tata_steel` |
| `PGPASSWORD` | both | Not defaulted; `rebuild.sh` prompts and writes it to `.env` |
| `PGGSSENCMODE` | both | `rebuild.sh` defaults it to `disable`; `db.py` passes it through if set |
| `.env` (repo root, gitignored) | `db.py` `_load_dotenv()`, `rebuild.sh` | `KEY=value` lines; real environment variables win |
| `PORT` | `dashboard/main.py` `__main__`, `rebuild.sh` | Server port (default 8000). `rebuild.sh` only uses it for the printed command / messages |
| `FORCE=1` | `rebuild.sh` | Continue even if `verify_seed.py` fails |
| `RW_PORT`, `RW_DB` | root `server.py` (legacy) | Port / SQLite path for the legacy app |

## 6. Key workflows

### 6.1 Load the plan from Excel
`rebuild.sh` → `scripts/build_data.build_dataset()` reads every `.xlsx` in `Source/`: `read_summary()` takes road-level values from `Phase- Summary` (BR-01), `read_workbook()`/`read_activities()` read each road sheet verbatim (BR-02). Raw activity names are mapped to canonical names by `config.normalize()`; unmapped names are reported, not dropped (they get `seq_no = NULL` and 0 weight). `config.road_weightage()` distributes the 25/10/15/20/30 major weights and rolls an absent major's weight forward (BR-04/05). Text dates in `dd/mm/yyyy` are converted and logged as data issues (BR-11). `gen_seed.py` writes `db/01_seed.sql`; a synthesized drain row (`from_summary_only`) is added when a road's sheet has no RCC Drain row (BR-09).

### 6.2 Enter weekly progress
UI → `POST /api/db/activities/{id}/weekly` (`submit_weekly_entry`) with `week_start_date` (any date; snapped to its Monday by `_monday_of`), `actual_qty` (that week's quantity in the activity's own unit, OPEN-01), optional `planned_qty_override`, `note`. The route computes the hypothetical cumulative total after the write and calls `_validate_gating`, which returns HTTP 409 with a readable message if the activity would run ahead of its predecessor in `seq_no` order, or (backward) leave an activity behind an already-recorded successor (BR-08). Otherwise it upserts on `(activity_id, week_start_date)`. `DELETE /api/db/activities/{id}/weekly/{week_start_date}` applies the same backward check.

### 6.3 How progress is computed
Activity % = `SUM(actual_qty) / planned_qty × 100`. Road % (`road_progress`) = Σ over the road's majors of activity % × `weight_pct` / 100; the drain is tracked separately and excluded (BR-06/09). Overall % (`overall_progress`) = road % weighted by `length_m` (BR-07). Planned % (`road_planned_progress`) uses a straight-line spread between each activity's start and end date (OPEN-02 default; `straight_line_weeks` does the weekly split). Vendor/LM performance, bottlenecks (behind-plan threshold, "holding up next activity", stale-at-zero), `critical_path` and `milestones` build on these.

### 6.4 Edit master data
`PUT /api/db/roads/{road_no}` and `PUT /api/db/activities/{id}` change only the supplied fields via `_apply_update`, write one `edit_log` row per changed field, return 409 if a BR-01/BR-02 source field is touched without `confirm_source_override=true`, and refuse a `weight_pct` change that breaks the road's 100 total. `GET /api/db/edit-log` and `GET /api/db/weight-audit` expose the audit trail.

### 6.5 Add a road without touching Excel
The Roads tab's "Add Road" form calls `POST /api/db/roads` (`create_road`, body `RoadCreate` with a list of `ActivityCreate`). The road is stored with `source_file = 'manual-entry'`; because rebuilds are additive it is never erased by `./rebuild.sh`.

## 7. API reference (`/api/db`)

`GET /roads`, `/roads/{n}`, `/roads/{n}/critical-path`, `/overview`, `/performance/vendor`, `/performance/line-manager`, `/bottlenecks?threshold_pct=15&stale_weeks=3`, `/milestones?window_days=30`, `/charts/activity-status`, `/charts/progress-timeline`, `/data-issues`, `/edit-log`, `/weight-audit`, `/activities/{id}/weekly`; `POST /activities/{id}/weekly`; `PUT /roads/{n}`, `/activities/{id}`; `DELETE /activities/{id}/weekly/{date}`; `POST /roads` (Add Road, with its activities); `GET/POST /manual-bottlenecks`, `PUT/DELETE /manual-bottlenecks/{id}`; `POST /weekly/import-csv` (`{csv: text}`, all-or-nothing, BR-08 per row); `GET /export/weekly-history`, `GET /export/summary` (CSV). `overview`, `performance/*`, `charts/*` and `export/summary` accept `?line_manager=`. Static files: `/` and `/static`.

## 8. Conventions & gotchas

- **Two apps in one repo.** The root SQLite app (`server.py`, endpoints `/api/data`, `/api/progress`, …) is independent of `dashboard/`. Notably it stores **cumulative** quantity per week, whereas `dashboard`'s `weekly_entry.actual_qty` is **per-week**. As of 2026-10-13 `dashboard/` also covers the three features the legacy app had that it lacked — manual bottleneck records, bulk weekly CSV import and CSV export — so the legacy app is a retirement candidate, but it has not been removed.
- **Weeks** run Monday–Sunday and are keyed by Monday (`weekly_entry` has CHECKs for this). Entry may be made on any day.
- **Rebuild is additive only.** No `DROP`, no `ALTER`, no migrations: a new column or constraint in `00_schema.sql` will not reach an existing database, and workbook changes to existing roads/activities are ignored (`ON CONFLICT DO NOTHING`; conflict keys `road(road_no)`, `activity(road_no, raw_name)`). `db/03_backup.sql`/`04_restore.sql` are leftovers from the old destructive rebuild and are no longer called.
- **Road 10 weight defect.** Two rows (`Paver Laying` and `RCC`) map to the `Road` major, so weights total 130 and `verify_seed.py` fails, blocking `rebuild.sh` without `FORCE=1`. Needs a ruling (`docs/02_build_and_rebuild.md` §0).
- **Bare `RCC`** = ROAD major on RCC roads, = RCC Drain on Paver roads (`config.normalize`).
- **Activity names are verbatim** from the sheets, including typos (BR-02).
- **Duplicated gating logic:** `calculations.check_gating` (forward-only, raises `ValueError`) is a weaker near-copy of `db_routes._validate_gating` (both directions, HTTP 409). The route uses the latter.
- **CPM** is the BR-03 sequence as a linear chain; overlaps are reported as planned parallel working (`overlap_flags`). Code comments say overlaps are expected; `docs/01_business_rules.md` BR-14 previously called them data-quality flags, and OPEN-03 in the same file is still listed as unresolved although BR-14 supersedes it.
- **Seed header** has no generation timestamp any more (`gen_seed.py` line commented out), so regenerating from an unchanged workbook should no longer change `db/01_seed.sql` (not re-run to confirm; the committed file still carries its last timestamp line).
- **Stale docstrings:** `dashboard/main.py` and `db_routes.py` refer to `db/rebuild.sh` and a "static dataset.json"; the script is `./rebuild.sh` at the repo root and no JSON tier exists.
- **Unverified:** per `docs/02_build_and_rebuild.md`, the DB tier and rebuild scripts had not been run against a live Postgres when that doc was last updated (2026-10-09). The Neon MCP connection also failed in the session that produced this doc, so nothing here was checked against a live database.
- **Editable-field policy** for BR-01/BR-02 fields (`confirm_source_override`) is a conservative placeholder, not a ruling.

## 9. Open questions / TODOs

- `OPEN-01…OPEN-06` in `docs/01_business_rules.md` (weekly entry unit, planned spread, CPM dependency type, vendor/LM performance measure, bottleneck definition, week definition). The code implements working defaults for each (per-unit entry, straight-line planning, sequence-chain CPM, "all measures" performance, three bottleneck reasons, Mon–Sun weeks), but the doc still lists them as open.
- Road 10 double-`Road`-major weighting (above).
- Known source-data issues for Phase 1 (roads 3, 4, 7, 17 dates) — listed in `docs/01_business_rules.md` §3.
- Deployment on Vercel/Render + Neon via `DATABASE_URL` is described (`docs/DEPLOY_VERCEL.md`) but unexercised.
- No automated tests, linter, or CI.
- Unmapped activity names (`config.KNOWN_UNMAPPED`: Dismantling, Subgrade Preparation, Profile correction, Levelling & Dressing, PCC, …) need a ruling on sequence position and weight.
- Housekeeping candidates: `clipboard_image_*.jpg`, `_write_test.md`, `_diag_gsb.py` in the repo root; `.gitignore` ignores `roadworks.db` and `.env`.
