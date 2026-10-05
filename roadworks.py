"""Road Works Weekly Progress - desktop app (Python standard library only).
Data is stored in roadworks.db (SQLite) next to this program / the .exe."""
import csv, os, sqlite3, sys, datetime as dt
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)          # bundled seed files when running as .exe
DB_PATH = os.path.join(APP_DIR, "roadworks.db")

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

# ---------------------------------------------------------------- data layer
def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def import_plan(con, path):
    rows = read_csv(path)
    with con:
        for r in rows:
            con.execute(
                """INSERT INTO activity VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(activity_id) DO UPDATE SET road_no=excluded.road_no, package=excluded.package,
                   vendor=excluded.vendor, line_manager=excluded.line_manager, activity=excluded.activity,
                   unit=excluded.unit, planned_qty=excluded.planned_qty, start_date=excluded.start_date,
                   end_date=excluded.end_date""",
                (r["activity_id"], int(r["road_no"]), r["package"], r["vendor"], r["line_manager"],
                 r["activity"], r["unit"], float(r["planned_qty"] or 0), r["start_date"], r["end_date"]))
    return len(rows)

def import_bottlenecks(con, path):
    rows = read_csv(path)
    with con:
        for r in rows:
            con.execute("INSERT OR IGNORE INTO bottleneck VALUES(?,?,?,?,?,?)",
                        (r["id"], int(r["road_no"] or 0), r["issue"], r["source"], r["status"] or "Open", r["remarks"]))
    return len(rows)

def import_progress(con, path):
    rows = [r for r in read_csv(path) if r.get("week_ending") and r.get("cum_qty", "") != ""]
    for r in rows:
        if not parse_day(r["week_ending"]):
            raise ValueError(f"Bad date '{r['week_ending']}' (use YYYY-MM-DD)")
    for r in rows:
        save_progress(con, r["week_ending"], r["activity_id"], float(r["cum_qty"]), r.get("remarks", ""))
    return len(rows)

def save_progress(con, week, activity_id, qty, remarks=""):
    with con:
        con.execute(
            """INSERT INTO progress VALUES(?,?,?,?,?)
               ON CONFLICT(week_ending, activity_id) DO UPDATE SET cum_qty=excluded.cum_qty,
               remarks=excluded.remarks, entered_at=excluded.entered_at""",
            (week, activity_id, qty, remarks, dt.datetime.now().isoformat(timespec="seconds")))

def seed(con):
    if not con.execute("SELECT 1 FROM activity LIMIT 1").fetchone():
        p = os.path.join(RES_DIR, "plan.csv")
        if os.path.exists(p): import_plan(con, p)
    if not con.execute("SELECT 1 FROM bottleneck LIMIT 1").fetchone():
        p = os.path.join(RES_DIR, "bottlenecks.csv")
        if os.path.exists(p): import_bottlenecks(con, p)

def parse_day(s):
    try: return dt.datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError: return None

def latest_before(con, week, inclusive=True):
    op = "<=" if inclusive else "<"
    q = f"SELECT activity_id, cum_qty, remarks FROM progress WHERE week_ending {op} ? ORDER BY week_ending"
    return {r["activity_id"]: r for r in con.execute(q, (week,))}

def compute(con, week):
    """Every activity with actual % (cumulative / planned, max 100) and planned % at the given week."""
    wk, latest, out = parse_day(week), latest_before(con, week), []
    for a in con.execute("SELECT * FROM activity ORDER BY road_no, activity_id"):
        a = dict(a)
        done = latest[a["activity_id"]]["cum_qty"] if a["activity_id"] in latest else 0.0
        a["done"] = done
        a["pct"] = min(100.0, done / a["planned_qty"] * 100) if a["planned_qty"] > 0 else 0.0
        s, e = parse_day(a["start_date"]), parse_day(a["end_date"])
        a["plan"], a["issue"] = None, ""
        if not s or not e: a["issue"] = "date not readable"
        elif e < s: a["issue"] = "end date before start date"
        elif wk: a["plan"] = 0.0 if wk <= s else 100.0 if wk >= e else (wk - s).days / (e - s).days * 100
        out.append(a)
    return out

def summarize(items):
    n = len(items)
    pct = sum(a["pct"] for a in items) / n if n else 0.0
    sched = [a["plan"] for a in items if a["plan"] is not None]
    return n, pct, (sum(sched) / len(sched) if sched else None)

