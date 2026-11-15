"""All derived calculations for the dashboard. Every function cites the
business-rule (BR-xx) or open-ruling (OPEN-xx) it implements, per
docs/01_business_rules.md. Nothing here invents a number the rules don't
already specify.
"""
import datetime

from dashboard import db

MAJOR_ORDER = ["Excavation", "GSB", "WMM", "DLC", "Road"]


# ---------------------------------------------------------------------------
# OPEN-02: straight-line default planned spread of an activity's planned_qty
# across its start_date..end_date, used whenever a week has no explicit
# planned_qty_override.
# ---------------------------------------------------------------------------
def week_bounds(d):
    """Monday..Sunday week containing date d (OPEN-06)."""
    start = d - datetime.timedelta(days=d.isoweekday() - 1)
    return start, start + datetime.timedelta(days=6)


def straight_line_weeks(start_date, end_date, planned_qty):
    """Return [(week_start, week_end, planned_for_this_week), ...] spanning
    start_date..end_date, splitting planned_qty evenly per calendar day then
    summed into Monday-start weeks (OPEN-02 default; OPEN-06 week keying)."""
    if not start_date or not end_date or end_date < start_date:
        return []
    total_days = (end_date - start_date).days + 1
    per_day = planned_qty / total_days if total_days else 0
    weeks = {}
    d = start_date
    while d <= end_date:
        ws, we = week_bounds(d)
        weeks.setdefault(ws, [we, 0.0])
        weeks[ws][1] += per_day
        d += datetime.timedelta(days=1)
    return [(ws, we, round(q, 3)) for ws, (we, q) in sorted(weeks.items())]


# ---------------------------------------------------------------------------
# BR-08 (percentage gating) was REMOVED by user ruling 2026-10-13. Progress
# may be entered for any activity in any order; no check_gating() exists any
# more and nothing restricts an activity from running ahead of the one before
# it. The pyramid/road views fill each layer by its own actual %.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BR-06: road progress % = sum(major % complete * major weight).
# BR-09: drain % tracked separately, same cumulative/planned mechanism.
# ---------------------------------------------------------------------------
def road_progress(road_no):
    """Returns (road_pct, drain_pct, over_planned_count).

    road_pct/drain_pct are each capped at 100: an activity whose recorded
    actual exceeds its planned_qty is reported as 100% complete, never above
    - the planned figure is the thing that is wrong in that case, not the
    progress. over_planned_count is how many (non-drain or drain) activities
    on this road are in that state, so the UI can warn the user to raise the
    planned_qty rather than silently showing >100%.
    """
    acts = db.query(
        """
        SELECT a.activity_id, a.raw_name, a.major, a.weight_pct, a.planned_qty,
               a.is_drain, COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a
        LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        WHERE a.road_no = %s
        GROUP BY a.activity_id, a.raw_name, a.major, a.weight_pct,
                 a.planned_qty, a.is_drain
        """,
        (road_no,),
    )
    road_pct = 0.0
    drain_pct = None
    over_planned = 0
    for a in acts:
        planned = float(a["planned_qty"]) if a["planned_qty"] else 0
        cum = float(a["cum_qty"])
        raw_pct = (cum / planned * 100) if planned else 0
        pct = min(raw_pct, 100)
        if planned and cum > planned + 1e-6:
            over_planned += 1
        if a["is_drain"]:
            drain_pct = round(pct, 2)
            continue  # drain is not one of the 5 majors (BR-09), excluded from road_pct
        if a["major"]:
            road_pct += pct * float(a["weight_pct"]) / 100
    return round(road_pct, 2), drain_pct, over_planned


def road_planned_progress(road_no, as_of=None):
    """BR-06-weighted straight-line planned-to-date % for a road (OPEN-02).

    Mirrors road_progress() but uses each activity's schedule expectation
    (straight line between start/end) instead of recorded actuals, so the UI
    can show "where this road should be today" next to where it actually is.
    """
    as_of = as_of or datetime.date.today()
    acts = db.query(
        "SELECT major, weight_pct, is_drain, start_date, end_date "
        "FROM activity WHERE road_no = %s",
        (road_no,),
    )
    return round(_road_planned_pct(acts, as_of), 2)


