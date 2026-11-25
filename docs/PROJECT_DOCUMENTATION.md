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

- **Data tier.** Eight tables in schema `tata_road` (`db/00_schema.sql`, idempotent `IF NOT EXISTS`): `road`, `activity`, `weekly_entry`, `data_issue`, `edit_log`, `manual_issue` (hand-logged bottlenecks), `road_issue` (hindrance windows) and `road_issue_shift` (what each window moved). A live-refresh feed (`db/05_live_change.sql`, `dashboard/live_feed.py`) is layered on top. `road`/`activity` come from the workbook (or the "Add Road" form, stamped `source_file = 'manual-entry'`); `weekly_entry` is an append-only ledger entered through the UI and starts empty on purpose, so every percentage reads 0% until real actuals exist.
- **API tier.** `dashboard/main.py` creates the FastAPI app, includes the `/api/db` router from `dashboard/db_routes.py`, and mounts `dashboard/static/` at `/static` and at `/` (`html=True`) through `NoCacheStaticFiles` (forces revalidation so rebuilt JS/CSS is never stale). There is no JSON/static data tier any more.
- **Calculation tier.** `dashboard/calculations.py` is the only place metrics are computed (road %, overall %, planned %, performance, bottlenecks, CPM, milestones, chart series). The browser renders what the API returns. The one exception is the "Canonical Activity Sequence" panel, which renders fixed `ACTIVITY_SEQUENCE` / `MAJOR_WEIGHTS` constants in `dashboard.js` mirrored from `scripts/config.py`.
- **DB access.** `dashboard/db.py` is plain psycopg2 (no ORM): `get_conn()`, `query()` (→ list of dicts), `execute()`, `execute_returning()`. `get_conn()` is a context manager that borrows a connection from a lazily-created `psycopg2.pool.ThreadedConnectionPool` (`_POOL_MINCONN=1`, `_POOL_MAXCONN=8`, created on first use by `_get_pool()`) and always returns it in `finally`; it does not commit — `execute()`/`execute_returning()` commit explicitly. The pool exists because opening a fresh Neon connection per query (TCP + TLS + `SET`) made page loads slow. `search_path` is set to `tata_road,public` on every connection via a `SET` statement (`_with_search_path`, `SEARCH_PATH_SQL`) because the code uses unqualified table names; a startup `options` parameter was dropped because Neon's pooled PgBouncer endpoint rejects it. The module docstring in `db.py` still refers to the old `SEARCH_PATH_OPTIONS`.

## 3. Directory structure

| Path | Responsibility |
|---|---|
| `dashboard/` | The FastAPI app: `main.py`, `db.py`, `db_routes.py`, `calculations.py`, `static/` (see `dashboard/README.md`) |
| `scripts/` | Excel → seed pipeline and checks: `config.py`, `build_data.py`, `gen_seed.py`, `verify_seed.py`, `diff_dates.py` (see `scripts/README.md`) |
| `db/` | SQL: schema, generated seed, verify, backup, restore (see `db/README.md`) |
| `docs/` | `01_business_rules.md` (rules/spec), `02_build_and_rebuild.md` (build mechanics), `DEPLOY_VERCEL.md` (Vercel + Neon), `DEPLOY_RENDER.md` (Render + Neon), this file |
| `Source/` | Input workbook(s). Every `.xlsx` here is read and combined (BR-13) |
| `rebuild.sh`, `run.bat`, `requirements.txt` | Incremental build script, Windows launcher, Python deps |
| `02_build.sh`, `03_validate.sh` | `02_build.sh` is a thin wrapper that `exec`s `rebuild.sh`; `03_validate.sh` is a read-only DB health check (pass/warn/fail, exit 1 on any FAIL) |
| `render.yaml`, `render_build.sh` | Render Blueprint (free web service, Singapore, Python 3.12.7, health check `/`) and its build script: installs deps, then existence-gated schema/seed bootstrap of the `DATABASE_URL` database; see `docs/DEPLOY_RENDER.md` |
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