def status(pct, plan):
    if plan is None: return "no schedule", ""
    return (f"Behind {plan - pct:.0f}%", "behind") if pct + 0.05 < plan else ("On / ahead", "ok")

def group(acts, key):
    g = {}
    for a in acts: g.setdefault(a[key], []).append(a)
    return g

# ---------------------------------------------------------------- UI helpers
def make_tree(parent, cols, widths):
    f = ttk.Frame(parent)
    t = ttk.Treeview(f, columns=[c for c, _ in cols], show="headings", selectmode="browse")
    for (c, title), w in zip(cols, widths):
        t.heading(c, text=title)
        t.column(c, width=w, anchor="e" if c.startswith("#") else "w")
    sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
    t.tag_configure("behind", foreground="#c62828"); t.tag_configure("ok", foreground="#1b7f3b")
    return f, t

class QtyDialog(simpledialog.Dialog):
    def __init__(self, parent, title, name, planned, qty, remarks):
        self.name, self.planned, self.qty, self.remarks, self.result = name, planned, qty, remarks, None
        super().__init__(parent, title)
    def body(self, m):
        ttk.Label(m, text=f"{self.name}\nPlanned quantity: {self.planned:g}").grid(row=0, columnspan=2, pady=4)
        ttk.Label(m, text="Cumulative done:").grid(row=1, column=0, sticky="e")
        ttk.Label(m, text="Remarks:").grid(row=2, column=0, sticky="e")
        self.e1, self.e2 = ttk.Entry(m), ttk.Entry(m, width=36)
        self.e1.insert(0, self.qty); self.e2.insert(0, self.remarks)
        self.e1.grid(row=1, column=1, sticky="w", padx=4, pady=2); self.e2.grid(row=2, column=1, padx=4, pady=2)
        return self.e1
    def validate(self):
        try: float(self.e1.get()); return True
        except ValueError: messagebox.showerror("Invalid", "Enter a number.", parent=self); return False
    def apply(self): self.result = (float(self.e1.get()), self.e2.get().strip())