# ---------------------------------------------------------------------------
# BR-07: overall % = length-weighted average across whatever roads exist
# right now (BR-12: not hard-coded to 17 roads / 5065 m).
# ---------------------------------------------------------------------------
def overall_progress(line_manager=None):
    if line_manager:
        roads = db.query("SELECT road_no, length_m FROM road WHERE line_manager = %s",
                         (line_manager,))
    else:
        roads = db.query("SELECT road_no, length_m FROM road")
    total_len = sum(float(r["length_m"]) for r in roads) or 1
    weighted = 0.0
    detail = []
    for r in roads:
        pct, drain_pct, over_planned = road_progress(r["road_no"])
        share = float(r["length_m"]) / total_len * 100
        weighted += pct * float(r["length_m"]) / total_len
        detail.append({"road_no": r["road_no"], "road_pct": pct, "drain_pct": drain_pct,
                       "planned_pct": road_planned_progress(r["road_no"]),
                       "over_planned_count": over_planned,
                       "length_m": float(r["length_m"]), "length_weight_pct": round(share, 2)})
    return round(weighted, 2), detail


# ---------------------------------------------------------------------------
# CSV export support (parity with the legacy app's /api/export/history and
# /api/export/summary). Row-shaping only; db_routes.py renders the CSV text
# and sets the download headers.
# ---------------------------------------------------------------------------
def weekly_history_rows():
    """Every recorded weekly actual, flattened for CSV export."""
    return db.query(
        """
        SELECT w.week_start_date, w.week_end_date, a.road_no, a.raw_name AS activity,
               w.actual_qty, w.note
        FROM weekly_entry w JOIN activity a ON a.activity_id = w.activity_id
        ORDER BY w.week_start_date, a.road_no, a.sheet_row_order
        """
    )


def road_summary_rows(line_manager=None):
    """One row per road: vendor, line manager, actual vs planned %, status.

    status is "On track" when actual_pct is at or above planned_pct (allowing
    a small tolerance for rounding), else "Behind".
    """
    if line_manager:
        roads = db.query(
            "SELECT road_no, location, vendor, line_manager FROM road "
            "WHERE line_manager = %s ORDER BY road_no", (line_manager,))
    else:
        roads = db.query("SELECT road_no, location, vendor, line_manager FROM road ORDER BY road_no")
    out = []
    for r in roads:
        actual_pct, _, _ = road_progress(r["road_no"])
        planned_pct = road_planned_progress(r["road_no"])
        n_acts = db.query("SELECT COUNT(*) AS n FROM activity WHERE road_no = %s", (r["road_no"],))[0]["n"]
        status = "On track" if actual_pct >= planned_pct - 1e-6 else "Behind"
        out.append({"road_no": r["road_no"], "location": r["location"], "vendor": r["vendor"],
                    "line_manager": r["line_manager"], "activities": n_acts,
                    "actual_pct": actual_pct, "planned_pct": planned_pct, "status": status})
    return out


def road_activity_rows(road_no):
    """One row per activity for a SINGLE road: planned vs actual qty/% and
    status, at activity grain.

    This is deliberately a different shape from road_summary_rows (which is
    one row per road across many roads) - a single-road export is about
    that road's activities, not about comparing it to other roads, so
    "activities count"/"vendor"/"line_manager" (useful across many roads)
    don't apply here, while per-activity planned_qty/actual_qty/dates do.
    planned_pct uses each activity's own straight-line expected % for today
    (OPEN-02), since activities on the same road can have different
    start/end windows (status mirrors road_summary_rows' On track/Behind
    convention, applied per activity instead of per road).
    """
    today = datetime.date.today()
    acts = db.query(
        """
        SELECT a.activity_id, a.raw_name, a.major, a.unit, a.planned_qty,
               a.start_date, a.end_date,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        WHERE a.road_no = %s
        GROUP BY a.activity_id, a.raw_name, a.major, a.unit, a.planned_qty,
                 a.start_date, a.end_date
        ORDER BY a.sheet_row_order
        """, (road_no,))
    out = []
    for a in acts:
        planned = float(a["planned_qty"]) if a["planned_qty"] else 0.0
        cum = float(a["cum_qty"])
        actual_pct = round(min(cum / planned * 100, 100), 1) if planned else 0.0
        planned_pct = round(_expected_pct_today(a["start_date"], a["end_date"], today), 1)
        status = "On track" if actual_pct >= planned_pct - 1e-6 else "Behind"
        out.append({
            "activity": a["raw_name"], "major": a["major"] or "", "unit": a["unit"] or "",
            "planned_qty": planned, "actual_qty": cum,
            "actual_pct": actual_pct, "planned_pct": planned_pct,
            "start_date": a["start_date"], "end_date": a["end_date"], "status": status,
        })
    return out


