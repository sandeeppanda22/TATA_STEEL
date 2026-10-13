# Build, Run, and Rebuild — Reference

What exists today, what's still open, and the exact commands for each piece.
See `docs/01_business_rules.md` for the rules; this doc is just the
build/run/rebuild mechanics.

---

## 0. Status (as of 2026-10-13)

| Layer | State |
|---|---|
| **DB** (`db/00_schema.sql`, `db/01_seed.sql`) | Schema is idempotent (`IF NOT EXISTS`) and now also has `manual_issue` (manual bottleneck log); seed is generated as `ON CONFLICT DO NOTHING`. Dry-run-verified against the dataset (17 roads, 177 activities, 10 data issues). **Never executed against a live Postgres in any session** this doc knows of. 6 tables: `road`, `activity`, `weekly_entry`, `data_issue`, `edit_log`, `manual_issue`. |
| **DB — backup/restore** (`db/03_backup.sql`, `db/04_restore.sql`) | **Obsolete in the normal flow** — `rebuild.sh` no longer drops the schema and no longer calls them. Written for the old destructive rebuild, never executed; manual option only. `db/02_verify.sql` is still run by `rebuild.sh`. |
| **API — static tier** (`/api/overview`, `/api/refresh`) | **Removed.** `dashboard/main.py` serves only `/api/db/*` and static files; there is no JSON data tier and no `data/*.json` files in the repo any more. |
| **API — DB tier** (`/api/db/*` in `dashboard/db_routes.py`) | Code-complete: roads, road detail, weekly entry (GET/POST append-only ledger, DELETE by `entry_id`; BR-08 gating removed 2026-10-13), master-data editing (`PUT /roads/{n}`, `PUT /activities/{id}`), `GET /edit-log`, `GET /weight-audit`, overview, vendor/LM performance, bottlenecks, milestones, critical path, data issues. Verified only by import/syntax check — **never run against a live DB**, since §0 above means there's nothing to query yet. Also: `POST /roads` (Add Road), manual bottleneck CRUD (`/manual-bottlenecks`), `POST /weekly/import-csv`, `GET /export/weekly-history`, `GET /export/summary`; several GETs accept `line_manager`. |
| **UI** (`dashboard/static/`) | Wired entirely to `/api/db/*` — KPI row and roads table from `GET /api/db/roads`, weight chart from `GET /api/db/overview`, data issues from `GET /api/db/data-issues`, plus the weekly data-entry and master-data edit forms (`livePanel`). The Canonical Activity Sequence panel is the one exception: it renders fixed `ACTIVITY_SEQUENCE`/`MAJOR_WEIGHTS` constants (mirrored from `scripts/config.py`) since those are programme rulings, not seeded data. |

### Open items that block trusting the numbers

- **BR-05 defect, road 10, unresolved.** `scripts/verify_seed.py` reports
  `road 10=130.0`. Road 10 (BF-2 Bin Building) has two activity rows mapping
  to the same major `Road` — `Paver Laying` and `RCC` — and `build_data.py`
  gives each the full 30 points, so the road's weights total 130 instead of
  100 and road 10 can report up to 130% complete. BR-04/BR-05 say nothing
  about two rows sharing one major, so this needs an explicit ruling, not a
  guess. It is the only road affected. `./rebuild.sh` now **stops** on this
  (pre-flight gate) unless run with `FORCE=1`.
- **Every percentage will read 0%** after a successful rebuild.
  `weekly_entry` is seeded empty on purpose (no fake data), so progress only
  appears once real actuals are entered.
- **Editable-field policy unruled.** BR-01 makes Phase-Summary absolute truth
  for every `road` column and BR-02 makes `activity.raw_name` verbatim, which
  conflicts with editing them. Current stand-in: the API allows such edits
  only when the request sets `confirm_source_override=true`, and records them
  in `edit_log` with `source_override = TRUE`. That is a conservative
  placeholder, not a decision.

Nothing below has been exercised end-to-end — these are the commands, not a
confirmation that they've been run successfully together.

---

## 1. Build / rebuild everything (DB + API + UI, one script)

`./rebuild.sh` (at the repo root) is the single build/rebuild entry point for
all three tiers. It does **not** start the server. As of 2026-10-13 it is
**incremental**: it never drops the schema and never overwrites existing rows.

