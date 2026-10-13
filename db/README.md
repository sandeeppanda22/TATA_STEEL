# db/

SQL for the PostgreSQL `tata_road` schema. Run in order by `../rebuild.sh`; see [`docs/02_build_and_rebuild.md`](../docs/02_build_and_rebuild.md).

| File | Role |
|---|---|
| `00_schema.sql` | Idempotent DDL (`CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`, no `DROP`, no `ALTER`) for `road`, `activity`, `weekly_entry`, `data_issue`, `edit_log`, `manual_issue`. A new column will not reach an existing DB |
| `01_seed.sql` | **Generated** by `scripts/gen_seed.py` from `Source/*.xlsx` as `INSERT … ON CONFLICT DO NOTHING` (adds only new roads/activities/issues); do not hand-edit |
| `02_verify.sql` | Read-only post-load checks (expects 17 roads / 177 activities / 10 issues for Phase 1) |
| `03_backup.sql` | Copies weekly entries and `edit_log` into the separate `tata_road_backup` schema. **Not called by `rebuild.sh` any more** (written for the old `DROP SCHEMA` rebuild); manual safety option, never executed |
| `04_restore.sql` | Restores them after reseed, remapping `activity_id` by `(road_no, sheet_row_order)`; unmatched rows go to `tata_road_backup.unrestored_weekly_entry`. **Not called by `rebuild.sh` any more**; manual use only |

BR-08 gating and CPM are enforced/computed in the API, not in SQL.
