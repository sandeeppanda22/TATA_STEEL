"""API routes backed by the PostgreSQL tata_road schema (not the static
dataset.json). These are the "live" endpoints: weekly data entry, computed
progress, vendor/LM performance, bottlenecks, milestones, CPM.

Requires db/rebuild.sh to have been run against a reachable Postgres
(local PGHOST/... or DATABASE_URL) - see dashboard/db.py.
"""
import contextlib
import csv
import datetime
import decimal
import io

import psycopg2.extras

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from dashboard import calculations as calc
from dashboard import db
from dashboard import live_feed

router = APIRouter(prefix="/api/db", tags=["db"])


# ---------------------------------------------------------------------------
# Roads / activities
# ---------------------------------------------------------------------------
@router.get("/roads")
def list_roads():
    roads = db.query(
        "SELECT road_no, phase, location, line_manager, vendor, road_type, length_m, "
        "width_m, drain_planned_m, cost_cr, start_date, end_date, source_file "
        "FROM road ORDER BY road_no"
    )
    out = []
    for r in roads:
        pct, drain_pct, over_planned = calc.road_progress(r["road_no"])
        out.append({**_jsonify(r), "progress_pct": pct, "drain_progress_pct": drain_pct,
                    "planned_pct": calc.road_planned_progress(r["road_no"]),
                    "over_planned_count": over_planned})
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
    pct, drain_pct, over_planned = calc.road_progress(road_no)
    acts_out = []
    for a in acts:
        a = _jsonify(a)
        planned = float(a["planned_qty"])
        cum = float(a["cum_qty"])
        raw_pct = (cum / planned * 100) if planned else 0
        # Displayed % is always capped at 100: an actual ahead of its planned
        # qty means the plan is wrong, not that progress is >100%. over_planned
        # flags that case so the UI can prompt raising planned_qty.
        a["pct"] = round(min(raw_pct, 100), 2)
        a["over_planned"] = bool(planned and cum > planned + 1e-6)
        acts_out.append(a)
    return {"road": _jsonify(road[0]), "progress_pct": pct, "drain_progress_pct": drain_pct,
            "over_planned_count": over_planned, "activities": acts_out}


def _check_date_order(start, end, what=""):
    """422 when an end date is before its start date. The one rule behind road
    create/edit and activity create/edit. `what` prefixes the message, e.g.
    "activity 'WMM': "."""
    if start and end and end < start:
        raise HTTPException(422, f"{what}end_date ({end}) is before start_date ({start})")


class ActivityCreate(BaseModel):
    raw_name: str
    major: str | None = None
    weight_pct: float = 0
    unit: str | None = None
    planned_qty: float            # required: an activity with no planned quantity can't track progress
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
    length_m: float | None = 0          # blank is accepted and stored as 0
    width_m: float | None = None
    drain_planned_m: float | None = 0  # blank is accepted and stored as 0
    cost_cr: float | None = None
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    phase: int = 1
    activities: list[ActivityCreate] = []