```bash
cd /Users/sandeep.panda03/Documents/TATA_STEEL
./rebuild.sh
PORT=9000 ./rebuild.sh        # match the port your dev server used, if not :8000
FORCE=1 ./rebuild.sh          # proceed even if the DB pre-flight check fails
```

What it does (`rebuild.sh`, 7 steps):

| Step | Action |
|---|---|
| 0 | Stops any dev server already running (`pkill -f "uvicorn dashboard.main:app"`) |
| 1 | API — confirms/installs `fastapi`, `uvicorn`, `psycopg2`, `openpyxl` |
| 2 | UI — confirms the static assets exist (`dashboard/static/{index.html,dashboard.js,style.css}`); no build step |
| 3 | `python3 scripts/build_data.py` — Excel → in-memory dataset (prints a summary only) |
| 4 | `cd scripts && python3 gen_seed.py` — writes `db/01_seed.sql` as `INSERT ... ON CONFLICT DO NOTHING` |
| 5 | `python3 scripts/verify_seed.py` — pre-flight. **Exits the rebuild if it fails** (override with `FORCE=1`) |
| 6 | `db/00_schema.sql` — idempotent (`CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`); creates only what is missing, no `DROP`, no `ALTER` |
| 7 | `db/01_seed.sql` — adds only roads / activities / data issues not already present (conflict targets: `road(road_no)`, `activity(road_no, raw_name)`, the `data_issue` dedup index) |
| – | `db/02_verify.sql` — read-only post-load checks (not `ON_ERROR_STOP`) |

Consequences: weekly actuals, master-data edits (`edit_log`) and roads added
through the dashboard ("Add Road", `POST /api/db/roads`, `source_file =
'manual-entry'`) all survive a rebuild. Conversely, **a changed value in the
workbook does not update an existing row** — `ON CONFLICT DO NOTHING` skips
it; change it through the UI (`PUT` routes) or by hand in SQL.

`db/03_backup.sql` and `db/04_restore.sql` (copy entered data to / from the
separate `tata_road_backup` schema) are **no longer called** by `rebuild.sh`.
They date from the old `DROP SCHEMA` rebuild and were never executed; keep
them only as a manual safety option. If you use them, note `04_restore.sql`
remaps weekly rows by `(road_no, sheet_row_order)`, not `activity_id`, and is
re-runnable (`NOT EXISTS` anti-join; it no longer needs the removed
`UNIQUE (activity_id, week_start_date)`).

Once this exits clean, start the server with a plain command:
```bash
python3 -m uvicorn dashboard.main:app --reload --host 0.0.0.0 --port 8000
```

Dependencies are global (no venv), per project convention — `requirements.txt`
includes `psycopg2-binary`.

To run the DB steps by hand instead:
```bash
cd /Users/sandeep.panda03/Documents/TATA_STEEL
psql -U postgres -d tata_steel -v ON_ERROR_STOP=1 -f db/00_schema.sql
psql -U postgres -d tata_steel -v ON_ERROR_STOP=1 -f db/01_seed.sql
psql -U postgres -d tata_steel -f db/02_verify.sql
```

Environment variables `rebuild.sh` reads (all optional, shown with defaults):
```bash
# PGHOST   — deliberately NOT defaulted; see "GSSAPI" below
PGPORT=5432
PGUSER=postgres
PGDATABASE=tata_steel
PGGSSENCMODE=disable
# PGPASSWORD — not defaulted; the script asks once (only if neither PGPASSWORD
#              nor ~/.pgpass already covers it), exports it for its own psql
#              calls, AND saves it to a gitignored ./.env (chmod 600) so the
#              dashboard server and every future rebuild read it automatically.
FORCE=0      # FORCE=1 loads the seed even if the pre-flight check fails
```

On success it prints row counts for `road`, `activity`, and restored
`weekly_entry` — that's the confirmation it landed.

**GSSAPI / Kerberos (found in an earlier session, and why `PGHOST` has no
default):** connecting over TCP (`psql -h localhost`) makes libpq attempt
GSSAPI first, which fails on this machine with
`Cannot find KDC for realm "APPLECONNECT.APPLE.COM"`. Two ways around it, both
in use above:
- **Omit `-h` / leave `PGHOST` unset** — libpq uses the local Unix socket,
  which skips GSSAPI entirely. This is the path `rebuild.sh` takes.
