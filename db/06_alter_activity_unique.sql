-- ALTER-only fix for databases created before activity had UNIQUE (road_no,
-- raw_name). The incremental seed (ON CONFLICT (road_no, raw_name)) needs it.
-- No DROP / TRUNCATE / data change. Idempotent: does nothing if the
-- constraint already exists, and aborts (changing nothing) if duplicate
-- (road_no, raw_name) pairs exist - those need a decision first.
SET search_path TO tata_road;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'tata_road.activity'::regclass AND contype = 'u'
           AND pg_get_constraintdef(oid) = 'UNIQUE (road_no, raw_name)'
    ) THEN
        RAISE NOTICE 'activity UNIQUE (road_no, raw_name) already present - skipping';
        RETURN;
    END IF;

    IF EXISTS (SELECT 1 FROM activity GROUP BY road_no, raw_name HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'duplicate (road_no, raw_name) rows in activity - resolve before adding the constraint';
    END IF;

    ALTER TABLE activity ADD CONSTRAINT activity_road_no_raw_name_key UNIQUE (road_no, raw_name);
END
$$;
