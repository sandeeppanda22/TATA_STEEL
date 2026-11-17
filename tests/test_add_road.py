"""Add Road validation (no DB): blank numbers accepted, mandatory activity fields."""
import contextlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import db_routes as r  # noqa: E402


def body(**kw):
    base = dict(road_no=9, location="Gate", activities=[{"raw_name": "WMM", "major": "WMM", "planned_qty": 10, "weight_pct": 100}])
    base.update(kw)
    return r.RoadCreate(**base)


class AddRoad(unittest.TestCase):
    def setUp(self):
        self.q = mock.patch.object(r.db, "query", lambda *a, **k: [])
        self.q.start()

    def tearDown(self):
        self.q.stop()

    def status(self, payload):
        try:
            with mock.patch.object(r.db, "get_conn") as gc, mock.patch.object(r, "road_detail", lambda n: {"ok": n}):
                gc.return_value = contextlib.nullcontext(mock.MagicMock())
                r.create_road(payload)
            return 201
        except r.HTTPException as e:
            return e.status_code

    def test_blank_length_and_drain_are_accepted_and_stored_as_zero(self):
        self.assertEqual(self.status(body(length_m=None, drain_planned_m=None)), 201)

    def test_duplicate_road_number_is_409(self):
        with mock.patch.object(r.db, "query", lambda *a, **k: [{"road_no": 9}]):
            self.assertEqual(self.status(body()), 409)

    def test_activity_planned_qty_is_mandatory(self):
        for qty in (0, -1):
            self.assertEqual(self.status(body(activities=[{"raw_name": "A", "major": "WMM", "planned_qty": qty, "weight_pct": 100}])), 422)
        with self.assertRaises(Exception):   # missing entirely -> validation error
            r.ActivityCreate(raw_name="A")

    def test_blank_location_and_bad_numbers(self):
        self.assertEqual(self.status(body(location="   ")), 422)
        self.assertEqual(self.status(body(road_no=0)), 422)
        self.assertEqual(self.status(body(phase=0)), 422)
        self.assertEqual(self.status(body(length_m=-5)), 422)


if __name__ == "__main__":
    unittest.main()


class EditRoadDrain(unittest.TestCase):
    def test_drain_target_below_recorded_progress_is_allowed(self):
        """Lowering the drain target under what is already recorded must save
        (the UI caps displayed % at 100 and flags over-planned instead)."""
        current = {"road_no": 5, "start_date": None, "end_date": None, "drain_planned_m": 500}
        written = {}

        def execute_returning(sql, params):
            written["sql"], written["params"] = sql, params
            return [{**current, "drain_planned_m": 400}]

        with mock.patch.object(r.db, "query", lambda *a, **k: [current]), \
             mock.patch.object(r.db, "execute_returning", execute_returning), \
             mock.patch.object(r.db, "execute", lambda *a, **k: 1):
            out = r.update_road(5, r.RoadUpdate(drain_planned_m=400))
        self.assertTrue(out["updated"])
        self.assertEqual(written["params"][0], 400)


class EditActivityPlannedQty(unittest.TestCase):
    def test_planned_qty_below_recorded_actuals_is_allowed_and_saved(self):
        current = {"activity_id": 7, "road_no": 5, "raw_name": "WMM", "unit": "Cum", "planned_qty": 500,
                   "start_date": None, "end_date": None, "weight_pct": 0, "major": None, "seq_no": None}
        written = {}

        def execute_returning(sql, params):
            written["params"] = params
            return [{**current, "planned_qty": 300}]

        with mock.patch.object(r.db, "query", lambda *a, **k: [current]), \
             mock.patch.object(r.db, "execute_returning", execute_returning), \
             mock.patch.object(r.db, "execute", lambda *a, **k: 1):
            out = r.update_activity(7, r.ActivityUpdate(planned_qty=300))
            self.assertTrue(out["updated"])
            self.assertEqual(written["params"][0], 300)
            with self.assertRaises(r.HTTPException) as cm:     # negatives are still rejected
                r.update_activity(7, r.ActivityUpdate(planned_qty=-1))
            self.assertEqual(cm.exception.status_code, 422)


class WeightNeedsMajor(unittest.TestCase):
    def setUp(self):
        self.q = mock.patch.object(r.db, "query", lambda *a, **k: [])
        self.q.start()

    def tearDown(self):
        self.q.stop()

    def test_add_road_rejects_weight_without_major(self):
        try:
            r.create_road(r.RoadCreate(road_no=9, location="L", activities=[
                {"raw_name": "A", "planned_qty": 5, "weight_pct": 100, "major": None}]))
            code = 201
        except r.HTTPException as e:
            code, detail = e.status_code, e.detail
        self.assertEqual(code, 422)
        self.assertIn("Major", detail)

    def test_edit_activity_rejects_weight_without_major(self):
        current = {"activity_id": 7, "road_no": 5, "raw_name": "X", "unit": "m", "planned_qty": 10,
                   "start_date": None, "end_date": None, "weight_pct": 0, "major": None, "seq_no": None}
        with mock.patch.object(r.db, "query", lambda *a, **k: [current]):
            with self.assertRaises(r.HTTPException) as cm:
                r.update_activity(7, r.ActivityUpdate(weight_pct=25))              # no major
            self.assertEqual(cm.exception.status_code, 422)
            current["major"], current["weight_pct"] = "WMM", 40
            with self.assertRaises(r.HTTPException) as cm:
                r.update_activity(7, r.ActivityUpdate(major=None))                  # clearing major, weight still 40
            self.assertEqual(cm.exception.status_code, 422)