- **`PGGSSENCMODE=disable`** — keeps TCP working when `PGHOST` is set
  explicitly (for a remote server). `rebuild.sh` exports this too.

**Password (`fe_sendauth: no password supplied`):** the local Postgres wants a
password over the socket, and the long-running server can't prompt for one. So
`rebuild.sh` saves the password you type into a gitignored `./.env` the first
time, and `dashboard/db.py` loads that `.env` at startup (dependency-free; a
real env var or Render's `DATABASE_URL` always wins over it). Run `./rebuild.sh`
once and the server connects automatically afterward — no exports, no manual
editing. Delete `./.env` to be prompted again, or edit it to change the value.
A `~/.pgpass` entry works too and takes precedence (if it exists, `rebuild.sh`
doesn't prompt and doesn't write `.env`).

## 4. Running the server against the live DB tier

Once `./rebuild.sh` has succeeded locally, the same `dashboard.main:app`
process picks up the `/api/db/*` routes automatically (already wired in
`main.py`) — `dashboard/db.py` connects using the same `PGHOST`/`PGPORT`/
`PGUSER`/`PGPASSWORD`/`PGDATABASE` vars as `rebuild.sh` (port 5432, user
`postgres`, db `tata_steel` by default; **no default `host`** — see the
GSSAPI note above). Export them before starting `uvicorn` only if they
differ from those defaults.

```bash
export PGPORT=5432 PGUSER=postgres PGDATABASE=tata_steel
export PGPASSWORD=...          # or let libpq prompt
python3 -m uvicorn dashboard.main:app --reload --host 0.0.0.0 --port 8000
curl http://localhost:8000/api/db/roads
```

The static tier is gone — `/api/db/*` is the only API tier, and the UI
(`dashboard/static/`) calls it directly.

## 5. Deployment (Vercel / Render + Neon)

`dashboard/db.py` checks `DATABASE_URL` first, before the local `PGHOST`/...
vars, so deployment is just an env var pointing at a Neon database. Vercel
packaging (`api/index.py`, `vercel.json`) and the one-time Neon seeding steps
are in [`DEPLOY_VERCEL.md`](DEPLOY_VERCEL.md); the Render Blueprint
(`render.yaml`, `render_build.sh`) is in [`DEPLOY_RENDER.md`](DEPLOY_RENDER.md). Neither deployment path has
been exercised end to end; Vercel never runs `rebuild.sh`.

---

## 6. What "done" means per layer, precisely

- **DB**: done = `./rebuild.sh` exits 0 with the pre-flight check passing
  (no `FORCE=1`) and prints non-zero `road`/`activity` counts against a
  reachable Postgres. Not done yet — and it cannot pass today, because the
  road 10 BR-05 defect (§0) fails the pre-flight.
- **DB — incremental rebuild**: done = re-running `./rebuild.sh` against a
  database that already holds weekly actuals, edits and a UI-added road leaves
  all of them untouched and adds only new workbook rows. Not exercised.
- **API (DB tier)**: done = each `/api/db/*` route returns correct data
  against a rebuilt DB, including a round-trip `POST .../weekly` entry that
  adds to the activity's cumulative (a second same-week POST appends a row; BR-08 gating no longer exists). Not exercised yet — code
  exists, behavior unverified.
- **API — edit layer**: done = a `PUT /api/db/roads/{n}` and a
  `PUT /api/db/activities/{id}` each change only the supplied fields, write one
  `edit_log` row per actually-changed field, return `409` with the
  `confirm_source_override` challenge when a BR-01/BR-02 source field is
  touched without it, and refuse a `weight_pct` edit that breaks the road's
  100 total. Also unexercised — same reason.
- **UI**: done = the dashboard reads live `/api/db/*` data AND has a working
  weekly entry screen AND road/activity edit forms that surface the `409`
  messages (source-override confirm step, BR-05 weight message) rather than failing silently. The read/write wiring to `/api/db/*`
  is in place (`dashboard/static/dashboard.js`); still unverified end-to-end
  against a rebuilt DB for the same reason as the rows above.
