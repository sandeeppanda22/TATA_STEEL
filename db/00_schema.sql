-- Active: 1781338529178@@127.0.0.1@5432@tata_steel@public
-- Tata Steel Road Programme — Phase 1+ schema
-- Incremental DDL: idempotent, safe to re-run any time. CREATE SCHEMA/TABLE/
-- INDEX all use IF NOT EXISTS, so re-running this file never drops or touches
-- existing rows — it only creates whatever is still missing (first run, or a
-- new table added later). Still no ALTER statements: a genuine column/type
-- change requires a deliberate addition here plus a user ruling, never a
-- silent migration. See docs/01_business_rules.md for the rules this schema
-- implements (BR-01..BR-16).

CREATE SCHEMA IF NOT EXISTS tata_road;
SET search_path TO tata_road;

-- ---------------------------------------------------------------------------
-- ROAD  (BR-01: Phase-Summary is absolute truth for every column here)
-- ---------------------------------------------------------------------------
-- source_file: BR-13, which workbook this came from. A road added through
-- the dashboard's "Add Road" form (not the Excel) is stamped 'manual-entry'
-- so it still satisfies NOT NULL and is clearly distinguishable in the UI.
CREATE TABLE IF NOT EXISTS road (
    road_no           INT PRIMARY KEY,          -- source road number, not a surrogate key
    phase             INT NOT NULL DEFAULT 1,    -- BR-12: more phases/roads added later
    scheme_sl_no       TEXT,
    location          TEXT NOT NULL,
    line_manager      TEXT,
    vendor            TEXT,
    road_type         TEXT,
    length_m          NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (length_m >= 0),
    width_m           NUMERIC(10,2),
    drain_planned_m   NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (drain_planned_m >= 0),  -- BR-09
    cost_cr           NUMERIC(12,3),
    start_date        DATE,                     -- BR-01: from Phase-Summary
    end_date          DATE,                     -- BR-01: from Phase-Summary
    source_file       TEXT NOT NULL,            -- BR-13: which workbook this came from
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ACTIVITY  (BR-02: raw_name is verbatim from the road sheet, never invented.
--            BR-03: seq_no = position in the canonical sequence, for roads
--            that have that step. BR-04/05: major + weight_pct.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity (
    activity_id        BIGSERIAL PRIMARY KEY,
    road_no            INT NOT NULL REFERENCES road(road_no) ON DELETE CASCADE,
    seq_no             INT,                      -- position in BR-03 sequence (1-17), NULL if not in it
    sheet_row_order    INT NOT NULL,             -- fallback display order = row order in source sheet
    raw_name           TEXT NOT NULL,            -- BR-02: exact text from the sheet, never changed
    major              TEXT CHECK (major IN ('Excavation','GSB','WMM','DLC','Road')),
    weight_pct         NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (weight_pct BETWEEN 0 AND 100),
    unit               TEXT,
    planned_qty        NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK (planned_qty >= 0),
    start_date         DATE,
    end_date           DATE,
    is_drain           BOOLEAN NOT NULL DEFAULT FALSE,   -- BR-09: the RCC Drain row for this road
    from_summary_only  BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE only for a drain row synthesized
                                                           -- from Phase-Summary because the road's
                                                           -- own sheet had no RCC Drain row (BR-09)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Lets the seed re-run be incremental: ON CONFLICT (road_no, raw_name) DO
    -- NOTHING means a road already in the DB keeps its existing activities
    -- untouched, and only genuinely new activity rows get inserted.
    UNIQUE (road_no, raw_name)
);
CREATE INDEX IF NOT EXISTS idx_activity_road ON activity(road_no);

-- ---------------------------------------------------------------------------
-- WEEKLY_ENTRY  (the data-entry engine. OPEN-01: quantity entered per week
--   in the activity's own unit, % computed as cumulative/planned_qty.
--   OPEN-02: planned_qty_override lets a week's auto-spread planned figure
--   be replaced manually; NULL means "use the straight-line default".
--   OPEN-06: week runs Monday..Sunday, keyed by week_start_date.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_entry (
    entry_id              BIGSERIAL PRIMARY KEY,
    activity_id           BIGINT NOT NULL REFERENCES activity(activity_id) ON DELETE CASCADE,
    week_start_date       DATE NOT NULL,          -- Monday
    week_end_date         DATE NOT NULL,          -- Sunday
    planned_qty_override  NUMERIC(14,3),          -- NULL = use straight-line default (OPEN-02)
    actual_qty             NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK (actual_qty >= 0),  -- this week's qty
    entered_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    note                  TEXT,
    UNIQUE (activity_id, week_start_date),
    CHECK (week_end_date = week_start_date + 6),
    CHECK (EXTRACT(ISODOW FROM week_start_date) = 1)   -- must be a Monday
);
CREATE INDEX IF NOT EXISTS idx_weekly_entry_activity ON weekly_entry(activity_id);
CREATE INDEX IF NOT EXISTS idx_weekly_entry_week ON weekly_entry(week_start_date);

-- ---------------------------------------------------------------------------
-- DATA_ISSUE  (every flag the builder raises - BR-11, sheet-vs-summary
--   mismatches, etc. Stored so historical issues survive a dashboard refresh
--   and are auditable, not just printed to a console.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_issue (
    issue_id      BIGSERIAL PRIMARY KEY,
    road_no       INT,                            -- nullable: some issues are workbook-level
    activity_name TEXT,
    field         TEXT,
    value         TEXT,
    issue         TEXT NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Dedup key for incremental reseeds: COALESCE collapses NULLs so repeated
-- re-runs of gen_seed.py (ON CONFLICT DO NOTHING against this same
-- expression list) don't pile up duplicate issue rows for an unchanged road.
CREATE UNIQUE INDEX IF NOT EXISTS idx_data_issue_dedup ON data_issue (
    COALESCE(road_no, -1), COALESCE(activity_name, ''), COALESCE(field, ''),
    COALESCE(value, ''), issue
);

-- ---------------------------------------------------------------------------
-- EDIT_LOG  (every manual change to seeded master data, recorded field by
--   field. Needed because road/activity rows are seeded from the source
--   workbook: once a value can be edited in the UI, the DB must be able to
--   answer "is this still what the sheet said, and if not, who changed it".
--   Written by the API on every PUT; never written by the seed.
--
--   source_override = TRUE marks an edit to a field BR-01 (Phase-Summary is
--   absolute truth) or BR-02 (activity names verbatim) says should not be
--   edited. Such edits are permitted but are deliberately flagged here, so
--   divergence from the source workbook is always visible rather than silent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS edit_log (
    edit_id         BIGSERIAL PRIMARY KEY,
    table_name      TEXT NOT NULL CHECK (table_name IN ('road','activity')),
    row_pk          BIGINT NOT NULL,           -- road.road_no or activity.activity_id
    field           TEXT NOT NULL,
    old_value       TEXT,                      -- TEXT so one table covers every column type
    new_value       TEXT,
    source_override BOOLEAN NOT NULL DEFAULT FALSE,
    edited_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_edit_log_row ON edit_log(table_name, row_pk);

-- ---------------------------------------------------------------------------
-- MANUAL_ISSUE  (hand-curated bottleneck/issue records, distinct from the
--   auto-derived bottlenecks in dashboard/calculations.py::bottlenecks().
--   This is the parity feature for the legacy app's bottleneck.csv/
--   post_bottleneck add/status/remarks/delete flow: a site reviewer can log
--   an issue the automatic rule doesn't catch (e.g. a vendor mobilisation
--   delay) and track it to closure.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manual_issue (
    issue_id    BIGSERIAL PRIMARY KEY,
    road_no     INT REFERENCES road(road_no) ON DELETE SET NULL,
    issue       TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'Weekly review',
    status      TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open', 'In progress', 'Closed')),
    remarks     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_manual_issue_road ON manual_issue(road_no);

-- NOTE on incremental rebuilds: there are deliberately no ALTER statements
-- above. Re-running this file (./rebuild.sh) only creates whatever is still
-- missing; existing rows in road/activity/weekly_entry/edit_log are never
-- touched. scripts/gen_seed.py pairs with this via ON CONFLICT DO NOTHING on
-- road(road_no), activity(road_no, raw_name), and the data_issue dedup index
-- above, so re-running the pipeline after the source workbook changes adds
-- only the new roads/activities/issues — it does not duplicate or overwrite
-- what is already there, including anything added through the UI "Add Road"
-- form (POST /api/db/roads) or any weekly actuals/edits already entered.

-- NOTE on gating (BR-08): percentage gating ("an activity's actual % cannot
-- exceed the previous present activity's actual %") compares across rows
-- within a road's seq_no order. That is enforced in the API layer at write
-- time, not as a CHECK constraint, since Postgres CHECK cannot reference
-- other rows. See api layer for the gating function.
--
-- NOTE on CPM (BR-14/OPEN-03): critical path uses each activity's own
-- start_date/end_date as-is (overlaps allowed, per ruling). CPM is computed
-- in the API layer from these dates, not stored as a separate graph table.
