-- Live-change feed: one tiny "last changed" marker per watched table.
-- Idempotent - safe to run on every build (CREATE ... IF NOT EXISTS / OR
-- REPLACE, triggers dropped and re-created, marker rows seeded ON CONFLICT
-- DO NOTHING). It never touches data in any watched table.
--
-- The browser polls GET /api/db/live/changes (cheap: reads a handful of
-- marker rows) and reloads only the tabs whose table's change_seq moved.
-- The trigger runs in the writer's own transaction, so a bump becomes visible
-- at COMMIT and vanishes on ROLLBACK - no clock, no look-back window.
--
-- The watched list below MUST equal LIVE_FEED_TABLES in
-- dashboard/live_feed.py (tests/test_live_feed.py enforces it).
-- Entry form: 'table'  or  'table!colA,colB' (routine-churn columns that must
-- NOT fire the trigger; none needed today - the form is supported for later).
--
-- There is no audit trigger in this schema (edit_log is written by the app
-- itself), so no exclusion is needed; live_change_marker is simply never in
-- the watched list. Never watch credential/secret tables.
--
-- Write-order rule: concurrent writers to one table queue on its marker row
-- until commit, and a transaction touching several watched tables in
-- opposite orders can deadlock (40P01). Keep write transactions short and
-- touch tables in one consistent order (road -> activity -> edit_log -> road_issue).

CREATE SCHEMA IF NOT EXISTS tata_road;
SET search_path TO tata_road;

CREATE SEQUENCE IF NOT EXISTS live_change_seq;

CREATE TABLE IF NOT EXISTS live_change_marker (
    table_name  TEXT PRIMARY KEY,
    change_seq  BIGINT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SECURITY DEFINER + pinned search_path: the app's writer role may lack USAGE
-- on the sequence / write access to the marker table.
CREATE OR REPLACE FUNCTION fn_live_change_marker() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = tata_road, pg_temp AS $$
BEGIN
    INSERT INTO live_change_marker (table_name, change_seq, changed_at)
    VALUES (TG_TABLE_NAME, nextval('live_change_seq'), now())
    ON CONFLICT (table_name) DO UPDATE
        SET change_seq = EXCLUDED.change_seq, changed_at = EXCLUDED.changed_at;
    RETURN NULL;
END;
$$;

DO $$
DECLARE
    watched   text[] := ARRAY[
        'road', 'activity', 'weekly_entry', 'data_issue', 'edit_log', 'manual_issue', 'road_issue'
    ];
    entry     text;
    tbl       text;
    skip_cols text;
    watch_cols text;
    r         record;
BEGIN
    FOREACH entry IN ARRAY watched LOOP
        tbl       := split_part(entry, '!', 1);
        skip_cols := nullif(split_part(entry, '!', 2), '');

        EXECUTE format('DROP TRIGGER IF EXISTS trg_live_change_marker ON %I', tbl);
        IF skip_cols IS NULL THEN
            EXECUTE format(
                'CREATE TRIGGER trg_live_change_marker AFTER INSERT OR UPDATE OR DELETE '
                'ON %I FOR EACH STATEMENT EXECUTE FUNCTION fn_live_change_marker()', tbl);
        ELSE
            -- fire on UPDATE only when a NON-churn column is in the SET list
            SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
              INTO watch_cols
              FROM pg_attribute
             WHERE attrelid = format('%I', tbl)::regclass
               AND attnum > 0 AND NOT attisdropped
               AND attname <> ALL (string_to_array(skip_cols, ','));
            EXECUTE format(
                'CREATE TRIGGER trg_live_change_marker AFTER INSERT OR DELETE OR UPDATE OF %s '
                'ON %I FOR EACH STATEMENT EXECUTE FUNCTION fn_live_change_marker()', watch_cols, tbl);
        END IF;

        INSERT INTO live_change_marker (table_name, change_seq)
        VALUES (tbl, nextval('live_change_seq'))
        ON CONFLICT (table_name) DO NOTHING;
    END LOOP;

    -- tables removed from the list: drop their trigger and marker row
    FOR r IN
        SELECT c.relname
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname = 'trg_live_change_marker' AND n.nspname = 'tata_road'
           AND NOT t.tgisinternal
    LOOP
        IF NOT EXISTS (SELECT 1 FROM unnest(watched) w WHERE split_part(w, '!', 1) = r.relname) THEN
            EXECUTE format('DROP TRIGGER trg_live_change_marker ON %I', r.relname);
            DELETE FROM live_change_marker WHERE table_name = r.relname;
        END IF;
    END LOOP;
END
$$;