`./03_validate.sh` afterwards (or against Neon with `DATABASE_URL=… ./03_validate.sh`) reports tables, row counts (17 / 177 / 10 at a clean seed, WARN otherwise), orphan rows, non-Monday weeks and BR-05 weight totals; road 10 = 130 is reported as a known WARN. On a self-managed Postgres where the `tata_steel` database does not exist yet, run `PGGSSENCMODE=disable psql -U postgres -d postgres -f db/create_database.sql` once first (not part of `rebuild.sh`).

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
UI -> `POST /api/db/activities/{id}/weekly` (`submit_weekly_entry`, `db_routes.py`) with `week_start_date` (any date; snapped to its Monday by `_monday_of`), `actual_qty` (this submission's quantity in the activity's own unit, a delta, OPEN-01), optional `planned_qty_override`, `note`. Since the 2026-10-13 rulings the table is an **append-only ledger**: each call is a plain `INSERT` that adds to the cumulative (no upsert, no `UNIQUE (activity_id, week_start_date)`), and there is **no BR-08 gating** - any activity may be entered in any order (`_validate_gating` is a no-op stub, `calculations.check_gating` was deleted). `GET /activities/{id}/weekly` (`activity_weekly`) lists every row in order (week, `entered_at`, `entry_id`) with a running `cumulative_actual`/`cumulative_pct` (capped at 100) and an `over_planned` flag. `PUT /api/db/activities/{id}/weekly/{entry_id}` (`edit_weekly_entry`, body `{actual_qty, note}`) edits a single row in place (keeps its week and `entered_at`; the cumulative follows via `SUM`; negative qty is rejected with 422, unknown entry 404) and logs each changed field to `edit_log` under table `activity` as `weekly_entry[<id>].actual_qty` / `.note` (the `edit_log.table_name` CHECK only allows road/activity). The weekly history rows in the UI have Edit (inline inputs, Save/Cancel) and Delete buttons. `DELETE /api/db/activities/{id}/weekly/{entry_id}` removes a single row. The bulk path `POST /weekly/import-csv` (`import_weekly_csv`) validates every `activity_id` exists, then inserts all rows in one transaction (all-or-nothing); rows append, they do not overwrite.

### 6.3 How progress is computed
Activity % = `SUM(actual_qty) / planned_qty × 100`, capped at 100 for display. `road_progress` returns `(road_pct, drain_pct, over_planned_count)`; an activity whose actual exceeds `planned_qty` counts as 100% and increments `over_planned_count` (surfaced as `over_planned_count` on `/roads` and `/roads/{n}`, `over_planned` per activity, and warning pills in `dashboard.js`: `roadOverPlannedPill`, `overPlannedWarning`) so the user raises the planned qty. Road % (`road_progress`) = Σ over the road's majors of activity % × `weight_pct` / 100; the drain is tracked separately and excluded (BR-06/09). Overall % (`overall_progress`) = road % weighted by `length_m` (BR-07). Planned % (`road_planned_progress`) uses a straight-line spread between each activity's start and end date (OPEN-02 default; `straight_line_weeks` does the weekly split). Vendor/LM performance, bottlenecks (behind-plan threshold, "holding up next activity", stale-at-zero), `critical_path` and `milestones` build on these.

### 6.4 Edit master data
`PUT /api/db/roads/{road_no}` and `PUT /api/db/activities/{id}` change only the supplied fields via `_apply_update`, write one `edit_log` row per changed field, and refuse a `weight_pct` change that breaks the road's 100 total. `GET /api/db/edit-log` and `GET /api/db/weight-audit` expose the audit trail.

### 6.5 Add a road
The Roads tab's "Add Road" form calls `POST /api/db/roads` (`create_road`, body `RoadCreate` with a list of `ActivityCreate`). The road is stored with `source_file = 'manual-entry'`; because rebuilds are additive it is never erased by `./rebuild.sh`.

