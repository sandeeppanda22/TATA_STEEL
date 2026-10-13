#!/usr/bin/env python3
"""Pre-flight check on the in-memory dataset before db/01_seed.sql is run.

Imports build_data.build_dataset() -- the same single-step parse gen_seed.py
uses -- so what is validated here is exactly what the seed will emit. No JSON
on disk, nothing assumed. Field names are taken from the ACTUAL dataset
structure and from scripts/gen_seed.py (which derives major / seq_no /
sheet_row_order / is_drain itself rather than reading them from the dict).

Checks only invariants the business rules actually state, plus the DDL
constraints in db/00_schema.sql that would hard-fail an INSERT. Reports
violations; does NOT fix anything.

Run:  python3 scripts/verify_seed.py
Exit: 0 if all checks pass, 1 if any FAIL.
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
import build_data  # noqa: E402  (same import gen_seed.py uses)
import config  # noqa: E402

fails, warns = [], []


def check(ok, label, detail=""):
    if ok:
        print(f"  PASS  {label}")
    else:
        fails.append(label)
        print(f"  FAIL  {label}  -> {detail}")


def warn(cond, label, detail=""):
    if cond:
        warns.append(label)
        print(f"  WARN  {label}  -> {detail}")


def is_sql_number(v):
    """gen_seed.py emits planned_qty unquoted via sql_num(), so the raw value
    must be numeric or the generated SQL is syntactically broken."""
    if v is None or v == "":
        return True  # becomes 0
    try:
        float(str(v))
        return True
    except ValueError:
        return False


def main():
    data = build_data.build_dataset()

    roads = data["roads"]
    issues = data.get("data_issues", [])
    pairs = [(r, a) for r in roads for a in r["activities"]]

    print(f"\nDataset:      in-memory build_data.build_dataset() (Source/*.xlsx)")
    print(f"Generated at: {data.get('generated_at')}")
    print(f"Source files: {', '.join(data.get('source_files', []))}")
    print(f"Roads: {len(roads)}   Activities (in JSON): {len(pairs)}   "
          f"Data issues: {len(issues)}")
    print(f"Totals from summary: length={data.get('total_length_m')} m  "
          f"drain={data.get('total_drain_m')} m  cost={data.get('total_cost_cr')} Cr")
    print()

    # ---- BR-05: major-activity weights must total 100 per road ------------
    print("BR-05  per-road weight totals (field: activity['weight'])")
    bad = []
    for r in roads:
        w = sum(float(a.get("weight") or 0) for a in r["activities"])
        if abs(w - 100.0) > 0.01:
            bad.append(f"road {r['road_no']}={round(w, 2)}")
    check(not bad, "every road's activity weights sum to 100", "; ".join(bad))

    # ---- BR-04: major classification, as gen_seed.py derives it -----------
    print("BR-04  major classification (derived: activity in config.MAJOR_ORDER)")
    majors = Counter(
        a["activity"] for _, a in pairs if a["activity"] in config.MAJOR_ORDER
    )
    check(bool(majors), "at least one activity maps to a major",
          "no activity matched config.MAJOR_ORDER")
    print(f"        MAJOR_ORDER = {list(config.MAJOR_ORDER)}")
    print(f"        counts      = {dict(sorted(majors.items()))}")
    weighted_no_major = [
        f"road {r['road_no']}/{a['raw_name']}" for r, a in pairs
        if float(a.get("weight") or 0) > 0 and a["activity"] not in config.MAJOR_ORDER
    ]
    check(not weighted_no_major, "no activity carries weight without being a major",
          "; ".join(weighted_no_major[:5]))

    # ---- generated-SQL syntax risks --------------------------------------
    print("SQL    generated-literal safety")
    nonnum = [f"road {r['road_no']}/{a['raw_name']}={a.get('planned_qty')!r}"
              for r, a in pairs if not is_sql_number(a.get("planned_qty"))]
    check(not nonnum, "planned_qty is numeric (emitted unquoted by gen_seed)",
          "; ".join(nonnum[:5]))
    for fld in ("length_m", "width_m", "drain_m", "cost_cr"):
        bad_f = [f"road {r['road_no']}={r.get(fld)!r}" for r in roads
                 if not is_sql_number(r.get(fld))]
        check(not bad_f, f"road.{fld} is numeric", "; ".join(bad_f[:5]))

    # ---- DDL constraints from db/00_schema.sql ---------------------------
    print("DDL    constraint pre-check (db/00_schema.sql)")
    check(not [r for r in roads if float(r.get("length_m") or 0) < 0],
          "road.length_m >= 0")
    check(not [r for r in roads if float(r.get("drain_m") or 0) < 0],
          "road.drain_planned_m >= 0")
    check(not [1 for _, a in pairs if float(a.get("planned_qty") or 0) < 0],
          "activity.planned_qty >= 0")
    oob = [f"road {r['road_no']}/{a['raw_name']}={a.get('weight')}"
           for r, a in pairs if not 0 <= float(a.get("weight") or 0) <= 100]
    check(not oob, "activity.weight_pct between 0 and 100", "; ".join(oob[:5]))
    dupes = [n for n, c in Counter(r["road_no"] for r in roads).items() if c > 1]
    check(not dupes, "road_no unique (PRIMARY KEY)", str(dupes))
    check(not [r for r in roads if not r.get("location")],
          "road.location NOT NULL")
    check(not [1 for _, a in pairs if not a.get("raw_name")],
          "activity.raw_name NOT NULL")
    check(not [r for r in roads if not r.get("source_file")],
          "road.source_file NOT NULL")
    bad_major_val = sorted({
        a["activity"] for _, a in pairs
        if a["activity"] in config.MAJOR_ORDER
        and a["activity"] not in {"Excavation", "GSB", "WMM", "DLC", "Road"}
    })
    check(not bad_major_val,
          "derived major passes activity.major CHECK constraint",
          ", ".join(bad_major_val))

    # ---- BR-09 drain synthesis, as gen_seed.py does it -------------------
    print("BR-09  drain rows")
    sheet_drains, synth = [], []
    for r in roads:
        has = any(a["activity"] == "RCC Drain" for a in r["activities"])
        if has:
            sheet_drains.append(r["road_no"])
        elif float(r.get("drain_m") or 0) > 0:
            synth.append(r["road_no"])
    print(f"        drain row present on the road's own sheet: {sheet_drains}")
    print(f"        synthesized from Phase-Summary drain_m:    {synth}")
    print(f"        activity rows the seed will emit: "
          f"{len(pairs)} + {len(synth)} synthetic = {len(pairs) + len(synth)}")
    check("RCC Drain" in config.ACTIVITY_SEQUENCE,
          "'RCC Drain' exists in config.ACTIVITY_SEQUENCE (gen_seed indexes it)",
          "gen_seed.py would raise ValueError for synthesized drains")

    # ---- data_issue rows -------------------------------------------------
    print("BR-11  data_issue rows")
    nondigit = [i for i in issues if not str(i.get("road_no")).isdigit()]
    print(f"        {len(issues)} issue rows; {len(nondigit)} will insert "
          f"road_no as NULL (non-numeric road_no)")
    if issues:
        flds = Counter(i.get("field") for i in issues)
        print(f"        by field: {dict(flds)}")

    # ---- observations, not blockers -------------------------------------
    print("Notes  data-quality observations (not blockers)")
    no_dates = [f"road {r['road_no']}/{a['raw_name']}" for r, a in pairs
                if not a.get("start_date") or not a.get("end_date")]
    warn(no_dates, f"{len(no_dates)} activities missing start or end date",
         "; ".join(no_dates[:3]) + (" ..." if len(no_dates) > 3 else ""))
    inverted = [f"road {r['road_no']}/{a['raw_name']}" for r, a in pairs
                if a.get("start_date") and a.get("end_date")
                and a["end_date"] < a["start_date"]]
    warn(inverted, f"{len(inverted)} activities end before they start",
         "; ".join(inverted[:3]))
    unmapped = data.get("unmapped_activities", [])
    warn(unmapped, f"{len(unmapped)} unmapped activity names (BR-02 verbatim kept)",
         "; ".join(str(u) for u in unmapped[:5]))
    converted = sum(1 for _, a in pairs
                    if a.get("start_date_converted") or a.get("end_date_converted"))
    print(f"        activities with a BR-11 date conversion: {converted}")
    print(f"        sum of road length_m: "
          f"{sum(float(r['length_m']) for r in roads)} m")

    print()
    if fails:
        print(f"RESULT: {len(fails)} FAIL, {len(warns)} warn — "
              f"do NOT run the seed until resolved.\n        failed: "
              f"{'; '.join(fails)}")
        return 1
    print(f"RESULT: all checks passed, {len(warns)} warn(s). Seed is safe to run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
