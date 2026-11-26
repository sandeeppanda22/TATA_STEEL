# dashboard/

The FastAPI application: Postgres-backed API plus the static front end. Overview and flows are in [`docs/PROJECT_DOCUMENTATION.md`](../docs/PROJECT_DOCUMENTATION.md); rules are in [`docs/01_business_rules.md`](../docs/01_business_rules.md).

| File | Role |
|---|---|
| `main.py` | Creates the `FastAPI` app, includes the `/api/db` router, mounts `static/` at `/static` and `/` via `NoCacheStaticFiles`. Run: `python3 -m uvicorn dashboard.main:app --reload --port 8000` |
| `db.py` | psycopg2 helpers (`get_conn`, `query`, `execute`, `execute_returning`). Reads `DATABASE_URL` or `PG*` vars and a repo-root `.env`; hands out connections from a `ThreadedConnectionPool` (max 8) via the `get_conn()` context manager and issues `SET search_path TO tata_road, public` on each checkout (`SEARCH_PATH_SQL`; a stale module docstring still mentions `SEARCH_PATH_OPTIONS`) |
| `db_routes.py` | All `/api/db/*` routes: roads, weekly entry (GET/POST append-only ledger; PUT/DELETE by `entry_id`; no gating), Add Road (`POST /roads`), add/delete activities on an existing road (`POST /roads/{n}/activities` via `add_activities`, `DELETE /activities/{id}?transfer_weight_to=` via `delete_activity`; BR-05 total-100 enforced, road locked in a transaction), `GET /activity-units`, road hindrance issues, master-data `PUT`s with `edit_log`, manual bottleneck CRUD (`manual_issue`), weekly CSV import, CSV exports, overview, performance, bottlenecks, milestones, critical path, charts, data issues, edit-log, weight-audit |
| `live_feed.py` | `LIVE_FEED_TABLES` (must equal the trigger list in `db/05_live_change.sql`) and a per-IP `RateLimiter` (60/min) for `GET /api/db/live/changes` |
| `calculations.py` | Every derived metric (road/overall/planned %, performance, bottlenecks, CPM — `critical_path` returns every activity, sequenced first then unsequenced with a `sequenced` flag — milestones, chart data), each function citing its BR-/OPEN- rule |
| `static/index.html`, `dashboard.js`, `live-refresh.js`, `style.css` | The single-page UI; calls `/api/db/*` directly, no build step; `live-refresh.js` polls the change feed |

Wiring notes: `main.py` registers only `db_routes.router`; nothing else is mounted. BR-08 percentage gating was removed (ruling 2026-10-13): `calculations.check_gating` no longer exists and `db_routes._validate_gating` is a retained no-op stub with no callers — progress may be entered for any activity in any order, and `weekly_entry` is an append-only ledger (every submission adds to the cumulative). Requires the `tata_road` schema to exist — run `./rebuild.sh` first.

Activity names are mapped to `seq_no`/`is_drain` server-side by `db_routes._canonical` (`scripts.config.normalize` + `ACTIVITY_SEQUENCE`; guarded import, falls back to NULL seq if `scripts` can't be imported), used by both `POST /roads` and `add_activities`. Front end: `uiPickWeightTarget` (weighted-delete popup), `renderAddActivityForm` (card table reusing `addRoadActivityRowHtml`), `renderRoadPyramid`/`csPyramid` ("Parallel Working"). Known stale docstring: `import_weekly_csv` still says BR-08 gating is enforced; it is not.
