# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Road Works Weekly Progress tracker (Tata Steel road packages). Web app only: `server.py` (stdlib `ThreadingHTTPServer`) serves `index.html` (single-file HTML/JS SPA) and persists to SQLite (`roadworks.db`). Pure Python standard library, no dependencies, no tests, no linter, no git.

`roadworks.py` / `build_exe.bat` are the legacy Tkinter desktop app — not used, only a reference. Its reusable logic was ported to `calc.py`.

## Commands

- Run: `python3 server.py` (serves on `127.0.0.1:8000`; `--lan` binds `0.0.0.0`). Env: `RW_PORT`, `RW_DB` (alternate DB path — use for safe experiments).

## Architecture

- Schema lives in `SCHEMA` in `server.py`. Tables: `activity` (plan: road, vendor, line manager, planned_qty, start/end dates), `progress` (PK `week_ending, activity_id`; stores **cumulative** quantity, not weekly deltas), `bottleneck`.
- `GET /api/data` dumps all three tables. Metrics (actual %, planned %, roll-ups) are computed **twice**: client-side in `index.html` (drives the UI) and server-side in `calc.py` (`/api/summary`, CSV export). Change a metric in both.
- Actual % = latest `cum_qty` on or before the selected week / planned_qty, capped at 100. Planned % = linear interpolation between start and end dates. Activities with unparseable or inverted dates have no planned %, and appear under "Data issues".
- Other GETs: `/api/summary?week=YYYY-MM-DD` (JSON roll-ups), `/api/export/summary?week=...` and `/api/export/history` (CSV downloads).
- `POST /api/import_progress` takes `{csv: text}` (columns `week_ending, activity_id, cum_qty|cumulative_qty, remarks`; dates via `norm_date`; all-or-nothing) and returns rows that are lower than the previous cumulative.
- API: `POST /api/progress | /api/bottleneck | /api/dates | /api/dates_bulk | /api/reload`. Validation errors raise `Bad` → HTTP 400 `{error}`. `dates_bulk` and `reload` run in a single transaction (any bad row cancels all).
- `index.html` falls back to an embedded `FALLBACK` dataset in read-only mode (`RO`) when `/api/data` is unreachable (e.g. opened as a file).
- **Seeding**: `plan.csv` / `bottlenecks.csv` load only when the respective table is empty. `/api/reload` re-reads them, upserting by `activity_id` / `id`; progress is never touched. `Source/17_Road_Planning_Sheets.xlsx` is the original source for the plan.

## Gotchas

- Dates must be `YYYY-MM-DD`. `plan.csv` contains malformed dates (e.g. `10-10-206` for R01-01); `/api/reload` runs them through `norm_date`, but initial seeding inserts them as-is.
- `roadworks.db.bak-*` files and `__pycache__/` are manual leftovers; back up `roadworks.db` before schema or bulk-data changes.
