# Deploying to Render

This is the Render deployment path for the Tata Steel Road Programme dashboard.
Render runs the FastAPI app as a **long-lived web server** (not serverless), so
there are no cold starts and none of the PgBouncer startup-parameter issues that
affected the serverless path. The database stays on **Neon** (cloud Postgres).

The repo already contains everything Render needs:
- `render.yaml` — the Blueprint (service definition).
- `requirements.txt` — Python deps.
- `dashboard/db.py` — already fixed to run `SET search_path TO tata_road, public`
  *after* connecting (works on Neon's pooled endpoint).

---

## One-time facts about this app

| Thing | Value |
|---|---|
| App module | `dashboard.main:app` |
| Build command | `bash render_build.sh` (installs deps; touches the DB only if `ALLOW_DB_BOOTSTRAP=yes`) |
| Start command | `uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT` |
| Python version | `3.12.7` (pinned via `PYTHON_VERSION` — local 3.14 is too new for prebuilt wheels) |
| Health check path | `/` |
| Secret env var | `DATABASE_URL` (entered in the Render dashboard, never committed) |
| DB safety lock | `ALLOW_DB_BOOTSTRAP` (`no` by default — build never touches the DB) |

### Render environment variables

| Key | Value | Notes |
|---|---|---|
| `DATABASE_URL` | the rotated Neon **pooled** URL from Step 1 | Secret. Required. |
| `ALLOW_DB_BOOTSTRAP` | `no` | The safety lock. Keep `no` for all normal deploys. Only set `yes` to seed a brand-new empty DB (Step 2). `render.yaml` sets it to `no`; if the service was created before this was added, add it by hand under Environment. |
| `PYTHON_VERSION` | `3.12.7` | Set by `render.yaml`. |

`PORT` is injected by Render — do not add it.

---

## Step 1 — Rotate and prepare the Neon connection string

The Neon password was exposed in logs earlier in this project. **Rotate it before
deploying** (Neon Console → your project → Roles → `neondb_owner` → Reset password).

Then build the connection string using the **pooled** endpoint host (the one with
`-pooler` in it) and `?sslmode=require`:

```
postgresql://neondb_owner:<ROTATED_PASSWORD>@ep-square-hill-b3avqzid-pooler.c-4.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

Keep this somewhere safe. **Do not commit it** to the repo.

---

## Step 2 — Seed the Neon database (only for a NEW, empty database)

**The build is locked by default.** `render_build.sh` only touches the database
when `ALLOW_DB_BOOTSTRAP` is exactly `yes`. With it unset or `no` (the value in
`render.yaml`) the build installs dependencies and exits — no schema, no seed — so
a redeploy, or a `DATABASE_URL` pointed at the wrong database, cannot change data.

**Your DB is already seeded? Skip this step** and leave `ALLOW_DB_BOOTSTRAP=no`.

To bootstrap a brand-new empty Neon database deliberately:

1. In Render → service → Environment, set `ALLOW_DB_BOOTSTRAP` = `yes`.
2. Deploy. With the lock open, the build applies `db/00_schema.sql` every time.
   That file is `CREATE … IF NOT EXISTS` only, so it **adds tables/indexes that
   don't exist yet and never touches an existing table, column or row**. It then
   loads `db/01_seed.sql` only if `tata_road.road` is empty — a populated DB is
   never reseeded.
3. **Set `ALLOW_DB_BOOTSTRAP` back to `no`** and save.

**Use this to roll out a NEW table:** add it to `db/00_schema.sql`, set the lock to
`yes`, deploy once, set it back to `no`. Existing tables are skipped.

**Caveat (matches the project's no-migrations rule):** a changed column, CHECK, or
new seed row on an *already-created* table is **not** picked up — only a deliberate
hard rebuild does. To force a reseed, back up, drop the `tata_road`
schema on Neon, then redeploy with the lock open.

### Manual seeding (optional fallback)

If you'd rather seed by hand (or `psql` wasn't available in the build image), run
from your machine against the rotated Neon URL — both files are safe to re-run:

```sh
# schema (idempotent — CREATE … IF NOT EXISTS)
psql "postgresql://neondb_owner:<ROTATED_PASSWORD>@ep-square-hill-b3avqzid-pooler.c-4.ap-southeast-1.aws.neon.tech/neondb?sslmode=require" \
  -f db/00_schema.sql

# seed (idempotent — ON CONFLICT DO NOTHING)
psql "postgresql://neondb_owner:<ROTATED_PASSWORD>@ep-square-hill-b3avqzid-pooler.c-4.ap-southeast-1.aws.neon.tech/neondb?sslmode=require" \
  -f db/01_seed.sql
```

After either path, run `DATABASE_URL="…" ./03_validate.sh` to confirm the data
(tables, counts, FK integrity).

> Known seed caveat (road 10 weights = 130) still applies and surfaces as a **WARN**
> in `03_validate.sh`, not a failure. If `db/01_seed.sql` was generated with
> `verify_seed.py` failing, confirm the road-10 ruling — see
> `docs/02_build_and_rebuild.md` §0.

---

### Database updates in this release (existing DB, no data touched)

This release adds two **new tables** (`road_issue`, `road_issue_shift`) and the
live-refresh feed (`live_change_marker`, a sequence and triggers). Everything is
`CREATE … IF NOT EXISTS` / trigger re-creation: no existing table, column or row
is changed, dropped or re-seeded. Until applied the app still runs, but the
per-road **Issue** feature errors and live refresh reports `enabled: false`.

Apply once, either way (both files are safe to re-run):

1. **By hand** (recommended): Neon SQL Editor, or
   `psql "$DATABASE_URL" -f db/00_schema.sql` then
   `psql "$DATABASE_URL" -f db/05_live_change.sql`.
2. **Via one deploy**: set `ALLOW_DB_BOOTSTRAP=yes`, deploy, then set it back to
   `no`. With the lock open the build runs `00_schema.sql` (missing tables only) and
   `05_live_change.sql`; the seed is skipped because `road` already has rows.

Not needed on Neon: `db/06_alter_activity_unique.sql` (only matters when
re-seeding with `rebuild.sh`).

**Pre-deploy checklist**
- Commit and push the changes to the branch Render deploys (`main`); they are
  currently uncommitted on `develop`.
- `DATABASE_URL` set; `ALLOW_DB_BOOTSTRAP` = `no` (or `yes` for the one-off above).
- Take a Neon branch/backup first if you can (cheap insurance, not required by the SQL).
- After deploy: `/api/db/roads` loads, and `/api/db/live/changes` shows `"enabled": true`.

---

## Step 3 — Deploy via the Blueprint

1. Push the branch with `render.yaml` to GitHub (`main`).
2. Render Dashboard → **New → Blueprint**.
3. Connect the `sandeeppanda22/TATA_STEEL` repo and pick branch `main`.
4. Render reads `render.yaml` and shows the `tata-steel-dashboard` web service.
5. It will prompt for the one secret marked `sync: false` → paste the rotated
   `DATABASE_URL` from Step 1 into **DATABASE_URL**. `ALLOW_DB_BOOTSTRAP` comes
   pre-filled as `no` from `render.yaml` — leave it unless seeding an empty DB.
6. Click **Apply**. Render installs deps, then starts uvicorn.

First build takes a few minutes. When it goes **Live**, open the service URL
(`https://tata-steel-dashboard.onrender.com`) — `/` serves the dashboard and
`/api/db/...` serves the API.

---

## Step 4 — Verify

- `https://<your-service>.onrender.com/` → the dashboard loads.
- `https://<your-service>.onrender.com/api/db/roads` → JSON list of roads
  (confirms the app reached Neon through `DATABASE_URL`).

---

## Free-tier note

Render's free web services **spin down after ~15 min of inactivity** and take
~30–60 s to wake on the next request. Fine for a demo/low-traffic dashboard; move
to a paid instance type if you need it always-on. Change `plan: free` in
`render.yaml` to upgrade.

---

## Redeploys

Every push to `main` triggers an automatic redeploy (Render watches the branch).
Each redeploy re-runs `render_build.sh`. While `ALLOW_DB_BOOTSTRAP` is `no` it
never touches the database, so redeploys cannot alter entered data. Changed
columns/CHECKs/new seed rows are **not** auto-applied — see the Step 2 caveat.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Build fails compiling psycopg2 | Python version drifted off 3.12 — confirm `PYTHON_VERSION=3.12.7` in the service env. |
| `unsupported startup parameter: search_path` | Old code — ensure the deployed commit has the `dashboard/db.py` post-connect `SET search_path` fix. |
| App up but every API call 500s | `DATABASE_URL` wrong/unset, password not rotated, or Neon not seeded (Step 2 — needs `ALLOW_DB_BOOTSTRAP=yes` once on an empty DB). |
| First request after idle is slow | Free-tier spin-down (see note above). Expected. |
