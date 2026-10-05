"""Road Works - local web server. Standard library only.
Serves index.html and saves to roadworks.db (same database/schema as roadworks.py).
Run:  python3 server.py          (Windows: python server.py)
      python3 server.py --lan    (let other PCs on your network open it)"""
import csv, json, os, re, sqlite3, sys, threading, webbrowser
import datetime as dt
import io
from urllib.parse import urlparse, parse_qs
import calc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("RW_DB", os.path.join(HERE, "roadworks.db"))
PORT = int(os.environ.get("RW_PORT", "8000"))
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity(
  activity_id TEXT PRIMARY KEY, road_no INTEGER NOT NULL, package TEXT, vendor TEXT, line_manager TEXT,
  activity TEXT, unit TEXT, planned_qty REAL, start_date TEXT, end_date TEXT);
CREATE TABLE IF NOT EXISTS progress(
  week_ending TEXT NOT NULL, activity_id TEXT NOT NULL REFERENCES activity(activity_id),
  cum_qty REAL NOT NULL, remarks TEXT DEFAULT '', entered_at TEXT,
  PRIMARY KEY(week_ending, activity_id));
CREATE TABLE IF NOT EXISTS bottleneck(
  id TEXT PRIMARY KEY, road_no INTEGER, issue TEXT, source TEXT, status TEXT DEFAULT 'Open', remarks TEXT DEFAULT '');