# ---------------------------------------------------------------------------
# OPEN-04: vendor / line-manager performance - "all" measures.
#   - progress_pct: weighted road progress across their roads (BR-06/07 style)
#   - slippage_days: today - planned end date, for roads not yet at 100%
#     (negative = ahead of schedule, i.e. not yet due)
#   - activities_behind: count of activities where actual % < expected %
#     for today (expected % from straight-line schedule, OPEN-02)
# ---------------------------------------------------------------------------
def _expected_pct_today(start_date, end_date, today):
    if not start_date or not end_date or end_date <= start_date:
        return 100.0 if (end_date and today >= end_date) else 0.0
    if today <= start_date:
        return 0.0
    if today >= end_date:
        return 100.0
    return round((today - start_date).days / (end_date - start_date).days * 100, 2)


def _road_filter(line_manager=None, vendor=None):
    """WHERE clause + params restricting roads by line manager and/or vendor."""
    conds, params = [], []
    if line_manager:
        conds.append("line_manager = %s"); params.append(line_manager)
    if vendor:
        conds.append("vendor = %s"); params.append(vendor)
    return (" WHERE " + " AND ".join(conds)) if conds else "", tuple(params)


def _group_performance(group_field, line_manager=None, vendor=None):
    """Per vendor / line manager: progress %, activities behind, planned vs
    actual QUANTITY (summed per unit only - m, m3, nos... aren't additive) and a
    road-wise breakdown (each road's progress/planned % and its activities'
    quantities) for the UI's click-to-expand row."""
    today = datetime.date.today()
    where, params = _road_filter(line_manager, vendor)
    roads = db.query(
        f"SELECT road_no, location, {group_field} AS grp, length_m FROM road{where}", params)
    acts = db.query(
        """
        SELECT a.activity_id, a.road_no, a.raw_name, a.unit, a.major, a.weight_pct, a.is_drain,
               a.start_date, a.end_date, a.planned_qty,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        GROUP BY a.activity_id, a.road_no, a.raw_name, a.unit, a.major, a.weight_pct, a.is_drain,
                 a.start_date, a.end_date, a.planned_qty, a.sheet_row_order
        ORDER BY a.sheet_row_order
        """
    )
    acts_by_road = {}
    for a in acts:
        acts_by_road.setdefault(a["road_no"], []).append(a)

    out = {}
    for r in roads:
        grp = r["grp"] or "(unassigned)"
        pct, _, _ = road_progress(r["road_no"])
        ra = acts_by_road.get(r["road_no"], [])
        o = out.setdefault(grp, {"group": grp, "roads": 0, "weighted_pct_sum": 0.0,
                                 "length_sum": 0.0, "activities_behind": 0,
                                 "units": {}, "road_rows": []})
        o["roads"] += 1
        o["weighted_pct_sum"] += pct * float(r["length_m"])
        o["length_sum"] += float(r["length_m"])
        act_rows = []
        for a in ra:
            pq = float(a["planned_qty"]) if a["planned_qty"] else 0.0
            done = float(a["cum_qty"])
            act_pct = (done / pq * 100) if pq else 0
            if act_pct < _expected_pct_today(a["start_date"], a["end_date"], today) - 1e-6:
                o["activities_behind"] += 1
            u = o["units"].setdefault(a["unit"] or "-", {"planned_qty": 0.0, "actual_qty": 0.0})
            u["planned_qty"] += pq
            u["actual_qty"] += done
            act_rows.append({
                "activity": a["raw_name"], "unit": a["unit"] or "",
                "planned_qty": round(pq, 2), "actual_qty": round(done, 2),
                "actual_pct": round(min(act_pct, 100), 1),
            })
        o["road_rows"].append({
            "road_no": r["road_no"], "location": r["location"],
            "progress_pct": round(pct, 2),
            "planned_pct": round(_road_planned_pct(ra, today), 2),
            "activities": act_rows,
        })

    results = []
    for grp, o in out.items():
        results.append({
            "group": grp,
            "roads": o["roads"],
            "progress_pct": round(o["weighted_pct_sum"] / o["length_sum"], 2) if o["length_sum"] else 0,
            "activities_behind": o["activities_behind"],
            "quantity_by_unit": [
                {"unit": u, "planned_qty": round(q["planned_qty"], 2), "actual_qty": round(q["actual_qty"], 2)}
                for u, q in sorted(o["units"].items())],
            "road_rows": sorted(o["road_rows"], key=lambda x: x["road_no"]),
        })
    return sorted(results, key=lambda x: -x["progress_pct"])


def vendor_performance(line_manager=None, vendor=None):
    return _group_performance("vendor", line_manager, vendor)


def line_manager_performance(line_manager=None, vendor=None):
    return _group_performance("line_manager", line_manager, vendor)


