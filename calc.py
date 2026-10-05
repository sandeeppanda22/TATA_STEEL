"""Progress calculations (no UI/Tk dependencies). Used by server.py; mirrors the logic in index.html."""
import datetime as dt

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
    if plan is None: return "no schedule"
    return f"Behind {plan - pct:.0f}%" if pct + 0.05 < plan else "On / ahead"

def group(acts, key):
    g = {}
    for a in acts: g.setdefault(a[key], []).append(a)
    return g

def rollup(items):
    n, pct, plan = summarize(items)
    return {"activities": n, "actual_pct": round(pct, 1), "planned_pct": None if plan is None else round(plan, 1), "status": status(pct, plan)}

def summary(con, week):
    acts = compute(con, week)
    return {"week": week, "overall": rollup(acts),
            "by_road": [{"road_no": k, "vendor": v[0]["vendor"], "line_manager": v[0]["line_manager"], **rollup(v)} for k, v in sorted(group(acts, "road_no").items())],
            "by_vendor": [{"vendor": k, **rollup(v)} for k, v in sorted(group(acts, "vendor").items())],
            "by_line_manager": [{"line_manager": k, **rollup(v)} for k, v in sorted(group(acts, "line_manager").items())]}
