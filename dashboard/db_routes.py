"""API routes backed by the PostgreSQL tata_road schema (not the static
dataset.json). These are the "live" endpoints: weekly data entry, computed
progress, vendor/LM performance, bottlenecks, milestones, CPM.

Requires db/rebuild.sh to have been run against a reachable Postgres
(local PGHOST/... or DATABASE_URL) - see dashboard/db.py.
"""
import csv
import datetime
import decimal
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from dashboard import calculations as calc
from dashboard import db

router = APIRouter(prefix="/api/db", tags=["db"])


# ---------------------------------------------------------------------------
# Roads / activities
# ---------------------------------------------------------------------------
@router.get("/roads")
def list_roads():
    roads = db.query(
        "SELECT road_no, location, line_manager, vendor, road_type, length_m, "
        "width_m, drain_planned_m, cost_cr, start_date, end_date, source_file "
        "FROM road ORDER BY road_no"
    )
    out = []
    for r in roads:
        pct, drain_pct = calc.road_progress(r["road_no"])
        out.append({**_jsonify(r), "progress_pct": pct, "drain_progress_pct": drain_pct,
                    "planned_pct": calc.road_planned_progress(r["road_no"])})
    return out


@router.get("/roads/{road_no}")
def road_detail(road_no: int):
    road = db.query("SELECT * FROM road WHERE road_no = %s", (road_no,))
    if not road:
        raise HTTPException(404, f"Road {road_no} not found")
    acts = db.query(
        """
        SELECT a.activity_id, a.seq_no, a.sheet_row_order, a.raw_name, a.major,
               a.weight_pct, a.unit, a.planned_qty, a.start_date, a.end_date,
               a.is_drain, a.from_summary_only,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        WHERE a.road_no = %s
        GROUP BY a.activity_id, a.seq_no, a.sheet_row_order, a.raw_name, a.major,
                 a.weight_pct, a.unit, a.planned_qty, a.start_date, a.end_date,
                 a.is_drain, a.from_summary_only
        ORDER BY a.sheet_row_order
        """,
        (road_no,),
    )
    pct, drain_pct = calc.road_progress(road_no)
    acts_out = []
    for a in acts:
        a = _jsonify(a)
        a["pct"] = round(float(a["cum_qty"]) / float(a["planned_qty"]) * 100, 2) if float(a["planned_qty"]) else 0
        acts_out.append(a)
    return {"road": _jsonify(road[0]), "progress_pct": pct, "drain_progress_pct": drain_pct,
            "activities": acts_out}


class ActivityCreate(BaseModel):
    raw_name: str
    major: str | None = None
    weight_pct: float = 0
    unit: str | None = None
    planned_qty: float = 0
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    seq_no: int | None = None


class RoadCreate(BaseModel):
    road_no: int
    scheme_sl_no: str | None = None
    location: str
    line_manager: str | None = None
    vendor: str | None = None
    road_type: str | None = None
    length_m: float = 0
    width_m: float | None = None
    drain_planned_m: float = 0
    cost_cr: float | None = None
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    phase: int = 1
    activities: list[ActivityCreate] = []


