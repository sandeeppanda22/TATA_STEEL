"""Characterisation tests for the metric functions in dashboard/calculations.py.

A fixed fake dataset is fed through the real functions (db.query is faked,
`today` is pinned) and the outputs are compared with tests/calc_golden.json.
They pin current behaviour so refactors (e.g. the shared activity_pct helper)
can be proven not to change any number.  Regenerate deliberately with:
    python3 tests/test_calculations.py --regen
"""
import datetime as d
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dashboard import calculations as c  # noqa: E402

D = d.date
TODAY = D(2026, 11, 15)
GOLDEN = os.path.join(ROOT, "tests", "calc_golden.json")


class FixedDate(d.date):
    @classmethod
    def today(cls):
        return TODAY


def act(i, road, name, seq, major, w, unit, planned, cum, start, end, drain=False):
    return {"activity_id": i, "road_no": road, "raw_name": name, "seq_no": seq, "major": major,
            "weight_pct": w, "unit": unit, "planned_qty": planned, "cum_qty": cum, "is_drain": drain,
            "start_date": start, "end_date": end, "sheet_row_order": i, "last_entry_week": None}


ROADS = [
    {"road_no": 3, "location": "Demo road", "grp": "V3", "line_manager": "LM2", "vendor": "V3", "length_m": 50,
     "start_date": D(2026, 10, 1), "end_date": D(2026, 11, 1)},
    {"road_no": 1, "location": "Gate", "grp": "V1", "line_manager": "LM1", "vendor": "V1", "length_m": 100,
     "start_date": D(2026, 10, 1), "end_date": D(2026, 12, 31)},
    {"road_no": 2, "location": "Yard", "grp": "V2", "line_manager": "LM1", "vendor": "V2", "length_m": 300,
     "start_date": D(2026, 9, 1), "end_date": D(2026, 11, 30)},
]
ACTS = [
    act(1, 1, "Excavation", 1, "Excavation", 50, "Cum", 1000, 250, D(2026, 10, 1), D(2026, 12, 31)),
    act(2, 1, "WMM", 2, "WMM", 50, "Cum", 500, 0, D(2026, 10, 1), D(2026, 10, 20)),
    act(3, 1, "RCC Drain", 3, None, 0, "Rmt", 120, 60, D(2026, 11, 20), D(2027, 1, 30), True),
    act(4, 2, "Excavation", 1, "Excavation", 60, "Cum", 800, 900, D(2026, 9, 1), D(2026, 10, 31)),   # over-planned
    act(5, 2, "GSB", 2, "GSB", 40, "Cum", 400, 100, D(2026, 10, 15), D(2026, 11, 30)),
    act(6, 2, "Dismantling", None, None, 0, "No", 0, 0, None, None),                                  # no qty, no dates
    act(7, 3, "Demo", 1, "Road", 100, "Cum", 0, 0, D(2026, 10, 1), D(2026, 11, 1)),                   # no planned qty, past schedule
]
WEEKLY = {1: 250, 4: 900, 5: 100, 3: 60}      # cumulative by activity (as_of independent here)


def fake_query(sql, params=()):
    s = " ".join(sql.split())
    if "FROM weekly_entry WHERE week_start_date <=" in s:
        return [{"activity_id": k, "q": v} for k, v in WEEKLY.items()]
    if s.startswith("SELECT activity_id, road_no, raw_name, unit, major"):          # group_plan activities
        return [dict(a) for a in ACTS]
    if "AS grp, length_m FROM road" in s and "start_date" not in s:                 # group_plan roads
        return [dict(r) for r in ROADS]
    if s.startswith("SELECT road_no, location,") and "AS grp" in s:                 # _group_performance roads
        return [dict(r) for r in ROADS]
    if "a.raw_name, a.unit, a.major, a.weight_pct, a.is_drain" in s:                # _group_performance acts
        return [dict(a) for a in ACTS]
    if "a.seq_no" in s and "last_entry_week" in s:                                  # bottlenecks
        by = {r["road_no"]: r for r in ROADS}
        return [{**a, "line_manager": by[a["road_no"]]["line_manager"], "location": by[a["road_no"]]["location"]} for a in ACTS]
    if s.startswith("SELECT a.activity_id, a.planned_qty, a.is_drain"):             # status counts
        return [dict(a) for a in ACTS]
    if s.startswith("SELECT a.activity_id, a.raw_name, a.major, a.unit, a.planned_qty"):   # road_activity_rows
        return [dict(a) for a in ACTS if a["road_no"] == params[0]]
    if s.startswith("SELECT a.activity_id, a.raw_name, a.major, a.weight_pct, a.planned_qty"):  # road_progress
        return [dict(a) for a in ACTS if a["road_no"] == params[0]]
    raise AssertionError("unexpected SQL: " + s[:120])


def jsonable(x):
    return json.loads(json.dumps(x, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else float(o), sort_keys=True))


def snapshot():
    real_date, real_query = c.datetime.date, c.db.query
    c.datetime.date, c.db.query = FixedDate, fake_query
    try:
        return jsonable({
            "road_progress_1": c.road_progress(1),
            "road_progress_2": c.road_progress(2),
            "road_activity_rows_1": c.road_activity_rows(1),
            "road_activity_rows_2": c.road_activity_rows(2),
            "vendor_performance": c.vendor_performance(),
            "group_plan_vendor": c.group_plan("vendor", TODAY, 30, detail=True),
            "group_target": c.group_target("line_manager", D(2026, 11, 16), D(2026, 12, 16)),
            "bottlenecks": c.bottlenecks(),
            "status_counts": c.activity_status_counts(),
        })
    finally:
        c.datetime.date, c.db.query = real_date, real_query


class NoPlannedQty(unittest.TestCase):
    """A road whose activity has no planned quantity still reads 100% backlog,
    so that activity must be listed (by %) rather than hidden."""
    def test_zero_qty_activity_is_listed_with_its_percent(self):
        real_date, real_query = c.datetime.date, c.db.query
        c.datetime.date, c.db.query = FixedDate, fake_query
        try:
            rows = c.group_plan("vendor", TODAY, 30, detail=True)
        finally:
            c.datetime.date, c.db.query = real_date, real_query
        v3 = next(r for r in rows if r["group"] == "V3")
        road = v3["road_rows"][0]
        self.assertEqual(road["backlog_pct"], 100.0)
        self.assertEqual(len(road["activities"]), 1)
        a = road["activities"][0]
        self.assertEqual((a["backlog_pct"], a["no_planned_qty"], a["backlog_qty"]), (100.0, True, 0.0))


class Characterisation(unittest.TestCase):
    def test_outputs_unchanged(self):
        self.assertEqual(snapshot(), json.load(open(GOLDEN)))


if __name__ == "__main__":
    if "--regen" in sys.argv:
        json.dump(snapshot(), open(GOLDEN, "w"), indent=1, sort_keys=True)
        print("wrote", GOLDEN)
    else:
        unittest.main()
