# dashboard/

The FastAPI application: Postgres-backed API plus the static front end. Overview and flows are in [`docs/PROJECT_DOCUMENTATION.md`](../docs/PROJECT_DOCUMENTATION.md); rules are in [`docs/01_business_rules.md`](../docs/01_business_rules.md).

| File | Role |
|---|---|
| `main.py` | Creates the `FastAPI` app, includes the `/api/db` router, mounts `static/` at `/static` and `/` via `NoCacheStaticFiles`. Run: `python3 -m uvicorn dashboard.main:app --reload --port 8000` |
| `db.py` | psycopg2 helpers (`get_conn`, `query`, `execute`, `execute_returning`). Reads `DATABASE_URL` or `PG*` vars and a repo-root `.env`; hands out connections from a `ThreadedConnectionPool` (max 8) via the `get_conn()` context manager and issues `SET search_path TO tata_road, public` on each checkout (`SEARCH_PATH_SQL`; a stale module docstring still mentions `SEARCH_PATH_OPTIONS`) |
| `db_routes.py` | All `/api/db/*` routes: roads, weekly entry (GET/POST/DELETE with BR-08 gating), Add Road (`POST /roads`), master-data `PUT`s with `edit_log`, manual bottleneck CRUD (`manual_issue`), weekly CSV import, CSV exports, overview, performance, bottlenecks, milestones, critical path, charts, data issues, edit-log, weight-audit |
| `calculations.py` | Every derived metric (road/overall/planned %, performance, bottlenecks, CPM, milestones, chart data), each function citing its BR-/OPEN- rule |
| `static/index.html`, `dashboard.js`, `style.css` | The single-page UI; calls `/api/db/*` directly, no build step |

Wiring notes: `main.py` registers only `db_routes.router`; nothing else is mounted. `calculations.check_gating` is not used by the routes (they use `db_routes._validate_gating`). Requires the `tata_road` schema to exist — run `./rebuild.sh` first.
