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
| Build command | `bash render_build.sh` (installs deps + first-deploy auto-seed of Neon) |
| Start command | `uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT` |
| Python version | `3.12.7` (pinned via `PYTHON_VERSION` — local 3.14 is too new for prebuilt wheels) |
| Health check path | `/` |
| Secret env var | `DATABASE_URL` (entered in the Render dashboard, never committed) |

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

## Step 2 — Seed the Neon database

The build command is now `bash render_build.sh`, which **auto-seeds Neon on the
first deploy**: after installing deps it checks the target DB (via `DATABASE_URL`)
and, only if the `tata_road.road` table is absent/empty, applies `db/00_schema.sql`
then `db/01_seed.sql`. This is existence-gated — on every later redeploy it sees the
table already present and seeded and **skips both**, so entered weekly actuals and
any UI-added road are never dropped or overwritten.

So for a normal Blueprint deploy you do **not** need to seed by hand — just make
sure `DATABASE_URL` is set (Step 3) and the first build will populate Neon.

**Caveat (matches the project's no-migrations rule):** the gate is per *table*, not
per column/constraint/seed-row. If you later change a column, a CHECK, or add a new
seed row for an already-created table, a redeploy will **not** pick it up — only a
deliberate hard rebuild will. To force a reseed, drop the `tata_road` schema on Neon
first (back it up beforehand), then redeploy.

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

## Step 3 — Deploy via the Blueprint

1. Push the branch with `render.yaml` to GitHub (`main`).
2. Render Dashboard → **New → Blueprint**.
3. Connect the `sandeeppanda22/TATA_STEEL` repo and pick branch `main`.
4. Render reads `render.yaml` and shows the `tata-steel-dashboard` web service.
5. It will prompt for the one secret marked `sync: false` → paste the rotated
   `DATABASE_URL` from Step 1 into **DATABASE_URL**.
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
Each redeploy re-runs `render_build.sh`, but its DB step is existence-gated: once
`tata_road` exists and is seeded it does nothing, so redeploys are safe and never
touch entered data. Changed columns/CHECKs/new seed rows on existing tables are
**not** auto-applied — see the Step 2 caveat for how to force a reseed.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Build fails compiling psycopg2 | Python version drifted off 3.12 — confirm `PYTHON_VERSION=3.12.7` in the service env. |
| `unsupported startup parameter: search_path` | Old code — ensure the deployed commit has the `dashboard/db.py` post-connect `SET search_path` fix. |
| App up but every API call 500s | `DATABASE_URL` wrong/unset, password not rotated, or Neon not seeded (Step 2). |
| First request after idle is slow | Free-tier spin-down (see note above). Expected. |