@router.post("/roads", status_code=201)
def create_road(payload: RoadCreate):
    """Add a new road straight into the dashboard, with its activities (and
    their own start/end dates), without touching Source/*.xlsx or running a
    rebuild. source_file is stamped 'manual-entry' so it is clearly
    distinguishable from a workbook-seeded road; because rebuilds are now
    incremental (ON CONFLICT DO NOTHING on road_no), a road added here is
    never erased by a later ./rebuild.sh.
    """
    if db.query("SELECT road_no FROM road WHERE road_no = %s", (payload.road_no,)):
        raise HTTPException(
            409, f"Road {payload.road_no} already exists. Road numbers must be "
                 "unique - pick an unused number, or edit the existing road instead."
        )

    if payload.length_m < 0:
        raise HTTPException(422, "length_m cannot be negative")
    if payload.drain_planned_m < 0:
        raise HTTPException(422, "drain_planned_m cannot be negative")
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(422, f"end_date ({payload.end_date}) is before start_date ({payload.start_date})")

    if not payload.activities:
        raise HTTPException(
            422, "Add at least one activity (with a start date and end date) so "
                 "this road has something to track weekly progress against."
        )

    names_seen = set()
    weight_total = 0.0
    for a in payload.activities:
        if not a.raw_name.strip():
            raise HTTPException(422, "Every activity needs a name")
        if a.raw_name in names_seen:
            raise HTTPException(422, f"Duplicate activity name on this road: {a.raw_name!r}")
        names_seen.add(a.raw_name)
        if a.major is not None and a.major not in VALID_MAJORS:
            raise HTTPException(422, f"major must be one of {sorted(VALID_MAJORS)} or null; got {a.major!r}")
        if not 0 <= a.weight_pct <= 100:
            raise HTTPException(422, f"activity {a.raw_name!r}: weight_pct must be between 0 and 100")
        if a.planned_qty < 0:
            raise HTTPException(422, f"activity {a.raw_name!r}: planned_qty cannot be negative")
        if a.start_date and a.end_date and a.end_date < a.start_date:
            raise HTTPException(422, f"activity {a.raw_name!r}: end_date is before start_date")
        weight_total += a.weight_pct

    # BR-05: a road's activity weights must total 100, same rule as on edits.
    if abs(weight_total - 100.0) > 0.01:
        raise HTTPException(
            422, f"Activity weights must total 100 (got {round(weight_total, 2)}). "
                 "Adjust the weight percentages before saving."
        )

    # One transaction: either the road and every activity land together, or
    # none of it does (a half-created road with no activities is worse than
    # no road at all).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO road (road_no, phase, scheme_sl_no, location, line_manager,
                    vendor, road_type, length_m, width_m, drain_planned_m, cost_cr,
                    start_date, end_date, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'manual-entry')
                """,
                (payload.road_no, payload.phase, payload.scheme_sl_no, payload.location,
                 payload.line_manager, payload.vendor, payload.road_type, payload.length_m,
                 payload.width_m, payload.drain_planned_m, payload.cost_cr,
                 payload.start_date, payload.end_date),
            )
            for i, a in enumerate(payload.activities, start=1):
                cur.execute(
                    """
                    INSERT INTO activity (road_no, seq_no, sheet_row_order, raw_name, major,
                        weight_pct, unit, planned_qty, start_date, end_date, is_drain,
                        from_summary_only)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                    """,
                    (payload.road_no, a.seq_no, i, a.raw_name, a.major, a.weight_pct,
                     a.unit, a.planned_qty, a.start_date, a.end_date,
                     a.raw_name.strip() == "RCC Drain"),
                )
        conn.commit()

    return road_detail(payload.road_no)


@router.get("/activities/{activity_id}/weekly")
def activity_weekly(activity_id: int):
    act = db.query("SELECT * FROM activity WHERE activity_id = %s", (activity_id,))
    if not act:
        raise HTTPException(404, "Activity not found")
    a = act[0]
    schedule = calc.straight_line_weeks(a["start_date"], a["end_date"], float(a["planned_qty"]))
    entries = {
        e["week_start_date"]: e
        for e in db.query(
            "SELECT * FROM weekly_entry WHERE activity_id = %s ORDER BY week_start_date",
            (activity_id,),
        )
    }
    # Merge the nominal schedule with every *actually recorded* week, even one
    # that falls outside this activity's own start/end window (e.g. saved via
    # the whole-road weekly update using a single date applied to all
    # activities). Without this merge, such an entry is invisible here (so it
    # can't be viewed/edited/deleted) yet is still counted in the activity's
    # live cumulative total everywhere else - the header and this list would
    # silently disagree.
    sched_map = {ws: (we, planned_default) for ws, we, planned_default in schedule}
    all_weeks = sorted(set(sched_map) | set(entries))
    cum = 0.0
    rows = []
    for ws in all_weeks:
        entry = entries.get(ws)
        if ws in sched_map:
            we, planned_default = sched_map[ws]
        else:
            we, planned_default = entry["week_end_date"], 0.0
        planned = float(entry["planned_qty_override"]) if entry and entry["planned_qty_override"] is not None else planned_default
        actual = float(entry["actual_qty"]) if entry else 0.0
        cum += actual
        rows.append({
            "week_start_date": ws.isoformat(), "week_end_date": we.isoformat(),
            "planned_this_week": round(planned, 3), "actual_this_week": actual,
            "cumulative_actual": round(cum, 3),
            "cumulative_pct": round(cum / float(a["planned_qty"]) * 100, 2) if a["planned_qty"] else 0,
            "note": entry["note"] if entry else None,
            "out_of_schedule": ws not in sched_map,
        })
    return {"activity": _jsonify(a), "weekly": rows}


# ---------------------------------------------------------------------------
# Weekly data entry (OPEN-01/02/06, BR-08 gating enforced here)
# ---------------------------------------------------------------------------
class WeeklyEntryIn(BaseModel):
    week_start_date: datetime.date  # any day in the week; resolved to its Monday
    actual_qty: float
    planned_qty_override: float | None = None
    note: str | None = None


def _monday_of(d: datetime.date) -> datetime.date:
    """Resolve any date to the Monday of the week containing it.

    Progress is stored and reported one row per week (OPEN-06: weeks run
    Mon-Sun), but data entry is deliberately NOT restricted to Mondays - the
    user enters on whatever day they are at the site, as often as they like,
    and the entry lands on that date's week. Normalising here rather than in
    the browser means every caller gets the same week, including the
    whole-road panel and any future import.
    """
    return d - datetime.timedelta(days=d.weekday())


@router.post("/activities/{activity_id}/weekly")
def submit_weekly_entry(activity_id: int, entry: WeeklyEntryIn):
    week_start = _monday_of(entry.week_start_date)
    if entry.actual_qty < 0:
        raise HTTPException(422, "actual_qty cannot be negative")

    acts = db.query("SELECT activity_id FROM activity WHERE activity_id = %s", (activity_id,))
    if not acts:
        raise HTTPException(404, "Activity not found")

    week_end = week_start + datetime.timedelta(days=6)

    # Simulate the post-write state to check BR-08 gating BEFORE committing.
    existing = db.query(
        "SELECT COALESCE(SUM(actual_qty),0) AS cum FROM weekly_entry "
        "WHERE activity_id = %s AND week_start_date != %s",
        (activity_id, week_start),
    )[0]["cum"]
    _validate_gating(activity_id, float(existing) + entry.actual_qty)

    rows = db.execute_returning(
        """
        INSERT INTO weekly_entry (activity_id, week_start_date, week_end_date,
            planned_qty_override, actual_qty, note)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (activity_id, week_start_date) DO UPDATE SET
            planned_qty_override = EXCLUDED.planned_qty_override,
            actual_qty = EXCLUDED.actual_qty,
            note = EXCLUDED.note,
            entered_at = now()
        RETURNING *
        """,
        (activity_id, week_start, week_end, entry.planned_qty_override,
         entry.actual_qty, entry.note),
    )
    return _jsonify(rows[0])


def _validate_gating(activity_id, hypothetical_cum_qty):
    """BR-08, enforced in BOTH directions against the hypothetical cumulative
    total this write (or deletion) would leave behind, so we reject before
    writing/deleting rather than after:
      - forward: this activity cannot end up ahead of its predecessor
        (seq_no - 1 present in the chain).
      - backward: reducing or deleting an earlier activity's entry cannot
        leave it behind an already-recorded successor. Without this check,
        a later activity validly saved while ahead could be stranded there
        once the earlier activity's entry is edited down or removed - the
        reported bug (e.g. Soil Disposal left at 6.25% after Excavation's
        entry was reduced to 3.12%).
    """
    rows = db.query(
        """
        SELECT a.activity_id, a.seq_no, a.raw_name, a.planned_qty,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a
        LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        WHERE a.road_no = (SELECT road_no FROM activity WHERE activity_id = %s)
          AND a.seq_no IS NOT NULL
        GROUP BY a.activity_id, a.seq_no, a.raw_name, a.planned_qty
        ORDER BY a.seq_no
        """,
        (activity_id,),
    )
    pct_by_seq, name_by_seq, this_seq, this_name = {}, {}, None, None
    for r in rows:
        cum = hypothetical_cum_qty if r["activity_id"] == activity_id else float(r["cum_qty"])
        pct_by_seq[r["seq_no"]] = (cum / float(r["planned_qty"]) * 100) if r["planned_qty"] else 0
        name_by_seq[r["seq_no"]] = r["raw_name"]
        if r["activity_id"] == activity_id:
            this_seq, this_name = r["seq_no"], r["raw_name"]
    if this_seq is None:
        return
    this_pct = pct_by_seq[this_seq]

    prior = [s for s in pct_by_seq if s < this_seq]
    if prior:
        prev_seq = max(prior)
        prev_pct = pct_by_seq[prev_seq]
        if this_pct > prev_pct + 1e-6:
            raise HTTPException(
                409,
                f"This entry would put {this_name} at {this_pct:.1f}% complete, "
                f"ahead of {name_by_seq[prev_seq]} at {prev_pct:.1f}%. "
                f"Enter progress on {name_by_seq[prev_seq]} first, or reduce this quantity."
            )

    for succ_seq in sorted(s for s in pct_by_seq if s > this_seq):
        succ_pct = pct_by_seq[succ_seq]
        if succ_pct > this_pct + 1e-6:
            raise HTTPException(
                409,
                f"This change would leave {this_name} at {this_pct:.1f}% complete, "
                f"behind {name_by_seq[succ_seq]} which is already at {succ_pct:.1f}%. "
                f"A layer cannot run ahead of the layer beneath it - reduce "
                f"{name_by_seq[succ_seq]} first, or do not lower this quantity below that."
            )


# ---------------------------------------------------------------------------
# Overall / performance / bottlenecks / milestones / CPM
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(line_manager: str | None = None):
    overall_pct, detail = calc.overall_progress(line_manager)
    return {"overall_pct": overall_pct, "roads": detail}


@router.get("/performance/vendor")
def vendor_performance(line_manager: str | None = None):
    return calc.vendor_performance(line_manager)


@router.get("/performance/line-manager")
def line_manager_performance(line_manager: str | None = None):
    return calc.line_manager_performance(line_manager)


@router.get("/bottlenecks")
def bottlenecks(threshold_pct: float = 15, stale_weeks: int = 3):
    return calc.bottlenecks(threshold_pct=threshold_pct, stale_weeks=stale_weeks)


@router.get("/milestones")
def milestones(window_days: int = 30):
    return calc.milestones(window_days=window_days)


@router.get("/charts/activity-status")
def charts_activity_status(line_manager: str | None = None):
    return calc.activity_status_counts(line_manager)


@router.get("/charts/progress-timeline")
def charts_progress_timeline(line_manager: str | None = None):
    return calc.progress_timeline(line_manager)


@router.get("/roads/{road_no}/critical-path")
def road_critical_path(road_no: int):
    return calc.critical_path(road_no)


@router.get("/data-issues")
def data_issues():
    return db.query("SELECT * FROM data_issue ORDER BY issue_id")


# ---------------------------------------------------------------------------
# Editing seeded master data
#
# Every road/activity row is seeded from the source workbook, so editing it
# means diverging from that workbook. Two business rules constrain this:
#
#   BR-01  Phase-Summary is absolute truth for EVERY column of `road`.
#   BR-02  activity.raw_name is verbatim from the road sheet, never changed.
#
# Those fields are therefore "source fields". Edits to them are not blocked
# outright -- the instruction was that seeded data must be editable -- but
# they require an explicit confirm_source_override=true in the request and
# are recorded in edit_log with source_override=TRUE, so divergence from the
# workbook is always visible instead of silent. Non-source fields (quantities,
# units, dates, weights on `activity`) edit normally.
#
# OPEN QUESTION, still unresolved: whether BR-01/BR-02 fields should be
# editable at all, or hard-blocked. The override flag is a conservative
# stand-in, not a ruling.
#
# Road creation (POST /roads, below) was previously not exposed because a
# rebuild dropped and recreated the whole schema, so an API-created road
# would have been erased on the next run. Rebuilds are now incremental
# (db/00_schema.sql uses IF NOT EXISTS, scripts/gen_seed.py uses ON CONFLICT
# DO NOTHING) and never touch an existing road_no, so a UI-added road
# (source_file = 'manual-entry') survives every future rebuild exactly like
# a workbook-seeded one. Deleting roads/activities is still not exposed -
# no deletion path has been asked for.
# ---------------------------------------------------------------------------
ROAD_EDITABLE = {
    "scheme_sl_no", "location", "line_manager", "vendor", "road_type",
    "length_m", "width_m", "drain_planned_m", "cost_cr",
    "start_date", "end_date", "phase",
}
# BR-01: every road column traces to Phase-Summary, so all of them are source
# fields. This set is intentionally equal to ROAD_EDITABLE, not a subset.
ROAD_SOURCE_FIELDS = set(ROAD_EDITABLE)

ACTIVITY_EDITABLE = {
    "raw_name", "major", "weight_pct", "unit", "planned_qty",
    "start_date", "end_date", "seq_no",
}
ACTIVITY_SOURCE_FIELDS = {"raw_name"}          # BR-02
VALID_MAJORS = {"Excavation", "GSB", "WMM", "DLC", "Road"}


class RoadUpdate(BaseModel):
    scheme_sl_no: str | None = None
    location: str | None = None
    line_manager: str | None = None
    vendor: str | None = None
    road_type: str | None = None
    length_m: float | None = None
    width_m: float | None = None
    drain_planned_m: float | None = None
    cost_cr: float | None = None
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    phase: int | None = None
    confirm_source_override: bool = False


class ActivityUpdate(BaseModel):
    raw_name: str | None = None
    major: str | None = None
    weight_pct: float | None = None
    unit: str | None = None
    planned_qty: float | None = None
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    seq_no: int | None = None
    confirm_source_override: bool = False


@router.put("/roads/{road_no}")
def update_road(road_no: int, payload: RoadUpdate):
    current = db.query("SELECT * FROM road WHERE road_no = %s", (road_no,))
    if not current:
        raise HTTPException(404, f"Road {road_no} not found")
    current = current[0]

    supplied = payload.model_dump(exclude_unset=True)
    supplied.pop("confirm_source_override", None)
    changes = {k: v for k, v in supplied.items() if k in ROAD_EDITABLE}
    if not changes:
        raise HTTPException(422, "No editable field supplied. Editable fields: "
                                 + ", ".join(sorted(ROAD_EDITABLE)))

    for fld in ("length_m", "drain_planned_m"):
        if fld in changes and changes[fld] is not None and float(changes[fld]) < 0:
            raise HTTPException(422, f"{fld} cannot be negative")

    new_start = changes.get("start_date", current["start_date"])
    new_end = changes.get("end_date", current["end_date"])
    if new_start and new_end and new_end < new_start:
        raise HTTPException(422, f"end_date ({new_end}) is before start_date ({new_start})")

    # A road's drain target cannot drop below drain progress already recorded,
    # or BR-09 drain % would exceed 100.
    if "drain_planned_m" in changes and changes["drain_planned_m"] is not None:
        done = db.query(
            "SELECT COALESCE(SUM(w.actual_qty),0) AS done FROM activity a "
            "JOIN weekly_entry w ON w.activity_id = a.activity_id "
            "WHERE a.road_no = %s AND a.is_drain", (road_no,),
        )[0]["done"]
        if float(changes["drain_planned_m"]) < float(done):
            raise HTTPException(
                409,
                f"drain_planned_m {changes['drain_planned_m']} is below the "
                f"{float(done)} m of drain already recorded as complete. Correct "
                f"the weekly drain entries first, or set a target >= {float(done)}."
            )

    touched_source = sorted(f for f in changes if f in ROAD_SOURCE_FIELDS)
    if touched_source and not payload.confirm_source_override:
        raise HTTPException(
            409,
            "The project summary sheet is the authoritative source for every road "
            f"field, including {', '.join(touched_source)}. Editing it makes this "
            "row differ from the source workbook, and the next hard rebuild will "
            "overwrite the change. Resend with confirm_source_override=true to "
            "proceed; the edit will be recorded in edit_log as a source override."
        )

    return _apply_update("road", "road_no", road_no, current, changes,
                         ROAD_SOURCE_FIELDS)


@router.put("/activities/{activity_id}")
def update_activity(activity_id: int, payload: ActivityUpdate):
    current = db.query("SELECT * FROM activity WHERE activity_id = %s", (activity_id,))
    if not current:
        raise HTTPException(404, f"Activity {activity_id} not found")
    current = current[0]
    road_no = current["road_no"]

    supplied = payload.model_dump(exclude_unset=True)
    supplied.pop("confirm_source_override", None)
    changes = {k: v for k, v in supplied.items() if k in ACTIVITY_EDITABLE}
    if not changes:
        raise HTTPException(422, "No editable field supplied. Editable fields: "
                                 + ", ".join(sorted(ACTIVITY_EDITABLE)))

    if "raw_name" in changes and not (changes["raw_name"] or "").strip():
        raise HTTPException(422, "raw_name cannot be blank (NOT NULL in schema)")

    # BR-04: major must be one of the five, matching the DDL CHECK.
    if "major" in changes and changes["major"] is not None:
        if changes["major"] not in VALID_MAJORS:
            raise HTTPException(
                422, f"major must be one of {sorted(VALID_MAJORS)} or null"
                     f"; got {changes['major']!r}")

    if "weight_pct" in changes and changes["weight_pct"] is not None:
        if not 0 <= float(changes["weight_pct"]) <= 100:
            raise HTTPException(422, "weight_pct must be between 0 and 100")

    if "planned_qty" in changes and changes["planned_qty"] is not None:
        if float(changes["planned_qty"]) < 0:
            raise HTTPException(422, "planned_qty cannot be negative")
        done = db.query(
            "SELECT COALESCE(SUM(actual_qty),0) AS done FROM weekly_entry "
            "WHERE activity_id = %s", (activity_id,),
        )[0]["done"]
        if float(done) > float(changes["planned_qty"]):
            raise HTTPException(
                409,
                f"planned_qty {changes['planned_qty']} is below the {float(done)} "
                f"{current['unit'] or 'units'} already recorded as complete, which "
                f"would report over 100%. Correct the weekly entries first, or set "
                f"planned_qty >= {float(done)}."
            )

    new_start = changes.get("start_date", current["start_date"])
    new_end = changes.get("end_date", current["end_date"])
    if new_start and new_end and new_end < new_start:
        raise HTTPException(422, f"end_date ({new_end}) is before start_date ({new_start})")

    # BR-05: the road's weights must still total 100 after this edit.
    if "weight_pct" in changes and changes["weight_pct"] is not None:
        others = db.query(
            "SELECT COALESCE(SUM(weight_pct),0) AS total FROM activity "
            "WHERE road_no = %s AND activity_id != %s", (road_no, activity_id),
        )[0]["total"]
        new_total = float(others) + float(changes["weight_pct"])
        if abs(new_total - 100.0) > 0.01:
            raise HTTPException(
                409,
                f"Road {road_no}'s activity weights must total 100. This "
                f"change would make the total {round(new_total, 2)} "
                f"(the road's other activities already account for "
                f"{round(float(others), 2)}). Adjust another activity's weight in "
                f"the same change set, or use a weight of "
                f"{round(100.0 - float(others), 2)}."
            )

    touched_source = sorted(f for f in changes if f in ACTIVITY_SOURCE_FIELDS)
    if touched_source and not payload.confirm_source_override:
        raise HTTPException(
            409,
            f"Activity names are recorded verbatim from the road sheet, so "
            f"{', '.join(touched_source)} should not normally be edited. The next "
            f"hard rebuild will overwrite the change. Resend with "
            f"confirm_source_override=true to proceed; the edit will be recorded "
            f"in edit_log as a source override."
        )

    return _apply_update("activity", "activity_id", activity_id, current, changes,
                         ACTIVITY_SOURCE_FIELDS)


def _apply_update(table, pk_col, pk_val, current, changes, source_fields):
    """Write the UPDATE and one edit_log row per actually-changed field.

    Fields whose new value equals the current value are dropped rather than
    logged, so the edit log records real changes only. Column names come from
    the module-level allowlists, never from request data.
    """
    real = {}
    for fld, new in changes.items():
        old = current.get(fld)
        old_cmp = float(old) if isinstance(old, decimal.Decimal) else old
        new_cmp = float(new) if isinstance(new, (int, float)) and not isinstance(new, bool) else new
        if isinstance(old_cmp, (int, float)) and isinstance(new_cmp, (int, float)):
            same = abs(float(old_cmp) - float(new_cmp)) < 1e-9
        else:
            same = old_cmp == new_cmp
        if not same:
            real[fld] = new

    if not real:
        return {"updated": False, "reason": "every supplied value already matches "
                                            "the stored value; nothing changed",
                table: _jsonify(current)}

    assigns = ", ".join(f"{f} = %s" for f in real)          # keys are allowlisted
    params = list(real.values()) + [pk_val]
    rows = db.execute_returning(
        f"UPDATE {table} SET {assigns} WHERE {pk_col} = %s RETURNING *", params
    )

    for fld, new in real.items():
        old = current.get(fld)
        db.execute(
            "INSERT INTO edit_log (table_name, row_pk, field, old_value, "
            "new_value, source_override) VALUES (%s,%s,%s,%s,%s,%s)",
            (table, int(pk_val), fld,
             None if old is None else str(old),
             None if new is None else str(new),
             fld in source_fields),
        )

    return {"updated": True, "changed_fields": sorted(real),
            "source_override_fields": sorted(f for f in real if f in source_fields),
            table: _jsonify(rows[0])}


@router.delete("/activities/{activity_id}/weekly/{week_start_date}")
def delete_weekly_entry(activity_id: int, week_start_date: datetime.date):
    """Remove one week's actual. Needed to correct a mis-keyed entry: the POST
    upsert can overwrite a value but cannot take a week back to 'nothing
    recorded', and a 0 actual is a different statement from no entry.

    Any day in the week may be given; it resolves to that week's Monday.
    """
    week_start = _monday_of(week_start_date)

    # Removing an earlier activity's entry lowers its cumulative, which can
    # strand an already-recorded successor ahead of it (BR-08) - check the
    # post-delete state before deleting, same as the POST path does.
    existing = db.query(
        "SELECT COALESCE(SUM(actual_qty),0) AS cum FROM weekly_entry "
        "WHERE activity_id = %s AND week_start_date != %s",
        (activity_id, week_start),
    )[0]["cum"]
    _validate_gating(activity_id, float(existing))

    n = db.execute(
        "DELETE FROM weekly_entry WHERE activity_id = %s AND week_start_date = %s",
        (activity_id, week_start),
    )
    if not n:
        raise HTTPException(404, f"No entry for activity {activity_id} in the week "
                                 f"starting {week_start}")
    return {"deleted": n, "activity_id": activity_id,
            "week_start_date": week_start.isoformat()}


@router.get("/edit-log")
def edit_log(table_name: str | None = None, row_pk: int | None = None,
             source_override_only: bool = False, limit: int = 500):
    """Audit trail of master-data edits. Also the record of where the DB now
    differs from the source workbook (source_override_only=true)."""
    where, params = [], []
    if table_name:
        if table_name not in ("road", "activity"):
            raise HTTPException(422, "table_name must be 'road' or 'activity'")
        where.append("table_name = %s")
        params.append(table_name)
    if row_pk is not None:
        where.append("row_pk = %s")
        params.append(row_pk)
    if source_override_only:
        where.append("source_override")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(max(1, min(limit, 5000)))
    return [
        _jsonify(r) for r in db.query(
            f"SELECT * FROM edit_log {clause} ORDER BY edited_at DESC LIMIT %s",
            tuple(params),
        )
    ]


@router.get("/weight-audit")
def weight_audit():
    """BR-05 health check, served from the DB so the UI can surface it.

    Lists any road whose activity weights do not total 100, plus the usual
    cause: two activity rows mapping to the same major, each carrying that
    major's full weight.
    """
    bad = db.query(
        "SELECT r.road_no, r.location, SUM(a.weight_pct) AS weight_total "
        "FROM road r JOIN activity a ON a.road_no = r.road_no "
        "GROUP BY r.road_no, r.location "
        "HAVING ABS(SUM(a.weight_pct) - 100) >= 0.01 ORDER BY r.road_no"
    )
    dupes = db.query(
        "SELECT road_no, major, COUNT(*) AS activity_rows, "
        "SUM(weight_pct) AS summed_weight, "
        "STRING_AGG(raw_name, ' | ' ORDER BY sheet_row_order) AS raw_names "
        "FROM activity WHERE major IS NOT NULL "
        "GROUP BY road_no, major HAVING COUNT(*) > 1 ORDER BY road_no, major"
    )
    return {
        "roads_not_totalling_100": [_jsonify(r) for r in bad],
        "duplicate_major_rows": [_jsonify(r) for r in dupes],
        "clean": not bad and not dupes,
    }


def _jsonify(row):
    """psycopg2 RealDictRow -> plain dict with date/Decimal made JSON-safe."""
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, decimal.Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Manual bottleneck/issue records. Parity feature for the legacy app's
# bottleneck.csv + post_bottleneck add/status/remarks/delete flow: the
# auto-derived bottlenecks() in calculations.py cover rule-based detection,
# but a reviewer can also log an issue by hand (vendor mobilisation delay,
# site access dispute, etc.) and track it to closure.
# ---------------------------------------------------------------------------
class ManualIssueCreate(BaseModel):
    road_no: int | None = None
    issue: str
    source: str = "Weekly review"


class ManualIssueUpdate(BaseModel):
    status: str | None = None
    remarks: str | None = None


MANUAL_ISSUE_STATUSES = {"Open", "In progress", "Closed"}


@router.get("/manual-bottlenecks")
def list_manual_bottlenecks(status: str | None = None, road_no: int | None = None):
    where, params = [], []
    if status:
        if status not in MANUAL_ISSUE_STATUSES:
            raise HTTPException(422, f"status must be one of {sorted(MANUAL_ISSUE_STATUSES)}")
        where.append("status = %s")
        params.append(status)
    if road_no is not None:
        where.append("road_no = %s")
        params.append(road_no)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return [
        _jsonify(r) for r in db.query(
            f"SELECT m.*, r.location FROM manual_issue m "
            f"LEFT JOIN road r ON r.road_no = m.road_no {clause} "
            "ORDER BY m.created_at DESC",
            tuple(params),
        )
    ]


@router.post("/manual-bottlenecks", status_code=201)
def create_manual_bottleneck(payload: ManualIssueCreate):
    if not payload.issue.strip():
        raise HTTPException(422, "issue is required")
    if payload.road_no is not None and not db.query("SELECT 1 FROM road WHERE road_no = %s", (payload.road_no,)):
        raise HTTPException(404, f"Road {payload.road_no} not found")
    rows = db.execute_returning(
        "INSERT INTO manual_issue (road_no, issue, source) VALUES (%s, %s, %s) RETURNING *",
        (payload.road_no, payload.issue.strip(), payload.source.strip() or "Weekly review"),
    )
    return _jsonify(rows[0])


@router.put("/manual-bottlenecks/{issue_id}")
def update_manual_bottleneck(issue_id: int, payload: ManualIssueUpdate):
    current = db.query("SELECT * FROM manual_issue WHERE issue_id = %s", (issue_id,))
    if not current:
        raise HTTPException(404, f"Manual issue {issue_id} not found")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "Supply status and/or remarks")
    if "status" in changes and changes["status"] not in MANUAL_ISSUE_STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(MANUAL_ISSUE_STATUSES)}")

    assigns = ", ".join(f"{f} = %s" for f in changes)  # keys are allowlisted via ManualIssueUpdate
    rows = db.execute_returning(
        f"UPDATE manual_issue SET {assigns}, updated_at = now() WHERE issue_id = %s RETURNING *",
        list(changes.values()) + [issue_id],
    )
    return _jsonify(rows[0])


@router.delete("/manual-bottlenecks/{issue_id}")
def delete_manual_bottleneck(issue_id: int):
    n = db.execute("DELETE FROM manual_issue WHERE issue_id = %s", (issue_id,))
    if not n:
        raise HTTPException(404, f"Manual issue {issue_id} not found")
    return {"deleted": n}


# ---------------------------------------------------------------------------
# CSV import / export. Parity feature for the legacy app's
# post_import_progress and /api/export/history + /api/export/summary.
# ---------------------------------------------------------------------------
@router.post("/weekly/import-csv")
def import_weekly_csv(payload: dict):
    """Bulk-import weekly actuals from pasted CSV text.

    Expected columns (header row, case-insensitive): activity_id,
    week_start_date, actual_qty, and optionally note / planned_qty_override.
    week_start_date accepts any day in the week; it resolves to that week's
    Monday, same as the single-entry endpoint. BR-08 gating is enforced per
    row and the whole import is one transaction: a single bad row rejects
    everything rather than leaving a partial import.
    """
    text = (payload or {}).get("csv", "")
    if not text.strip():
        raise HTTPException(422, "csv is empty")

    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]
    if not rows:
        raise HTTPException(422, "No data rows found in CSV")

    parsed = []
    for i, r in enumerate(rows, start=2):  # row 1 is the header
        try:
            activity_id = int(r["activity_id"])
        except (KeyError, ValueError):
            raise HTTPException(422, f"row {i}: activity_id must be an integer")
        try:
            week_raw = datetime.date.fromisoformat(r.get("week_start_date", ""))
        except ValueError:
            raise HTTPException(422, f"row {i}: week_start_date must be YYYY-MM-DD")
        try:
            actual_qty = float(r.get("actual_qty", ""))
        except ValueError:
            raise HTTPException(422, f"row {i}: actual_qty must be a number")
        if actual_qty < 0:
            raise HTTPException(422, f"row {i}: actual_qty cannot be negative")
        override_raw = r.get("planned_qty_override", "")
        planned_qty_override = float(override_raw) if override_raw else None
        parsed.append({
            "activity_id": activity_id, "week_start": _monday_of(week_raw),
            "actual_qty": actual_qty, "note": r.get("note") or None,
            "planned_qty_override": planned_qty_override,
        })

    # Validate every activity exists and BR-08 gating holds, in the hypothetical
    # post-import state, before writing anything.
    by_activity = {}
    for p in parsed:
        by_activity.setdefault(p["activity_id"], []).append(p)
    for activity_id, entries in by_activity.items():
        if not db.query("SELECT 1 FROM activity WHERE activity_id = %s", (activity_id,)):
            raise HTTPException(404, f"Unknown activity_id {activity_id}")
        weeks_in_import = {e["week_start"] for e in entries}
        existing = db.query(
            "SELECT COALESCE(SUM(actual_qty),0) AS cum FROM weekly_entry "
            "WHERE activity_id = %s AND week_start_date NOT IN %s",
            (activity_id, tuple(weeks_in_import) + (datetime.date(1900, 1, 1),)),
        )[0]["cum"]
        hypothetical = float(existing) + sum(e["actual_qty"] for e in entries)
        _validate_gating(activity_id, hypothetical)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for p in parsed:
                week_end = p["week_start"] + datetime.timedelta(days=6)
                cur.execute(
                    """
                    INSERT INTO weekly_entry (activity_id, week_start_date, week_end_date,
                        planned_qty_override, actual_qty, note)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (activity_id, week_start_date) DO UPDATE SET
                        planned_qty_override = EXCLUDED.planned_qty_override,
                        actual_qty = EXCLUDED.actual_qty,
                        note = EXCLUDED.note,
                        entered_at = now()
                    """,
                    (p["activity_id"], p["week_start"], week_end,
                     p["planned_qty_override"], p["actual_qty"], p["note"]),
                )
        conn.commit()

    return {"imported": len(parsed)}


def _csv_response(rows, fieldnames, filename):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(fieldnames)
    for r in rows:
        writer.writerow(r)
    body = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store"},
    )


@router.get("/export/weekly-history")
def export_weekly_history():
    rows = calc.weekly_history_rows()
    data = [
        [r["week_start_date"].isoformat(), r["road_no"], r["activity"],
         float(r["actual_qty"]), r["note"] or ""]
        for r in rows
    ]
    return _csv_response(data, ["week_start_date", "road_no", "activity", "actual_qty", "note"],
                          "progress_history.csv")


@router.get("/export/summary")
def export_summary(line_manager: str | None = None):
    rows = calc.road_summary_rows(line_manager)
    data = [
        [f"Road {r['road_no']}", r["location"], r["vendor"] or "", r["line_manager"] or "",
         r["activities"], r["actual_pct"], r["planned_pct"], r["status"]]
        for r in rows
    ]
    today = datetime.date.today().isoformat()
    return _csv_response(
        data, ["road", "location", "vendor", "line_manager", "activities", "actual_pct", "planned_pct", "status"],
        f"summary_{today}.csv",
    )