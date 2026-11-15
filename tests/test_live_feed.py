"""Live-change feed tests. Run:  python3 -m unittest discover -s tests -v
(DB trigger test is skipped unless a Postgres is reachable.)"""
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import live_feed  # noqa: E402


class WhitelistParity(unittest.TestCase):
    def test_sql_trigger_list_matches_live_feed_tables(self):
        sql = open(os.path.join(ROOT, "db", "05_live_change.sql")).read()
        m = re.search(r"watched\s+text\[\]\s*:=\s*ARRAY\[(.*?)\]", sql, re.S)
        self.assertIsNotNone(m)
        tables = [t.split("!")[0] for t in re.findall(r"'([^']+)'", m.group(1))]
        self.assertEqual(sorted(tables), sorted(live_feed.LIVE_FEED_TABLES))

    def test_no_sensitive_or_marker_table_is_watched(self):
        self.assertNotIn("live_change_marker", live_feed.LIVE_FEED_TABLES)


class RateLimiter(unittest.TestCase):
    def test_blocks_after_limit_and_recovers(self):
        now = [0.0]
        rl = live_feed.RateLimiter(limit=3, window=60, clock=lambda: now[0])
        self.assertEqual([rl.check("a") for _ in range(3)], [0, 0, 0])
        self.assertGreater(rl.check("a"), 0)
        self.assertEqual(rl.check("b"), 0)          # per-client
        now[0] = 61
        self.assertEqual(rl.check("a"), 0)


try:
    from fastapi.testclient import TestClient
    from dashboard.main import app
    HAVE_CLIENT = True
except Exception:  # pragma: no cover
    HAVE_CLIENT = False


@unittest.skipUnless(HAVE_CLIENT, "fastapi TestClient unavailable")
class Endpoint(unittest.TestCase):
    def setUp(self):
        live_feed.limiter = live_feed.RateLimiter()
        import dashboard.db_routes as r
        r.live_feed.limiter = live_feed.limiter
        self.client = TestClient(app)

    def test_returns_only_whitelisted_tables_no_record_data(self):
        rows = [{"table_name": t, "change_seq": i + 1} for i, t in enumerate(live_feed.LIVE_FEED_TABLES)]
        rows.append({"table_name": "user_secrets", "change_seq": 99})
        with mock.patch("dashboard.db.query", return_value=rows):
            body = self.client.get("/api/db/live/changes").json()
        self.assertEqual([c["table_name"] for c in body["changed"]], list(live_feed.LIVE_FEED_TABLES))
        for c in body["changed"]:
            self.assertEqual(set(c), {"table_name", "change_seq"})

    def test_rate_limited_with_retry_after(self):
        live_feed.limiter.limit = 2
        with mock.patch("dashboard.db.query", return_value=[]):
            codes = [self.client.get("/api/db/live/changes").status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        r = self.client.get("/api/db/live/changes")
        self.assertIn("retry-after", r.headers)

    def test_missing_marker_table_means_feed_off_not_500(self):
        with mock.patch("dashboard.db.query", side_effect=Exception('relation "live_change_marker" does not exist')):
            body = self.client.get("/api/db/live/changes").json()
        self.assertEqual(body, {"enabled": False, "changed": []})


def _pg_available():
    try:
        from dashboard import db
        db.query("SELECT 1")
        return db.query("SELECT to_regclass('live_change_marker') AS t")[0]["t"] is not None
    except Exception:
        return False


@unittest.skipUnless(_pg_available(), "Postgres with 05_live_change.sql applied not reachable")
class TriggerBehaviour(unittest.TestCase):
    def test_bump_on_commit_none_on_rollback(self):
        from dashboard import db
        seq = lambda: db.query("SELECT change_seq FROM live_change_marker WHERE table_name='manual_issue'")[0]["change_seq"]
        before = seq()
        with db.get_conn() as c:  # rolled back
            cur = c.cursor()
            cur.execute("UPDATE manual_issue SET remarks = remarks WHERE false")  # statement trigger fires even for 0 rows
            c.rollback()
        self.assertEqual(seq(), before)
        with db.get_conn() as c:  # committed
            cur = c.cursor()
            cur.execute("UPDATE manual_issue SET remarks = remarks WHERE false")
            c.commit()
        self.assertGreater(seq(), before)


if __name__ == "__main__":
    unittest.main()
