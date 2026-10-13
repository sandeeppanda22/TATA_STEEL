# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Tata Steel Road Programme (Phase 1: 17 roads) progress dashboard. The current app is `dashboard/` — FastAPI + PostgreSQL (`tata_road` schema) + a plain HTML/JS/CSS front end, seeded from the Excel workbook in `Source/`. Business rules are in `docs/01_business_rules.md` (BR-xx confirmed, OPEN-xx unruled) and are cited throughout the code — read them before changing any calculation. Longer write-up: `docs/PROJECT_DOCUMENTATION.md`.

An earlier stdlib/SQLite app still sits in the repo root (`server.py`, `index.html`, `calc.py`, `plan.csv`, `bottlenecks.csv`, `roadworks.db`) plus the legacy Tkinter app (`roadworks.py`, `build_exe.bat`). They are separate from `dashboard/` and share no code or data with it; treat them as legacy/reference unless asked. **Feature parity check (2026-10-13):** `dashboard/` now also covers the legacy app's manual bottleneck records (`manual_issue` table, `/api/db/manual-bottlenecks` CRUD), bulk weekly-progress CSV import (`POST /api/db/weekly/import-csv`, all-or-nothing, appends ledger rows), and CSV export of progress history / summary (`GET /api/db/export/weekly-history`, `GET /api/db/export/summary`) — the three gaps found when the legacy app was audited for retirement.

## Commands

- Full rebuild (DB + deps check + seed regen + verify; does **not** start the server): `./rebuild.sh`. Incremental: `db/00_schema.sql` uses `CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS` and `scripts/gen_seed.py` emits `ON CONFLICT DO NOTHING`, so re-running it only adds roads/activities/issues that are new in `Source/*.xlsx` — it never drops the schema or touches an existing row (weekly actuals, edits, and any UI-added road all survive). Flags via env: `FORCE=1` (load even if `scripts/verify_seed.py` fails), `PORT=`.
- Read-only DB health check: `./03_validate.sh` (tables, counts 17/177/10, orphans, Monday weeks, BR-05 totals; uses `DATABASE_URL` if set, else local `PG*`; exit 1 only on hard failures — road 10=130 is a WARN). `./02_build.sh` is just a wrapper that `exec`s `./rebuild.sh`. `db/create_database.sql` (one-time, run against the `postgres` maintenance DB) creates the `tata_steel` database on a self-managed server.
- Render deploy (long-lived uvicorn, `render.yaml` Blueprint): build command `bash render_build.sh` installs deps and, if `DATABASE_URL` is set and `psql` exists, applies `db/00_schema.sql` only when `tata_road.road` is absent and `db/01_seed.sql` only when it is empty (per-table gate: later schema/seed changes are not picked up). Start: `uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT`. See `docs/DEPLOY_RENDER.md`.
- Vercel deploy: `api/index.py` re-exports `dashboard.main.app`, `vercel.json` rewrites all paths to it, DB via `DATABASE_URL` (Neon); Vercel never runs `rebuild.sh` — seed Neon by hand, see `docs/DEPLOY_VERCEL.md`.
- Run server: `python3 -m uvicorn dashboard.main:app --reload --host 0.0.0.0 --port 8000` (or `run.bat` on Windows).
- Pre-flight only: `python3 scripts/verify_seed.py`. Dataset summary: `python3 scripts/build_data.py`. Regenerate seed: `cd scripts && python3 gen_seed.py`.
- Deps: `pip install -r requirements.txt` (fastapi, uvicorn, openpyxl, psycopg2-binary). No venv, no tests, no linter.
- DB connection: `DATABASE_URL`, else `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE` (defaults port 5432, user `postgres`, db `tata_steel`, **no default host** → Unix socket, avoids a GSSAPI/Kerberos failure on this machine). `dashboard/db.py` also loads a gitignored repo-root `.env` that `rebuild.sh` writes.

## Architecture

