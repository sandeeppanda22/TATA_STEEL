# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Tata Steel Road Programme (Phase 1: 17 roads) progress dashboard. The current app is `dashboard/` — FastAPI + PostgreSQL (`tata_road` schema) + a plain HTML/JS/CSS front end, seeded from the Excel workbook in `Source/`. Business rules are in `docs/01_business_rules.md` (BR-xx confirmed, OPEN-xx unruled) and are cited throughout the code — read them before changing any calculation. Longer write-up: `docs/PROJECT_DOCUMENTATION.md`.

An earlier stdlib/SQLite app still sits in the repo root (`server.py`, `index.html`, `calc.py`, `plan.csv`, `bottlenecks.csv`, `roadworks.db`) plus the legacy Tkinter app (`roadworks.py`, `build_exe.bat`). They are separate from `dashboard/` and share no code or data with it; treat them as legacy/reference unless asked. **Feature parity check (2026-10-13):** `dashboard/` now also covers the legacy app's manual bottleneck records (`manual_issue` table, `/api/db/manual-bottlenecks` CRUD), bulk weekly-progress CSV import (`POST /api/db/weekly/import-csv`), and CSV export of progress history / summary (`GET /api/db/export/weekly-history`, `GET /api/db/export/summary`) — the three gaps found when the legacy app was audited for retirement.

## Commands

- Full rebuild (DB + deps check + seed regen + verify; does **not** start the server): `./rebuild.sh`. Incremental: `db/00_schema.sql` uses `CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS` and `scripts/gen_seed.py` emits `ON CONFLICT DO NOTHING`, so re-running it only adds roads/activities/issues that are new in `Source/*.xlsx` — it never drops the schema or touches an existing row (weekly actuals, edits, and any UI-added road all survive). Flags via env: `FORCE=1` (load even if `scripts/verify_seed.py` fails), `PORT=`.
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
- `weekly_entry.actual_qty` is **that week's quantity (a delta)**; cumulative = `SUM(actual_qty)`. Weeks are Monday–Sunday, keyed by `week_start_date`; `submit_weekly_entry` snaps any date to its Monday. (The legacy SQLite app stores cumulative instead — don't mix them up.)
- BR-08 gating (activity % ≤ previous present activity's %) is enforced in the API at write time (`_validate_gating` in `db_routes.py`, both directions, HTTP 409), not in SQL. A near-duplicate `check_gating` in `calculations.py` is forward-only and raises `ValueError`.
- Edits to `road`/`activity` go through `PUT` routes, are logged field-by-field in `edit_log`, and edits to BR-01/BR-02 source fields need `confirm_source_override=true` (else 409).
- All SQL uses unqualified table names; `db.get_conn()` runs `SET search_path TO tata_road, public` right after connecting (not as a libpq `options` startup parameter — Neon's pooled PgBouncer endpoint rejects that).

## Gotchas

- `./rebuild.sh` is incremental and never drops the schema or discards entered data — see Architecture above. The DB tier has never been run end-to-end against a live Postgres per `docs/02_build_and_rebuild.md`.
- Known unresolved data defect: road 10 maps two rows (`Paver Laying`, `RCC`) to the `Road` major → weights total 130, so `verify_seed.py` fails and `rebuild.sh` stops unless `FORCE=1`. Needs a user ruling (see `docs/02_build_and_rebuild.md` §0).
- Bare `RCC` row means the ROAD major on RCC roads but an RCC Drain on Paver roads (`config.normalize`, `road_type` dependent).
- `Source/` is untracked/changed in git: the workbook is now `Final Sheet for Claude Project-Phase-1.xlsx`; every `.xlsx` in `Source/` is read (BR-13).
- Stray files in the root (`clipboard_image_*.jpg`, `_write_test.md`, `_diag_gsb.py`, `roadworks.db.bak-*`, `__pycache__/`) are scratch/leftovers, not part of the app.
- Back up the DB before schema or bulk-data changes.
- `db/03_backup.sql` / `db/04_restore.sql` are no longer called by `rebuild.sh` (the old `KEEP=1` backup/restore dance is obsolete now that rebuilds are incremental) — left in place as a manual safety-backup option, not part of the normal flow.