# ---------------------------------------------------------------------------
# Monthly backlog + next-month target, per vendor / line manager.
#
# Everything is a BR-06 weighted road % (same basis as road_progress), then
# length-weighted across a group's roads (same basis as _group_performance),
# so the figures are comparable with the existing Progress % column.
#   planned(d)  straight-line planned % at date d            (OPEN-02)
#   actual(d)   recorded % counting ledger rows with week_start_date <= d
#   backlog     max(0, planned(as_of) - actual(as_of)), PER ACTIVITY, weighted
#   target      max(0, planned(as_of + horizon) - actual(as_of)), per activity
#   next_planned target - backlog: work still needed in the window on top of
#               the backlog. It already accounts for actual progress - an
#               activity ahead of schedule needs less (or none) of the
#               window's scheduled work.
# ---------------------------------------------------------------------------
_GROUP_FIELDS = ("vendor", "line_manager")


def _month_end(year, month):
    nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def backlog_months():
    """Months (YYYY-MM) for the backlog dropdown: from the programme's first
    activity start up to the CURRENT month. Backlog is work that was due and
    isn't done, so a future month has none and is never offered."""
    r = db.query("SELECT MIN(start_date) AS lo FROM activity")[0]
    today = datetime.date.today()
    lo = min(x for x in (r["lo"], today) if x)
    out, y, m = [], lo.year, lo.month
    while (y, m) <= (today.year, today.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = y + (m == 12), m % 12 + 1
    return out


def group_plan(group_field, as_of, horizon_days=30, line_manager=None, vendor=None, detail=False):
    """Per-group actual / planned / backlog / next-period planned / target,
    all as of `as_of` (a date). See the block comment above for definitions.

    detail=True also returns, per group, a road-wise breakdown (`road_rows`,
    each with its activities' quantities) and `quantity_by_unit` totals.
    Quantities are only ever summed within one unit (m, m3, nos ... are not
    additive), which is why the group totals are keyed by unit."""
    if group_field not in _GROUP_FIELDS:
        raise ValueError(f"group_field must be one of {_GROUP_FIELDS}")
    horizon_end = as_of + datetime.timedelta(days=horizon_days)
    where, params = _road_filter(line_manager, vendor)
    roads = db.query(f"SELECT road_no, location, {group_field} AS grp, length_m FROM road{where}", params)
    acts = db.query(
        "SELECT activity_id, road_no, raw_name, unit, major, weight_pct, is_drain, "
        "planned_qty, start_date, end_date FROM activity ORDER BY sheet_row_order")
    cum = {r["activity_id"]: float(r["q"]) for r in db.query(
        "SELECT activity_id, SUM(actual_qty) AS q FROM weekly_entry "
        "WHERE week_start_date <= %s GROUP BY activity_id", (as_of,))}
    acts_by_road = {}
    for a in acts:
        acts_by_road.setdefault(a["road_no"], []).append(a)

    groups = {}
    for r in roads:
        ra = acts_by_road.get(r["road_no"], [])
        w = float(r["length_m"] or 0)
        actual = _road_pct_from_cum(ra, cum)
        planned = _road_planned_pct(ra, as_of)
                # Backlog / target are worked out PER ACTIVITY against its actual
        # progress (an activity that is ahead of schedule does not offset one
        # that is behind), then BR-06 weighted into the road:
        #   backlog = max(0, planned(as_of)  - actual)
        #   target  = max(0, planned(to)     - actual)   <- needs actual too
        #   next    = target - backlog  (work still needed in the window
        #                                beyond the backlog; 0 if already ahead)
        backlog = target = 0.0
        behind = 0
        act_rows = []
        for a in ra:
            pq = float(a["planned_qty"]) if a["planned_qty"] else 0
            done = cum.get(a["activity_id"], 0.0)
            ap = (done / pq * 100) if pq else 0
            exp0 = _expected_pct_today(a["start_date"], a["end_date"], as_of)
            exp1 = _expected_pct_today(a["start_date"], a["end_date"], horizon_end)
            if ap < exp0 - 1e-6:
                behind += 1
            if a["major"] and not a["is_drain"]:
                wt = float(a["weight_pct"]) / 100
                capped = min(ap, 100.0)
                backlog += wt * max(0.0, exp0 - capped)
                target += wt * max(0.0, exp1 - capped)
            if detail:
                bq = max(0.0, pq * exp0 / 100 - done)
                tq = max(0.0, pq * exp1 / 100 - done)
                nq = tq - bq
                if tq < 0.005:
                    continue  # nothing left to do in this period (no backlog, none needed)
                act_rows.append({
                    "activity": a["raw_name"], "unit": a["unit"] or "",
                    "planned_qty": round(pq, 2), "actual_qty": round(done, 2),
                    "backlog_qty": round(bq, 2), "next_planned_qty": round(nq, 2),
                    "target_qty": round(tq, 2),
                })
        nxt = target - backlog
        g = groups.setdefault(r["grp"] or "(unassigned)", {
            "roads": 0, "w": 0.0, "actual": 0.0, "planned": 0.0, "backlog": 0.0,
            "next": 0.0, "target": 0.0, "behind": 0, "road_rows": [], "units": {}})
        if detail:  # every road is listed, even with no activity rows in this period
            g["road_rows"].append({
                "road_no": r["road_no"], "location": r["location"],
                "actual_pct": round(actual, 2), "planned_pct": round(planned, 2),
                "backlog_pct": round(backlog, 2), "next_planned_pct": round(nxt, 2),
                "target_pct": round(target, 2),
                "target_cumulative_pct": round(min(100.0, actual + target), 2),
                "activities": act_rows,
            })
            for ar in act_rows:
                u = g["units"].setdefault(ar["unit"] or "-", {"backlog_qty": 0.0, "next_planned_qty": 0.0, "target_qty": 0.0})
                for k in u:
                    u[k] += ar[k]
        g["roads"] += 1
        g["w"] += w
        g["behind"] += behind
        for k, v in (("actual", actual), ("planned", planned), ("backlog", backlog),
                     ("next", nxt), ("target", target)):
            g[k] += v * w

    out = []
    for name, g in groups.items():
        d = g["w"] or 1.0
        actual = g["actual"] / d
        extra = {}
        if detail:
            extra = {
                "road_rows": sorted(g["road_rows"], key=lambda x: x["road_no"]),
                "quantity_by_unit": [
                    {"unit": u, **{k: round(v, 2) for k, v in q.items()}}
                    for u, q in sorted(g["units"].items())],
            }
        out.append({
            **extra,
            "group": name, "roads": g["roads"],
            "actual_pct": round(actual, 2),
            "planned_pct": round(g["planned"] / d, 2),
            "backlog_pct": round(g["backlog"] / d, 2),
            "next_planned_pct": round(g["next"] / d, 2),
            "target_pct": round(g["target"] / d, 2),
            "target_cumulative_pct": round(min(100.0, actual + g["target"] / d), 2),
            "activities_behind": g["behind"],
        })
    return sorted(out, key=lambda x: -x["backlog_pct"])


def group_backlog(group_field, month=None, line_manager=None, vendor=None):
    """Backlog as at the END of `month` (YYYY-MM, default current month). For
    the current month the "end" is today - days that haven't happened can't
    have produced a backlog. Future months are rejected."""
    today = datetime.date.today()
    y, m = (int(x) for x in month.split("-")) if month else (today.year, today.month)
    if (y, m) > (today.year, today.month):
        raise ValueError("backlog is only available up to the current month")
    as_of = min(_month_end(y, m), today)
    return {"month": f"{y:04d}-{m:02d}", "as_of": as_of.isoformat(),
            "rows": group_plan(group_field, as_of, 30, line_manager, vendor)}


def group_target(group_field, date_from=None, date_to=None, line_manager=None, vendor=None):
    """Target for the window date_from..date_to (default: today .. +30 days;
    the window can't start in the past - a target is forward-looking)
    = backlog as at date_from + planned work between date_from and date_to."""
    today = datetime.date.today()
    start = date_from or today
    end = date_to or (start + datetime.timedelta(days=30))
    if start < today:
        raise ValueError("a target window must start today or later; past dates are not allowed")
    if end <= start:
        raise ValueError("'to' date must be after 'from' date")
    return {"from": start.isoformat(), "to": end.isoformat(),
            "rows": group_plan(group_field, start, (end - start).days, line_manager, vendor, detail=True)}


# ---------------------------------------------------------------------------
# OPEN-05: bottleneck - "all" conditions. An activity is reported as a
# bottleneck if ANY of:
#   (a) behind planned-to-date by more than `threshold_pct` (default 15)
#   (b) it is gating a successor (BR-08): a later activity in the same road
#       is waiting at its own cap because of this one
#   (c) zero actual progress recorded for >= `stale_weeks` (default 3)
#       consecutive weeks since its start_date has passed
# Thresholds are deliberately parameters, not invented fixed facts - call
# with your own numbers if 15%/3 weeks isn't what you want.
# ---------------------------------------------------------------------------
def bottlenecks(threshold_pct=15, stale_weeks=3):
    today = datetime.date.today()
    acts = db.query(
        """
        SELECT a.activity_id, a.road_no, a.raw_name, a.seq_no, a.start_date,
               a.end_date, a.planned_qty, r.line_manager, r.location,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty,
               MAX(w.week_start_date) AS last_entry_week
        FROM activity a
        JOIN road r ON r.road_no = a.road_no
        LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        GROUP BY a.activity_id, a.road_no, a.raw_name, a.seq_no, a.start_date,
                 a.end_date, a.planned_qty, r.line_manager, r.location
        ORDER BY a.road_no, a.seq_no
        """
    )
    by_road = {}
    for a in acts:
        by_road.setdefault(a["road_no"], []).append(a)

    out = []
    for road_no, road_acts in by_road.items():
        seq_acts = sorted((a for a in road_acts if a["seq_no"] is not None), key=lambda a: a["seq_no"])
        for i, a in enumerate(seq_acts):
            pct = (float(a["cum_qty"]) / float(a["planned_qty"]) * 100) if a["planned_qty"] else 0
            exp = _expected_pct_today(a["start_date"], a["end_date"], today)
            reasons = []
            if exp - pct > threshold_pct:
                reasons.append(f"behind planned by {exp - pct:.1f}% (more than {threshold_pct}%)")
            if i + 1 < len(seq_acts):
                nxt = seq_acts[i + 1]
                nxt_pct = (float(nxt["cum_qty"]) / float(nxt["planned_qty"]) * 100) if nxt["planned_qty"] else 0
                if abs(nxt_pct - pct) < 1e-6 and pct < 100 and nxt["start_date"] and nxt["start_date"] <= today:
                    reasons.append("holding up the next activity in sequence")
            if a["start_date"] and a["start_date"] <= today and pct == 0:
                weeks_since_start = (today - a["start_date"]).days // 7
                if weeks_since_start >= stale_weeks:
                    reasons.append(f"zero progress for {weeks_since_start} weeks since start")
            if reasons:
                out.append({"road_no": road_no, "line_manager": a["line_manager"],
                           "location": a["location"], "activity": a["raw_name"],
                           "actual_pct": round(min(pct, 100), 1), "expected_pct": exp, "reasons": reasons})
    return out


# ---------------------------------------------------------------------------
# BR-14 / OPEN-03: Critical Path Method, BR-03-backbone method (confirmed
# ruling, superseding the earlier date-feasibility draft).
#
# The dependency graph is NOT derived from dates. BR-03's 17-step canonical
# sequence (seq_no) IS the real physical dependency chain for a road's
# present activities - you cannot do WMM before Excavation finishes, that is
# a construction-logic fact, not an inference. So the "critical path" for a
# road is simply its full chain of present activities (seq_no order) - a
# single line, no branching, so every present activity is on the path by
# definition (zero float in a pure linear chain).
#
# Real recorded start_date/end_date are used only to compute duration and to
# surface overlaps: wherever activity[i+1].start_date < activity[i].end_date,
# that reflects planned parallel working (e.g. soil disposal starting once
# excavation is partway done) - expected on a real site, not a data error.
# It's surfaced (never silently hidden) along with how far through its own
# schedule the predecessor was, by date proportion, when the successor
# started (pct_predecessor_elapsed) - a descriptive figure, not a threshold.
# ---------------------------------------------------------------------------
def critical_path(road_no):
    acts = db.query(
        "SELECT a.activity_id, a.raw_name, a.seq_no, a.start_date, a.end_date, a.planned_qty, "
        "a.is_drain, COALESCE(SUM(w.actual_qty), 0) AS cum_qty "
        "FROM activity a LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id "
        "WHERE a.road_no = %s AND a.seq_no IS NOT NULL "
        "GROUP BY a.activity_id, a.raw_name, a.seq_no, a.start_date, a.end_date, a.planned_qty, a.is_drain "
        "ORDER BY a.seq_no", (road_no,)
    )
    if not acts:
        return {"road_no": road_no, "path": [], "duration_days": 0,
                "note": "no sequenced activities for this road"}

    dated = [a for a in acts if a["start_date"] and a["end_date"]]
    overlaps = []
    for prev, nxt in zip(dated, dated[1:]):
        if nxt["start_date"] < prev["end_date"]:
            # How far through its own schedule the predecessor was (by date
            # proportion) when the successor kicked off - this is expected,
            # planned parallel working (e.g. Soil Disposal starting once
            # Excavation is partway done), not a data error.
            prev_span = (prev["end_date"] - prev["start_date"]).days
            elapsed = (nxt["start_date"] - prev["start_date"]).days
            pct_elapsed = round(max(0, min(100, (elapsed / prev_span) * 100)), 0) if prev_span > 0 else 100
            overlaps.append({
                "after": prev["raw_name"], "before": nxt["raw_name"],
                "pct_predecessor_elapsed": pct_elapsed,
                "note": f"{nxt['raw_name']} started {nxt['start_date'].strftime('%d-%m-%Y')}, once "
                        f"{prev['raw_name']} was {pct_elapsed:.0f}% through its schedule "
                        f"(ends {prev['end_date'].strftime('%d-%m-%Y')}) - planned parallel working",
            })

    duration_days = (max(a["end_date"] for a in dated) - min(a["start_date"] for a in dated)).days + 1 if dated else 0

    return {
        "road_no": road_no,
        "method": "Canonical build sequence is the dependency chain; "
                  "recorded dates used for duration only",
        "path": [{"activity": a["raw_name"], "seq_no": a["seq_no"],
                  "is_drain": a["is_drain"],
                  "start": a["start_date"].isoformat() if a["start_date"] else None,
                  "end": a["end_date"].isoformat() if a["end_date"] else None,
                  "actual_pct": round(min(100, (float(a["cum_qty"]) / float(a["planned_qty"]) * 100)), 1)
                                if a["planned_qty"] else 0}
                 for a in acts],
        "duration_days": duration_days,
        "overlap_flags": overlaps,
    }


# ---------------------------------------------------------------------------
# BR-15: milestones - each activity's completion, 25/50/100% marks, and
# road-level handover. "Completed" date = the week_start_date of the first
# weekly_entry whose cumulative total crosses the mark. Plus a projected
# (progress-rate-based) and a planned (end_date) view, one month ahead.
# ---------------------------------------------------------------------------
def milestones(window_days=30):
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=window_days)
    acts = db.query(
        """
        SELECT a.activity_id, a.road_no, a.raw_name, a.planned_qty, a.end_date,
               r.location
        FROM activity a JOIN road r ON r.road_no = a.road_no
        """
    )
    out_completed, out_upcoming_planned, out_upcoming_projected = [], [], []
    for a in acts:
        if not a["planned_qty"]:
            continue
        weekly = db.query(
            "SELECT week_start_date, actual_qty FROM weekly_entry "
            "WHERE activity_id = %s ORDER BY week_start_date", (a["activity_id"],)
        )
        # "Completed" marks need weekly actuals; the "planned to finish" list
        # below only needs the planned end_date, so it must run even when no
        # weekly data has been entered yet (e.g. right after a clean rebuild).
        cum = 0.0
        crossed = {}
        for w in weekly:
            cum += float(w["actual_qty"])
            pct = cum / float(a["planned_qty"]) * 100
            for mark in (25, 50, 100):
                if pct >= mark and mark not in crossed:
                    crossed[mark] = w["week_start_date"]
        for mark, wk in crossed.items():
            out_completed.append({"road_no": a["road_no"], "location": a["location"],
                                  "activity": a["raw_name"], "mark_pct": mark,
                                  "reached_week": wk.isoformat()})
        if 100 not in crossed and a["end_date"] and today <= a["end_date"] <= horizon:
            out_upcoming_planned.append({"road_no": a["road_no"], "location": a["location"],
                                         "activity": a["raw_name"], "planned_end": a["end_date"].isoformat()})
            if len(weekly) >= 2:
                days_elapsed = (weekly[-1]["week_start_date"] - weekly[0]["week_start_date"]).days or 1
                rate = cum / days_elapsed
                remaining = float(a["planned_qty"]) - cum
                if rate > 0:
                    projected = today + datetime.timedelta(days=remaining / rate)
                    if projected <= horizon:
                        out_upcoming_projected.append({
                            "road_no": a["road_no"], "location": a["location"],
                            "activity": a["raw_name"], "projected_completion": projected.isoformat()})
    return {
        "completed": out_completed,
        "planned_next_30_days": out_upcoming_planned,
        "projected_next_30_days": out_upcoming_projected,
    }


