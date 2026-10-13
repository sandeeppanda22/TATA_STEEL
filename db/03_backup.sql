-- ============================================================================
-- db/03_backup.sql — save entered data before a hard rebuild.
--
-- WHY THIS EXISTS: 00_schema.sql starts with DROP SCHEMA tata_road CASCADE.
-- Everything a user typed in -- weekly actuals and master-data edits -- lives
-- in that schema and would be destroyed. The seed only restores what came
-- from the Excel; it cannot restore entered data, because entered data was
-- never in the Excel.
--
-- Backups go into a SEPARATE schema (tata_road_backup) which the rebuild's
-- DROP does not touch.
--
-- Rebuild sequence, in order:
--     psql -d tata_steel -f db/03_backup.sql     <- run FIRST
--     psql -d tata_steel -f db/00_schema.sql
--     psql -d tata_steel -f db/01_seed.sql
--     psql -d tata_steel -f db/04_restore.sql    <- run LAST
--     psql -d tata_steel -f db/02_verify.sql
--
-- Safe to run on a fresh database where tata_road does not exist yet, and
-- safe to run repeatedly -- but each run REPLACES the previous backup, so do
-- not run it twice expecting two generations.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tata_road_backup;

-- The backup tables are created unconditionally with a fixed shape, so the
-- rest of this script (and 04_restore.sql) can rely on them existing even on
-- a fresh database where there is nothing to copy.
--
-- Weekly actuals are keyed by a STABLE natural key, not activity_id:
-- activity_id is BIGSERIAL and gets reassigned by every reseed, so storing it
-- would make the restore attach rows to the wrong activities.
-- (road_no, sheet_row_order) comes from the source sheet's row order and is
-- reproduced identically by each reseed.
DROP TABLE IF EXISTS tata_road_backup.weekly_entry;
CREATE TABLE tata_road_backup.weekly_entry (
    road_no               INT,
    sheet_row_order       INT,
    raw_name              TEXT,          -- kept so the restore can cross-check the remap
    week_start_date       DATE,
    week_end_date         DATE,
    planned_qty_override  NUMERIC(14,3),
    actual_qty            NUMERIC(14,3),
    entered_at            TIMESTAMPTZ,
    note                  TEXT
);

DROP TABLE IF EXISTS tata_road_backup.edit_log;
CREATE TABLE tata_road_backup.edit_log (
    edit_id         BIGINT,
    table_name      TEXT,
    row_pk          BIGINT,
    field           TEXT,
    old_value       TEXT,
    new_value       TEXT,
    source_override BOOLEAN,
    edited_at       TIMESTAMPTZ
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'tata_road' AND table_name = 'weekly_entry')
    THEN
        INSERT INTO tata_road_backup.weekly_entry
        SELECT a.road_no, a.sheet_row_order, a.raw_name,
               w.week_start_date, w.week_end_date,
               w.planned_qty_override, w.actual_qty, w.entered_at, w.note
        FROM tata_road.weekly_entry w
        JOIN tata_road.activity a ON a.activity_id = w.activity_id;
        RAISE NOTICE 'backed up % weekly_entry row(s)',
            (SELECT count(*) FROM tata_road_backup.weekly_entry);
    ELSE
        RAISE NOTICE 'tata_road.weekly_entry not present - nothing to back up (fresh database)';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'tata_road' AND table_name = 'edit_log')
    THEN
        INSERT INTO tata_road_backup.edit_log
        SELECT edit_id, table_name, row_pk, field, old_value, new_value,
               source_override, edited_at
        FROM tata_road.edit_log;
        RAISE NOTICE 'backed up % edit_log row(s)',
            (SELECT count(*) FROM tata_road_backup.edit_log);
    ELSE
        RAISE NOTICE 'tata_road.edit_log not present - nothing to back up';
    END IF;
END $$;

-- Record when this backup was taken, so 04_restore.sql reports the age of
-- what it is restoring instead of restoring blind.
DROP TABLE IF EXISTS tata_road_backup.backup_meta;
CREATE TABLE tata_road_backup.backup_meta AS
SELECT now() AS taken_at,
       (SELECT count(*) FROM tata_road_backup.weekly_entry) AS weekly_entry_rows,
       (SELECT count(*) FROM tata_road_backup.edit_log)     AS edit_log_rows;

SELECT * FROM tata_road_backup.backup_meta;