# ---------------------------------------------------------------- application
class App(tk.Tk):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.title("Road Works Weekly Progress"); self.geometry("1180x700")
        self.edits, self.edit_week = {}, None
        self.week_var = tk.StringVar(value=dt.date.today().isoformat())
        self._menu(); self._top()
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=8, pady=6)
        self.nb = nb
        self._dashboard(nb); self._entry(nb); self._bottlenecks(nb); self._issues(nb)
        self.refresh_all()

    # ---- chrome
    def _menu(self):
        m = tk.Menu(self); f = tk.Menu(m, tearoff=0)
        f.add_command(label="Import weekly progress from CSV / Excel-saved CSV...", command=lambda: self._import(import_progress))
        f.add_command(label="Import plan CSV (updates activities)...", command=lambda: self._import(import_plan))
        f.add_separator()
        f.add_command(label="Export progress history to CSV...", command=self.export_history)
        f.add_command(label="Export current summary to CSV...", command=self.export_summary)
        f.add_separator(); f.add_command(label="Exit", command=self.destroy)
        m.add_cascade(label="File", menu=f); self.config(menu=m)

    def _top(self):
        b = ttk.Frame(self); b.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(b, text="Week ending (YYYY-MM-DD):").pack(side="left")
        self.week_box = ttk.Combobox(b, textvariable=self.week_var, width=14)
        self.week_box.pack(side="left", padx=6)
        self.week_box.bind("<<ComboboxSelected>>", self.on_week); self.week_box.bind("<Return>", self.on_week)
        ttk.Button(b, text="Refresh", command=self.on_week).pack(side="left")
        self.kpi = ttk.Label(b, font=("TkDefaultFont", 11, "bold")); self.kpi.pack(side="left", padx=20)

    def week(self):
        w = self.week_var.get().strip()
        if not parse_day(w):
            messagebox.showerror("Invalid date", "Use the format YYYY-MM-DD, e.g. 2026-10-25."); return None
        return w

    def on_week(self, *_):
        w = self.week()
        if not w: return
        if self.edits and w != self.edit_week:
            if not messagebox.askyesno("Unsaved entries", "Discard unsaved entries for the previous week?"):
                self.week_var.set(self.edit_week); return
            self.edits = {}
        self.refresh_all()

    def refresh_all(self):
        weeks = [r[0] for r in self.con.execute("SELECT DISTINCT week_ending FROM progress ORDER BY 1")]
        self.week_box["values"] = weeks
        self.refresh_dashboard(); self.load_activities(); self.load_bottlenecks()

    # ---- dashboard
    def _dashboard(self, nb):
        fr = ttk.Frame(nb); nb.add(fr, text="Dashboard")
        sub = ttk.Notebook(fr); sub.pack(fill="both", expand=True)
        cols = lambda first: [("name", first), ("#n", "Activities"), ("bar", "Progress"), ("#act", "Actual %"), ("#pl", "Planned %"), ("st", "Status")]
        w = [330, 80, 130, 80, 80, 130]
        self.t_road = self._dash_tab(sub, "By road", [("road", "Road"), ("vendor", "Vendor"), ("mgr", "Line manager")] + cols("Activities")[1:],
                                     [80, 190, 140, 80, 110, 80, 80, 120])
        self.t_vendor = self._dash_tab(sub, "By vendor", cols("Vendor"), w)
        self.t_mgr = self._dash_tab(sub, "By line manager", cols("Line manager"), w)

    def _dash_tab(self, sub, title, cols, widths):
        fr = ttk.Frame(sub); sub.add(fr, text=title)
        f, t = make_tree(fr, cols, widths); f.pack(fill="both", expand=True); return t

    @staticmethod
    def _bar(p): return "█" * int(p // 10) + "░" * (10 - int(p // 10))

    def refresh_dashboard(self):
        w = self.week_var.get().strip()
        if not parse_day(w): return
        acts = compute(self.con, w)
        n, p, pl = summarize(acts)
        openb = self.con.execute("SELECT COUNT(*) FROM bottleneck WHERE lower(status) NOT IN ('closed','resolved')").fetchone()[0]
        self.kpi.config(text=f"Overall {p:.1f}%  |  Planned {'–' if pl is None else f'{pl:.1f}%'}  |  Complete {sum(a['pct'] >= 100 for a in acts)}/{n}  |  Open bottlenecks {openb}")
        for t in (self.t_road, self.t_vendor, self.t_mgr): t.delete(*t.get_children())
        for road, items in sorted(group(acts, "road_no").items()):
            n, p, pl = summarize(items); st, tag = status(p, pl)
            self.t_road.insert("", "end", tags=(tag,), values=(f"Road {road}", items[0]["vendor"], items[0]["line_manager"], n, self._bar(p), f"{p:.1f}", "–" if pl is None else f"{pl:.1f}", st))
        for t, key in ((self.t_vendor, "vendor"), (self.t_mgr, "line_manager")):
            for k, items in sorted(group(acts, key).items()):
                n, p, pl = summarize(items); st, tag = status(p, pl)
                t.insert("", "end", tags=(tag,), values=(k, n, self._bar(p), f"{p:.1f}", "–" if pl is None else f"{pl:.1f}", st))
        self.refresh_issues(acts)

    # ---- weekly entry
    def _entry(self, nb):
        fr = ttk.Frame(nb); nb.add(fr, text="Enter weekly progress")
        top = ttk.Frame(fr); top.pack(fill="x", pady=6)
        ttk.Label(top, text="Road:").pack(side="left")
        roads = [r[0] for r in self.con.execute("SELECT DISTINCT road_no FROM activity ORDER BY 1")]
        self.road_var = tk.StringVar(value=f"Road {roads[0]}" if roads else "")
        cb = ttk.Combobox(top, textvariable=self.road_var, values=[f"Road {r}" for r in roads], width=10, state="readonly")
        cb.pack(side="left", padx=6); cb.bind("<<ComboboxSelected>>", lambda e: self.load_activities())
        self.pkg = ttk.Label(top, foreground="gray"); self.pkg.pack(side="left", padx=10)
        ttk.Button(top, text="Save entries", command=self.save_edits).pack(side="right")
        ttk.Label(fr, text="Double-click an activity to enter the cumulative quantity done up to the week ending above.", foreground="gray").pack(anchor="w")
        f, self.t_act = make_tree(fr, [("id", "ID"), ("act", "Activity"), ("unit", "Unit"), ("#pl", "Planned"), ("#prev", "Previous cum."), ("#this", "This week cum."), ("#pct", "%"), ("rem", "Remarks")],
                                  [70, 240, 60, 80, 100, 110, 60, 300])
        f.pack(fill="both", expand=True); self.t_act.bind("<Double-1>", self.edit_row)

    def road_no(self):
        try: return int(self.road_var.get().split()[1])
        except (IndexError, ValueError): return None

    def load_activities(self):
        if not hasattr(self, "t_act"): return
        self.t_act.delete(*self.t_act.get_children())
        w, road = self.week_var.get().strip(), self.road_no()
        if not parse_day(w) or road is None: return
        prev, cur = latest_before(self.con, w, False), {r["activity_id"]: r for r in self.con.execute("SELECT * FROM progress WHERE week_ending=?", (w,))}
        rows = self.con.execute("SELECT * FROM activity WHERE road_no=? ORDER BY activity_id", (road,)).fetchall()
        self.pkg.config(text=rows[0]["package"][:90] if rows else "")
        for a in rows:
            aid = a["activity_id"]
            qty, rem = self.edits.get(aid, (cur[aid]["cum_qty"], cur[aid]["remarks"]) if aid in cur else ("", ""))
            pct = f"{min(100, qty / a['planned_qty'] * 100):.0f}" if qty != "" and a["planned_qty"] > 0 else ""
            self.t_act.insert("", "end", iid=aid, values=(aid, a["activity"], a["unit"], f"{a['planned_qty']:g}", f"{prev[aid]['cum_qty']:g}" if aid in prev else "", qty if qty == "" else f"{qty:g}", pct, rem))

    def edit_row(self, _):
        sel = self.t_act.selection()
        if not sel: return
        aid, v = sel[0], self.t_act.item(sel[0], "values")
        d = QtyDialog(self, "Enter progress", f"{aid}  {v[1]} ({v[2]})", float(v[3]), v[5], v[7])
        if d.result:
            self.edits[aid] = d.result; self.edit_week = self.week_var.get().strip(); self.load_activities()

    def save_edits(self):
        w = self.week()
        if not w or not self.edits: return messagebox.showinfo("Nothing to save", "No entries have been changed.") if w else None
        prev = latest_before(self.con, w, False)
        for aid, (qty, _) in self.edits.items():
            if aid in prev and qty < prev[aid]["cum_qty"]:
                if not messagebox.askyesno("Lower than before", f"{aid}: {qty:g} is lower than the previous cumulative {prev[aid]['cum_qty']:g}.\nSave anyway?"): return
        for aid, (qty, rem) in self.edits.items(): save_progress(self.con, w, aid, qty, rem)
        n, self.edits = len(self.edits), {}
        self.refresh_all(); messagebox.showinfo("Saved", f"{n} entries saved for week ending {w}.")

    # ---- bottlenecks
    def _bottlenecks(self, nb):
        fr = ttk.Frame(nb); nb.add(fr, text="Bottlenecks")
        bar = ttk.Frame(fr); bar.pack(fill="x", pady=6)
        for text, cmd in (("Add", self.bn_add), ("Set status", self.bn_status), ("Edit remarks", self.bn_remarks), ("Delete", self.bn_delete)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)
        f, self.t_bn = make_tree(fr, [("id", "ID"), ("#road", "Road"), ("issue", "Issue"), ("src", "Source"), ("st", "Status"), ("rem", "Remarks")], [60, 50, 480, 170, 90, 240])
        f.pack(fill="both", expand=True)

    def load_bottlenecks(self):
        self.t_bn.delete(*self.t_bn.get_children())
        for b in self.con.execute("SELECT * FROM bottleneck ORDER BY id"):
            self.t_bn.insert("", "end", iid=b["id"], values=(b["id"], b["road_no"], b["issue"], b["source"], b["status"], b["remarks"]))

    def bn_sel(self):
        s = self.t_bn.selection()
        if not s: messagebox.showinfo("Select a row", "Select a bottleneck first.")
        return s[0] if s else None

    def bn_add(self):
        road = simpledialog.askinteger("Bottleneck", "Road number:", parent=self)
        issue = road and simpledialog.askstring("Bottleneck", "Issue:", parent=self)
        if not issue: return
        n = self.con.execute("SELECT COUNT(*) FROM bottleneck").fetchone()[0] + 1
        while self.con.execute("SELECT 1 FROM bottleneck WHERE id=?", (f"BN-{n:02d}",)).fetchone(): n += 1
        with self.con: self.con.execute("INSERT INTO bottleneck VALUES(?,?,?,?,?,?)", (f"BN-{n:02d}", road, issue.strip(), "Weekly review", "Open", ""))
        self.refresh_all()

    def bn_status(self):
        i = self.bn_sel()
        s = i and simpledialog.askstring("Status", "Open / In progress / Closed:", parent=self)
        if s:
            with self.con: self.con.execute("UPDATE bottleneck SET status=? WHERE id=?", (s.strip(), i))
            self.refresh_all()

    def bn_remarks(self):
        i = self.bn_sel()
        r = i and simpledialog.askstring("Remarks", "Remarks:", initialvalue=self.t_bn.item(i, "values")[5], parent=self)
        if r is not None and i:
            with self.con: self.con.execute("UPDATE bottleneck SET remarks=? WHERE id=?", (r.strip(), i))
            self.refresh_all()

    def bn_delete(self):
        i = self.bn_sel()
        if i and messagebox.askyesno("Delete", f"Delete {i}?"):
            with self.con: self.con.execute("DELETE FROM bottleneck WHERE id=?", (i,))
            self.refresh_all()

    # ---- data issues
    def _issues(self, nb):
        fr = ttk.Frame(nb); nb.add(fr, text="Data issues")
        bar = ttk.Frame(fr); bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Fix dates of selected", command=self.fix_dates).pack(side="left", padx=3)
        ttk.Label(bar, text="Activities whose dates cannot be used for the planned %.", foreground="gray").pack(side="left", padx=8)
        f, self.t_iss = make_tree(fr, [("id", "ID"), ("act", "Activity"), ("s", "Start"), ("e", "End"), ("why", "Problem")], [70, 260, 110, 110, 220])
        f.pack(fill="both", expand=True)

    def refresh_issues(self, acts):
        self.t_iss.delete(*self.t_iss.get_children())
        for a in acts:
            if a["issue"]: self.t_iss.insert("", "end", iid=a["activity_id"], values=(a["activity_id"], f"Road {a['road_no']} - {a['activity']}", a["start_date"], a["end_date"], a["issue"]))

    def fix_dates(self):
        s = self.t_iss.selection()
        if not s: return
        v = self.t_iss.item(s[0], "values")
        new = []
        for label, old in (("Start", v[2]), ("End", v[3])):
            x = simpledialog.askstring("Fix date", f"{label} date (YYYY-MM-DD):", initialvalue=old if parse_day(old) else "", parent=self)
            if x is None: return
            if not parse_day(x): return messagebox.showerror("Invalid", "Use YYYY-MM-DD.")
            new.append(x.strip())
        if parse_day(new[1]) < parse_day(new[0]): return messagebox.showerror("Invalid", "End date is before start date.")
        with self.con: self.con.execute("UPDATE activity SET start_date=?, end_date=? WHERE activity_id=?", (new[0], new[1], s[0]))
        self.refresh_all()

    # ---- import / export
    def _import(self, fn):
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not p: return
        try: n = fn(self.con, p)
        except Exception as e: return messagebox.showerror("Import failed", str(e))
        self.refresh_all(); messagebox.showinfo("Imported", f"{n} rows imported.")

    def _export(self, header, rows, name):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=name, filetypes=[("CSV", "*.csv")])
        if p:
            with open(p, "w", newline="", encoding="utf-8-sig") as f: w = csv.writer(f); w.writerow(header); w.writerows(rows)
            messagebox.showinfo("Exported", p)

    def export_history(self):
        self._export(["week_ending", "activity_id", "road_no", "activity", "cumulative_qty", "remarks"],
                     [tuple(r) for r in self.con.execute("SELECT p.week_ending,p.activity_id,a.road_no,a.activity,p.cum_qty,p.remarks FROM progress p JOIN activity a USING(activity_id) ORDER BY 1,2")], "progress_history.csv")

    def export_summary(self):
        w = self.week()
        if not w: return
        rows = [(f"Road {r}", i[0]["vendor"], i[0]["line_manager"], *[round(x, 1) if x is not None else "" for x in summarize(i)[1:]])
                for r, i in sorted(group(compute(self.con, w), "road_no").items())]
        self._export(["road", "vendor", "line_manager", "actual_pct", "planned_pct"], rows, f"summary_{w}.csv")

if __name__ == "__main__":
    c = connect(); seed(c); App(c).mainloop()