- Pipeline: `Source/*.xlsx` → `scripts/build_data.py` (in memory) → `scripts/gen_seed.py` → `db/01_seed.sql` → Postgres, incrementally (`ON CONFLICT DO NOTHING` on `road(road_no)`, `activity(road_no, raw_name)`, and a `data_issue` dedup index). `scripts/config.py` holds the canonical activity sequence (BR-03), major weights (BR-04), raw-name→canonical map and `road_weightage()` (BR-05).
- A road can also be added directly through the dashboard (`POST /api/db/roads`, "Add Road" button on the Roads tab) with its own activities, start/end dates — no Excel edit or rebuild needed. It is stamped `source_file = 'manual-entry'` and, because the rebuild is incremental, is never erased by a later `./rebuild.sh`.
- `dashboard/main.py` mounts only the `/api/db/*` router (`dashboard/db_routes.py`) and the static front end (`dashboard/static/`, served with `no-cache`). There is no JSON/static data tier any more.
- `dashboard/calculations.py` holds all metrics (road/overall %, vendor/LM performance, bottlenecks, CPM, milestones, chart data). Metrics are computed **only server-side** here; `dashboard.js` renders what the API returns (apart from the fixed `ACTIVITY_SEQUENCE`/`MAJOR_WEIGHTS` display constants mirrored from `scripts/config.py` — change both).
- `weekly_entry` is an **append-only ledger** (ruling 2026-10-13): `actual_qty` is that submission's quantity (a delta), every POST/CSV-import row is its own `INSERT` that **adds** to the cumulative (`SUM(actual_qty)`), and there is no `UNIQUE (activity_id, week_start_date)` in `00_schema.sql` any more. Each row still carries its Monday–Sunday `week_start_date` (`submit_weekly_entry` snaps any date to its Monday). `DELETE /activities/{id}/weekly/{entry_id}` removes one row by `entry_id`. Caveat: `CREATE TABLE IF NOT EXISTS` never alters an existing DB, so one created before this change still has the old UNIQUE and, with the plain `INSERT`, rejects a second entry in the same week until the constraint is dropped by hand. (The legacy SQLite app stores cumulative instead — don't mix them up.)
- Displayed activity/road/bottleneck % is capped at 100; `road_progress()` returns `(road_pct, drain_pct, over_planned_count)` and the API/UI flag `over_planned` where actual > planned_qty.
- BR-08 gating was **removed** (ruling 2026-10-13): progress may be entered for any activity in any order. `calculations.check_gating` is gone; `db_routes._validate_gating` is a no-op stub with no callers. Don't re-add checks without a new ruling.
- Edits to `road`/`activity` go through `PUT` routes, are logged field-by-field in `edit_log`, and edits to BR-01/BR-02 source fields need `confirm_source_override=true` (else 409).
- All SQL uses unqualified table names; `db.get_conn()` is a context manager that borrows from a lazily-created `ThreadedConnectionPool` (1–8 conns) and runs `SET search_path TO tata_road, public` on every checkout (not as a libpq `options` startup parameter — Neon's pooled PgBouncer endpoint rejects that). It does not commit: `execute*` commit explicitly, and the connection goes back to the pool in `finally`.

## Gotchas

- `./rebuild.sh` is incremental and never drops the schema or discards entered data — see Architecture above. The DB tier has never been run end-to-end against a live Postgres per `docs/02_build_and_rebuild.md`.
- Known unresolved data defect: road 10 maps two rows (`Paver Laying`, `RCC`) to the `Road` major → weights total 130, so `verify_seed.py` fails and `rebuild.sh` stops unless `FORCE=1`. Needs a user ruling (see `docs/02_build_and_rebuild.md` §0).
- Bare `RCC` row means the ROAD major on RCC roads but an RCC Drain on Paver roads (`config.normalize`, `road_type` dependent).
- `Source/` is untracked/changed in git: the workbook is now `Final Sheet for Claude Project-Phase-1.xlsx`; every `.xlsx` in `Source/` is read (BR-13).
- Weekly-entry code/schema changes (ledger, gating removal) are uncommitted on `develop` as of 2026-10-13 (`dashboard/*`, `db/00_schema.sql`, `docs/01_business_rules.md`).
- `db/04_restore.sql` is re-runnable: weekly rows restore with a `NOT EXISTS` anti-join on `(activity_id, week_start_date, entered_at, actual_qty)` and `edit_log` rows on their content + `edited_at` (`IS NOT DISTINCT FROM` for the nullable values). It no longer uses `ON CONFLICT (activity_id, week_start_date)` — that needed the removed UNIQUE and errored on a current schema.
- Stale docstring: `import_weekly_csv` (`db_routes.py`) still says "BR-08 gating is enforced per row"; the code does no gating.
- Stray files in the root (`clipboard_image_*.jpg`, `_write_test.md`, `_diag_gsb.py`, `roadworks.db.bak-*`, `__pycache__/`) are scratch/leftovers, not part of the app.
- Back up the DB before schema or bulk-data changes.
- `db/03_backup.sql` / `db/04_restore.sql` are no longer called by `rebuild.sh` (the old `KEEP=1` backup/restore dance is obsolete now that rebuilds are incremental) — left in place as a manual safety-backup option, not part of the normal flow.