# ---------------------------------------------------------------------------
# Chart feeds (read-only). Ported from the legacy stdlib app's Charts tab,
# but computed with THIS app's rules: overall % is length-weighted (BR-07),
# planned % is the straight-line schedule expectation (OPEN-02), so the
# S-curve lines up with every other number on the dashboard rather than the
# legacy app's equal-weight average.
# ---------------------------------------------------------------------------
def _road_pct_from_cum(acts_for_road, cum_by_act):
    """BR-06 road % from a per-activity cumulative-quantity map."""
    road_pct = 0.0
    for a in acts_for_road:
        if a["is_drain"] or not a["major"]:
            continue
        planned = float(a["planned_qty"]) if a["planned_qty"] else 0
        cum = cum_by_act.get(a["activity_id"], 0.0)
        pct = (cum / planned * 100) if planned else 0
        if pct > 100:
            pct = 100
        road_pct += pct * float(a["weight_pct"]) / 100
    return road_pct


def _road_planned_pct(acts_for_road, as_of):
    """BR-06-weighted straight-line planned % for a road at date `as_of`."""
    road_pct = 0.0
    for a in acts_for_road:
        if a["is_drain"] or not a["major"]:
            continue
        exp = _expected_pct_today(a["start_date"], a["end_date"], as_of)
        road_pct += exp * float(a["weight_pct"]) / 100
    return road_pct