"""

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.executescript(SCHEMA); return c

def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f: return list(csv.DictReader(f))

def seed():
    c = db()
    with c:
        if not c.execute("SELECT 1 FROM activity").fetchone() and os.path.exists(os.path.join(HERE, "plan.csv")):
            for r in rows(os.path.join(HERE, "plan.csv")):
                c.execute("INSERT INTO activity VALUES(?,?,?,?,?,?,?,?,?,?)", (r["activity_id"], int(r["road_no"]), r["package"], r["vendor"],
                          r["line_manager"], r["activity"], r["unit"], float(r["planned_qty"] or 0), r["start_date"], r["end_date"]))
        if not c.execute("SELECT 1 FROM bottleneck").fetchone() and os.path.exists(os.path.join(HERE, "bottlenecks.csv")):
            for r in rows(os.path.join(HERE, "bottlenecks.csv")):
                c.execute("INSERT INTO bottleneck VALUES(?,?,?,?,?,?)", (r["id"], int(r["road_no"] or 0), r["issue"], r["source"], r["status"] or "Open", r["remarks"]))
    c.close()

def table(c, sql, *a): return [dict(r) for r in c.execute(sql, a)]

def api_data():
    c = db()
    d = {"activities": table(c, "SELECT * FROM activity ORDER BY road_no, activity_id"),
         "progress": table(c, "SELECT week_ending, activity_id, cum_qty, remarks FROM progress ORDER BY week_ending"),
         "bottlenecks": table(c, "SELECT * FROM bottleneck ORDER BY id")}
    c.close(); return d

class Bad(Exception): pass

def need(cond, msg):
    if not cond: raise Bad(msg)

def post_progress(b):
    week, entries = b.get("week", ""), b.get("entries", [])
    need(DAY.match(week), "Week must be YYYY-MM-DD"); need(entries, "No entries")
    c = db()
    try:
        with c:
            for e in entries:
                q = float(e["qty"]); need(q == q and q >= 0 and q != float("inf"), "Quantity must be a number >= 0")
                need(c.execute("SELECT 1 FROM activity WHERE activity_id=?", (e["activity_id"],)).fetchone(), "Unknown activity " + str(e["activity_id"]))
                c.execute("""INSERT INTO progress VALUES(?,?,?,?,?) ON CONFLICT(week_ending, activity_id)
                             DO UPDATE SET cum_qty=excluded.cum_qty, remarks=excluded.remarks, entered_at=excluded.entered_at""",
                          (week, e["activity_id"], q, str(e.get("remarks", ""))[:500], dt.datetime.now().isoformat(timespec="seconds")))
    finally: c.close()
    return {"saved": len(entries)}

def post_bottleneck(b):
    op, c = b.get("op"), db()
    try:
        with c:
            if op == "add":
                need(str(b.get("issue", "")).strip(), "Issue is required")
                n = c.execute("SELECT COUNT(*) FROM bottleneck").fetchone()[0] + 1
                while c.execute("SELECT 1 FROM bottleneck WHERE id=?", (f"BN-{n:02d}",)).fetchone(): n += 1
                c.execute("INSERT INTO bottleneck VALUES(?,?,?,?,?,?)", (f"BN-{n:02d}", int(b["road_no"]), b["issue"].strip(), "Weekly review", "Open", ""))
            elif op == "status":
                need(b.get("status") in ("Open", "In progress", "Closed"), "Bad status")
                c.execute("UPDATE bottleneck SET status=? WHERE id=?", (b["status"], b["id"]))
            elif op == "remarks": c.execute("UPDATE bottleneck SET remarks=? WHERE id=?", (str(b.get("remarks", ""))[:500], b["id"]))
            elif op == "delete": c.execute("DELETE FROM bottleneck WHERE id=?", (b["id"],))
            else: raise Bad("Unknown operation")
    finally: c.close()
    return {"ok": True}

def post_dates(b):
    s, e = b.get("start", ""), b.get("end", "")
    need(DAY.match(s) and DAY.match(e), "Dates must be YYYY-MM-DD"); need(e >= s, "End date is before start date")
    c = db()
    try:
        with c: c.execute("UPDATE activity SET start_date=?, end_date=? WHERE activity_id=?", (s, e, b["activity_id"]))
    finally: c.close()
    return {"ok": True}

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y")

def norm_date(s):
    """Excel often rewrites dates on save; accept common formats (day first) and store YYYY-MM-DD."""
    s = (s or "").strip()
    for f in DATE_FORMATS:
        try: return dt.datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError: pass
    return s   # left as-is; shows up under Data issues

def post_reload(_):
    """Re-read plan.csv / bottlenecks.csv. Rows with the same ID are overwritten; weekly progress is never touched."""
    pp, bp = os.path.join(HERE, "plan.csv"), os.path.join(HERE, "bottlenecks.csv")
    need(os.path.exists(pp), "plan.csv not found next to server.py")
    c, out = db(), {"added": 0, "updated": 0, "bn_added": 0, "bn_updated": 0}
    try:
        with c:   # one transaction: any bad row cancels the whole reload
            seen = set()
            for i, r in enumerate(rows(pp), start=2):
                try:
                    aid = r["activity_id"].strip(); need(aid, "activity_id is empty")
                    v = (int(r["road_no"]), r["package"], r["vendor"], r["line_manager"], r["activity"], r["unit"],
                         float(r["planned_qty"]), norm_date(r["start_date"]), norm_date(r["end_date"]))
                except (KeyError, ValueError, Bad) as e: raise Bad(f"plan.csv row {i}: {e}")
                need(aid not in seen, f"plan.csv row {i}: duplicate activity_id {aid}"); seen.add(aid)
                out["updated" if c.execute("SELECT 1 FROM activity WHERE activity_id=?", (aid,)).fetchone() else "added"] += 1
                c.execute("""INSERT INTO activity VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(activity_id) DO UPDATE SET
                             road_no=excluded.road_no, package=excluded.package, vendor=excluded.vendor, line_manager=excluded.line_manager,
                             activity=excluded.activity, unit=excluded.unit, planned_qty=excluded.planned_qty,
                             start_date=excluded.start_date, end_date=excluded.end_date""", (aid, *v))
            out["kept"] = c.execute("SELECT COUNT(*) FROM activity").fetchone()[0] - len(seen)
            if os.path.exists(bp):
                for i, r in enumerate(rows(bp), start=2):
                    try: bid, road = r["id"].strip(), int(r["road_no"] or 0); need(bid, "id is empty")
                    except (KeyError, ValueError, Bad) as e: raise Bad(f"bottlenecks.csv row {i}: {e}")
                    out["bn_updated" if c.execute("SELECT 1 FROM bottleneck WHERE id=?", (bid,)).fetchone() else "bn_added"] += 1
                    c.execute("""INSERT INTO bottleneck VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET road_no=excluded.road_no,
                                 issue=excluded.issue, source=excluded.source, status=excluded.status, remarks=excluded.remarks""",
                              (bid, road, r["issue"], r["source"], r["status"] or "Open", r["remarks"]))
    finally: c.close()
    return out

def post_dates_bulk(b):
    """Update many activities' dates in one transaction: one bad row cancels the whole batch."""
    items = b.get("items", []); need(items, "No changes")
    c = db()
    try:
        with c:
            for it in items:
                aid, st, en = it.get("activity_id"), it.get("start", ""), it.get("end", "")
                for x in (st, en):
                    need(DAY.match(x or ""), f"{aid}: invalid date")
                    try: dt.datetime.strptime(x, "%Y-%m-%d")
                    except ValueError: raise Bad(f"{aid}: {x} is not a real date")
                need(en >= st, f"{aid}: end date is before start date")
                need(c.execute("UPDATE activity SET start_date=?, end_date=? WHERE activity_id=?", (st, en, aid)).rowcount == 1, f"Unknown activity {aid}")
    finally: c.close()
    return {"updated": len(items)}

