-- ============================================================================
-- db/04_restore.sql — put entered data back after a hard rebuild.
--
-- Run AFTER 00_schema.sql and 01_seed.sql:
--     psql -d tata_steel -f db/04_restore.sql
--
-- Restores from tata_road_backup (written by db/03_backup.sql). Remaps each
-- weekly row onto the NEW activity_id by matching (road_no, sheet_row_order),
-- because activity_id is reassigned by the reseed.
--
-- Anything that cannot be remapped is NOT silently dropped -- it is left in
-- tata_road_backup.unrestored_weekly_entry and reported at the end. That
-- happens when the source sheet changed shape between the backup and the
-- reseed (a row inserted, removed, or reordered), which shifts
-- sheet_row_order. Those rows need a human decision, not a guess.
--
-- Does nothing (and says so) if no backup exists.
-- ============================================================================
SET search_path TO tata_road;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = 'tata_road_backup'
                     AND table_name = 'weekly_entry')
    THEN
        RAISE NOTICE 'no backup found (tata_road_backup.weekly_entry missing) - nothing to restore. If you expected data here, db/03_backup.sql was never run.';
    END IF;
END $$;

\echo ''
\echo '=== backup being restored ==='
SELECT * FROM tata_road_backup.backup_meta;

-- ---------------------------------------------------------------------------
-- Weekly actuals. ON CONFLICT DO NOTHING so a restore never overwrites a
-- value that already exists post-rebuild.
-- ---------------------------------------------------------------------------
INSERT INTO tata_road.weekly_entry
    (activity_id, week_start_date, week_end_date,
     planned_qty_override, actual_qty, entered_at, note)
SELECT a.activity_id, b.week_start_date, b.week_end_date,
       b.planned_qty_override, b.actual_qty, b.entered_at, b.note
FROM tata_road_backup.weekly_entry b
JOIN tata_road.activity a
  ON a.road_no = b.road_no
 AND a.sheet_row_order = b.sheet_row_order
ON CONFLICT (activity_id, week_start_date) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Rows that found no matching activity after the reseed. Kept, not discarded.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS tata_road_backup.unrestored_weekly_entry;
CREATE TABLE tata_road_backup.unrestored_weekly_entry AS
SELECT b.*,
       'no activity matched (road_no, sheet_row_order) after reseed - source sheet row order likely changed'
           AS reason
FROM tata_road_backup.weekly_entry b
WHERE NOT EXISTS (
    SELECT 1 FROM tata_road.activity a
    WHERE a.road_no = b.road_no AND a.sheet_row_order = b.sheet_row_order
);

-- ---------------------------------------------------------------------------
-- Rows that matched by position but whose activity NAME changed. These DID
-- restore (position is the key), but a changed name means the sheet was
-- edited, so the attachment deserves a human check.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS tata_road_backup.suspect_remap;
CREATE TABLE tata_road_backup.suspect_remap AS
SELECT DISTINCT b.road_no, b.sheet_row_order,
       b.raw_name AS name_at_backup,
       a.raw_name AS name_now
FROM tata_road_backup.weekly_entry b
JOIN tata_road.activity a
  ON a.road_no = b.road_no AND a.sheet_row_order = b.sheet_row_order
WHERE a.raw_name IS DISTINCT FROM b.raw_name;

-- ---------------------------------------------------------------------------
-- Edit log. Historical record only -- restoring it does NOT re-apply the
-- edits to road/activity, because the reseed deliberately reset those rows to
-- what the source workbook says. Re-applying automatically would silently
-- undo the rebuild. Use the report below to decide what to re-enter.
-- ---------------------------------------------------------------------------
INSERT INTO tata_road.edit_log
    (table_name, row_pk, field, old_value, new_value,
     source_override, edited_at)
SELECT table_name, row_pk, field, old_value, new_value,
       source_override, edited_at
FROM tata_road_backup.edit_log;

\echo ''
\echo '=== restore result ==='
SELECT (SELECT count(*) FROM tata_road.weekly_entry)                      AS weekly_rows_now,
       (SELECT count(*) FROM tata_road_backup.weekly_entry)               AS weekly_rows_in_backup,
       (SELECT count(*) FROM tata_road_backup.unrestored_weekly_entry)    AS weekly_rows_NOT_restored,
       (SELECT count(*) FROM tata_road_backup.suspect_remap)              AS positions_whose_name_changed,
       (SELECT count(*) FROM tata_road.edit_log)                          AS edit_log_rows_now;

\echo ''
\echo '=== weekly rows that could NOT be restored (need a manual decision) ==='
SELECT road_no, sheet_row_order, raw_name, week_start_date, actual_qty
FROM tata_road_backup.unrestored_weekly_entry
ORDER BY road_no, sheet_row_order, week_start_date;

\echo ''
\echo '=== positions that restored but whose activity name changed (verify) ==='
SELECT * FROM tata_road_backup.suspect_remap ORDER BY road_no, sheet_row_order;

\echo ''
\echo '=== master-data edits recorded BEFORE the rebuild (NOT re-applied) ==='
\echo '    The reseed reset road/activity to the source workbook. Re-enter'
\echo '    anything here that is still wanted.'
SELECT table_name, row_pk, field, old_value, new_value, source_override, edited_at
FROM tata_road.edit_log
ORDER BY edited_at;

\echo ''
\echo '=== restore complete ==='