def activity_status_counts(line_manager=None):
    """Donut feed: how many (non-drain) activities are complete / in progress
    / not started, by cumulative actual vs planned_qty as of now.

    line_manager: when given, only activities on that line manager's roads are
    counted (Charts tab filter)."""
    where, params = "", ()
    if line_manager:
        where = "WHERE r.line_manager = %s"
        params = (line_manager,)
    rows = db.query(
        f"""
        SELECT a.activity_id, a.planned_qty, a.is_drain,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a
        JOIN road r ON r.road_no = a.road_no
        LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        {where}
        GROUP BY a.activity_id, a.planned_qty, a.is_drain
        """,
        params,
    )
    complete = in_progress = not_started = 0
    for r in rows:
        if r["is_drain"]:
            continue
        planned = float(r["planned_qty"]) if r["planned_qty"] else 0
        pct = (float(r["cum_qty"]) / planned * 100) if planned else 0
        if pct >= 100:
            complete += 1
        elif pct > 0:
            in_progress += 1
        else:
            not_started += 1
    return {"complete": complete, "in_progress": in_progress, "not_started": not_started}


def progress_timeline(line_manager=None):
    """S-curve feed. Returns a continuous length-weighted PLANNED line over the
    full schedule span (weekly points), and an ACTUAL line at each week that
    has saved progress (cumulative up to that week). Both use BR-06/07.

    line_manager: when given, only that line manager's roads are included
    (Charts tab filter)."""
    if line_manager:
        roads = db.query("SELECT road_no, length_m FROM road WHERE line_manager = %s",
                         (line_manager,))
    else:
        roads = db.query("SELECT road_no, length_m FROM road")
    total_len = sum(float(r["length_m"]) for r in roads) or 1
    len_by_road = {r["road_no"]: float(r["length_m"]) for r in roads}

    acts = db.query(
        """
        SELECT activity_id, road_no, major, weight_pct, planned_qty, is_drain,
               start_date, end_date
        FROM activity
        """
    )
    # Keep only activities on the roads in scope.
    acts = [a for a in acts if a["road_no"] in len_by_road]
    acts_by_road = {}
    for a in acts:
        acts_by_road.setdefault(a["road_no"], []).append(a)

    entries = db.query(
        "SELECT activity_id, week_start_date, actual_qty FROM weekly_entry "
        "ORDER BY week_start_date"
    )

    def overall(fn):
        total = 0.0
        for road_no, racts in acts_by_road.items():
            total += fn(racts) * len_by_road.get(road_no, 0) / total_len
        return round(total, 2)

    # ---- planned line: continuous weekly points across the schedule span ----
    starts = [a["start_date"] for a in acts if a["start_date"]]
    ends = [a["end_date"] for a in acts if a["end_date"]]
    planned = []
    if starts and ends:
        span_start, _ = week_bounds(min(starts))
        span_end = max(ends)
        wk = span_start
        while wk <= span_end:
            wk_end = wk + datetime.timedelta(days=6)
            pct = overall(lambda racts, d=wk_end: _road_planned_pct(racts, d))
            planned.append({"week": wk.isoformat(), "pct": pct})
            wk += datetime.timedelta(days=7)

    # ---- actual line: one point per week that has entries (cumulative) ------
    actual_weeks = sorted({e["week_start_date"] for e in entries})
    actual = []
    for w in actual_weeks:
        cum_by_act = {}
        for e in entries:
            if e["week_start_date"] <= w:
                cum_by_act[e["activity_id"]] = cum_by_act.get(e["activity_id"], 0.0) + float(e["actual_qty"])
        pct = overall(lambda racts: _road_pct_from_cum(racts, cum_by_act))
        actual.append({"week": w.isoformat(), "pct": pct})

    return {"planned": planned, "actual": actual}