def post_import_progress(b):
    """Import weekly progress from CSV text. Columns: week_ending, activity_id, cum_qty (or cumulative_qty), remarks. Blank quantities are skipped."""
    text = b.get("csv", ""); need(text.strip(), "CSV is empty")
    rs = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    c, n, lower = db(), 0, []
    try:
        with c:   # one transaction: any bad row cancels the whole import
            for i, r in enumerate(rs, start=2):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                qty = r.get("cum_qty", r.get("cumulative_qty", ""))
                if not r.get("week_ending") or qty == "": continue
                wk = norm_date(r["week_ending"]); need(DAY.match(wk), f"row {i}: bad week_ending '{r['week_ending']}'")
                aid = r.get("activity_id", ""); need(c.execute("SELECT 1 FROM activity WHERE activity_id=?", (aid,)).fetchone(), f"row {i}: unknown activity '{aid}'")
                try: q = float(qty)
                except ValueError: raise Bad(f"row {i}: quantity '{qty}' is not a number")
                need(q == q and 0 <= q != float("inf"), f"row {i}: quantity must be a number >= 0")
                prev = c.execute("SELECT cum_qty FROM progress WHERE activity_id=? AND week_ending<? ORDER BY week_ending DESC LIMIT 1", (aid, wk)).fetchone()
                if prev and q < prev[0]: lower.append(f"{aid} ({wk})")
                c.execute("""INSERT INTO progress VALUES(?,?,?,?,?) ON CONFLICT(week_ending, activity_id)
                             DO UPDATE SET cum_qty=excluded.cum_qty, remarks=excluded.remarks, entered_at=excluded.entered_at""",
                          (wk, aid, q, r.get("remarks", "")[:500], dt.datetime.now().isoformat(timespec="seconds")))
                n += 1
    finally: c.close()
    return {"imported": n, "lower_than_previous": lower}

def history_csv():
    c = db()
    try:
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["week_ending", "activity_id", "road_no", "activity", "cum_qty", "remarks"])
        w.writerows(tuple(r) for r in c.execute("SELECT p.week_ending,p.activity_id,a.road_no,a.activity,p.cum_qty,p.remarks FROM progress p JOIN activity a USING(activity_id) ORDER BY 1,2"))
    finally: c.close()
    return out.getvalue()

def summary_csv(week):
    c = db()
    try: d = calc.summary(c, week)
    finally: c.close()
    out = io.StringIO(); w = csv.writer(out); w.writerow(["road", "vendor", "line_manager", "activities", "actual_pct", "planned_pct", "status"])
    for r in d["by_road"]: w.writerow([f"Road {r['road_no']}", r["vendor"], r["line_manager"], r["activities"], r["actual_pct"], "" if r["planned_pct"] is None else r["planned_pct"], r["status"]])
    return out.getvalue()

ROUTES = {"/api/import_progress": post_import_progress, "/api/dates_bulk": post_dates_bulk, "/api/reload": post_reload, "/api/progress": post_progress, "/api/bottleneck": post_bottleneck, "/api/dates": post_dates}

class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def send_csv(self, text, name):
        body = ("\ufeff" + text).encode()
        self.send_response(200); self.send_header("Content-Type", "text/csv; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"'); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); week = q.get("week", [dt.date.today().isoformat()])[0]
        if u.path in ("/", "/index.html"):
            self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif u.path == "/api/data": self.send(200, api_data())
        elif u.path in ("/api/summary", "/api/export/summary") and not DAY.match(week): self.send(400, {"error": "week must be YYYY-MM-DD"})
        elif u.path == "/api/summary":
            c = db()
            try: self.send(200, calc.summary(c, week))
            finally: c.close()
        elif u.path == "/api/export/summary": self.send_csv(summary_csv(week), f"summary_{week}.csv")
        elif u.path == "/api/export/history": self.send_csv(history_csv(), "progress_history.csv")
        else: self.send(404, {"error": "not found"})

    def do_POST(self):
        fn = ROUTES.get(self.path)
        if not fn: return self.send(404, {"error": "not found"})
        try: self.send(200, fn(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")))
        except Bad as e: self.send(400, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e: self.send(400, {"error": "Invalid input: " + str(e)})

    def log_message(self, *a): pass

if __name__ == "__main__":
    seed()
    lan = "--lan" in sys.argv
    srv = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", PORT), H)
    url = f"http://localhost:{PORT}"
    print(f"Road Works is running at {url}   (database: {DB})\nPress Ctrl+C to stop.")
    if "--no-browser" not in sys.argv: threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nStopped.")
