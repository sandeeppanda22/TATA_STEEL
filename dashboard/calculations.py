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
# BR-08: percentage gating. An activity's actual % may not exceed the
# actual % of the previous PRESENT activity in BR-03 order (seq_no), within
# the same road. Activities with seq_no IS NULL (not in BR-03 - BR-02's
# non-weighted extras) are outside the gated chain entirely.
# ---------------------------------------------------------------------------
def check_gating(activity_id):
    """Raise ValueError with a human message if this activity's cumulative %
    would exceed the previous present (seq_no-ordered) activity's %."""
    rows = db.query(
        """
        SELECT a.activity_id, a.seq_no, a.road_no, a.planned_qty,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a
        LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        WHERE a.road_no = (SELECT road_no FROM activity WHERE activity_id = %s)
          AND a.seq_no IS NOT NULL
        GROUP BY a.activity_id, a.seq_no, a.road_no, a.planned_qty
        ORDER BY a.seq_no
        """,
        (activity_id,),
    )
    pct_by_seq = {}
    this_seq = None
    for r in rows:
        pct = (float(r["cum_qty"]) / float(r["planned_qty"]) * 100) if r["planned_qty"] else 0
        pct_by_seq[r["seq_no"]] = pct
        if r["activity_id"] == activity_id:
            this_seq = r["seq_no"]
    if this_seq is None:
        return  # activity not in the gated chain (BR-02 extra, no seq_no)
    prior_seqs = [s for s in pct_by_seq if s < this_seq]
    if not prior_seqs:
        return
    prev_pct = pct_by_seq[max(prior_seqs)]
    this_pct = pct_by_seq[this_seq]
    if this_pct > prev_pct + 1e-6:
        raise ValueError(
            f"This activity would be at {this_pct:.1f}% complete, "
            f"ahead of the previous activity in sequence at {prev_pct:.1f}%. "
            f"A layer cannot run ahead of the layer beneath it."
        )


# ---------------------------------------------------------------------------
# BR-06: road progress % = sum(major % complete * major weight).
# BR-09: drain % tracked separately, same cumulative/planned mechanism.
# ---------------------------------------------------------------------------
def road_progress(road_no):
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
    for a in acts:
        pct = (float(a["cum_qty"]) / float(a["planned_qty"]) * 100) if a["planned_qty"] else 0
        if a["is_drain"]:
            drain_pct = round(pct, 2)
            continue  # drain is not one of the 5 majors (BR-09), excluded from road_pct
        if a["major"]:
            road_pct += pct * float(a["weight_pct"]) / 100
    return round(road_pct, 2), drain_pct


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
        pct, drain_pct = road_progress(r["road_no"])
        share = float(r["length_m"]) / total_len * 100
        weighted += pct * float(r["length_m"]) / total_len
        detail.append({"road_no": r["road_no"], "road_pct": pct, "drain_pct": drain_pct,
                       "planned_pct": road_planned_progress(r["road_no"]),
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
        actual_pct, _ = road_progress(r["road_no"])
        planned_pct = road_planned_progress(r["road_no"])
        n_acts = db.query("SELECT COUNT(*) AS n FROM activity WHERE road_no = %s", (r["road_no"],))[0]["n"]
        status = "On track" if actual_pct >= planned_pct - 1e-6 else "Behind"
        out.append({"road_no": r["road_no"], "location": r["location"], "vendor": r["vendor"],
                    "line_manager": r["line_manager"], "activities": n_acts,
                    "actual_pct": actual_pct, "planned_pct": planned_pct, "status": status})
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


def _group_performance(group_field, line_manager=None):
    today = datetime.date.today()
    if line_manager:
        roads = db.query(
            f"SELECT road_no, {group_field} AS grp, length_m, start_date, end_date "
            "FROM road WHERE line_manager = %s", (line_manager,))
    else:
        roads = db.query(
            f"SELECT road_no, {group_field} AS grp, length_m, start_date, end_date FROM road")
    acts = db.query(
        """
        SELECT a.activity_id, a.road_no, a.start_date, a.end_date, a.planned_qty,
               COALESCE(SUM(w.actual_qty), 0) AS cum_qty
        FROM activity a LEFT JOIN weekly_entry w ON w.activity_id = a.activity_id
        GROUP BY a.activity_id, a.road_no, a.start_date, a.end_date, a.planned_qty
        """
    )
    acts_by_road = {}
    for a in acts:
        acts_by_road.setdefault(a["road_no"], []).append(a)

    out = {}
    for r in roads:
        grp = r["grp"] or "(unassigned)"
        pct, _ = road_progress(r["road_no"])
        o = out.setdefault(grp, {"group": grp, "roads": 0, "weighted_pct_sum": 0.0,
                                 "length_sum": 0.0, "slippage_days": [], "activities_behind": 0})
        o["roads"] += 1
        o["weighted_pct_sum"] += pct * float(r["length_m"])
        o["length_sum"] += float(r["length_m"])
        if r["end_date"]:
            o["slippage_days"].append((today - r["end_date"]).days if pct < 100 else None)
        for a in acts_by_road.get(r["road_no"], []):
            exp = _expected_pct_today(a["start_date"], a["end_date"], today)
            act_pct = (float(a["cum_qty"]) / float(a["planned_qty"]) * 100) if a["planned_qty"] else 0
            if act_pct < exp - 1e-6:
                o["activities_behind"] += 1

    results = []
    for grp, o in out.items():
        slips = [s for s in o["slippage_days"] if s is not None]
        results.append({
            "group": grp,
            "roads": o["roads"],
            "progress_pct": round(o["weighted_pct_sum"] / o["length_sum"], 2) if o["length_sum"] else 0,
            "avg_slippage_days": round(sum(slips) / len(slips), 1) if slips else None,
            "activities_behind": o["activities_behind"],
        })
    return sorted(results, key=lambda x: -x["progress_pct"])


def vendor_performance(line_manager=None):
    return _group_performance("vendor", line_manager)


def line_manager_performance(line_manager=None):
    return _group_performance("line_manager", line_manager)


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
                reasons.append(f"behind planned by {exp - pct:.1f} pts (> {threshold_pct})")
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
                           "actual_pct": round(pct, 1), "expected_pct": exp, "reasons": reasons})
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
