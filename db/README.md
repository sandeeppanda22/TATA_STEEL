# db/

SQL for the PostgreSQL `tata_road` schema. Run in order by `../rebuild.sh`; see [`docs/02_build_and_rebuild.md`](../docs/02_build_and_rebuild.md).

| File | Role |
|---|---|
| `create_database.sql` | One-time bootstrap: creates the `tata_steel` **database** itself (the numbered files all run *inside* it and assume it exists). Idempotent + never drops — guarded by a `pg_database` existence check + `\gexec`. **Not run by `rebuild.sh`** and runs against the `postgres` maintenance DB, not `tata_steel`: `PGGSSENCMODE=disable psql -U postgres -d postgres -f db/create_database.sql`. Only needed on a local/self-managed server; Neon/Render already have the DB |
| `00_schema.sql` | Idempotent DDL (`CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`, no `DROP`, no `ALTER`) for `road`, `activity`, `weekly_entry`, `data_issue`, `edit_log`, `manual_issue`. A new column or constraint change will not reach an existing DB |
| `01_seed.sql` | **Generated** by `scripts/gen_seed.py` from `Source/*.xlsx` as `INSERT … ON CONFLICT DO NOTHING` (adds only new roads/activities/issues); do not hand-edit |
| `02_verify.sql` | Read-only post-load checks (expects 17 roads / 177 activities / 10 issues for Phase 1) |
| `03_backup.sql` | Copies weekly entries and `edit_log` into the separate `tata_road_backup` schema. **Not called by `rebuild.sh` any more** (written for the old `DROP SCHEMA` rebuild); manual safety option, never executed |
| `04_restore.sql` | Restores them after reseed, remapping `activity_id` by `(road_no, sheet_row_order)`; safe to re-run (anti-join, no double-count); unmatched rows go to `tata_road_backup.unrestored_weekly_entry`. **Not called by `rebuild.sh` any more**; manual use only |

`weekly_entry` is an append-only ledger (no `UNIQUE (activity_id, week_start_date)`; ruling 2026-10-13) and BR-08 gating no longer exists. An already-created database keeps its old UNIQUE because the DDL never alters; drop it by hand if a second same-week entry is rejected. `04_restore.sql` does not depend on that constraint: it is re-runnable and guards against duplicates with `NOT EXISTS` anti-joins (weekly rows on `activity_id, week_start_date, entered_at, actual_qty`; `edit_log` on its content, `IS NOT DISTINCT FROM` for nullable columns). CPM is computed in the API, not in SQL.
