# Deploying the dashboard to Vercel

This deploys the FastAPI + static front end (`dashboard/`) to **Vercel** as a
single Python serverless function, backed by a **Neon** PostgreSQL database.
It mirrors the Render deployment path (`DATABASE_URL` → Neon; see `DEPLOY_RENDER.md`); nothing
in the app code is forked for Vercel.

> **Scope.** This is the live `dashboard/` app only. The legacy root app
> (`server.py`, `roadworks.py`, `index.html`, …) is not deployed.

---

## 0. How it fits together

| Piece | Role on Vercel |
|---|---|
| `api/index.py` | Serverless entry point — re-exports the FastAPI `app` from `dashboard/main.py`. Vercel's `@vercel/python` runtime detects the module-level `app`. |
| `vercel.json` | Rewrites **every** path to `api/index.py`, so both `/api/db/*` and the static front end (mounted at `/` inside `dashboard/main.py`) are served by the one function. |
| `requirements.txt` (repo root) | Installed automatically at build time (`fastapi`, `uvicorn`, `openpyxl`, `psycopg2-binary`). |
| **Neon** PostgreSQL | The database. Vercel functions are stateless — there is **no local Postgres** and no filesystem DB. The schema + data must already live in Neon (see §3). |

Key consequence: **Vercel never runs `rebuild.sh`.** The build step only installs
Python deps and packages the function. Seeding the database is a separate,
one-time step you run yourself against Neon (§3).

---

## 1. Prerequisites

- A Vercel account with access to this repo (Git integration, or the Vercel CLI).
- A Neon project + database, and its **pooled** connection string.
- `psql` locally (to seed Neon once), and the repo checked out.

> **Session note (2026-10-13):** the Neon MCP connector failed to connect this
> session (`ERR_PROXY_TUNNEL: Proxy refused to open a tunnel: 403 Forbidden`), so
> the Neon connection string below could not be fetched or verified automatically.
> Copy it manually from the Neon console → **Connection Details**. Treat every
> credential in this doc as a **placeholder** — never commit a real one.

---

## 2. Files added for Vercel

Already in the repo:

```
api/index.py      # from dashboard.main import app   (re-export only)
vercel.json       # routes all paths to the function
```

`requirements.txt` and the `dashboard/` package are reused as-is.

---

## 3. Seed Neon (one time, and after any source change)

Vercel does not seed the DB. Point the existing pipeline at Neon from your
machine. Use the Neon connection string (placeholder shown):

```sh
export DATABASE_URL="postgresql://<USER>:<PASSWORD>@<HOST>.neon.tech/<DB>?sslmode=require"

# 1. Create the schema (idempotent — safe to re-run)
psql "$DATABASE_URL" -f db/00_schema.sql

# 2. Generate the seed from Source/*.xlsx, then load it (ON CONFLICT DO NOTHING)
cd scripts && python3 gen_seed.py && cd ..
psql "$DATABASE_URL" -f db/01_seed.sql
```

The schema is `tata_road`; the app sets `search_path` itself on every
connection (as a `SET` statement in `db.get_conn()`, which works on Neon's pooled PgBouncer endpoint where a startup `options` parameter would be rejected), so no role-level default is needed. Because both files are
incremental, re-running them after the workbook changes only adds new
roads/activities/issues and never drops entered weekly actuals.

> `rebuild.sh` can also do this if you first `export DATABASE_URL=...` and
> `export PGHOST=<host> PGGSSENCMODE=disable`, but the two `psql` commands above
> are the minimum and avoid the local-socket assumptions in `rebuild.sh`.

---

## 4. Configure Vercel environment variables

In the Vercel project → **Settings → Environment Variables**, add (for the
environments you deploy — Production and/or Preview):

| Variable | Value (placeholder) | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://<USER>:<PASSWORD>@<HOST>.neon.tech/<DB>?sslmode=require` | Neon **pooled** string. `dashboard/db.py` uses this first. `sslmode=require` is mandatory for Neon. |

Do **not** set `PGHOST`/`PGGSSENCMODE` on Vercel — those exist only to dodge a
local Kerberos/GSSAPI issue on the dev Mac and are irrelevant in the Linux
serverless runtime. `DATABASE_URL` alone is the deployment path.

---

## 5. Deploy

**Option A — Git integration (recommended)**

1. Import the repo in Vercel (**Add New → Project**).
2. Framework preset: **Other** (the `vercel.json` drives everything).
3. Leave build/output settings empty — `vercel.json` + `requirements.txt` are
   authoritative.
4. Add the env var from §4, then **Deploy**. Every push to the production
   branch redeploys.

**Option B — Vercel CLI**

```sh
npm i -g vercel          # once
vercel                   # preview deploy (prompts to link the project)
vercel --prod            # production deploy
```

Set the env var once via `vercel env add DATABASE_URL production` (it will
prompt for the value — paste the Neon string), or in the dashboard.

---

## 6. Verify

After the deploy finishes:

```sh
curl https://<your-project>.vercel.app/api/db/overview        # JSON metrics
curl -I https://<your-project>.vercel.app/                    # 200, serves index.html
```

Then open the URL in a browser — the dashboard should load and every tab should
populate from Neon. If `/api/db/overview` 500s, it is almost always the DB:
check `DATABASE_URL`, that `sslmode=require` is present, and that §3 actually
seeded the Neon database.

---

## 7. Caveats specific to serverless

- **Cold starts.** The first request after idle re-imports the app and opens a
  new DB connection; expect a short delay. Neon's pooled endpoint keeps this
  manageable.
- **No connection pooling in-process.** Each request opens its own psycopg2
  connection (as the app already does). Always use Neon's **pooled** host, not
  the direct host, so many short-lived function invocations don't exhaust
  Postgres connections.
- **Read/write still works** (weekly entry, edits, Add Road) — these hit Neon
  like any other client. There is just no local DB and no `rebuild.sh` at
  deploy time.
- **Static assets** are served through the Python function, not Vercel's edge
  CDN. Fine for this dashboard's traffic; if asset latency ever matters, the
  `dashboard/static/` dir could instead be served as Vercel static files with
  only `/api/*` routed to the function.
