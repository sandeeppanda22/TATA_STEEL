"""Road-issue shift/edit/delete rules, run against a tiny in-memory fake
cursor (no DB). The fake understands only the statements db_routes issues."""
import contextlib
import datetime as d
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import db_routes  # noqa: E402

D = d.date


class FakeDB:
    def __init__(self, road, acts):
        self.road = dict(road)
        self.acts = {a["activity_id"]: dict(a) for a in acts}
        self.issues, self.shifts, self.log = {}, [], []
        self.committed = self.rolled = False
        self.n = 0

    # connection API
    def cursor(self, **kw): return Cur(self)
    def commit(self): self.committed = True
    def rollback(self): self.rolled = True


class Cur:
    def __init__(self, db): self.db, self._r = db, None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def fetchone(self): return self._r
    def fetchall(self): return self._r

    def execute(self, sql, p=()):
        db, s = self.db, " ".join(sql.split())
        self._r = None
        if s.startswith("SELECT road_no FROM road"): self._r = {"road_no": 1}
        elif s.startswith("SELECT * FROM road_issue"): self._r = db.issues.get(p[0])
        elif s.startswith("INSERT INTO road_issue ("):
            db.n += 1
            db.issues[db.n] = {"issue_id": db.n, "road_no": p[0], "from_date": p[1], "to_date": p[2],
                               "shift_days": p[3], "activities_shifted": 0, "reason": p[4]}
            self._r = {"issue_id": db.n}
        elif s.startswith("UPDATE road_issue SET activities_shifted"): db.issues[p[1]]["activities_shifted"] = p[0]
        elif s.startswith("UPDATE road_issue SET from_date"):
            i = db.issues[p[5]]; i.update(from_date=p[0], to_date=p[1], shift_days=p[2], activities_shifted=p[3], reason=p[4])
        elif s.startswith("DELETE FROM road_issue_shift"): db.shifts = [x for x in db.shifts if x[0] != p[0]]
        elif s.startswith("DELETE FROM road_issue"): db.issues.pop(p[0])
        elif s.startswith("SELECT activity_id, moved_start"):
            self._r = [{"activity_id": a, "moved_start": m} for i, a, m in db.shifts if i == p[0]]
        elif s.startswith("INSERT INTO road_issue_shift (issue_id, activity_id, moved_start) VALUES (%s,NULL"):
            db.shifts.append((p[0], None, p[1]))
        elif s.startswith("INSERT INTO road_issue_shift"): db.shifts.append((p[0], p[1], p[2]))
        elif "FROM activity a LEFT JOIN weekly_entry" in s:
            self._r = [a for a in db.acts.values()]
        elif s.startswith("SELECT start_date, end_date FROM road"): self._r = dict(db.road)
        elif s.startswith("SELECT start_date, end_date FROM activity"): self._r = dict(db.acts[p[0]])
        elif s.startswith("UPDATE road SET"): db.road.update(start_date=p[0], end_date=p[1])
        elif s.startswith("UPDATE activity SET"): db.acts[p[2]].update(start_date=p[0], end_date=p[1])
        elif s.startswith("INSERT INTO edit_log"): db.log.append(p)
        else: raise AssertionError("unexpected SQL: " + s)


TODAY = [D(2026, 11, 12)]   # inside the default 10-14 Nov test window


def call(fn, fake, *a, **kw):
    with mock.patch.object(db_routes.db, "get_conn", lambda: contextlib.nullcontext(fake)), \
         mock.patch.object(db_routes, "_today", lambda: TODAY[0]):
        return fn(*a, **kw)


def act(i, s, e, planned=100, cum=0):
    return {"activity_id": i, "start_date": s, "end_date": e, "planned_qty": planned, "cum": cum}


def fresh():
    road = {"start_date": D(2026, 10, 1), "end_date": D(2026, 12, 31)}
    acts = [
        act(1, D(2026, 10, 1), D(2026, 10, 20)),             # ended before issue
        act(2, D(2026, 10, 15), D(2026, 11, 30)),            # spans From
        act(3, D(2026, 11, 12), D(2026, 12, 10)),            # starts inside window
        act(4, D(2026, 12, 1), D(2026, 12, 31), 100, 100),   # complete
    ]
    return FakeDB(road, acts)


def create(fake, frm=D(2026, 11, 10), to=D(2026, 11, 14)):
    return call(db_routes.create_road_issue, fake, 1, db_routes.RoadIssueCreate(from_date=frm, to_date=to, reason="x"))


class Shift(unittest.TestCase):
    def test_rule_and_counts(self):
        f = fresh()
        out = create(f)
        self.assertEqual((out["shift_days"], out["moved"], out["completed"], out["ended_before"]), (5, 2, 1, 1))
        self.assertEqual((f.acts[2]["start_date"], f.acts[2]["end_date"]), (D(2026, 10, 15), D(2026, 12, 5)))
        self.assertEqual((f.acts[3]["start_date"], f.acts[3]["end_date"]), (D(2026, 11, 17), D(2026, 12, 15)))
        self.assertEqual(f.acts[1]["end_date"], D(2026, 10, 20))
        self.assertEqual(f.acts[4]["end_date"], D(2026, 12, 31))
        self.assertEqual(f.road["end_date"], D(2027, 1, 5))
        self.assertTrue(f.committed and not f.rolled)

    def test_delete_restores_everything(self):
        f = fresh(); before = (dict(f.road), {k: dict(v) for k, v in f.acts.items()})
        out = create(f)
        r = call(db_routes.delete_road_issue, f, 1, out["issue_id"])
        self.assertTrue(r["deleted"])
        self.assertEqual((f.road, f.acts), before)
        self.assertEqual(f.issues, {})

    def test_edit_reapplies_new_window(self):
        f = fresh(); before_end = f.acts[2]["end_date"]
        out = create(f)
        call(db_routes.update_road_issue, f, 1, out["issue_id"],
             db_routes.RoadIssueCreate(from_date=D(2026, 11, 10), to_date=D(2026, 11, 11), reason="y"))
        self.assertEqual(f.acts[2]["end_date"], before_end + d.timedelta(days=2))
        self.assertEqual(f.issues[out["issue_id"]]["shift_days"], 2)

    def test_edit_delete_blocked_after_to_date(self):
        f = fresh(); out = create(f)
        TODAY[0] = D(2026, 11, 15)          # day after To
        try:
            for fn, args in ((db_routes.delete_road_issue, ()),
                             (db_routes.update_road_issue,
                              (db_routes.RoadIssueCreate(from_date=D(2026, 11, 10), to_date=D(2026, 11, 12)),))):
                with self.assertRaises(db_routes.HTTPException) as cm:
                    call(fn, f, 1, out["issue_id"], *args)
                self.assertEqual(cm.exception.status_code, 409)
            TODAY[0] = D(2026, 11, 14)      # To date itself still editable
            call(db_routes.delete_road_issue, f, 1, out["issue_id"])
        finally:
            TODAY[0] = D(2026, 11, 12)

    def test_bad_range(self):
        with self.assertRaises(Exception):
            create(fresh(), D(2026, 11, 14), D(2026, 11, 10))


if __name__ == "__main__":
    unittest.main()