**Add Road form rules** (Weight % is locked at 0 until a Major is chosen - in Add Road and in Edit Activity - and the API rejects a non-zero weight on an activity with no Major, because only the five majors carry weight, BR-04/06) (`dashboard.js` `validateAddRoad`, mirrored server-side in `create_road`): Road No is pre-filled with the next free number but editable, and is checked live against existing roads; Line Manager, Vendor and Road Type are dropdowns of existing values that always start blank (never auto-selected) with an "Add new..." option that rejects duplicates; Phase is the same kind of dropdown (required) with "+ Add new phase" (next number suggested, existing numbers rejected) - a new phase becomes a new group on the Roads tab; Road No and Location are required; blank Length / Drain are stored as 0; every activity needs a name and a planned quantity > 0 (untouched rows are ignored) and weights must total 100. The **Edit Road** form uses the same dropdowns (the road's current Vendor, Line Manager, Road Type and Phase pre-selected, each with an "Add new..." option and the same duplicate / phase-number checks) on the same even grid; both forms share `comboFieldHtml` / `comboCheck` / `comboValue`. API errors are shown as plain sentences (`apiErrorMessages`) instead of the raw validation output.

### 6.6 Dashboard page load
`dashboard.js` `init()` loads its five panels concurrently (`Promise.all`: `refreshLiveRoads`, `renderIssues`, `renderWeightAudit`, `renderNavBadges`, `renderEditLog`), and `refreshLiveRoads()` fetches `/roads`, `/overview` and `/charts/activity-status` in parallel (the last may fail on its own; the overall panel then renders without it). Combined with the connection pool this removes the serial round-trips that made the first load slow.

### 6.7 Vendor / LM performance, backlog and targets
`calculations.group_plan()` computes, per vendor or line manager, BR-06-weighted actual / planned / backlog / next-planned / target %, and (with `detail=True`) a road-wise breakdown with per-activity quantities. Per activity: `backlog = max(0, planned(as_of) − actual)`, `target = max(0, planned(to) − actual)`, `next planned = target − backlog` — so a target always accounts for actual progress. Quantities are summed only within one unit. The **Vendor / LM Performance** tab has Vendor, Line Manager (these two narrow each other's options), Status and Backlog-month filters (months up to the current one only; the current month is measured as at today); the **Targets** tab adds a From/To window (default today → +30 days; past dates are rejected; 7 / 14 / 30-day quick buttons start tomorrow). The Performance and Targets summary rows are selectable (click / Enter): each expands into a road-wise breakdown with per-activity planned vs actual quantity (Performance) or backlog / next-planned / target quantity (Targets); the Performance table shows planned and actual quantity per unit and no longer has an average-slippage column. Each group row in the Targets table and the Backlog table has a **Print** button that prints just that vendor / line manager (summary plus road-wise details) via a temporary `#printSheet` (`body.printing-single`); backlog rows are also selectable and expand to the roads (and activities) that carry a backlog, from `GET /performance/backlog?...&detail=true`, which the backlog print reuses. The existing Print / Export button still prints the whole tab. Any filter dropdown that has exactly one real option selects it automatically (`autoSelectSingle` in `dashboard.js`). Endpoints: `/performance/vendor|line-manager`, `/performance/months`, `/performance/backlog`, `/performance/target` (all accept `vendor` / `line_manager`).

### 6.8 Road issues (hindrance windows)
Per-road **Issue** button → `POST /roads/{n}/issues` (From/To, reason). Unfinished activities and the road's dates move later by the window length (rules in `db_routes.create_road_issue`); `road_issue_shift` records what moved so `PUT`/`DELETE /roads/{n}/issues/{id}` can reverse it. Closed issues (To date passed) can no longer be edited or deleted. Every date change is logged in `edit_log`.

### 6.9 Live refresh
`db/05_live_change.sql` triggers bump one marker per watched table; the browser polls `GET /live/changes` (`static/live-refresh.js`, wired in `dashboard.js` `LIVE_TABLE_TABS`) and reloads only the affected tabs. A "New updates available" banner replaces a silent reload only when the user has unsaved typing. See `CLAUDE.md`.

### 6.10 Phase groups (Roads tab)
`GET /roads` now returns each road's `phase`. The Roads tab (`renderLiveRoadsTable`) groups roads by phase into collapsible sections (`phaseSection`): a header with the phase name, road count and length-weighted % complete, and the roads table inside. Sections are built from the distinct `phase` values in the data, so a new phase (Add Road with a new Phase number, or editing a road's Phase — now in the Edit Road form) appears automatically with the same collapse behaviour, filters and live refresh. Collapsed phases are remembered in `localStorage` (`tata_phase_collapsed`); a road opened programmatically (e.g. from Weight Audit) re-opens its phase. The top banner and page title no longer say "Phase 1".

## 7. API reference (`/api/db`)

`GET /roads`, `/roads/{n}`, `/roads/{n}/critical-path`, `/overview`, `/performance/vendor`, `/performance/line-manager`, `/bottlenecks?threshold_pct=15&stale_weeks=3`, `/milestones?window_days=30`, `/charts/activity-status`, `/charts/progress-timeline`, `/data-issues`, `/edit-log`, `/weight-audit`, `/activities/{id}/weekly`; `POST /activities/{id}/weekly`; `PUT /roads/{n}`, `/activities/{id}`; `PUT /activities/{id}/weekly/{entry_id}`; `DELETE /activities/{id}/weekly/{entry_id}`; `POST /roads` (Add Road, with its activities); `GET/POST /roads/{n}/issues`, `PUT/DELETE /roads/{n}/issues/{id}`; `GET /performance/months|backlog|target`; `GET /live/changes`; `GET/POST /manual-bottlenecks`, `PUT/DELETE /manual-bottlenecks/{id}`; `POST /weekly/import-csv` (`{csv: text}`, all-or-nothing, appends ledger rows); `GET /export/weekly-history` (CSV), `GET /export/summary` (Excel `.xlsx`; `columns=`, `road_no=`). `overview`, `performance/*`, `charts/*` and `export/summary` accept `?line_manager=`. Static files: `/` and `/static`.

## 8. Conventions & gotchas

- **Two apps in one repo.** The root SQLite app (`server.py`, endpoints `/api/data`, `/api/progress`, …) is independent of `dashboard/`. Notably it stores **cumulative** quantity per week, whereas `dashboard`'s `weekly_entry.actual_qty` is a **per-submission delta** (append-only ledger). As of 2026-10-13 `dashboard/` also covers the three features the legacy app had that it lacked — manual bottleneck records, bulk weekly CSV import (`POST /api/db/weekly/import-csv`, all-or-nothing in one transaction, appends ledger rows) and CSV export — so the legacy app is a retirement candidate, but it has not been removed.
- **Weeks** run Monday–Sunday; each ledger row records its Monday (`weekly_entry` has CHECKs for this). Entry may be made on any day, and many times per week - they add up.
- **Ledger vs existing DBs.** `00_schema.sql` dropped `UNIQUE (activity_id, week_start_date)`, but `CREATE TABLE IF NOT EXISTS` will not remove it from a database created earlier; there a second same-week entry would fail until the constraint is dropped manually. Also, `activity_weekly` no longer returns `planned_this_week` / `out_of_schedule`, and `dashboard.js` charts clip future weeks' actuals (`w <= today`) and auto-scale the y-axis.
- **Rebuild is additive only.** No `DROP`, no `ALTER`, no migrations: a new column or constraint in `00_schema.sql` will not reach an existing database, and workbook changes to existing roads/activities are ignored (`ON CONFLICT DO NOTHING`; conflict keys `road(road_no)`, `activity(road_no, raw_name)`). `db/03_backup.sql`/`04_restore.sql` are leftovers from the old destructive rebuild and are no longer called. `04_restore.sql` is re-runnable (`NOT EXISTS` anti-joins, no `ON CONFLICT` on the removed UNIQUE), so it does not error on a current schema or double-count on a second run.
- **Road 10 weight defect.** Two rows (`Paver Laying` and `RCC`) map to the `Road` major, so weights total 130 and `verify_seed.py` fails, blocking `rebuild.sh` without `FORCE=1`. Needs a ruling (`docs/02_build_and_rebuild.md` §0).
- **Bare `RCC`** = ROAD major on RCC roads, = RCC Drain on Paver roads (`config.normalize`).
- **Activity names are verbatim** from the sheets, including typos (BR-02).
- **BR-08 gating removed (2026-10-13).** `docs/01_business_rules.md` marks it superseded; the pyramid view fills each layer by its own %. The remaining `_validate_gating` stub in `db_routes.py` has no callers. `scripts/config.py` docstring still mentions gating.
- **CPM** is the BR-03 sequence as a linear chain; overlaps are reported as planned parallel working (`overlap_flags`). Code comments say overlaps are expected; `docs/01_business_rules.md` BR-14 previously called them data-quality flags, and OPEN-03 in the same file is still listed as unresolved although BR-14 supersedes it.
- **Seed header** has no generation timestamp any more (`gen_seed.py` line commented out), so regenerating from an unchanged workbook should no longer change `db/01_seed.sql` (not re-run to confirm; the committed file still carries its last timestamp line).
- **Stale docstrings:** `dashboard/main.py` and `db_routes.py` refer to `db/rebuild.sh` and a "static dataset.json"; the script is `./rebuild.sh` at the repo root and no JSON tier exists.
- **Unverified:** per `docs/02_build_and_rebuild.md`, the DB tier and rebuild scripts had not been run against a live Postgres when that doc was last updated (2026-10-09). The Neon MCP connection also failed in the session that produced this doc, so nothing here was checked against a live database.
- **Editable fields.** Every road/activity field in the edit allowlists can be changed freely; the old source-override confirmation was removed because the database, not the workbook, is now the source of truth. `edit_log.source_override` is a legacy column, no longer written.

## 9. Open questions / TODOs

- `OPEN-01…OPEN-06` in `docs/01_business_rules.md` (weekly entry unit, planned spread, CPM dependency type, vendor/LM performance measure, bottleneck definition, week definition). The code implements working defaults for each (per-unit entry, straight-line planning, sequence-chain CPM, "all measures" performance, three bottleneck reasons, Mon–Sun weeks), but the doc still lists them as open.
- Road 10 double-`Road`-major weighting (above).
- The 2026-10-13 ledger/gating/over-planned changes are now merged into `main`; the ledger change has still not been run against a live database. `import_weekly_csv`'s docstring still claims BR-08 gating is enforced (it is not).
- Known source-data issues for Phase 1 (roads 3, 4, 7, 17 dates) — listed in `docs/01_business_rules.md` §3.
- Deployment on Vercel (`docs/DEPLOY_VERCEL.md`) and Render (`docs/DEPLOY_RENDER.md`) with Neon via `DATABASE_URL` is described but unexercised. `render_build.sh` gates per table, so a changed column/CHECK/seed row is not applied to an already-created Neon database. `docs/DEPLOY_RENDER.md` says the Neon password was exposed in logs earlier and must be rotated before deploying.
- Tests: `python3 -m unittest discover -s tests` (live feed, road issues; the trigger test needs a live Postgres) and `node --test tests/`. No linter or CI.
- Unmapped activity names (`config.KNOWN_UNMAPPED`: Dismantling, Subgrade Preparation, Profile correction, Levelling & Dressing, PCC, …) need a ruling on sequence position and weight.
- Housekeeping candidates: `clipboard_image_*.jpg`, `_write_test.md`, `_diag_gsb.py` in the repo root; `.gitignore` ignores `roadworks.db` and `.env`.
