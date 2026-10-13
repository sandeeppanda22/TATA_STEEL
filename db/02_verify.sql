-- ============================================================================
-- db/02_verify.sql — post-load verification. READ-ONLY, changes nothing.
--
-- Run AFTER 00_schema.sql and 01_seed.sql:
--     psql -d tata_steel -f db/02_verify.sql
--
-- Every check prints a row. "OK" in the status column means the invariant
-- holds. Anything else is a real finding to act on -- these are not
-- cosmetic. Nothing here is auto-corrected.
-- ============================================================================
SET search_path TO tata_road;

\echo ''
\echo '=== 1. Row counts (expect 17 roads / 177 activities / 10 issues) ==='
SELECT 'road'          AS table_name, count(*) AS rows FROM road
UNION ALL SELECT 'activity',     count(*) FROM activity
UNION ALL SELECT 'weekly_entry', count(*) FROM weekly_entry
UNION ALL SELECT 'data_issue',   count(*) FROM data_issue
ORDER BY table_name;

\echo ''
\echo '=== 2. Phase-Summary totals (BR-01: summary is absolute truth) ==='
SELECT count(*)                      AS roads,
       sum(length_m)                 AS total_length_m,
       sum(drain_planned_m)          AS total_drain_m,
       sum(cost_cr)                  AS total_cost_cr
FROM road;

\echo ''
\echo '=== 3. BR-05: per-road weights must total 100 ==='
\echo '    (any row returned here is a DEFECT -- progress % will be wrong)'
SELECT r.road_no,
       r.location,
       sum(a.weight_pct) AS weight_total,
       CASE WHEN abs(sum(a.weight_pct) - 100) < 0.01
            THEN 'OK' ELSE 'DEFECT' END AS status
FROM road r
JOIN activity a ON a.road_no = r.road_no
GROUP BY r.road_no, r.location
HAVING abs(sum(a.weight_pct) - 100) >= 0.01
ORDER BY r.road_no;

\echo ''
\echo '=== 3b. Root cause check: >1 activity sharing the same major ==='
\echo '    (each duplicate receives the FULL major weight -> over-100 totals)'
SELECT road_no,
       major,
       count(*)                      AS activity_rows,
       sum(weight_pct)               AS summed_weight,
       string_agg(raw_name, ' | ' ORDER BY sheet_row_order) AS raw_names
FROM activity
WHERE major IS NOT NULL
GROUP BY road_no, major
HAVING count(*) > 1
ORDER BY road_no, major;

\echo ''
\echo '=== 4. BR-04: major coverage per road ==='
SELECT r.road_no,
       r.road_type,
       count(*) FILTER (WHERE a.major = 'Excavation') AS excav,
       count(*) FILTER (WHERE a.major = 'GSB')        AS gsb,
       count(*) FILTER (WHERE a.major = 'WMM')        AS wmm,
       count(*) FILTER (WHERE a.major = 'DLC')        AS dlc,
       count(*) FILTER (WHERE a.major = 'Road')       AS road_major,
       count(*) FILTER (WHERE a.major IS NULL)        AS unweighted
FROM road r
LEFT JOIN activity a ON a.road_no = r.road_no
GROUP BY r.road_no, r.road_type
ORDER BY r.road_no;

\echo ''
\echo '=== 5. BR-09: drain rows (RMT, tracked separately, excluded from %) ==='
SELECT a.road_no,
       r.drain_planned_m           AS summary_drain_m,
       a.planned_qty               AS activity_qty,
       a.unit,
       a.from_summary_only         AS synthesized,
       CASE WHEN abs(a.planned_qty - r.drain_planned_m) < 0.01
            THEN 'matches summary' ELSE 'DIFFERS from summary' END AS status
FROM activity a
JOIN road r ON r.road_no = a.road_no
WHERE a.is_drain
ORDER BY a.road_no;

\echo ''
\echo '=== 5b. Roads with a summary drain target but NO drain activity ==='
\echo '    (any row here means drain progress is untrackable for that road)'
SELECT r.road_no, r.location, r.drain_planned_m
FROM road r
WHERE r.drain_planned_m > 0
  AND NOT EXISTS (SELECT 1 FROM activity a
                  WHERE a.road_no = r.road_no AND a.is_drain)
ORDER BY r.road_no;

\echo ''
\echo '=== 6. Date sanity (observations, not blockers) ==='
SELECT 'activity end before start' AS finding, count(*) AS rows
FROM activity WHERE start_date IS NOT NULL AND end_date IS NOT NULL
  AND end_date < start_date
UNION ALL
SELECT 'activity missing a date', count(*)
FROM activity WHERE start_date IS NULL OR end_date IS NULL
UNION ALL
SELECT 'activity outside its road window', count(*)
FROM activity a JOIN road r ON r.road_no = a.road_no
WHERE a.start_date IS NOT NULL AND r.start_date IS NOT NULL
  AND (a.start_date < r.start_date OR a.end_date > r.end_date)
