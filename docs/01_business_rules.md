# Tata Steel Road Programme — Business Rules (Phase 1)

Short reference for the progress-tracking model. Two sections only:
**CONFIRMED** (ruled by user — safe to build on) and
**OPEN** (needs a ruling before the affected feature can be built).

Nothing in this document is inferred from the source data. Where the source
is ambiguous, the item sits in OPEN.

---

## 1. CONFIRMED RULES

### BR-01 — Source of truth
The `Phase- Summary` sheet is **absolute truth** for road-level values:
length, width, drain, cost, start date, end date, vendor, line manager.
Where a road sheet's own activity dates disagree with the summary, the
summary wins and the difference is reported as a data issue.

### BR-02 — Activity list is the road sheet, verbatim
The activities listed on each road's sheet are final. Nothing is added,
removed, renamed, or invented. Names display exactly as typed in the sheet
(including existing inconsistencies, e.g. `Salitex board`,
`RCC(reinforcemnt+shutering)`, `Dismentalling of Paver`).

### BR-03 — Canonical programme sequence
Not every road has every activity, but whatever activities a road has are
ordered by this sequence:

1. Vegitation clearance
2. Six Party Clearance
3. Heap Cutting for road diversion
4. Diversion Road Making
5. Excavation (Box Cutting)
6. Soil Disposal
7. GSB
8. WMM
9. DLC
10. Shuttering
11. Reinforcement
12. Salitex Board
13. ROAD
14. Bitumen Paint
15. Kerb wall
16. Angle Fixing
17. RCC Drain

### BR-04 — Major activity weightage (within one road)
Five majors carry all the weight; every other activity carries 0%.

| Major | Weight |
|---|---|
| Excavation | 25% |
| GSB | 10% |
| WMM | 15% |
| DLC | 20% |
| RCC / Paver (ROAD) | 30% |

`RCC`, `RCC RoAD`, `RCC(reinforcemnt+shutering)` and `Paver Laying` all
count as the single **ROAD** major (30%).

### BR-05 — Weight roll-forward
If a road has no row for a given major, that major's weight is added to the
**next present major** in BR-03 order. Any weight left over at the end goes
to the last present major. A road's weights always total 100%.

Worked examples from Phase 1:
- Road 9 (no DLC): DLC's 20% → ROAD, so ROAD = 50%
- Road 14 (no GSB, no DLC): WMM = 25%, ROAD = 50%
- Roads 3 & 4 (RCC only): ROAD = 100%

### BR-06 — Road progress %
A road's % complete = sum of (each major's own % complete × its BR-04/BR-05
weight). Non-weighted activities are tracked and displayed but contribute
0% to the road figure.

### BR-07 — Overall programme %
Length-weighted across roads:

```
overall % = Σ (road % × road length) / Σ (road length)
```

Phase 1 total length = 5,065 m. A road's length share is its own
weightage ("more running meter → more weightage"), e.g. Road 3 at 800 m
carries 15.79% of the programme.

### BR-08 — Dependency gating (percentage-based)
An activity's actual % cannot exceed the actual % of the activity
immediately before it (in BR-03 order, counting only activities present on
that road). A layer cannot be ahead of the layer beneath it. Entry that
breaches this is rejected with a readable message.

### BR-09 — Drain
Tracked in running metres (RMT) with its own % complete:
`drain % = drain RMT complete / drain RMT planned`.
The planned figure comes from the summary (BR-01) even when the road's own
sheet has no RCC Drain row. Drain sits last in BR-03.

### BR-10 — RMT
Running metre (RMT) = metre. Treated as the same unit.

### BR-11 — Text dates
Dates stored as `dd/mm/yyyy` text in the source are read as day/month/year
and converted to ISO. Every conversion is reported as a data issue so the
change is visible, never silent. (Phase 1: 4 cells across Roads 3 and 4.)

### BR-12 — Phases
Phase 1 = 17 roads. Later phases add roads; all totals, shares and
weightage recompute from whatever is in the source folder. Nothing is
hard-coded to 17 roads or 5,065 m.

### BR-13 — Data refresh
Updating the Excel in `Source/` (or adding another `.xlsx` in the same
format) and clicking **Refresh Data** reloads the dashboard. Every `.xlsx`
in the folder is read and combined; duplicate road numbers across files are
reported, not silently merged.

