# tests/

Unit tests for the `dashboard/` app. Overview: [`docs/PROJECT_DOCUMENTATION.md`](../docs/PROJECT_DOCUMENTATION.md).

Run: `python3 -m unittest discover -s tests` (Python) and `node --test tests/` (JS). Single test: `python3 -m unittest tests.test_road_issue`, or `node --test tests/tables.test.js`.

| File | Role |
|---|---|
| `test_calculations.py` | Pins `dashboard/calculations.py` metric outputs (with a faked `query`) against `calc_golden.json`; regenerate only on a deliberate behaviour change: `python3 tests/test_calculations.py --regen` |
| `calc_golden.json` | Golden snapshot used by the above |
| `test_add_road.py` | Add Road validation in `db_routes.create_road` (no DB) |
| `test_road_issue.py` | Road hindrance-window (`road_issue`) date-shift rules |
| `test_live_feed.py` | `live_feed.LIVE_FEED_TABLES` matches `db/05_live_change.sql`, rate limiter; the trigger test needs a live Postgres |
| `api_errors.test.js` | `dashboard.js` API error-message formatting |
| `live_refresh.test.js` | `static/live-refresh.js` polling behaviour |
| `tables.test.js` | `dashboard.js` table helpers (`expandableTable` etc.) |