UNION ALL
SELECT 'road end before start', count(*)
FROM road WHERE start_date IS NOT NULL AND end_date IS NOT NULL
  AND end_date < start_date;

\echo ''
\echo '=== 7. BR-11 recorded data issues (carried from the source sheet) ==='
SELECT road_no, activity_name, field, value, issue
FROM data_issue
ORDER BY road_no NULLS LAST, activity_name, field;

\echo ''
\echo '=== 8. Referential + constraint integrity ==='
SELECT 'orphan activity (no road)' AS check_name,
       count(*) AS violations,
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'DEFECT' END AS status
FROM activity a WHERE NOT EXISTS (SELECT 1 FROM road r WHERE r.road_no = a.road_no)
UNION ALL
SELECT 'orphan weekly_entry (no activity)', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'DEFECT' END
FROM weekly_entry w
WHERE NOT EXISTS (SELECT 1 FROM activity a WHERE a.activity_id = w.activity_id)
UNION ALL
SELECT 'weekly_entry not Monday-start (OPEN-06)', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'DEFECT' END
FROM weekly_entry WHERE EXTRACT(ISODOW FROM week_start_date) <> 1
UNION ALL
SELECT 'weekly_entry week not exactly 7 days', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'DEFECT' END
FROM weekly_entry WHERE week_end_date <> week_start_date + 6
UNION ALL
SELECT 'road with zero activities', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'REVIEW' END
FROM road r WHERE NOT EXISTS (SELECT 1 FROM activity a WHERE a.road_no = r.road_no)
UNION ALL
SELECT 'actual_qty exceeding planned_qty', count(*),
       CASE WHEN count(*) = 0 THEN 'OK' ELSE 'REVIEW' END
FROM (SELECT a.activity_id, a.planned_qty, sum(w.actual_qty) AS done
      FROM activity a JOIN weekly_entry w ON w.activity_id = a.activity_id
      GROUP BY a.activity_id, a.planned_qty) t
WHERE t.planned_qty > 0 AND t.done > t.planned_qty;

\echo ''
\echo '=== 9. Progress today (BR-06 road %, BR-07 overall %) ==='
\echo '    Reads 0.00 until real actuals are entered -- weekly_entry is'
\echo '    seeded EMPTY on purpose (no fake data).'
WITH act AS (
    SELECT a.road_no, a.weight_pct, a.planned_qty,
           COALESCE((SELECT sum(w.actual_qty) FROM weekly_entry w
                     WHERE w.activity_id = a.activity_id), 0) AS done
    FROM activity a
    WHERE a.major IS NOT NULL AND NOT a.is_drain
), road_pct AS (
    SELECT road_no,
           sum(CASE WHEN planned_qty > 0
                    THEN LEAST(done / planned_qty, 1) * weight_pct
                    ELSE 0 END) AS pct
    FROM act GROUP BY road_no
)
SELECT r.road_no, r.location, r.length_m,
       ROUND(COALESCE(p.pct, 0), 2) AS road_pct
FROM road r LEFT JOIN road_pct p ON p.road_no = r.road_no
ORDER BY r.road_no;

WITH act AS (
    SELECT a.road_no, a.weight_pct, a.planned_qty,
           COALESCE((SELECT sum(w.actual_qty) FROM weekly_entry w
                     WHERE w.activity_id = a.activity_id), 0) AS done
    FROM activity a
    WHERE a.major IS NOT NULL AND NOT a.is_drain
), road_pct AS (
    SELECT road_no,
           sum(CASE WHEN planned_qty > 0
                    THEN LEAST(done / planned_qty, 1) * weight_pct
                    ELSE 0 END) AS pct
    FROM act GROUP BY road_no
)
SELECT ROUND(sum(r.length_m * COALESCE(p.pct, 0)) / NULLIF(sum(r.length_m), 0), 2)
           AS overall_pct_length_weighted
FROM road r LEFT JOIN road_pct p ON p.road_no = r.road_no;

\echo ''
\echo '=== 10. Drain progress (BR-09, RMT, separate from road %) ==='
SELECT ROUND(sum(COALESCE(d.done, 0)), 2) AS drain_done_m,
       sum(r.drain_planned_m)             AS drain_planned_m,
       ROUND(100 * sum(COALESCE(d.done, 0))
             / NULLIF(sum(r.drain_planned_m), 0), 2) AS drain_pct
FROM road r
LEFT JOIN (
    SELECT a.road_no, sum(w.actual_qty) AS done
    FROM activity a JOIN weekly_entry w ON w.activity_id = a.activity_id
    WHERE a.is_drain GROUP BY a.road_no
) d ON d.road_no = r.road_no;

\echo ''
\echo '=== verification complete ==='