### BR-14 — Critical path
Critical path uses CPM on single start/end dates. PERT is not possible —
the source has no optimistic/likely/pessimistic estimates.

**Method (confirmed 2026-10-09, supersedes the earlier date-feasibility
draft):** the dependency graph is BR-03's canonical 17-step sequence itself
— that order is the real physical construction dependency (you cannot run
WMM before Excavation finishes), not a display-only ordering. For a given
road, the critical path is the full chain of its present activities in
seq_no order — a single line with no branching, so every present activity
sits on the path. Recorded start_date/end_date are used only to compute
duration. Wherever a later activity's start_date falls before its
predecessor's end_date, that's reported as an **overlap flag**
(`overlap_flags` in the API, with how far through its schedule the
predecessor was) — surfaced, never silently dropped and never used to
re-order the chain. The implementation describes these as planned parallel
working, not as data errors. This ruling also resolves OPEN-03 below.
Implemented in `dashboard/calculations.critical_path()`.

### BR-15 — Milestones
Milestones comprise: each activity's completion, each 25% / 50% / 100%
mark, and road-level handover points. Both a planned month-ahead view and a
projected view (based on progress rate) are shown.

### BR-16 — Reporting
Every report and chart must be printable.

---

## 2. OPEN DECISIONS

These block specific features. Each needs one ruling.

### OPEN-01 — Weekly entry unit
Each activity has its own source unit (Cum, Sqm, MT, RMT). When entering
weekly progress, is the figure entered **in that activity's own unit**
(e.g. 120 Cum of Excavation), with
`% = cumulative ÷ planned qty`? Or is a direct % entered instead?
**Blocks:** weekly entry screen, all actual-based figures.

### OPEN-02 — Planned spread across weeks
The source gives each activity one start date and one end date, plus a
total planned quantity — but no week-by-week plan. To show *planned vs
actual* per week, the planned quantity has to be distributed across that
date range. Options:
- (a) Straight-line — equal quantity per day/week across the range
- (b) You supply a weekly planned figure during entry
- (c) S-curve

I will not pick one; straight-line is the simplest but it is still an
assumption about your schedule.
**Blocks:** planned-vs-actual, trend charts (MoM/QoQ/HoH), earned-value style views.

### OPEN-03 — CPM dependency type
BR-03 gives the activity order, but the source dates **overlap** (e.g.
Shuttering, Reinforcement and ROAD all start 2026-11-15 on Road 7). So the
sequence is not a strict finish-to-start chain. For CPM, should
dependencies be:
- (a) Finish-to-Start on the BR-03 order (ignores the overlapping source dates), or
- (b) Taken from the source dates as-is (overlaps allowed, sequence used only for display order)?
**Blocks:** critical path, float, projected completion.

### OPEN-04 — Vendor / line-manager performance measure
What makes a vendor or LM "performing"? Candidates: % complete vs planned
to date, slippage in days, number of activities behind, weighted progress
across their roads.
**Blocks:** vendor and LM performance views.

### OPEN-05 — Bottleneck definition
What qualifies an activity as a bottleneck? Candidates: behind planned by
more than X%, blocking a successor via BR-08 gating, zero progress for N
weeks, on the critical path and slipping.
**Blocks:** bottleneck view.

### OPEN-06 — Week definition
Which day does a reporting week start on, and is a week labelled by its
start date or its end date?
**Blocks:** weekly entry keys, all week-over-week aggregation.

---

## 3. KNOWN SOURCE DATA ISSUES (Phase 1)

Reported in the dashboard's Data Issues panel. Not corrected in the source.

| Road | Issue |
|---|---|
| 3, 4 | 4 date cells stored as `dd/mm/yyyy` text — converted per BR-11 |
| 3 | Sheet start 2026-05-10 vs summary 2026-10-05 — summary used per BR-01 |
| 4 | Sheet start 2026-07-10 vs summary 2026-10-07; end off by 1 day — summary used |
| 7 | `WMM` end date earlier than its start date — needs source correction |
| 17 | Sheet start 2026-01-02 vs summary 2026-10-15 — looks like a 2026/2027 year typo in the sheet; summary used |

Activities present in the source but carrying no weight under BR-04
(tracked and displayed, 0% weight): `Dismantling`, `Dismentalling of
Paver`, `Subgrade Preparation`, `Subgrade preparation`, `Profile
correction`, `Levelling &Dressing`, `Levelling and Dressing`, `PCC`.