@router.post("/roads", status_code=201)
def create_road(payload: RoadCreate):
    """Add a new road, with its activities (and their own start/end dates),
    straight into the database. source_file is stamped 'manual-entry'; rebuilds
    are incremental (ON CONFLICT DO NOTHING on road_no), so a road added here is
    never erased by a later ./rebuild.sh.
    """
    if db.query("SELECT road_no FROM road WHERE road_no = %s", (payload.road_no,)):
        raise HTTPException(
            409, f"Road {payload.road_no} already exists. Road numbers must be "
                 "unique - pick an unused number, or edit the existing road instead."
        )

    length_m = payload.length_m or 0
    drain_planned_m = payload.drain_planned_m or 0
    if payload.road_no < 1:
        raise HTTPException(422, "Road number must be 1 or higher")
    if payload.phase < 1:
        raise HTTPException(422, "Phase must be 1 or higher")
    if not payload.location.strip():
        raise HTTPException(422, "Location is required")
    if length_m < 0:
        raise HTTPException(422, "Length cannot be negative")
    if drain_planned_m < 0:
        raise HTTPException(422, "Drain planned cannot be negative")
    _check_date_order(payload.start_date, payload.end_date)

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
            raise HTTPException(422, f"Activity {a.raw_name!r}: Weight % must be between 0 and 100")
        if a.planned_qty <= 0:
            raise HTTPException(422, f"Activity {a.raw_name!r}: planned quantity is required and must be greater than 0")
        _check_date_order(a.start_date, a.end_date, f"Activity {a.raw_name!r}: ")
        if a.major is None and a.weight_pct:
            raise HTTPException(
                422, f"Activity {a.raw_name!r}: Weight % applies only to activities with a Major. "
                     "Choose a Major, or leave the weight at 0.")
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
                (payload.road_no, payload.phase, payload.scheme_sl_no, payload.location.strip(),
                 (payload.line_manager or '').strip() or None, (payload.vendor or '').strip() or None,
                 payload.road_type, length_m,
                 payload.width_m, drain_planned_m, payload.cost_cr,
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
    # Append-only ledger: list every submission in chronological order (by the
    # week it belongs to, then by when it was entered) with a running
    # cumulative. Each row is one submission - the user may have several in the
    # same week - so each carries its own entry_id for individual deletion.
    entries = db.query(
        """
        SELECT entry_id, week_start_date, week_end_date, actual_qty,
               planned_qty_override, note, entered_at
        FROM weekly_entry WHERE activity_id = %s
        ORDER BY week_start_date, entered_at, entry_id
        """,
        (activity_id,),
    )
    planned_qty = float(a["planned_qty"]) if a["planned_qty"] else 0.0
    cum = 0.0
    rows = []
    for e in entries:
        actual = float(e["actual_qty"])
        cum += actual
        raw_pct = (cum / planned_qty * 100) if planned_qty else 0
        rows.append({
            "entry_id": e["entry_id"],
            "week_start_date": e["week_start_date"].isoformat(),
            "week_end_date": e["week_end_date"].isoformat(),
            "entered_at": e["entered_at"].isoformat() if e["entered_at"] else None,
            "actual_this_week": actual,
            "cumulative_actual": round(cum, 3),
            # Capped at 100 - an actual ahead of planned means the plan is
            # wrong, not that progress exceeds completion.
            "cumulative_pct": round(min(raw_pct, 100), 2),
            "note": e["note"],
        })
    over_planned = bool(planned_qty and cum > planned_qty + 1e-6)
    return {"activity": _jsonify(a), "weekly": rows, "over_planned": over_planned}


# ---------------------------------------------------------------------------
# Weekly data entry (OPEN-01/02/06). Append-only ledger; no BR-08 gating.
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

    # Append-only ledger (ruling 2026-10-13): every submission is its own row
    # and ADDS to the activity's cumulative - it never replaces the week's
    # figure, so the user can update many times a week. No sequencing/gating
    # check: progress may be entered for any activity in any order.
    rows = db.execute_returning(
        """
        INSERT INTO weekly_entry (activity_id, week_start_date, week_end_date,
            planned_qty_override, actual_qty, note)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (activity_id, week_start, week_end, entry.planned_qty_override,
         entry.actual_qty, entry.note),
    )
    return _jsonify(rows[0])


def _validate_gating(activity_id, hypothetical_cum_qty):  # noqa: ARG001
    """REMOVED (ruling 2026-10-13). BR-08 percentage gating no longer applies:
    progress may be entered for any activity in any order. Kept as a no-op so
    any lingering caller does not break; new code must not call it."""
    return


# ---------------------------------------------------------------------------
# Overall / performance / bottlenecks / milestones / CPM
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(line_manager: str | None = None):
    overall_pct, detail = calc.overall_progress(line_manager)
    return {"overall_pct": overall_pct, "roads": detail}


@router.get("/performance/vendor")
def vendor_performance(line_manager: str | None = None, vendor: str | None = None):
    return calc.vendor_performance(line_manager, vendor)


@router.get("/performance/line-manager")
def line_manager_performance(line_manager: str | None = None, vendor: str | None = None):
    return calc.line_manager_performance(line_manager, vendor)


def _group_field(group: str) -> str:
    if group not in ("vendor", "line_manager"):
        raise HTTPException(422, "group must be 'vendor' or 'line_manager'")
    return group


@router.get("/performance/months")
def performance_months():
    return calc.backlog_months()


@router.get("/performance/backlog")
def performance_backlog(group: str = "vendor", month: str | None = None,
                        line_manager: str | None = None, vendor: str | None = None,
                        detail: bool = False):
    """Backlog per vendor / line manager as at the end of `month` (YYYY-MM)."""
    if month is not None:
        try:
            datetime.datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")
    try:
        return calc.group_backlog(_group_field(group), month, line_manager, vendor, detail)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/performance/target")
def performance_target(group: str = "vendor", date_from: datetime.date | None = None,
                       date_to: datetime.date | None = None,
                       line_manager: str | None = None, vendor: str | None = None):
    """Target for date_from..date_to (default today..+30d) = backlog at
    date_from + planned work in the window."""
    try:
        return calc.group_target(_group_field(group), date_from, date_to, line_manager, vendor)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/live/changes")
def live_changes(request: Request):
    """Cheap change feed for the browser's live refresh: one marker per
    whitelisted table. Rate-limited per client (60/min). No record data."""
    nostore = {"Cache-Control": "no-store"}
    wait = live_feed.limiter.check(live_feed.client_key(request))
    if wait:
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                            headers={**nostore, "Retry-After": str(wait)})
    try:
        rows = db.query(
            "SELECT table_name, change_seq FROM live_change_marker WHERE table_name = ANY(%s)",
            (list(live_feed.LIVE_FEED_TABLES),))
    except Exception as e:  # marker table not installed yet -> feed simply off
        if "live_change_marker" in str(e):
            return JSONResponse({"enabled": False, "changed": []}, headers=nostore)
        raise
    seq = {r["table_name"]: int(r["change_seq"]) for r in rows}
    return JSONResponse(
        {"enabled": True, "changed": [{"table_name": t, "change_seq": seq.get(t, 0)}
                                      for t in live_feed.LIVE_FEED_TABLES]},
        headers=nostore)


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
# Editing road / activity master data
#
# The database is the source of truth. Any road or activity field in the
# allowlists below can be edited through the PUT routes; every change is
# written to edit_log (table, row, field, old value, new value, time), so the
# history is always recoverable. Roads are added from the dashboard
# (POST /roads, source_file = 'manual-entry'). Deleting roads/activities is
# not exposed - no deletion path has been asked for.
# ---------------------------------------------------------------------------
ROAD_EDITABLE = {
    "scheme_sl_no", "location", "line_manager", "vendor", "road_type",
    "length_m", "width_m", "drain_planned_m", "cost_cr",
    "start_date", "end_date", "phase",
}
ACTIVITY_EDITABLE = {
    "raw_name", "major", "weight_pct", "unit", "planned_qty",
    "start_date", "end_date", "seq_no",
}
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


class ActivityUpdate(BaseModel):
    raw_name: str | None = None
    major: str | None = None
    weight_pct: float | None = None
    unit: str | None = None
    planned_qty: float | None = None
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    seq_no: int | None = None


@router.put("/roads/{road_no}")
def update_road(road_no: int, payload: RoadUpdate):
    current = db.query("SELECT * FROM road WHERE road_no = %s", (road_no,))
    if not current:
        raise HTTPException(404, f"Road {road_no} not found")
    current = current[0]

    supplied = payload.model_dump(exclude_unset=True)
    changes = {k: v for k, v in supplied.items() if k in ROAD_EDITABLE}
    if not changes:
        raise HTTPException(422, "No editable field supplied. Editable fields: "
                                 + ", ".join(sorted(ROAD_EDITABLE)))

    for fld in ("length_m", "drain_planned_m"):
        if fld in changes and changes[fld] is not None and float(changes[fld]) < 0:
            raise HTTPException(422, f"{fld} cannot be negative")

    _check_date_order(changes.get("start_date", current["start_date"]),
                      changes.get("end_date", current["end_date"]))

    return _apply_update("road", "road_no", road_no, current, changes)


@router.put("/activities/{activity_id}")
def update_activity(activity_id: int, payload: ActivityUpdate):
    current = db.query("SELECT * FROM activity WHERE activity_id = %s", (activity_id,))
    if not current:
        raise HTTPException(404, f"Activity {activity_id} not found")
    current = current[0]
    road_no = current["road_no"]

    supplied = payload.model_dump(exclude_unset=True)
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
            raise HTTPException(422, "Weight % must be between 0 and 100")

    # Weight % only exists for the five majors (BR-04/06): without a Major it must be 0.
    if "major" in changes or "weight_pct" in changes:
        eff_major = changes["major"] if "major" in changes else current["major"]
        eff_weight = changes["weight_pct"] if "weight_pct" in changes else current["weight_pct"]
        if eff_major is None and float(eff_weight or 0) != 0:
            raise HTTPException(
                422, "Weight % applies only to activities with a Major. Choose a Major for this "
                     "activity, or set its weight to 0.")

    if "planned_qty" in changes and changes["planned_qty"] is not None:
        # The planned quantity entered is always respected - it is NOT blocked by
        # actuals already recorded (displayed % is capped at 100 and the activity is
        # flagged "actual > planned" instead).
        if float(changes["planned_qty"]) < 0:
            raise HTTPException(422, "Planned quantity cannot be negative")

    _check_date_order(changes.get("start_date", current["start_date"]),
                      changes.get("end_date", current["end_date"]))

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

    return _apply_update("activity", "activity_id", activity_id, current, changes)


def _apply_update(table, pk_col, pk_val, current, changes):
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
            "INSERT INTO edit_log (table_name, row_pk, field, old_value, new_value) "
            "VALUES (%s,%s,%s,%s,%s)",
            (table, int(pk_val), fld,
             None if old is None else str(old),
             None if new is None else str(new)),
        )

    return {"updated": True, "changed_fields": sorted(real),
            table: _jsonify(rows[0])}


# ---------------------------------------------------------------------------
# Road issues (hindrance windows). Adding one pushes the road's unfinished
# work later by the window length so the plan reflects the lost days.
#
#   shift_days = (to_date - from_date) + 1            (inclusive)
#   activity ends before from_date          -> untouched (already past)
#   activity fully complete (actual>=planned)-> untouched
#   activity starts on/after from_date      -> start AND end move +shift_days
#                                              (anything starting inside the
#                                              window lands after to_date)
#   activity in progress across from_date   -> only its end moves +shift_days
#   road.start_date / end_date              -> same rule as an activity
# Everything runs in ONE transaction and every changed date is logged in
# edit_log.
#
# road_issue_shift remembers exactly what each issue moved (which activities,
# whether the start moved too, and the road). Editing or deleting an issue
# first REVERSES that by subtracting shift_days from the current dates (so
# later manual edits and other issues' shifts are preserved), then - for an
# edit - re-applies the new window.
# ---------------------------------------------------------------------------
class RoadIssueCreate(BaseModel):
    from_date: datetime.date
    to_date: datetime.date
    reason: str | None = None


def _issue_days(frm, to):
    if to < frm:
        raise HTTPException(422, "to_date must be on or after from_date")
    return (to - frm).days + 1


def _log_dates(cur, table, pk, fld, old_v, new_v):
    if old_v != new_v:
        cur.execute(
            "INSERT INTO edit_log (table_name, row_pk, field, old_value, new_value) "
            "VALUES (%s,%s,%s,%s,%s)",
            (table, pk, fld, None if old_v is None else str(old_v),
             None if new_v is None else str(new_v)))


def _apply_issue(cur, road_no, issue_id, frm, delta):
    """Shift the road's unfinished work; remember what moved. Returns counts."""
    def moved(start, end):
        if end is None or end < frm:
            return None
        if start is not None and start >= frm:
            return start + delta, end + delta, True
        return start, end + delta, False

    counts = {"moved": 0, "completed": 0, "ended_before": 0, "no_end_date": 0}
    cur.execute(
        """
        SELECT a.activity_id, a.start_date, a.end_date, a.planned_qty,
               COALESCE(SUM(w.actual_qty), 0) AS cum
        FROM activity a LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        WHERE a.road_no = %s
        GROUP BY a.activity_id, a.start_date, a.end_date, a.planned_qty
        ORDER BY a.activity_id
        """, (road_no,))
    for a in cur.fetchall():
        if a["end_date"] is None:
            counts["no_end_date"] += 1
            continue
        new = moved(a["start_date"], a["end_date"])
        if new is None:
            counts["ended_before"] += 1
            continue
        planned = float(a["planned_qty"] or 0)
        if planned and float(a["cum"]) >= planned - 1e-9:
            counts["completed"] += 1
            continue
        cur.execute("UPDATE activity SET start_date = %s, end_date = %s WHERE activity_id = %s",
                    (new[0], new[1], a["activity_id"]))
        cur.execute("INSERT INTO road_issue_shift (issue_id, activity_id, moved_start) VALUES (%s,%s,%s)",
                    (issue_id, a["activity_id"], new[2]))
        counts["moved"] += 1
        _log_dates(cur, "activity", a["activity_id"], "start_date", a["start_date"], new[0])
        _log_dates(cur, "activity", a["activity_id"], "end_date", a["end_date"], new[1])
    cur.execute("SELECT start_date, end_date FROM road WHERE road_no = %s", (road_no,))
    road = cur.fetchone()
    rd = moved(road["start_date"], road["end_date"])
    if rd is not None:
        cur.execute("UPDATE road SET start_date = %s, end_date = %s WHERE road_no = %s",
                    (rd[0], rd[1], road_no))
        cur.execute("INSERT INTO road_issue_shift (issue_id, activity_id, moved_start) VALUES (%s,NULL,%s)",
                    (issue_id, rd[2]))
        _log_dates(cur, "road", road_no, "start_date", road["start_date"], rd[0])
        _log_dates(cur, "road", road_no, "end_date", road["end_date"], rd[1])
    return counts


def _revert_issue(cur, road_no, issue_id, shift_days):
    """Undo what _apply_issue did: subtract shift_days from the current dates."""
    delta = datetime.timedelta(days=shift_days)
    cur.execute("SELECT activity_id, moved_start FROM road_issue_shift WHERE issue_id = %s", (issue_id,))
    for sh in cur.fetchall():
        if sh["activity_id"] is None:
            cur.execute("SELECT start_date, end_date FROM road WHERE road_no = %s", (road_no,))
            row = cur.fetchone()
            if row is None:
                continue
            ns = row["start_date"] - delta if (sh["moved_start"] and row["start_date"]) else row["start_date"]
            ne = row["end_date"] - delta if row["end_date"] else row["end_date"]
            cur.execute("UPDATE road SET start_date = %s, end_date = %s WHERE road_no = %s", (ns, ne, road_no))
            _log_dates(cur, "road", road_no, "start_date", row["start_date"], ns)
            _log_dates(cur, "road", road_no, "end_date", row["end_date"], ne)
        else:
            cur.execute("SELECT start_date, end_date FROM activity WHERE activity_id = %s", (sh["activity_id"],))
            row = cur.fetchone()
            if row is None:
                continue
            ns = row["start_date"] - delta if (sh["moved_start"] and row["start_date"]) else row["start_date"]
            ne = row["end_date"] - delta if row["end_date"] else row["end_date"]
            cur.execute("UPDATE activity SET start_date = %s, end_date = %s WHERE activity_id = %s",
                        (ns, ne, sh["activity_id"]))
            _log_dates(cur, "activity", sh["activity_id"], "start_date", row["start_date"], ns)
            _log_dates(cur, "activity", sh["activity_id"], "end_date", row["end_date"], ne)
    cur.execute("DELETE FROM road_issue_shift WHERE issue_id = %s", (issue_id,))


def _issue_json(r):
    return {**r, "from_date": r["from_date"].isoformat(), "to_date": r["to_date"].isoformat(),
            "created_at": r["created_at"].isoformat()}


@contextlib.contextmanager
def _issue_tx():
    """One transaction with a dict cursor; commit on success, rollback on error."""
    with db.get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _lock_road(cur, road_no):
    cur.execute("SELECT road_no FROM road WHERE road_no = %s FOR UPDATE", (road_no,))
    if not cur.fetchone():
        raise HTTPException(404, f"Road {road_no} not found")


def _today():
    return datetime.date.today()


def _get_issue(cur, road_no, issue_id):
    cur.execute("SELECT * FROM road_issue WHERE issue_id = %s AND road_no = %s FOR UPDATE", (issue_id, road_no))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Issue {issue_id} not found on road {road_no}")
    return row


def _get_open_issue(cur, road_no, issue_id):
    """Like _get_issue, but an issue whose To date has passed is closed history
    and can no longer be edited or deleted."""
    row = _get_issue(cur, road_no, issue_id)
    if row["to_date"] < _today():
        raise HTTPException(409, "This issue's To date has passed; it can no longer be edited or deleted.")
    return row


@router.get("/roads/{road_no}/issues")
def list_road_issues(road_no: int):
    return [_issue_json(r) for r in db.query(
        "SELECT * FROM road_issue WHERE road_no = %s ORDER BY issue_id DESC", (road_no,))]


@router.post("/roads/{road_no}/issues", status_code=201)
def create_road_issue(road_no: int, payload: RoadIssueCreate):
    shift = _issue_days(payload.from_date, payload.to_date)
    reason = (payload.reason or "").strip() or None
    with _issue_tx() as cur:
        _lock_road(cur, road_no)
        cur.execute(
            "INSERT INTO road_issue (road_no, from_date, to_date, shift_days, activities_shifted, reason) "
            "VALUES (%s,%s,%s,%s,0,%s) RETURNING issue_id",
            (road_no, payload.from_date, payload.to_date, shift, reason))
        issue_id = cur.fetchone()["issue_id"]
        counts = _apply_issue(cur, road_no, issue_id, payload.from_date, datetime.timedelta(days=shift))
        cur.execute("UPDATE road_issue SET activities_shifted = %s WHERE issue_id = %s", (counts["moved"], issue_id))
    return {"issue_id": issue_id, "road_no": road_no, "shift_days": shift, **counts}


@router.put("/roads/{road_no}/issues/{issue_id}")
def update_road_issue(road_no: int, issue_id: int, payload: RoadIssueCreate):
    shift = _issue_days(payload.from_date, payload.to_date)
    reason = (payload.reason or "").strip() or None
    with _issue_tx() as cur:
        _lock_road(cur, road_no)
        old = _get_open_issue(cur, road_no, issue_id)
        _revert_issue(cur, road_no, issue_id, old["shift_days"])
        counts = _apply_issue(cur, road_no, issue_id, payload.from_date, datetime.timedelta(days=shift))
        cur.execute(
            "UPDATE road_issue SET from_date=%s, to_date=%s, shift_days=%s, activities_shifted=%s, reason=%s "
            "WHERE issue_id=%s", (payload.from_date, payload.to_date, shift, counts["moved"], reason, issue_id))
    return {"issue_id": issue_id, "road_no": road_no, "shift_days": shift, **counts}


@router.delete("/roads/{road_no}/issues/{issue_id}")
def delete_road_issue(road_no: int, issue_id: int):
    """Delete the issue AND put its shifted dates back."""
    with _issue_tx() as cur:
        _lock_road(cur, road_no)
        old = _get_open_issue(cur, road_no, issue_id)
        _revert_issue(cur, road_no, issue_id, old["shift_days"])
        cur.execute("DELETE FROM road_issue WHERE issue_id = %s", (issue_id,))
    return {"deleted": True, "issue_id": issue_id, "reverted_days": old["shift_days"],
            "activities_restored": old["activities_shifted"]}


@router.delete("/activities/{activity_id}/weekly/{entry_id}")
def delete_weekly_entry(activity_id: int, entry_id: int):
    """Remove ONE ledger submission by its entry_id. In the append-only model
    a week can hold several submissions, so deletion targets a single row (not
    the whole week). No gating check: removing a row only lowers the
    activity's cumulative, which is always allowed now (BR-08 superseded)."""
    n = db.execute(
        "DELETE FROM weekly_entry WHERE entry_id = %s AND activity_id = %s",
        (entry_id, activity_id),
    )
    if not n:
        raise HTTPException(404, f"No weekly entry {entry_id} for activity {activity_id}")
    return {"deleted": n, "activity_id": activity_id, "entry_id": entry_id}


@router.get("/edit-log")
def edit_log(table_name: str | None = None, row_pk: int | None = None,
             limit: int = 500):
    """Audit trail of road / activity edits (newest first)."""
    where, params = [], []
    if table_name:
        if table_name not in ("road", "activity"):
            raise HTTPException(422, "table_name must be 'road' or 'activity'")
        where.append("table_name = %s")
        params.append(table_name)
    if row_pk is not None:
        where.append("row_pk = %s")
        params.append(row_pk)
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

    # Validate every activity exists before writing anything. No gating check
    # (BR-08 superseded) - any order is allowed.
    for p in parsed:
        if not db.query("SELECT 1 FROM activity WHERE activity_id = %s", (p["activity_id"],)):
            raise HTTPException(404, f"Unknown activity_id {p['activity_id']}")

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for p in parsed:
                week_end = p["week_start"] + datetime.timedelta(days=6)
                # Append-only ledger: each imported row ADDS to the activity's
                # cumulative; it does not overwrite an existing week's figure.
                cur.execute(
                    """
                    INSERT INTO weekly_entry (activity_id, week_start_date, week_end_date,
                        planned_qty_override, actual_qty, note)
                    VALUES (%s, %s, %s, %s, %s, %s)
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


def _fmt_date_ddmmyyyy(d):
    """DD-MM-YYYY for any date written into a user-facing text output.

    Mirrors fmtDate() in dashboard.js. The project convention is: dates
    travel as ISO (YYYY-MM-DD) through the DB and the API because that is
    machine format - unambiguous and sortable - and are converted to
    DD-MM-YYYY only at the point a human reads them. Exported files are a
    human-read surface that does NOT pass through dashboard.js, so the
    conversion has to happen here instead.
    """
    return d.strftime("%d-%m-%Y") if d else ""


def _xlsx_response(rows, headers, filename):
    """Build an .xlsx download with a bold header row and columns
    auto-sized to their content.

    A plain .csv is just comma-separated text - it has no concept of bold
    or column width, so there is no CSV-side way to satisfy "headers bold,
    columns sized to content". A real spreadsheet is the only honest way
    to deliver that, hence openpyxl (already a project dependency) instead
    of csv.writer here.

    Date cells are written as real dates carrying an explicit DD-MM-YYYY
    number format rather than as pre-formatted text: Excel then DISPLAYS
    DD-MM-YYYY (the project convention) while still treating the value as
    a date, so sorting and filtering in the spreadsheet stay correct. A
    text "15-10-2026" would sort alphabetically, which is wrong.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append(r)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, (datetime.date, datetime.datetime)):
                cell.number_format = "DD-MM-YYYY"

    for col_idx, header in enumerate(headers, start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        widest = len(str(header))
        for r in rows:
            val = r[col_idx - 1]
            if isinstance(val, (datetime.date, datetime.datetime)):
                shown = 10  # DD-MM-YYYY as Excel will render it
            else:
                shown = len(str(val)) if val is not None else 0
            widest = max(widest, shown)
        ws.column_dimensions[letter].width = widest + 2  # padding so text isn't flush against the cell edge

    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Cache-Control": "no-store"},
    )


@router.get("/export/weekly-history")
def export_weekly_history():
    rows = calc.weekly_history_rows()
    data = [
        [_fmt_date_ddmmyyyy(r["week_start_date"]), r["road_no"], r["activity"],
         float(r["actual_qty"]), r["note"] or ""]
        for r in rows
    ]
    return _csv_response(data, ["Week Starting", "Road", "Activity", "Actual Qty", "Note"],
                          "progress_history.csv")


_SUMMARY_COLUMNS = ["road", "location", "vendor", "line_manager", "activities", "actual_pct", "planned_pct", "status"]
_SUMMARY_LABELS = {
    "road": "Road", "location": "Location", "vendor": "Vendor", "line_manager": "Line Manager",
    "activities": "Activities", "actual_pct": "Actual %", "planned_pct": "Planned %", "status": "Status",
}

# Single-road export is activity-grain (calc.road_activity_rows), a
# different shape from the all-roads summary above: "vendor"/"line_manager"/
# "activities"(count) describe a road among many roads and don't apply to
# one road's own activity list, while planned_qty/actual_qty/dates do.
_ROAD_COLUMNS = ["activity", "major", "unit", "planned_qty", "actual_qty",
                 "actual_pct", "planned_pct", "start_date", "end_date", "status"]
_ROAD_LABELS = {
    "activity": "Activity", "major": "Major", "unit": "Unit", "planned_qty": "Planned Qty",
    "actual_qty": "Actual Qty", "actual_pct": "Actual %", "planned_pct": "Planned %",
    "start_date": "Start Date", "end_date": "End Date", "status": "Status",
}


@router.get("/export/summary")
def export_summary(line_manager: str | None = None, columns: str | None = None, road_no: int | None = None):
    """Excel (.xlsx) export of the road summary - bold header row, columns
    auto-sized to content, headers shown in the same wording as the Roads
    tab UI (not the underlying db/API field names).

    `columns` is an optional comma-separated subset of the columns valid for
    the chosen scope (the column-picker dialog on the Roads tab sends this)
    so the user only gets the fields they asked for. Omitted/blank -> all
    columns for that scope.

    `road_no` switches the export from the all-roads summary
    (_SUMMARY_COLUMNS, one row per road) to a single road's own activities
    (_ROAD_COLUMNS, one row per activity) - the two scopes show different
    things, so they also have different column sets.
    """
    today = datetime.date.today().isoformat()

    if road_no is not None:
        rows = calc.road_activity_rows(road_no)
        if not rows:
            raise HTTPException(404, f"road {road_no} not found or has no activities")
        full = {
            "activity": lambda r: r["activity"],
            "major": lambda r: r["major"],
            "unit": lambda r: r["unit"],
            "planned_qty": lambda r: r["planned_qty"],
            "actual_qty": lambda r: r["actual_qty"],
            "actual_pct": lambda r: r["actual_pct"],
            "planned_pct": lambda r: r["planned_pct"],
            "start_date": lambda r: r["start_date"] or "",
            "end_date": lambda r: r["end_date"] or "",
            "status": lambda r: r["status"],
        }
        valid_columns, labels = _ROAD_COLUMNS, _ROAD_LABELS
        fname = f"summary_road{road_no}_{today}.xlsx"
    else:
        rows = calc.road_summary_rows(line_manager)
        full = {
            "road": lambda r: f"Road {r['road_no']}",
            "location": lambda r: r["location"],
            "vendor": lambda r: r["vendor"] or "",
            "line_manager": lambda r: r["line_manager"] or "",
            "activities": lambda r: r["activities"],
            "actual_pct": lambda r: r["actual_pct"],
            "planned_pct": lambda r: r["planned_pct"],
            "status": lambda r: r["status"],
        }
        valid_columns, labels = _SUMMARY_COLUMNS, _SUMMARY_LABELS
        fname = f"summary_{today}.xlsx"

    selected = [c.strip() for c in columns.split(",") if c.strip()] if columns else valid_columns
    unknown = [c for c in selected if c not in valid_columns]
    if unknown:
        raise HTTPException(422, f"unknown column(s) for this scope: {', '.join(unknown)}")
    if not selected:
        raise HTTPException(422, "select at least one column")

    data = [[full[c](r) for c in selected] for r in rows]
    headers = [labels[c] for c in selected]
    return _xlsx_response(data, headers, fname)