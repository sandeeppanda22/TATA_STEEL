// Tata Steel Road Dashboard - renders /api/db/* (Postgres-backed) data into
// the page. Pure DOM rendering (no canvas) so every section prints cleanly.
//
// Every panel on this page is DB-backed EXCEPT "Canonical Activity Sequence &
// Weightage" below: ACTIVITY_SEQUENCE / MAJOR_WEIGHTS are fixed programme
// constants (user rulings #2/#3), not seeded data - scripts/config.py is
// their source of truth. Keep the two literals below in sync with it by hand
// if a ruling ever changes them.
const ACTIVITY_SEQUENCE = [
  "Vegetation Clearance", "Six Party Clearance", "Heap Cutting for Road Diversion",
  "Diversion Road Making", "Excavation", "Soil Disposal", "GSB", "WMM", "DLC",
  "Shuttering", "Reinforcement", "Salitex Board", "Road", "Bitumen Paint",
  "Kerb Wall", "Angle Fixing", "RCC Drain",
];
const MAJOR_WEIGHTS = { Excavation: 25, GSB: 10, WMM: 15, DLC: 20, Road: 30 };

const fmt = n => (n === null || n === undefined || n === '') ? '-' : Number(n).toLocaleString();
const esc = s => (s == null ? '' : String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])));
// DD-MM-YYYY per standing date-format preference. Dates from the API are
// YYYY-MM-DD (optionally with a time/offset suffix for timestamps); this is
// purely a display transform, no parsing/timezone math.
const fmtDate = d => {
  if (!d) return '-';
  const m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : esc(d);
};
// DD-MM-YYYY HH:MM for timestamps (edit log "edited_at" etc.).
const fmtDateTime = d => {
  if (!d) return '-';
  const m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (m) return `${m[3]}-${m[2]}-${m[1]} ${m[4]}:${m[5]}`;
  return fmtDate(d);
};

// --- Week helpers -----------------------------------------------------------
// Progress is stored and reported one row per week (week_start_date = Monday),
// but data entry is NOT restricted to Mondays: the user may enter on any day,
// as often as they like, and whatever date they pick is resolved to the Monday
// of that date's week. All maths here is on local date parts only (no UTC
// conversion), so a date never slips a day across the IST offset.
const isoToParts = iso => {
  const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
};
const toISO = dt => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
const todayISO = () => toISO(new Date());
// Monday of the week containing `iso` (weeks run Mon-Sun, OPEN-06).
const mondayOf = iso => {
  const p = isoToParts(iso);
  if (!p) return '';
  const dt = new Date(p[0], p[1] - 1, p[2]);
  dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));
  return toISO(dt);
};
// "12-10-2026 to 18-10-2026" - shown under the date box so the user can see
// which week the day they picked will be recorded against.
const weekLabel = iso => {
  const mon = mondayOf(iso);
  if (!mon) return '';
  const p = isoToParts(mon);
  const sun = new Date(p[0], p[1] - 1, p[2]);
  sun.setDate(sun.getDate() + 6);
  return `${fmtDate(mon)} to ${fmtDate(toISO(sun))}`;
};

// Turns an API error body (plain string, or FastAPI/pydantic's technical list)
// into short sentences a user can act on, e.g. "Length must be a number".
const API_FIELD_LABELS = {
  road_no: 'Road No', phase: 'Phase', scheme_sl_no: 'Scheme No', location: 'Location',
  line_manager: 'Line Manager', vendor: 'Vendor', road_type: 'Road Type', length_m: 'Length',
  width_m: 'Width', drain_planned_m: 'Drain Planned', cost_cr: 'Cost', start_date: 'Start Date',
  end_date: 'End Date', raw_name: 'Activity name', major: 'Major', weight_pct: 'Weight %',
  unit: 'Unit', planned_qty: 'Planned Qty', actual_qty: 'Actual Qty', week_start_date: 'Week',
  seq_no: 'Sequence', note: 'Note', remarks: 'Remarks', status: 'Status', issue: 'Issue',
  from_date: 'From date', to_date: 'To date', reason: 'Reason', csv: 'CSV',
};

function apiErrorMessages(detail) {
  if (detail == null || detail === '') return ['Something went wrong. Please try again.'];
  if (typeof detail === 'string') return [detail];
  if (!Array.isArray(detail)) return [typeof detail === 'object' && detail.message ? String(detail.message) : JSON.stringify(detail)];
  const out = detail.map(e => {
    if (!e || typeof e !== 'object') return String(e);
    const loc = (e.loc || []).filter(x => x !== 'body');
    const field = loc[loc.length - 1];
    const ai = loc.indexOf('activities');
    const prefix = ai >= 0 && typeof loc[ai + 1] === 'number' ? `Activity ${loc[ai + 1] + 1}: ` : '';
    let label = API_FIELD_LABELS[field] || String(field == null ? 'Value' : field).replace(/_/g, ' ');
    label = label.charAt(0).toUpperCase() + label.slice(1);
    const ctx = e.ctx || {};
    const numeric = /^(float|int|decimal)_(type|parsing)$/.test(e.type || '');
    const dated = /^date(_from_datetime)?_(type|parsing)$/.test(e.type || '') || e.type === 'date_type';
    let msg;
    if (e.type === 'missing' || ((numeric || dated) && e.input === null)) msg = `${label} is required`;
    else if (numeric) msg = `${label} must be a number`;
    else if (e.type === 'int_from_float') msg = `${label} must be a whole number`;
    else if (dated) msg = `${label} must be a valid date`;
    else if (e.type === 'string_type') msg = `${label} must be text`;
    else if (e.type === 'greater_than') msg = `${label} must be greater than ${ctx.gt}`;
    else if (e.type === 'greater_than_equal') msg = `${label} must be at least ${ctx.ge}`;
    else if (e.type === 'less_than_equal') msg = `${label} must be at most ${ctx.le}`;
    else if (e.type === 'less_than') msg = `${label} must be less than ${ctx.lt}`;
    else if (e.type === 'value_error') msg = String(e.msg || '').replace(/^Value error,\s*/, '');
    else msg = `${label}: ${e.msg || 'is not valid'}`;
    return prefix + msg;
  });
  return [...new Set(out)];
}

function apiError(out, res) {
  const messages = apiErrorMessages(out && out.detail != null ? out.detail : (res ? 'Request failed (HTTP ' + res.status + ')' : null));
  const err = new Error(messages.join('; '));
  err.messages = messages;
  err.status = res ? res.status : undefined;
  err.body = out;
  return err;
}

async function dbGet(path) {
  const res = await fetch(path, { cache: 'no-store' });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw apiError(out, res);
  return out;
}

async function dbSend(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw apiError(out, res);
  if (window.LIVE_FEED) window.LIVE_FEED.noteLocalWrite();   // our own save: don't banner its echo
  return out;
}

/* =====================================================================
   KPI row - client-side reduce() over GET /api/db/roads. No new backend
   endpoint; that response already carries length_m, drain_planned_m,
   cost_cr per road.
   ===================================================================== */
function renderKpis(roads) {
  const totals = roads.reduce((acc, r) => ({
    length_m: acc.length_m + (Number(r.length_m) || 0),
    drain_m: acc.drain_m + (Number(r.drain_planned_m) || 0),
    cost_cr: acc.cost_cr + (Number(r.cost_cr) || 0),
  }), { length_m: 0, drain_m: 0, cost_cr: 0 });
  const tiles = [
    ['Total Roads', roads.length],
    ['Total Length (m)', fmt(totals.length_m)],
    ['Total Drain (m)', fmt(totals.drain_m)],
    ['Total Cost (Cr)', Math.round(totals.cost_cr * 100) / 100],
  ];
  document.getElementById('kpiRow').innerHTML = tiles.map(([l, v]) => `
    <div class="kpi-tile"><div class="kpi-value">${v}</div><div class="kpi-label">${l}</div></div>`).join('');
  // Source-file note intentionally not shown (removed per request).
  document.getElementById('genNote').textContent = '';
}

/* =====================================================================
   Length-weighted share by road - GET /api/db/overview (length_weight_pct
   per road), labelled with location from the already-loaded roads list.
   ===================================================================== */
function renderWeightChart(overview, roads) {
  const locByRoad = new Map(roads.map(r => [r.road_no, r.location]));
  const sorted = [...overview.roads].sort((a, b) => b.length_weight_pct - a.length_weight_pct);
  const max = sorted[0] ? (sorted[0].length_weight_pct || 1) : 1;
  document.getElementById('weightChart').innerHTML = sorted.map(r => `
    <div class="bar-row">
      <div class="bar-label">R${r.road_no} ${esc((locByRoad.get(r.road_no) || '').slice(0, 24))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(r.length_weight_pct / max) * 100}%"></div></div>
      <div class="bar-pct">${r.length_weight_pct}%</div>
    </div>`).join('');
}

/* =====================================================================
   CHARTS TAB - ported from the legacy app's Charts view, restyled into the
   Tata navy/steel/accent palette. Pure inline SVG, no chart library. Feeds:
   GET /api/db/charts/progress-timeline (S-curve), /charts/activity-status
   (donut), /overview + /performance/{vendor,line-manager} (bars).
   ===================================================================== */
const CHART_COL = { actual: '#0A2A4A', planned: '#B36B00', ok: '#1E8E3E', steel: '#4A5560' };
const PROGRESS_BARS_LEGEND =
  `<div class="chart-legend">` +
  `<span><i style="background:${CHART_COL.actual}"></i>Actual cumulative % (length-weighted)</span>` +
  `<span><i style="background:${CHART_COL.planned}"></i>Planned cumulative % (schedule)</span></div>`;

function csBar(items) {
  if (!items.length) return '<p class="hint">No data.</p>';
  return items.map(it => {
    const pct = it.pct == null ? null : Math.max(0, Math.min(100, Number(it.pct)));
    return `<div class="bar-row">
      <div class="bar-label">${esc(it.label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct == null ? 0 : pct}%"></div></div>
      <div class="bar-pct">${pct == null ? '-' : pct.toFixed(0) + '%'}</div>
    </div>`;
  }).join('');
}

function csDonut(parts) {
  const total = parts.reduce((s, p) => s + p.n, 0);
  const r = 60, C = 2 * Math.PI * r, denom = total || 1;
  let off = 0;
  const arcs = parts.map(p => {
    const len = C * p.n / denom;
    const s = `<circle r="${r}" cx="90" cy="90" fill="none" stroke="${p.color}" stroke-width="26" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 90 90)"><title>${esc(p.label)}: ${p.n}</title></circle>`;
    off += len;
    return s;
  }).join('');
  return `<div class="donut-wrap">
    <svg viewBox="0 0 180 180" class="donut-svg">
      <circle r="${r}" cx="90" cy="90" fill="none" stroke="#EDF0F2" stroke-width="26"/>
      ${arcs}
      <text x="90" y="88" text-anchor="middle" fill="#0A2A4A" font-size="26" font-weight="800">${total}</text>
      <text x="90" y="108" text-anchor="middle" fill="#4A5560" font-size="11">activities</text>
    </svg>
    <ul class="donut-legend">${parts.map(p => `<li><span style="background:${p.color}"></span>${esc(p.label)}: <b>${p.n}</b></li>`).join('')}</ul>
  </div>`;
}

// Cumulative % complete per week - actual against planned - as grouped bars,
// with month-on-month (MoM) and quarter-on-quarter (QoQ) movement figures.
// The planned series spans the full schedule weekly; the actual value for each
// week is the cumulative actual as of that week (carried forward from the last
// week that had saved progress). MoM/QoQ compare the latest actual week with
// the actual 4 and 13 weeks earlier (one month / one quarter of weekly points).
function csProgressBars(data) {
  const planned = (data && data.planned) || [], actual = (data && data.actual) || [];
  if (!planned.length && !actual.length) return '<p class="hint">No schedule or progress data yet.</p>';

  // Backbone of weeks: the planned series if present, else the actual weeks.
  const weeks = (planned.length ? planned.map(p => p.week) : actual.map(a => a.week));
  const plannedBy = new Map(planned.map(p => [p.week, Number(p.pct)]));

  // Carry the actual cumulative forward across the backbone: for each week use
  // the latest recorded actual point on or before it (null before the first).
  // Capped at today - a week that hasn't happened yet has no actual by
  // definition, so it should show no actual bar rather than a flat carried
  // value stretching the last real entry all the way out to the end of the
  // planned schedule (which otherwise reads as "actual" progress for months
  // nothing was actually reported).
  const today = todayISO();
  const actualPts = actual.map(a => ({ week: a.week, pct: Number(a.pct) }))
    .sort((x, y) => (x.week < y.week ? -1 : 1));
  const actualBy = new Map();
  let ai = 0, carried = null;
  weeks.forEach(w => {
    while (ai < actualPts.length && actualPts[ai].week <= w) { carried = actualPts[ai].pct; ai++; }
    actualBy.set(w, w <= today ? carried : null);
  });

  // ---- MoM / QoQ, measured on the actual series by week index -------------
  const lastIdx = (() => { for (let i = weeks.length - 1; i >= 0; i--) if (actualBy.get(weeks[i]) != null) return i; return -1; })();
  const movement = (backWeeks, label) => {
    if (lastIdx < 0) return `<span class="movement-stat"><b>${label}</b> &mdash;</span>`;
    const now = actualBy.get(weeks[lastIdx]);
    const prevIdx = lastIdx - backWeeks;
    if (prevIdx < 0 || actualBy.get(weeks[prevIdx]) == null) {
      return `<span class="movement-stat"><b>${label}</b> n/a <span class="hint">(not enough history)</span></span>`;
    }
    const delta = now - actualBy.get(weeks[prevIdx]);
    const sign = delta > 0 ? '+' : '';
    const cls = delta > 0 ? 'move-up' : (delta < 0 ? 'move-down' : '');
    return `<span class="movement-stat ${cls}"><b>${label}</b> ${sign}${delta.toFixed(1)}%</span>`;
  };
  const latestActual = lastIdx >= 0 ? actualBy.get(weeks[lastIdx]) : null;
  const latestPlanned = lastIdx >= 0 ? (plannedBy.get(weeks[lastIdx]) ?? null) : null;
  const summary = `<div class="movement-row">
      <span class="movement-stat"><b>Latest actual</b> ${latestActual == null ? '—' : latestActual.toFixed(1) + '%'}</span>
      <span class="movement-stat"><b>Planned to date</b> ${latestPlanned == null ? '—' : latestPlanned.toFixed(1) + '%'}</span>
      ${movement(4, 'MoM')}
      ${movement(13, 'QoQ')}
    </div>`;

  // ---- grouped bars --------------------------------------------------------
  const W = 760, H = 300, L = 42, R = 16, T = 12, B = 40, pw = W - L - R, ph = H - T - B;

  // Y-axis scales to whatever's actually in view: zoomed into a single quarter
  // where progress only runs 20-35%, a fixed 0-100 axis flattens the bars to a
  // sliver. Find the max value in this (already filtered) backbone, round up
  // to a readable step, and scale the axis to that instead of always 100.
  let dataMax = 0;
  weeks.forEach(w => {
    const pv = plannedBy.get(w); if (pv != null) dataMax = Math.max(dataMax, pv);
    const av = actualBy.get(w); if (av != null) dataMax = Math.max(dataMax, av);
  });
  const axisMax = (() => {
    if (dataMax <= 0) return 100;
    const step = dataMax <= 10 ? 2 : dataMax <= 20 ? 5 : 10;
    return Math.min(100, Math.ceil(dataMax / step) * step);
  })();
  const fmtAxisPct = v => (Math.round(v * 10) / 10) + '%';

  const Y = p => T + ph * (1 - p / axisMax);
  const groupW = pw / weeks.length;
  const barW = Math.max(1.5, Math.min(14, groupW * 0.4));
  let g = '';
  [0, 0.25, 0.5, 0.75, 1].forEach(frac => {
    const p = axisMax * frac;
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(p)}" y2="${Y(p)}" stroke="#EDF0F2"/>` +
         `<text x="${L - 6}" y="${Y(p) + 3}" fill="#4A5560" font-size="10" text-anchor="end">${fmtAxisPct(p)}</text>`;
  });

  // Month ticks: one per calendar month present, centered under that month's
  // own span of bars (not just its first week) - collected up front so the
  // label density can also be thinned to fit the width. With a long schedule
  // (dozens of months) a label every month overlaps its neighbours
  // ("Dec25Feb26Mar26..." running together); only draw every Nth tick, N
  // chosen so each label gets enough horizontal room. A month that is the
  // only one in view naturally centers under the whole plot area this way.
  const monthGroups = [];
  let curKey = null, curStart = 0;
  weeks.forEach((w, i) => {
    const parts = isoToParts(w);
    const key = parts ? parts[0] + '-' + parts[1] : null;
    if (key !== curKey) {
      if (curKey !== null) monthGroups.push({ start: curStart, end: i - 1, parts: isoToParts(weeks[curStart]) });
      curKey = key;
      curStart = i;
    }
  });
  if (curKey !== null) monthGroups.push({ start: curStart, end: weeks.length - 1, parts: isoToParts(weeks[curStart]) });
  const monthTicks = monthGroups.map(mg => ({ cx: L + groupW * ((mg.start + mg.end) / 2 + 0.5), parts: mg.parts }));
  const MIN_LABEL_GAP = 34; // px in the 760-wide viewBox needed for a "Mon YY" label
  const avgTickGap = monthTicks.length > 1 ? pw / monthTicks.length : pw;
  const labelStride = Math.max(1, Math.ceil(MIN_LABEL_GAP / avgTickGap));

  weeks.forEach((w, i) => {
    const cx = L + groupW * (i + 0.5);
    const pPct = plannedBy.get(w);
    const aPct = actualBy.get(w);
    if (pPct != null) {
      const h = ph * Math.min(pPct, axisMax) / axisMax;
      g += `<rect x="${(cx - barW).toFixed(1)}" y="${Y(pPct).toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" fill="${CHART_COL.planned}"><title>${fmtDate(w)} planned: ${pPct.toFixed(1)}%</title></rect>`;
    }
    if (aPct != null) {
      const h = ph * Math.min(aPct, axisMax) / axisMax;
      g += `<rect x="${cx.toFixed(1)}" y="${Y(aPct).toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" fill="${CHART_COL.actual}"><title>${fmtDate(w)} actual: ${aPct.toFixed(1)}%</title></rect>`;
    }
  });
  monthTicks.forEach((t, k) => {
    if (k % labelStride !== 0) return;
    g += `<text x="${t.cx.toFixed(1)}" y="${H - 12}" fill="#4A5560" font-size="9" text-anchor="middle">${new Date(t.parts[0], t.parts[1] - 1, 1).toLocaleString('en', { month: 'short', year: '2-digit' })}</text>`;
  });
  return summary + `<svg viewBox="0 0 ${W} ${H}" class="scurve-svg">${g}</svg>`;
}

/* ---------------------------------------------------------------------
   Charts tab filters (year / quarter / month / week / line manager).
   Ruling (2026-10-13): filtering is MUTUAL across all five - whichever one
   is picked first, every other filter's option list narrows to match it,
   not just a one-way Year->Quarter->Month->Week cascade. Concretely:
     - Each of Year/Quarter/Month/Week computes its own option list from
       weeks matching the OTHER THREE date filters' current values (see
       weeksForLevel()), so picking Week (or Month, or Quarter) first
       narrows Year just as much as picking Year first narrows Week.
     - Line Manager is sent to the server (?line_manager=) and reshapes
       every card; that fetch also scopes CHART_ALL_WEEKS, so an LM pick
       narrows the date filters too. The reverse - date filters narrowing
       the Line Manager list - is approximated from LIVE_ROADS' own
       start_date/end_date overlap against the selected date range, since
       weekly entries don't carry a line_manager of their own (only the
       parent road does).
   Year/quarter/month/week only make sense against the week-indexed
   timeline chart, so those are applied client-side by slicing the week
   backbone inside csProgressBars() before it draws bars.
   --------------------------------------------------------------------- */
const CHART_DATE_FILTER_IDS = { year: 'chartFilterYear', quarter: 'chartFilterQuarter', month: 'chartFilterMonth', week: 'chartFilterWeek' };
const CHART_MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// Indian financial year: April–March. Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec, Q4 Jan–Mar.
const CHART_FQ_LABELS = { 1: 'Q1 (Apr–Jun)', 2: 'Q2 (Jul–Sep)', 3: 'Q3 (Oct–Dec)', 4: 'Q4 (Jan–Mar)' };
let CHART_ALL_WEEKS = [];

// Financial-year start year for a week's Monday date (month>=Apr → that calendar
// year, Jan–Mar → the previous year). Returns a number, e.g. 2026 for FY 2026-27.
function fyStartOf(iso) {
  const p = isoToParts(iso);
  if (!p) return null;
  return p[1] >= 4 ? p[0] : p[0] - 1;
}
function fyLabel(start) { return `${start}-${String(start + 1).slice(-2)}`; }
// Financial quarter 1..4 for a week's month.
function fqOf(iso) {
  const p = isoToParts(iso);
  if (!p) return null;
  const m = p[1];
  return m >= 4 && m <= 6 ? 1 : m >= 7 && m <= 9 ? 2 : m >= 10 && m <= 12 ? 3 : 4;
}

function chartFilterValues() {
  const out = {};
  Object.entries(CHART_DATE_FILTER_IDS).forEach(([field, id]) => {
    const el = document.getElementById(id);
    out[field] = el ? el.value : '';
  });
  const lmEl = document.getElementById('chartFilterLm');
  out.line_manager = lmEl ? lmEl.value : '';
  return out;
}

// Does a week match a given subset of the date filters? `levels` lists which of
// year/quarter/month/week to apply.
function weekMatchesLevels(w, f, levels) {
  const p = isoToParts(w);
  if (!p) return false;
  if (levels.includes('year') && f.year && fyStartOf(w) !== Number(f.year)) return false;
  if (levels.includes('quarter') && f.quarter && fqOf(w) !== Number(f.quarter)) return false;
  if (levels.includes('month') && f.month && p[1] !== Number(f.month)) return false;
  if (levels.includes('week') && f.week && w !== f.week) return false;
  return true;
}

// Full match: every active date filter must hold. Used by renderChartsTab().
function weekMatchesChartDateFilter(w, f) {
  return weekMatchesLevels(w, f, ['year', 'quarter', 'month', 'week']);
}

const CHART_DATE_LEVELS = ['year', 'quarter', 'month', 'week'];

// Options for one date level are built from weeks matching the OTHER three
// levels' current selections only - never the level's own value. That is
// what makes the four date filters mutually/simultaneously narrowing:
// picking Week first narrows Year/Quarter/Month exactly as much as picking
// Year first narrows Quarter/Month/Week.
function weeksForLevel(level, f) {
  const otherLevels = CHART_DATE_LEVELS.filter(l => l !== level);
  return CHART_ALL_WEEKS.filter(w => weekMatchesLevels(w, f, otherLevels));
}

function addDaysIso(iso, n) {
  const p = isoToParts(iso);
  if (!p) return iso;
  const dt = new Date(p[0], p[1] - 1, p[2]);
  dt.setDate(dt.getDate() + n);
  return toISO(dt);
}

// Rebuild all five selects (year/quarter/month/week/line manager) so each
// only offers values still reachable given every OTHER filter's current
// selection - a selection no longer reachable is dropped back to "All".
function populateChartFilters() {
  const f = chartFilterValues();

  const yearSel = document.getElementById('chartFilterYear');
  if (yearSel) {
    // FY 2025-26 (start year 2025) excluded from the picker on request - the
    // schedule's planned baseline starts there but there's no real work yet.
    const years = [...new Set(weeksForLevel('year', f).map(fyStartOf).filter(v => v != null && v !== 2025))].sort((a, b) => a - b);
    yearSel.innerHTML = '<option value="">All</option>' +
      years.map(y => `<option value="${y}">FY ${fyLabel(y)}</option>`).join('');
    yearSel.value = years.map(String).includes(f.year) ? f.year : '';
    autoSelectSingle(yearSel);
    f.year = yearSel.value;
  }

  const quarterSel = document.getElementById('chartFilterQuarter');
  if (quarterSel) {
    const qs = [...new Set(weeksForLevel('quarter', f).map(fqOf).filter(v => v != null))].sort((a, b) => a - b);
    quarterSel.innerHTML = '<option value="">All</option>' +
      qs.map(q => `<option value="${q}">${CHART_FQ_LABELS[q]}</option>`).join('');
    quarterSel.value = qs.map(String).includes(f.quarter) ? f.quarter : '';
    autoSelectSingle(quarterSel);
    f.quarter = quarterSel.value;
  }

  const monthSel = document.getElementById('chartFilterMonth');
  if (monthSel) {
    const months = [...new Set(weeksForLevel('month', f).map(w => isoToParts(w)?.[1]).filter(Boolean))].sort((a, b) => a - b);
    monthSel.innerHTML = '<option value="">All</option>' +
      months.map(m => `<option value="${m}">${CHART_MONTH_NAMES[m - 1]}</option>`).join('');
    monthSel.value = months.map(String).includes(f.month) ? f.month : '';
    autoSelectSingle(monthSel);
    f.month = monthSel.value;
  }

  const weekSel = document.getElementById('chartFilterWeek');
  if (weekSel) {
    const opts = weeksForLevel('week', f);
    weekSel.innerHTML = '<option value="">All</option>' +
      opts.map(w => `<option value="${w}">${weekLabel(w)}</option>`).join('');
    weekSel.value = opts.includes(f.week) ? f.week : '';
    autoSelectSingle(weekSel);
    f.week = weekSel.value;
  }

  // Line Manager — narrowed by the date filters currently selected (the
  // other half of the mutual link; CHART_ALL_WEEKS is already LM-scoped by
  // renderChartsTab's own fetch, which covers the date-filters-narrowed-by-LM
  // direction). The actual selected date range is approximated against
  // LIVE_ROADS' road-level start_date/end_date overlap, since weekly entries
  // don't carry their own line_manager - only the parent road does.
  const lmSel = document.getElementById('chartFilterLm');
  if (lmSel) {
    const keep = lmSel.value;
    const dateFilterActive = !!(f.year || f.quarter || f.month || f.week);
    const matching = CHART_ALL_WEEKS.filter(w => weekMatchesChartDateFilter(w, f));
    let roadsInRange = LIVE_ROADS || [];
    if (dateFilterActive) {
      if (!matching.length) {
        roadsInRange = [];
      } else {
        const rangeStart = matching[0];
        const rangeEnd = addDaysIso(matching[matching.length - 1], 6);
        roadsInRange = (LIVE_ROADS || []).filter(r =>
          (!r.start_date || r.start_date <= rangeEnd) && (!r.end_date || r.end_date >= rangeStart));
      }
    }
    const vals = [...new Set(roadsInRange.map(r => String(r.line_manager || '').trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b));
    lmSel.innerHTML = '<option value="">All</option>' + vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    lmSel.value = vals.includes(keep) ? keep : '';
    // only when it's the programme's sole line manager (a date-narrowed list must stay a choice,
    // because picking an LM here also re-scopes the chart fetch)
    if (new Set((LIVE_ROADS || []).map(r => r.line_manager).filter(Boolean)).size === 1) autoSelectSingle(lmSel);
  }
}

function wireChartFilters() {
  const bar = document.getElementById('chartFilterBar');
  if (!bar || bar.dataset.bound) return;
  [...Object.values(CHART_DATE_FILTER_IDS), 'chartFilterLm'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => {
      // Year/quarter/month changed: the week select's own options narrow,
      // so refresh it (and keep selection if still valid) before re-rendering.
      populateChartFilters();
      renderChartsTab();
    });
  });
  const clear = document.getElementById('chartFilterClear');
  if (clear) clear.addEventListener('click', () => {
    [...Object.values(CHART_DATE_FILTER_IDS), 'chartFilterLm'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    populateChartFilters();
    renderChartsTab();
  });
  bar.dataset.bound = '1';
}

async function renderChartsTab() {
  wireChartFilters();
  const setErr = msg => ['progressBarsWrap', 'statusDonutWrap', 'barRoadWrap', 'barVendorWrap', 'barLmWrap']
    .forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = `<p class="hint">${esc(msg)}</p>`; });
  try {
    const f = chartFilterValues();
    const lmQ = f.line_manager ? `?line_manager=${encodeURIComponent(f.line_manager)}` : '';
    const [timeline, status, overview, vendor, lm] = await Promise.all([
      dbGet('/api/db/charts/progress-timeline' + lmQ),
      dbGet('/api/db/charts/activity-status' + lmQ),
      dbGet('/api/db/overview' + lmQ),
      dbGet('/api/db/performance/vendor' + lmQ),
      dbGet('/api/db/performance/line-manager' + lmQ),
    ]);
    CHART_ALL_WEEKS = [...new Set([...(timeline.planned || []).map(p => p.week), ...(timeline.actual || []).map(a => a.week)])].sort();
    populateChartFilters();

    const dateFilterActive = f.year || f.quarter || f.month || f.week;
    const filteredTimeline = dateFilterActive ? {
      planned: (timeline.planned || []).filter(p => weekMatchesChartDateFilter(p.week, f)),
      actual: (timeline.actual || []).filter(a => weekMatchesChartDateFilter(a.week, f)),
    } : timeline;
    const countEl = document.getElementById('chartFilterCount');
    if (countEl) {
      const shownWeeks = new Set([...filteredTimeline.planned.map(p => p.week), ...filteredTimeline.actual.map(a => a.week)]).size;
      countEl.textContent = dateFilterActive
        ? `Showing ${shownWeeks} of ${CHART_ALL_WEEKS.length} weeks`
        : (CHART_ALL_WEEKS.length ? `Showing all ${CHART_ALL_WEEKS.length} weeks` : '');
    }

    const locByRoad = new Map((LIVE_ROADS || []).map(r => [r.road_no, r.location]));
    document.getElementById('progressBarsWrap').innerHTML = (filteredTimeline.planned.length || filteredTimeline.actual.length)
      ? csProgressBars(filteredTimeline) + PROGRESS_BARS_LEGEND
      : '<p class="hint">No weeks match the filters selected above.</p>';
    document.getElementById('statusDonutWrap').innerHTML = csDonut([
      { label: 'Complete', n: status.complete, color: CHART_COL.ok },
      { label: 'In progress', n: status.in_progress, color: CHART_COL.actual },
      { label: 'Not started', n: status.not_started, color: CHART_COL.steel },
    ]);
    const roadBars = [...overview.roads]
      .sort((a, b) => b.road_pct - a.road_pct)
      .map(r => ({ label: `R${r.road_no} ${(locByRoad.get(r.road_no) || '').slice(0, 22)}`, pct: r.road_pct }));
    document.getElementById('barRoadWrap').innerHTML = csBar(roadBars);
    document.getElementById('barVendorWrap').innerHTML = csBar(vendor.map(v => ({ label: v.group, pct: v.progress_pct })));
    document.getElementById('barLmWrap').innerHTML = csBar(lm.map(v => ({ label: v.group, pct: v.progress_pct })));
    populatePyramidRoadSelect();
    const pyrSel = document.getElementById('pyramidRoadSelect');
    if (pyrSel) {
      if (!pyrSel.dataset.bound) {
        pyrSel.addEventListener('change', () => loadPyramidChart(pyrSel.value));
        pyrSel.dataset.bound = '1';
      }
      if (pyrSel.options.length) loadPyramidChart(pyrSel.value);
    }
  } catch (err) {
    setErr('Could not load charts: ' + err.message);
  }
}


function renderSequence() {
  document.getElementById('seqWrap').innerHTML = `
    <ol class="seq-list">${ACTIVITY_SEQUENCE.map(a =>
      `<li>${esc(a)}${MAJOR_WEIGHTS[a] ? ` <span class="seq-weight">${MAJOR_WEIGHTS[a]}%</span>` : ''}</li>`).join('')}</ol>
    <p class="hint">Majors carry weightage; an absent major's weight rolls forward to the next present major. This is the nominal build order only - progress may be entered for any activity in any sequence.</p>`;
}

/* =====================================================================
   Data Issues - GET /api/db/data-issues. Column is activity_name, not
   activity, on the data_issue table.
   ===================================================================== */
async function renderIssues() {
  const el = document.getElementById('issuesWrap');
  try {
    const issues = await dbGet('/api/db/data-issues');
    if (!issues.length) { el.innerHTML = '<p>No data issues detected.</p>'; return; }
    el.innerHTML = `<table class="data-table"><thead><tr><th>Road</th><th>Activity</th><th>Field</th><th>Value</th><th>Issue</th></tr></thead>
      <tbody>${issues.map(i =>
        `<tr class="issue-row"><td>${i.road_no}</td><td class="wrap">${esc(i.activity_name)}</td><td>${esc(i.field)}</td><td class="wrap">${esc(i.value)}</td><td class="wrap">${esc(i.issue)}</td></tr>`).join('')}</tbody></table>`;
  } catch (err) {
    el.innerHTML = `<p class="hint">Could not load data issues: ${esc(err.message)}</p>`;
  }
}

document.getElementById('printBtn').addEventListener('click', () => window.print());

/* =====================================================================
   TAB NAV - sidebar .nav-item buttons toggle the matching .tab-panel.
   Lazy-loads each heavier panel's data on its first activation only
   Each tab refetches on every activation so data is never stale; an
   in-flight guard stops rapid clicks from double-firing a loader.
   The active tab is persisted so a page reload reopens it.
   ===================================================================== */
const ACTIVE_TAB_KEY = 'tata_active_tab';
const TAB_INFLIGHT = {};
const TAB_LOADERS = {
  charts: renderChartsTab,
  'critical-path': renderCriticalPathTab,
  performance: renderPerformanceTab,
  targets: renderTargetsTab,
  bottlenecks: renderBottlenecksTab,
  milestones: renderMilestonesTab,
};

function activateTab(name) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.tabPanel === name));
  try { localStorage.setItem(ACTIVE_TAB_KEY, name); } catch (e) { /* storage unavailable */ }
  const loader = TAB_LOADERS[name];
  if (window.LIVE_FEED) window.LIVE_FEED.onTabActivated(name, !!loader);
  if (loader && !TAB_INFLIGHT[name]) {
    TAB_INFLIGHT[name] = true;
    Promise.resolve()
      .then(() => loader())
      .finally(() => { TAB_INFLIGHT[name] = false; });
  }
}

document.querySelectorAll('.nav-item').forEach(btn =>
  btn.addEventListener('click', () => activateTab(btn.dataset.tab)));

function restoreActiveTab() {
  let saved = null;
  try { saved = localStorage.getItem(ACTIVE_TAB_KEY); } catch (e) { /* storage unavailable */ }
  const valid = saved && document.querySelector(`.nav-item[data-tab="${saved}"]`);
  const first = document.querySelector('.nav-item');
  const target = valid ? saved : (first ? first.dataset.tab : null);
  if (target) activateTab(target);
}

/* =====================================================================
   SIDENAV OVERLAY DRAWER - on narrow screens the sidebar is an off-canvas
   drawer: hidden by default, hamburger opens it over a dimmed backdrop,
   and it auto-closes as soon as a nav tab is picked (or the backdrop is
   clicked). On wide screens the sidebar is just a normal fixed column and
   the hamburger is hidden (CSS handles that split at the 900px breakpoint)
   so this JS only ever needs to toggle one "open" class plus the backdrop.
   ===================================================================== */
function initSidenav() {
  const nav = document.getElementById('sidenav');
  const toggle = document.getElementById('sidenavToggle');
  const backdrop = document.getElementById('sidenavBackdrop');
  if (!nav || !toggle) return;

  const open = () => { nav.classList.add('open'); backdrop && backdrop.classList.add('show'); };
  const close = () => { nav.classList.remove('open'); backdrop && backdrop.classList.remove('show'); };

  toggle.addEventListener('click', () => nav.classList.contains('open') ? close() : open());
  backdrop && backdrop.addEventListener('click', close);
  // Picking a tab from the drawer closes it (no-op on desktop, where it's
  // never "open" in the first place).
  nav.addEventListener('click', e => { if (e.target.closest('.nav-item')) close(); });
  // Crossing the 900px breakpoint back to desktop should leave the drawer
  // in its default (closed-overlay / normal-column) state either way.
  window.matchMedia('(max-width: 900px)').addEventListener('change', close);
}

/* =====================================================================
   LIVE DB TIER - everything below reads/writes Postgres via /api/db/*.
   This is also the page's only roads table (the static duplicate was
   dropped - livePanel/renderLiveRoadsTable is the one roads table).
   Ruling (2026-10-10): "dont show any flag. just keep an edit option
   for source to edit.." -> no passive banners anywhere in this section;
   every anomaly surfaces as a direct edit control instead.
   ===================================================================== */

let LIVE_ROADS = [];
let EXPANDED_ROAD = null;

/* =====================================================================
   In-app notifications - replaces the browser's native alert()/confirm().
   uiToast(msg, kind)  kind: 'ok' | 'error' | 'info'   (auto-dismisses)
   uiConfirm(msg, {okLabel, danger})  -> Promise<boolean>  (styled dialog)
   ===================================================================== */
function uiToast(msg, kind) {
  let host = document.getElementById('uiToastHost');
  if (!host) {
    host = document.createElement('div');
    host.id = 'uiToastHost';
    host.className = 'ui-toast-host no-print';
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
  }
  const t = document.createElement('div');
  t.className = 'ui-toast ui-toast-' + (kind || 'info');
  t.textContent = msg;
  const close = () => { t.classList.add('leaving'); setTimeout(() => t.remove(), 200); };
  t.addEventListener('click', close);
  host.appendChild(t);
  setTimeout(close, kind === 'error' ? 8000 : 5000);
}

function uiConfirm(message, opts) {
  opts = opts || {};
  return new Promise(resolve => {
    const dlg = document.createElement('dialog');
    dlg.className = 'ui-dialog no-print';
    dlg.innerHTML = `
      <div class="ui-dialog-body"></div>
      <div class="ui-dialog-foot">
        <button type="button" class="btn-small btn-cancel" data-no>Cancel</button>
        <button type="button" class="btn-small ${opts.danger ? 'btn-danger' : 'btn-save'}" data-yes></button>
      </div>`;
    dlg.querySelector('.ui-dialog-body').textContent = message;
    dlg.querySelector('[data-yes]').textContent = opts.okLabel || 'OK';
    const done = v => { dlg.close(); dlg.remove(); resolve(v); };
    dlg.querySelector('[data-yes]').addEventListener('click', () => done(true));
    dlg.querySelector('[data-no]').addEventListener('click', () => done(false));
    dlg.addEventListener('cancel', e => { e.preventDefault(); done(false); });
    document.body.appendChild(dlg);
    dlg.showModal();
    dlg.querySelector('[data-no]').focus();
  });
}

function setLiveStatus(msg, isError, opts) {
  opts = opts || {};
  // Road-scoped messages (saves, validation/gating errors, load failures for
  // one road) render in a strip directly ABOVE that road's action buttons,
  // not in the global strip at the top of the Live panel. Pass {global:true} to
  // force the top strip (e.g. a whole-page reload).
  const rn = opts.global ? null : (opts.roadNo != null ? opts.roadNo : EXPANDED_ROAD);
  if (rn != null) {
    // Remember it. A save almost always calls refreshLiveRoads() right after,
    // which rebuilds every road block and would otherwise wipe this strip -
    // so the message would flash for a few ms and vanish. renderLiveRoadsTable
    // re-applies the stored status after re-rendering (see reapplyRoadStatus).
    ROAD_STATUS = msg ? { roadNo: rn, msg: msg, isError: !!isError } : null;
    const rel = document.getElementById('road-status-' + rn);
    if (rel) {
      rel.textContent = msg || '';
      rel.style.color = isError ? 'var(--accent)' : 'var(--ok)';
      rel.hidden = !msg;
      const top = document.getElementById('liveStatus');
      if (top) top.textContent = '';   // never duplicate the message on top
      return;
    }
  }
  const el = document.getElementById('liveStatus');
  el.textContent = msg || '';
  el.style.color = isError ? 'var(--accent)' : '';
}

// Re-apply the last road-scoped status after the live roads table is rebuilt.
// Scoped to the currently expanded road so a stale message never resurfaces
// under a different or collapsed road.
let ROAD_STATUS = null;
function reapplyRoadStatus() {
  if (!ROAD_STATUS || ROAD_STATUS.roadNo !== EXPANDED_ROAD) return;
  const rel = document.getElementById('road-status-' + ROAD_STATUS.roadNo);
  if (!rel) return;
  rel.textContent = ROAD_STATUS.msg;
  rel.style.color = ROAD_STATUS.isError ? 'var(--accent)' : 'var(--ok)';
  rel.hidden = false;
}

/* =====================================================================
   STATUS PILLS + NAV BADGES - ported from the legacy root page.
   - A road shows an On/ahead vs Behind pill (actual % vs planned-to-date %)
     and a clickable "flagged" pill when it has open bottlenecks.
   - The sidebar nav items carry live count badges (bottlenecks, data
     issues, weight-audit fails), like the old "Bottlenecks 3" tab.
   ===================================================================== */
let BOTTLENECK_BY_ROAD = new Map();

function roadStatusPill(road) {
  const actual = Number(road.progress_pct) || 0;
  const planned = road.planned_pct == null ? null : Number(road.planned_pct);
  if (planned == null) return '';
  if (actual + 0.05 < planned) {
    return `<span class="pill pill-bad">Behind ${(planned - actual).toFixed(0)}%</span>`;
  }
  return `<span class="pill pill-ok">On / ahead</span>`;
}

function roadCompletionBar(road) {
  const actual = Math.max(0, Math.min(100, Number(road.progress_pct) || 0));
  const planned = road.planned_pct == null ? null : Math.max(0, Math.min(100, Number(road.planned_pct)));
  const tick = planned != null
    ? `<b style="left:calc(${planned}% - 1px)" title="Planned ${planned.toFixed(0)}% by this week"></b>` : '';
  return `<span class="road-pb" title="Actual ${actual.toFixed(0)}% complete"><i style="width:${actual}%"></i>${tick}</span>`;
}

function roadBottleneckPill(road) {
  const n = BOTTLENECK_BY_ROAD.get(road.road_no) || 0;
  if (!n) return '';
  return `<button type="button" class="pill pill-bad" data-goto-bottlenecks="1"` +
         ` title="${n} flagged ${n === 1 ? 'activity' : 'activities'} - view Bottlenecks">⚠️ ${n}</button>`;
}

function roadOverPlannedPill(road) {
  const n = road.over_planned_count || 0;
  if (!n) return '';
  return `<span class="pill pill-bad" title="${n} ${n === 1 ? 'activity has' : 'activities have'} actual qty above its planned qty - % is shown capped at 100%. Raise the planned qty in Edit Activities.">⚠ ${n} actual &gt; planned</span>`;
}

function setNavBadge(tab, count, variant) {
  const btn = document.querySelector(`.nav-item[data-tab="${tab}"]`);
  if (!btn) return;
  let b = btn.querySelector('.nav-badge');
  if (!count) { if (b) b.remove(); return; }
  if (!b) { b = document.createElement('span'); btn.appendChild(b); }
  b.className = 'nav-badge nav-badge-' + (variant || 'bad');
  b.textContent = count;
}

async function renderNavBadges() {
  try {
    const bn = await dbGet('/api/db/bottlenecks');
    BOTTLENECK_BY_ROAD = new Map();
    (bn || []).forEach(b => BOTTLENECK_BY_ROAD.set(b.road_no, (BOTTLENECK_BY_ROAD.get(b.road_no) || 0) + 1));
    setNavBadge('bottlenecks', (bn || []).length, 'bad');
    renderLiveRoadsTable();  // re-render so road rows pick up the per-road counts
  } catch (e) { /* leave badge/pills off if unreachable */ }
  // Data Issues nav item intentionally carries no count badge.
  try {
    const wa = await dbGet('/api/db/weight-audit');
    const n = (wa.roads_not_totalling_100 || []).length + (wa.duplicate_major_rows || []).length;
    setNavBadge('audit', n, 'warn');
  } catch (e) { /* leave */ }
}

const ROAD_EDIT_FIELDS = [
  ['location', 'text'], ['vendor', 'text'], ['line_manager', 'text'],
  ['road_type', 'text'], ['length_m', 'number'], ['width_m', 'number'],
  ['drain_planned_m', 'number'], ['cost_cr', 'number'],
  ['start_date', 'date'], ['end_date', 'date'], ['phase', 'number'],
];
const ACTIVITY_EDIT_FIELDS = [
  ['raw_name', 'text'], ['major', 'select'], ['weight_pct', 'number'],
  ['unit', 'text'], ['planned_qty', 'number'],
  ['start_date', 'date'], ['end_date', 'date'],
];
const VALID_MAJORS = ['', 'Excavation', 'GSB', 'WMM', 'DLC', 'Road'];

// Weight % belongs to the five majors only. With no Major chosen the weight is locked at 0
// (and unlocked again as soon as a Major is picked). Used by Add Road and Edit Activity.
function syncWeightToMajor(majorEl, weightEl) {
  const none = !majorEl.value;
  if (none) weightEl.value = '0';
  weightEl.disabled = none;
  weightEl.title = none ? 'Weight % applies only to activities with a Major - choose a Major first' : '';
}

function fieldInput(name, type, value) {
  if (type === 'select') {
    return `<select data-field="${name}">${VALID_MAJORS.map(m =>
      `<option value="${m}" ${value === m ? 'selected' : ''}>${m || '(none)'}</option>`).join('')}</select>`;
  }
  const v = value == null ? '' : esc(String(value));
  return `<input data-field="${name}" type="${type}" value="${v}">`;
}

// Readable labels for the Edit Road / Edit Activity forms (not raw column names).
const EDIT_FIELD_LABELS = {
  location: 'Location', vendor: 'Vendor', line_manager: 'Line Manager', road_type: 'Road Type',
  length_m: 'Length (m)', width_m: 'Width (m)', drain_planned_m: 'Drain Planned (m)', cost_cr: 'Cost (Cr)',
  start_date: 'Start Date', end_date: 'End Date', phase: 'Phase',
  raw_name: 'Activity name', major: 'Major', weight_pct: 'Weight %', unit: 'Unit', planned_qty: 'Planned Qty',
};

function editFormRows(fields, obj) {
  return fields.map(([name, type]) => {
    const combo = !!ROAD_COMBOS[name];     // line manager / vendor / road type / phase: dropdown + "add new"
    return `
    <div class="form-row">
      <label>${esc(EDIT_FIELD_LABELS[name] || name.replace(/_/g, ' '))}</label>
      ${combo ? comboFieldHtml(name, obj[name] == null ? '' : obj[name]) : fieldInput(name, type, obj[name])}
      ${combo ? `<span class="field-msg" data-msg-for="${name}"></span>` : ''}
    </div>`;
  }).join('');
}

async function submitEdit(kind, id, fields, formEl) {
  // dropdown + "add new" fields must be valid before anything is sent
  const badCombos = fields.map(([n]) => n).filter(n => {
    if (!ROAD_COMBOS[n]) return false;
    if (n === 'phase' && formEl.querySelector('[data-field="phase"]').value === '') {
      fieldMsg(formEl, 'phase', 'Select a phase, or add a new one.', 'error');
      formEl.querySelector('[data-field="phase"]').classList.add('field-error');
      return true;
    }
    return !comboCheck(formEl, n, true, false);
  });
  if (badCombos.length) {
    setLiveStatus('Please fix the highlighted fields before saving.', true);
    return false;
  }
  const payload = {};
  fields.forEach(([name, type]) => {
    const input = formEl.querySelector(`[data-field="${name}"]`);
    let val = ROAD_COMBOS[name] ? comboValue(formEl, name) : input.value;
    if (val === '') { payload[name] = null; return; }
    payload[name] = (type === 'number') ? Number(val) : val;
  });
  const path = kind === 'road' ? `/api/db/roads/${id}` : `/api/db/activities/${id}`;
  try {
    await dbSend('PUT', path, payload);
    setLiveStatus((kind === 'road' ? 'Road ' + id : 'Activity ' + id) + ' saved.', false);
    await refreshLiveRoads();
    return true;
  } catch (err) {
    setLiveStatus('Save failed: ' + err.message, true);
    return false;
  }
}

/* ---------------------------------------------------------------------
   Add Road form (Live Progress tab). POSTs straight to the DB via
   POST /api/db/roads - no rebuild needed, the new road and its activities
   are picked up on the very next refreshLiveRoads(). Activity weights must
   total 100 (BR-05, same rule as editing), enforced both here (so the user
   sees it immediately) and again server-side.
   --------------------------------------------------------------------- */
const ADD_ROAD_FIELDS = [
  ['road_no', 'number', 'Road No'], ['phase', 'number', 'Phase'],
  ['location', 'text', 'Location'], ['line_manager', 'text', 'Line Manager'],
  ['vendor', 'text', 'Vendor'], ['road_type', 'text', 'Road Type'],
  ['length_m', 'number', 'Length (m)'], ['width_m', 'number', 'Width (m)'],
  ['drain_planned_m', 'number', 'Drain Planned (m)'], ['cost_cr', 'number', 'Cost (Cr)'],
  ['start_date', 'date', 'Start Date'], ['end_date', 'date', 'End Date'],
];
const ADD_ROAD_REQUIRED = new Set(['road_no', 'phase', 'location']);
const ROAD_COMBOS = { line_manager: 'line manager', vendor: 'vendor', road_type: 'road type', phase: 'phase' };   // dropdown + "add new"
const NEW_OPTION = '__new__';
let ADD_ROAD_ROW_SEQ = 1;

// Smallest road number not in use yet - fills gaps (roads 1-20 and 23 exist -> 21) instead of max + 1.
const nextFreeRoadNo = () => {
  const used = new Set((LIVE_ROADS || []).map(r => Number(r.road_no)));
  let n = 1;
  while (used.has(n)) n += 1;
  return n;
};

// Distinct existing values for a road field (line manager / vendor), sorted.
// Phases already in use, numerically sorted as strings ("2" before "10").
const existingPhases = () => [...new Set((LIVE_ROADS || []).map(phaseOf))].sort((x, y) => x - y).map(String);
const nextPhaseNo = () => String((LIVE_ROADS || []).reduce((m, r) => Math.max(m, phaseOf(r)), 0) + 1);

function existingRoadValues(field) {
  if (field === 'phase') return existingPhases();
  return [...new Set((LIVE_ROADS || []).map(r => String(r[field] || '').trim()).filter(Boolean))]
    .sort((x, y) => x.localeCompare(y));
}

/* ---- dropdown + "add new" fields (Add Road AND Edit Road share these) ------
   comboFieldHtml  builds the <select> and its hidden "new value" box
   fieldMsg        shows a message under a field / marks the right box red
   comboCheck      validates the new-value box (duplicates, phase number)
   comboValue      the value to send: the chosen option, or the typed new one
   `root` is the form container, so any number of forms can use them. */
function comboFieldHtml(name, current, id) {
  const label = ROAD_COMBOS[name], isPhase = name === 'phase';
  const vals = existingRoadValues(name);
  const cur = current == null ? undefined : String(current);
  // editing: keep the road's own value selected; adding: always start blank (never auto-select,
  // even when only one value exists - the user chooses on purpose)
  const picked = v => cur !== undefined && v === cur;
  return `
    <select${id ? ` id="${id}"` : ''} data-field="${name}" data-combo="1">
      <option value="">Select ${esc(label)}...</option>
      ${vals.map(v => `<option value="${esc(v)}"${picked(v) ? ' selected' : ''}>${isPhase ? 'Phase ' : ''}${esc(v)}</option>`).join('')}
      <option value="${NEW_OPTION}">+ Add new ${esc(label)}...</option>
    </select>
    <div class="combo-new" data-new-wrap-for="${name}" hidden>
      <span class="combo-new-tag">New ${esc(label)}</span>
      ${isPhase
        ? '<input type="number" min="1" step="1" data-new-for="phase" placeholder="Phase number" autocomplete="off">'
        : `<input type="text" data-new-for="${name}" placeholder="Type the ${esc(label)} name" autocomplete="off">`}
    </div>`;
}

function fieldMsg(root, name, text, kind) {
  const el = root.querySelector(`[data-msg-for="${name}"]`);
  // for a dropdown + "add new" field the thing to flag is the new-value box, not the dropdown
  const input = ROAD_COMBOS[name]
    ? root.querySelector(`[data-new-for="${name}"]`) : root.querySelector(`[data-field="${name}"]`);
  if (el) { el.textContent = text || ''; el.className = 'field-msg' + (text ? ' ' + (kind || 'error') : ''); }
  if (input) input.classList.toggle('field-error', kind === 'error' && !!text);
}

// `strict` = the user pressed Save: an empty new-value box is then an error; while they are
// still choosing it is just a prompt. `prefill` suggests the next phase number.
function comboCheck(root, field, strict, prefill) {
  const sel = root.querySelector(`[data-field="${field}"]`);
  const inp = root.querySelector(`[data-new-for="${field}"]`);
  const box = root.querySelector(`[data-new-wrap-for="${field}"]`);
  if (!sel || !inp || !box) return true;
  const isNew = sel.value === NEW_OPTION;
  box.hidden = !isNew;
  if (!isNew) { inp.value = ''; fieldMsg(root, field, '', null); return true; }
  const isPhase = field === 'phase';
  if (isPhase && prefill && inp.value === '') inp.value = nextPhaseNo();
  const name = inp.value.trim();
  if (!name) {
    fieldMsg(root, field, isPhase ? 'Enter the new phase number.' : `Enter the new ${ROAD_COMBOS[field]} name.`, strict ? 'error' : 'hint');
    return false;
  }
  if (isPhase) {
    const n = Number(name);
    if (!Number.isInteger(n) || n < 1) { fieldMsg(root, field, 'Phase must be a whole number, 1 or higher.', 'error'); return false; }
    if (existingPhases().includes(String(n))) { fieldMsg(root, field, `Phase ${n} already exists - choose it from the list.`, 'error'); return false; }
    fieldMsg(root, field, `Phase ${n} will be created as a new group.`, 'ok');
    return true;
  }
  const dup = existingRoadValues(field).find(v => v.toLowerCase() === name.toLowerCase());
  if (dup) { fieldMsg(root, field, `"${dup}" already exists - choose it from the list.`, 'error'); return false; }
  fieldMsg(root, field, `New ${ROAD_COMBOS[field]} "${name}" will be added.`, 'ok');
  return true;
}

function comboValue(root, name) {
  const sel = root.querySelector(`[data-field="${name}"]`);
  if (sel.value !== NEW_OPTION) return sel.value;
  return root.querySelector(`[data-new-for="${name}"]`).value.trim();
}

// Wire the dropdown behaviour on any form that contains combo fields (once per form).
function wireComboForm(root) {
  if (root.dataset.comboBound) return;
  root.dataset.comboBound = '1';
  root.addEventListener('change', ev => {
    const t = ev.target;
    if (!t.matches('select[data-combo]')) return;
    comboCheck(root, t.dataset.field, false, true);
    if (t.value === NEW_OPTION) root.querySelector(`[data-new-for="${t.dataset.field}"]`).focus();
  });
  root.addEventListener('input', ev => {
    if (ev.target.dataset.newFor) comboCheck(root, ev.target.dataset.newFor, false, false);
  });
}

function addRoadFieldRows() {
  return ADD_ROAD_FIELDS.map(([name, type, label]) => {
    const req = ADD_ROAD_REQUIRED.has(name) ? ' <span class="req" title="Required">*</span>' : '';
    let control;
    if (ROAD_COMBOS[name]) {
      control = comboFieldHtml(name, undefined, `addRoad_${name}`);
    } else {
      const pre = name === 'road_no' ? ` value="${nextFreeRoadNo()}" min="1" step="1"`
        : (type === 'number' ? ' min="0" step="any"' : '');
      control = `<input id="addRoad_${name}" data-field="${name}" type="${type}"${pre}>`;
    }
    return `
    <div class="form-row">
      <label for="addRoad_${name}">${esc(label)}${req}</label>
      ${control}
      <span class="field-msg" data-msg-for="${name}"></span>
    </div>`;
  }).join('');
}

function addRoadActivityRowHtml(idx) {
  return `<tr data-activity-row="${idx}">
    <td><input data-aname type="text" placeholder="Activity name (required)"></td>
    <td><select data-amajor>${VALID_MAJORS.map(m => `<option value="${m}">${m || '(none)'}</option>`).join('')}</select></td>
    <td><input data-aweight type="number" step="0.01" min="0" max="100" value="0" style="width:70px;" disabled title="Weight % applies only to activities with a Major - choose a Major first"></td>
    <td><input data-aunit type="text" style="width:70px;"></td>
    <td><input data-aqty type="number" step="any" min="0" placeholder="Required" style="width:100px;"></td>
    <td><input data-astart type="date"></td>
    <td><input data-aend type="date"></td>
    <td><button type="button" class="btn-small btn-danger" data-remove-activity-row="${idx}">Remove</button></td>
  </tr>`;
}

function updateAddRoadWeightHint() {
  const hint = document.getElementById('addRoadWeightHint');
  if (!hint) return;
  const total = [...document.querySelectorAll('#addRoadActivityBody [data-aweight]')]
    .reduce((s, el) => s + (Number(el.value) || 0), 0);
  hint.textContent = `Weight total: ${total.toFixed(2)}%${Math.abs(total - 100) > 0.01 ? ' (must equal 100 to save)' : ' - OK'}`;
  hint.style.color = Math.abs(total - 100) > 0.01 ? 'var(--accent)' : 'var(--ok)';
}

function renderAddRoadForm() {
  const wrap = document.getElementById('addRoadFormWrap');
  if (!wrap) return;
  wrap.innerHTML = `
    <h3>Add New Road</h3>
    <p class="hint">Fields marked <span class="req">*</span> are required. Add at least one activity with a name and planned quantity; each activity can have its own start and end date. Activity weights must total 100.</p>
    <div class="edit-form form-grid">${addRoadFieldRows()}</div>
    <div class="table-scroll">
    <table class="data-table" id="addRoadActivityTable">
      <thead><tr><th>Activity <span class="req">*</span></th><th>Major</th><th>Weight %</th><th>Unit</th><th>Planned Qty <span class="req">*</span></th><th>Start</th><th>End</th><th></th></tr></thead>
      <tbody id="addRoadActivityBody">${addRoadActivityRowHtml(0)}</tbody>
    </table>
    </div>
    <div class="form-row" style="margin-top:10px; display:flex; align-items:center; gap:14px;">
      <button type="button" id="addRoadActivityBtn" class="btn-small">+ Add Activity</button>
      <span class="hint" id="addRoadWeightHint"></span>
    </div>
    <div class="form-row" style="margin-top:10px; display:flex; gap:10px;">
      <button type="button" id="addRoadSaveBtn" class="btn-small btn-save">Save Road</button>
      <button type="button" id="addRoadCancelBtn" class="btn-small">Cancel</button>
    </div>
    <div id="addRoadStatus" class="hint" style="margin-top:8px;"></div>`;
  ADD_ROAD_ROW_SEQ = 1;
  updateAddRoadWeightHint();
  wireAddRoadForm();
  checkRoadNo();              // validate the pre-filled number straight away
}

/* ---- live field messages ------------------------------------------------ */
const addRoadEl = name => document.querySelector(`#addRoadFormWrap [data-field="${name}"]`);

const addRoadRoot = () => document.getElementById('addRoadFormWrap');
const setFieldMsg = (name, text, kind) => fieldMsg(addRoadRoot(), name, text, kind);

// Live road-number check against the roads already loaded (kept fresh by the
// live feed); the server still has the final say (409) if two people race.
function checkRoadNo() {
  const input = addRoadEl('road_no');
  if (!input) return true;
  const raw = input.value.trim();
  if (raw === '') { setFieldMsg('road_no', 'Road number is required.', 'error'); return false; }
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) { setFieldMsg('road_no', 'Use a whole number, 1 or higher.', 'error'); return false; }
  const clash = (LIVE_ROADS || []).find(r => Number(r.road_no) === n);
  if (clash) {
    setFieldMsg('road_no', `Road ${n} already exists (${clash.location}). Next free number: ${nextFreeRoadNo()}.`, 'error');
    return false;
  }
  setFieldMsg('road_no', `Road ${n} is available.`, 'ok');
  return true;
}

const checkComboNew = (field, strict, prefill) => comboCheck(addRoadRoot(), field, strict, prefill);

function wireAddRoadForm() {
  const wrap = document.getElementById('addRoadFormWrap');
  if (!wrap || wrap.dataset.bound) return;
  wrap.addEventListener('click', (ev) => {
    if (ev.target.id === 'addRoadActivityBtn') {
      document.getElementById('addRoadActivityBody')
        .insertAdjacentHTML('beforeend', addRoadActivityRowHtml(ADD_ROAD_ROW_SEQ++));
    } else if (ev.target.dataset.removeActivityRow != null) {
      const body = document.getElementById('addRoadActivityBody');
      if (body.children.length > 1) ev.target.closest('tr').remove();
      updateAddRoadWeightHint();
    } else if (ev.target.id === 'addRoadSaveBtn') {
      submitAddRoad();
    } else if (ev.target.id === 'addRoadCancelBtn') {
      toggleAddRoadForm(false);
    }
  });
  wrap.addEventListener('input', (ev) => {
    const t = ev.target;
    if (t.matches('[data-aweight]')) updateAddRoadWeightHint();
    if (t.matches('[data-field="road_no"]')) checkRoadNo();
    if (t.classList.contains('field-error') && t.value !== '') t.classList.remove('field-error');
  });
  wireComboForm(wrap);
  wrap.addEventListener('change', (ev) => {
    if (ev.target.matches('[data-amajor]')) {
      const row = ev.target.closest('tr');
      syncWeightToMajor(ev.target, row.querySelector('[data-aweight]'));
      updateAddRoadWeightHint();
    }
  });
  wrap.dataset.bound = '1';
}

/* ---- collect + validate -------------------------------------------------- */
// A row the user hasn't touched (no name, qty, unit or dates) is ignored; any
// row with something typed in it must be complete.
function addRoadRows() {
  return [...document.querySelectorAll('#addRoadActivityBody tr')].map(row => {
    const v = sel => row.querySelector(sel).value.trim();
    return {
      row,
      raw_name: v('[data-aname]'),
      major: row.querySelector('[data-amajor]').value || null,
      weight_raw: v('[data-aweight]'),
      unit: v('[data-aunit]') || null,
      qty_raw: v('[data-aqty]'),
      start_date: v('[data-astart]') || null,
      end_date: v('[data-aend]') || null,
    };
  });
}
const rowIsBlank = a => !a.raw_name && !a.qty_raw && !a.unit && !a.start_date && !a.end_date
  && (a.weight_raw === '' || Number(a.weight_raw) === 0);

function validateAddRoad() {
  const errors = [];           // { el, msg }
  const wrap = document.getElementById('addRoadFormWrap');
  wrap.querySelectorAll('.field-error').forEach(e => e.classList.remove('field-error'));
  const fail = (el, msg, field) => { errors.push({ el, msg }); if (el) el.classList.add('field-error'); if (field) setFieldMsg(field, msg, 'error'); };

  if (!checkRoadNo()) fail(addRoadEl('road_no'), document.querySelector('[data-msg-for="road_no"]').textContent);

  const loc = addRoadEl('location');
  if (!loc.value.trim()) fail(loc, 'Location is required.', 'location'); else setFieldMsg('location', '', null);

  ['length_m', 'width_m', 'drain_planned_m', 'cost_cr'].forEach(f => {
    const el = addRoadEl(f);
    if (el.value !== '' && (!Number.isFinite(Number(el.value)) || Number(el.value) < 0)) {
      fail(el, `${API_FIELD_LABELS[f]} must be a number, 0 or more.`, f);
    } else setFieldMsg(f, '', null);
  });

  Object.keys(ROAD_COMBOS).forEach(f => {
    if (!checkComboNew(f, true)) fail(document.querySelector(`#addRoadFormWrap [data-new-for="${f}"]`),
      document.querySelector(`[data-msg-for="${f}"]`).textContent);
  });

  const phaseSel = addRoadEl('phase');
  if (phaseSel.value === '') fail(phaseSel, 'Select a phase, or add a new one.', 'phase');

  const s = addRoadEl('start_date'), e = addRoadEl('end_date');
  if (s.value && e.value && e.value < s.value) fail(e, 'End date cannot be before the start date.', 'end_date'); else setFieldMsg('end_date', '', null);

  const rows = addRoadRows().filter(a => !rowIsBlank(a));
  if (!rows.length) {
    fail(wrap.querySelector('[data-aname]'), 'Add at least one activity with a name and planned quantity.');
  }
  const seen = new Set();
  let weightTotal = 0;
  rows.forEach((a, i) => {
    const tag = `Activity ${i + 1}`;
    const q = a.row.querySelector('[data-aqty]'), n = a.row.querySelector('[data-aname]');
    if (!a.raw_name) fail(n, `${tag}: activity name is required.`);
    else if (seen.has(a.raw_name.toLowerCase())) fail(n, `${tag}: "${a.raw_name}" is entered twice.`);
    else seen.add(a.raw_name.toLowerCase());
    if (a.qty_raw === '' || !Number.isFinite(Number(a.qty_raw)) || Number(a.qty_raw) <= 0) {
      fail(q, `${tag}${a.raw_name ? ` (${a.raw_name})` : ''}: planned quantity is required and must be more than 0.`);
    }
    const w = Number(a.weight_raw || 0);
    if (!Number.isFinite(w) || w < 0 || w > 100) fail(a.row.querySelector('[data-aweight]'), `${tag}: weight must be between 0 and 100.`);
    else weightTotal += w;
    if (a.start_date && a.end_date && a.end_date < a.start_date) fail(a.row.querySelector('[data-aend]'), `${tag}: end date is before its start date.`);
  });
  if (rows.length && Math.abs(weightTotal - 100) > 0.01) {
    fail(wrap.querySelector('[data-aweight]'), `Activity weights must total 100 (now ${weightTotal.toFixed(2)}).`);
  }
  return errors;
}

function collectAddRoadPayload() {
  const payload = {};
  ADD_ROAD_FIELDS.forEach(([name, type]) => {
    const input = addRoadEl(name);
    let val = input ? input.value : '';
    if (ROAD_COMBOS[name]) val = comboValue(addRoadRoot(), name);
    if (val === '') { payload[name] = (name === 'length_m' || name === 'drain_planned_m') ? 0 : null; return; }
    payload[name] = (type === 'number') ? Number(val) : val;
  });
  payload.activities = addRoadRows().filter(a => !rowIsBlank(a)).map(a => ({
    raw_name: a.raw_name, major: a.major, weight_pct: Number(a.weight_raw || 0), unit: a.unit,
    planned_qty: Number(a.qty_raw), start_date: a.start_date, end_date: a.end_date,
  }));
  return payload;
}

function showAddRoadErrors(messages) {
  const statusEl = document.getElementById('addRoadStatus');
  statusEl.style.color = 'var(--accent)';
  statusEl.innerHTML = `<b>Please fix the following:</b><ul class="form-errors">${messages.map(m => `<li>${esc(m)}</li>`).join('')}</ul>`;
}

async function submitAddRoad() {
  const statusEl = document.getElementById('addRoadStatus');
  const errors = validateAddRoad();
  if (errors.length) {
    showAddRoadErrors(errors.map(x => x.msg));
    const first = errors.find(x => x.el);
    if (first) { first.el.scrollIntoView({ block: 'center', behavior: 'smooth' }); first.el.focus({ preventScroll: true }); }
    return;
  }
  const payload = collectAddRoadPayload();
  statusEl.textContent = 'Saving...';
  statusEl.style.color = '';
  try {
    await dbSend('POST', '/api/db/roads', payload);
    toggleAddRoadForm(false);
    setLiveStatus(`Road ${payload.road_no} added.`, false, { global: true });
    uiToast(`Road ${payload.road_no} added.`, 'ok');
    await refreshLiveRoads();
  } catch (err) {
    showAddRoadErrors(err.messages || [err.message]);
    if (err.status === 409) checkRoadNo();
  }
}

function toggleAddRoadForm(show) {
  const wrap = document.getElementById('addRoadFormWrap');
  const toggleBtn = document.getElementById('addRoadToggle');
  if (!wrap) return;
  if (show === undefined) show = wrap.style.display === 'none';
  wrap.style.display = show ? '' : 'none';
  if (toggleBtn) toggleBtn.textContent = show ? 'Cancel' : '+ Add Road';
  if (show) renderAddRoadForm();
}

function weeklyHistoryRows(activityId, entries) {
  if (!entries.length) return '<tr><td colspan="6">No entries yet.</td></tr>';
  return entries.map(e => `
    <tr>
      <td>${fmtDate(e.week_start_date)}</td>
      <td>${e.entered_at ? fmtDate(e.entered_at.slice(0, 10)) : '-'}</td>
      <td>${fmt(e.actual_this_week)}</td>
      <td>${fmt(e.cumulative_actual)}</td>
      <td>${e.cumulative_pct == null ? '-' : e.cumulative_pct + '%'}</td>
      <td class="wrap">${esc(e.note)}</td>
      <td><button class="btn-small btn-edit" data-edit-entry="${e.entry_id}" data-act="${activityId}" data-qty="${e.actual_this_week ?? ''}" data-note="${esc(e.note || '').replace(/"/g, '&quot;')}">Edit</button>
          <button class="btn-small btn-danger" data-del-entry="${e.entry_id}" data-act="${activityId}">Delete</button></td>
    </tr>`).join('');
}

// The per-activity summary row in the "View Past Data" panel (Cum. Actual, %
// Complete) is built once from a cached snapshot and does not auto-refresh.
// Call this after every load/save/delete below so that row - and the cached
// road.activities entry other panels read from - never goes stale.
function syncWeeklySummaryRow(activityId, entries) {
  const last = entries.length ? entries[entries.length - 1] : null;
  const cum = last ? last.cumulative_actual : 0;
  const pct = last ? last.cumulative_pct : 0;
  const detailRow = document.getElementById('act-weekly-' + activityId);
  const summaryRow = detailRow && detailRow.previousElementSibling;
  if (summaryRow) {
    const cells = summaryRow.querySelectorAll('td');
    if (cells.length >= 5) {
      cells[3].textContent = fmt(cum);
      cells[4].textContent = (pct == null ? '-' : pct + '%');
    }
  }
  for (const road of LIVE_ROADS) {
    if (!road.activities) continue;
    const act = road.activities.find(a => String(a.activity_id) === String(activityId));
    if (act) { act.cum_qty = cum; act.pct = pct; break; }
  }
}

async function renderWeeklyPanel(activityId, wrapEl) {
  wrapEl.innerHTML = 'Loading weekly history...';
  try {
    const resp = await dbGet(`/api/db/activities/${activityId}/weekly`);
    const entries = resp.weekly || [];
    syncWeeklySummaryRow(activityId, entries);
    wrapEl.innerHTML = `
      <table class="data-table sub weekly-table">
        <thead><tr><th>Week (Mon)</th><th>Entered</th><th>Actual (this entry)</th><th>Cum. Actual</th><th>Cum. %</th><th>Note</th><th></th></tr></thead>
        <tbody>${weeklyHistoryRows(activityId, entries)}</tbody>
      </table>
      <div class="form-row weekly-form">
        <input type="date" class="wk-date" value="${todayISO()}" title="Any day of the week - it is recorded against that week">
        <input type="number" class="wk-actual" placeholder="actual qty (adds to total)">
        <input type="number" class="wk-planned" placeholder="planned override (optional)">
        <input type="text" class="wk-note" placeholder="note (optional)">
        <button class="btn-small btn-save wk-submit">Add Entry</button>
      </div>
      <p class="hint wk-weeknote">Recorded against week <b>${weekLabel(todayISO())}</b>. Each entry <b>adds to</b> the running total - it does not replace a week. Pick any day in a week.</p>`;
    wrapEl.querySelectorAll('[data-edit-entry]').forEach(btn => btn.addEventListener('click', () => {
      const tr = btn.closest('tr');
      const cells = tr.querySelectorAll('td');
      cells[2].innerHTML = `<input type="number" class="inline-input ed-qty" value="${esc(btn.dataset.qty)}">`;
      cells[5].innerHTML = `<input type="text" class="inline-input ed-note" value="${esc(btn.dataset.note).replace(/"/g, '&quot;')}">`;
      cells[6].innerHTML = '<button class="btn-small btn-save ed-save">Save</button> <button class="btn-small btn-cancel ed-cancel">Cancel</button>';
      cells[6].querySelector('.ed-cancel').addEventListener('click', () => renderWeeklyPanel(activityId, wrapEl));
      cells[6].querySelector('.ed-save').addEventListener('click', async () => {
        const qty = tr.querySelector('.ed-qty').value;
        if (qty === '' || Number(qty) < 0) { setLiveStatus('Enter a quantity of 0 or more.', true); return; }
        try {
          await dbSend('PUT', `/api/db/activities/${btn.dataset.act}/weekly/${btn.dataset.editEntry}`,
            { actual_qty: Number(qty), note: tr.querySelector('.ed-note').value || null });
          setLiveStatus('Entry updated.', false);
          await renderWeeklyPanel(activityId, wrapEl);
          await refreshLiveRoads();
        } catch (err) { setLiveStatus('Update failed: ' + err.message, true); }
      });
    }));
    wrapEl.querySelectorAll('[data-del-entry]').forEach(btn => btn.addEventListener('click', async () => {
      if (!(await uiConfirm('Delete this entry?', { okLabel: 'Delete', danger: true }))) return;
      try {
        await dbSend('DELETE', `/api/db/activities/${btn.dataset.act}/weekly/${btn.dataset.delEntry}`);
        setLiveStatus('Entry deleted.', false);
        await renderWeeklyPanel(activityId, wrapEl);
        await refreshLiveRoads();
      } catch (err) { setLiveStatus('Delete failed: ' + err.message, true); }
    }));
    const wkDateEl = wrapEl.querySelector('.wk-date');
    const wkNoteEl = wrapEl.querySelector('.wk-weeknote');
    wkDateEl.addEventListener('change', () => {
      const lbl = weekLabel(wkDateEl.value);
      wkNoteEl.innerHTML = lbl
        ? `Recorded against week <b>${lbl}</b>. Each entry <b>adds to</b> the running total - it does not replace a week. Pick any day in a week.`
        : 'Pick a date.';
    });
    wrapEl.querySelector('.wk-submit').addEventListener('click', async () => {
      const picked = wkDateEl.value;
      const actual_qty = wrapEl.querySelector('.wk-actual').value;
      const plannedRaw = wrapEl.querySelector('.wk-planned').value;
      const note = wrapEl.querySelector('.wk-note').value;
      if (!picked || actual_qty === '') { setLiveStatus('A date and the actual qty are required.', true); return; }
      // Any day is fine; the week it belongs to is what gets stored.
      const week_start_date = mondayOf(picked);
      const body = { week_start_date, actual_qty: Number(actual_qty) };
      if (plannedRaw !== '') body.planned_qty_override = Number(plannedRaw);
      if (note) body.note = note;
      try {
        await dbSend('POST', `/api/db/activities/${activityId}/weekly`, body);
        setLiveStatus('Weekly entry saved.', false);
        await renderWeeklyPanel(activityId, wrapEl);
        await refreshLiveRoads();
      } catch (err) { setLiveStatus('Weekly save failed: ' + err.message, true); }
    });
  } catch (err) {
    wrapEl.innerHTML = `<p class="hint">Could not load weekly history: ${esc(err.message)}</p>`;
  }
}

// Hide activity rows in road `rn` whose [start,end] range does not overlap
// the [from,to] window. Empty from/to = unbounded that side; an activity with
// no start (or no end) date is treated as unbounded on that side and so always
// shows. Overlap rule: start <= to AND end >= from.
function applyActivityDateFilter(rn, from, to) {
  const detail = document.getElementById('live-detail-' + rn);
  if (!detail) return;
  detail.querySelectorAll('tr.live-activity-row').forEach(row => {
    const s = row.dataset.start || '';
    const e = row.dataset.end || '';
    let show = true;
    if (to && s && s > to) show = false;      // starts after the window
    if (from && e && e < from) show = false;   // ends before the window
    row.style.display = show ? '' : 'none';
  });
}

// A small inline warning shown next to an activity's % when its actual qty
// has run past its planned qty - the % itself is capped at 100 (never shown
// above), this pill is the prompt to go raise planned_qty in Edit Activities.
function overPlannedWarning(a) {
  if (!a.over_planned) return '';
  return ` <span class="pill pill-bad" title="Actual (${fmt(a.cum_qty)}) exceeds planned (${fmt(a.planned_qty)}). Raise the planned qty in Edit Activities.">⚠ actual &gt; planned</span>`;
}

function liveActivityRow(a) {
  return `
    <tr class="live-activity-row" data-act="${a.activity_id}" data-start="${a.start_date || ''}" data-end="${a.end_date || ''}">
      <td class="wrap">${esc(a.raw_name)}</td><td>${a.weight_pct}%</td>
      <td>${esc(a.unit)}</td><td>${fmt(a.planned_qty)}</td>
      <td>${fmtDate(a.start_date)}</td><td>${fmtDate(a.end_date)}</td>
      <td>${fmt(a.cum_qty)}</td><td>${a.pct == null ? '-' : a.pct + '%'}${overPlannedWarning(a)}</td>
    </tr>`;
}

// Row with the Edit action only - used inside the dedicated "Edit Activities"
// panel (master-data editing; kept out of the default glance table per user request).
function liveActivityEditRow(road, a) {
  return `
    <tr class="live-activity-row" data-act="${a.activity_id}">
      <td class="wrap">${esc(a.raw_name)}</td><td>${esc(a.major) || '-'}</td><td>${a.weight_pct}%</td>
      <td>${esc(a.unit)}</td><td>${fmt(a.planned_qty)}</td>
      <td>${fmt(a.cum_qty)}</td><td>${a.pct == null ? '-' : a.pct + '%'}${overPlannedWarning(a)}</td>
      <td>
        <button class="btn-small btn-edit" data-edit-act="${a.activity_id}">Edit</button>
      </td>
    </tr>
    <tr class="live-activity-form" id="act-edit-${a.activity_id}" hidden>
      <td colspan="8"><form class="edit-form" data-kind="activity" data-id="${a.activity_id}"></form></td>
    </tr>`;
}

// Row for the "View Past Data" panel: a View/Hide toggle that reveals the activity's
// full weekly history (view + add/update + delete) via renderWeeklyPanel.
function liveActivityWeeklyRow(a) {
  return `
    <tr class="live-activity-row" data-act="${a.activity_id}">
      <td class="wrap">${esc(a.raw_name)}</td><td>${esc(a.unit)}</td>
      <td>${fmt(a.planned_qty)}</td><td>${fmt(a.cum_qty)}</td>
      <td>${a.pct == null ? '-' : a.pct + '%'}${overPlannedWarning(a)}</td>
      <td><button class="btn-small btn-edit" data-weekly-act="${a.activity_id}">View / Update</button></td>
    </tr>
    <tr class="live-activity-weekly" id="act-weekly-${a.activity_id}" hidden>
      <td colspan="6"><div class="weekly-wrap"></div></td>
    </tr>`;
}

function roadWeeklyInputRow(a) {
  return `
    <tr>
      <td class="wrap">${esc(a.raw_name)}</td><td>${esc(a.unit)}</td>
      <td>${fmt(a.planned_qty)}</td><td>${fmt(a.cum_qty)}</td>
      <td><input type="number" class="rwk-qty" data-act="${a.activity_id}" placeholder="0"></td>
    </tr>`;
}

// Whole-road weekly entry: one week date, one "actual this week" input per
// activity, looped against the existing per-activity POST .../weekly endpoint
// (no bulk endpoint exists server-side yet). Replaces the old per-activity
// "Weekly" button in the default view.
//
// Each input is the quantity done IN THAT WEEK (a delta), same meaning as the
// per-activity "View Past Data" panel's "actual qty" field - NOT a running
// cumulative total. weekly_entry rows are summed across weeks everywhere
// (calculations.py) to produce the running total shown in "Current Cum.".
// Entering again for the same week ADDS to that week's figure (append-only
// ledger); it does not replace it.
function renderRoadWeeklyPanel(road, wrapEl) {
  const acts = road.activities || [];
  wrapEl.innerHTML = `
    <div class="detail-box">
      <p class="hint">Enter the actual quantity done <b>this week</b> for each activity (not the running total). Leave an activity blank to skip it. Entering again for the same week <b>adds to</b> that week's figure.</p>
      <div class="form-row weekly-form">
        <label>Date</label>
        <input type="date" class="rwk-date" value="${todayISO()}">
        <input type="text" class="rwk-note" placeholder="note (optional, applied to all rows entered)">
      </div>
      <p class="hint rwk-weeknote">Recorded against week <b>${weekLabel(todayISO())}</b>. Pick any day in a week - no need to pick the Monday.</p>
      <div class="table-scroll">
      <table class="data-table sub">
        <thead><tr><th>Activity</th><th>Unit</th><th>Planned</th><th>Current Cum.</th><th>Actual this week</th></tr></thead>
        <tbody>${acts.map(roadWeeklyInputRow).join('') || '<tr><td colspan="5">No activities.</td></tr>'}</tbody>
      </table>
      </div>
      <div class="form-row">
        <button class="btn-small btn-save rwk-submit">Save Week for Road</button>
        <button class="btn-small btn-cancel rwk-cancel">Cancel</button>
      </div>
      <div class="rwk-result hint"></div>
    </div>`;
  wrapEl.querySelector('.rwk-cancel').addEventListener('click', () => { wrapEl.hidden = true; wrapEl.innerHTML = ''; });
  const rwkDateEl = wrapEl.querySelector('.rwk-date');
  const rwkNoteEl = wrapEl.querySelector('.rwk-weeknote');
  rwkDateEl.addEventListener('change', () => {
    const lbl = weekLabel(rwkDateEl.value);
    rwkNoteEl.innerHTML = lbl
      ? `Recorded against week <b>${lbl}</b>. Pick any day in a week - no need to pick the Monday.`
      : 'Pick a date.';
  });
  wrapEl.querySelector('.rwk-submit').addEventListener('click', async () => {
    const resultEl = wrapEl.querySelector('.rwk-result');
    const picked = rwkDateEl.value;
    if (!picked) { setLiveStatus('Pick a date before saving.', true); return; }
    // Any day is fine; the week it belongs to is what gets stored.
    const week_start_date = mondayOf(picked);
    const note = wrapEl.querySelector('.rwk-note').value;
    const inputs = Array.from(wrapEl.querySelectorAll('.rwk-qty')).filter(i => i.value !== '');
    if (!inputs.length) { setLiveStatus("Enter at least one activity's actual for this week.", true); return; }
    let ok = 0; const failed = [];
    // Name lookup so failures read "Excavation: ..." not "#123: ...". resultEl
    // and the status strip both use textContent, so raw names are safe here.
    const nameOf = (id) => {
      const a = acts.find(x => String(x.activity_id) === String(id));
      return a && a.raw_name ? a.raw_name : ('activity #' + id);
    };
    for (const input of inputs) {
      const activityId = input.dataset.act;
      const body = { week_start_date, actual_qty: Number(input.value) };
      if (note) body.note = note;
      try {
        await dbSend('POST', `/api/db/activities/${activityId}/weekly`, body);
        ok++;
      } catch (err) {
        failed.push(`${nameOf(activityId)}: ${err.message}`);
      }
    }
    resultEl.textContent = `Saved ${ok} of ${inputs.length} activities.` + (failed.length ? ' Failed - ' + failed.join('; ') : '');
    resultEl.style.color = failed.length ? 'var(--accent)' : '';
    if (failed.length) {
      setLiveStatus(`Road ${road.road_no}: saved ${ok}/${inputs.length}. Problems - ${failed.join('; ')}`, true);
    } else {
      setLiveStatus(`Weekly update for Road ${road.road_no}: ${ok} ${ok === 1 ? 'activity' : 'activities'} saved.`, false);
    }
    // Always refresh, even on a partial failure - the activities that DID
    // save are already committed in the DB and must show their new numbers.
    // The error message is not lost: setLiveStatus recorded it in ROAD_STATUS
    // and renderLiveRoadsTable re-applies it after rebuilding the panel.
    await refreshLiveRoads();
    await ensureRoadActivities(road.road_no);
  });
}

// "Edit Activities" panel: master-data editing (name, major, weight, unit,
// planned, dates) for each activity. Kept out of the default glance table.
function renderRoadActivitiesEditor(road, wrapEl) {
  const acts = road.activities || [];
  wrapEl.innerHTML = `
    <div class="detail-box">
      <p class="hint">Edit activity master data here (name, major, weight, unit, planned qty, dates).</p>
      <div class="table-scroll">
      <table class="data-table sub">
        <thead><tr><th>Activity</th><th>Major</th><th>Weight</th><th>Unit</th><th>Planned</th><th>Cum. Actual</th><th>% Complete</th><th></th></tr></thead>
        <tbody>${acts.map(a => liveActivityEditRow(road, a)).join('') || '<tr><td colspan="8">No activities.</td></tr>'}</tbody>
      </table>
      </div>
    </div>`;
  wrapEl.querySelectorAll('[data-edit-act]').forEach(btn => btn.addEventListener('click', () => {
    const id = btn.dataset.editAct;
    const row = wrapEl.querySelector('#act-edit-' + id);
    row.hidden = !row.hidden;
    if (!row.hidden) {
      const act = acts.find(a => String(a.activity_id) === String(id));
      if (act) buildEditForm(row.querySelector('.edit-form'), 'activity', id, ACTIVITY_EDIT_FIELDS, act);
    }
  }));
}

// "Issue" panel: log a hindrance window (From/To) on this road. Saving shifts
// the road's unfinished activities later by the window length (server rule in
// create_road_issue). Each logged issue can be edited (dates re-applied) or
// deleted (its shift is reversed) from the list below the form.
function issueResultMessage(out, verb) {
  const n = out.moved, d = out.shift_days;
  let msg = `${verb}: ${n} activit${n === 1 ? 'y' : 'ies'} shifted by ${d} day${d === 1 ? '' : 's'}`;
  const why = [];
  if (out.completed) why.push(`${out.completed} already completed`);
  if (out.ended_before) why.push(`${out.ended_before} ended before the From date`);
  if (out.no_end_date) why.push(`${out.no_end_date} without an end date`);
  if (why.length) msg += ` (not shifted: ${why.join(', ')})`;
  return msg + '.';
}

async function renderRoadIssuePanel(road, wrapEl) {
  const rn = road.road_no;
  wrapEl.innerHTML = `
    <div class="detail-box">
      <p class="hint">Log a site issue with the dates it blocks work. Every unfinished activity on this road moves later by the length of the window, so nothing is planned inside it. Finished activities and ones that ended before the From date are left alone.</p>
      <div class="form-row no-print" style="gap:10px;align-items:center;">
        <label>From <input type="date" class="inline-input" id="issueFrom-${rn}"></label>
        <label>To <input type="date" class="inline-input" id="issueTo-${rn}"></label>
        <input type="text" class="inline-input" id="issueReason-${rn}" placeholder="Issue / reason" style="min-width:240px;">
        <button type="button" class="btn-small btn-save" id="issueSave-${rn}">Add Issue</button>
        <span class="hint" id="issueHint-${rn}"></span>
      </div>
      <div id="issueList-${rn}"><p class="hint">Loading issues...</p></div>
    </div>`;
  const q = sel => wrapEl.querySelector(sel);
  const from = q('#issueFrom-' + rn), to = q('#issueTo-' + rn), hint = q('#issueHint-' + rn);
  const daysBetween = (f, t) => (f && t && t >= f) ? Math.round((Date.parse(t) - Date.parse(f)) / 86400000) + 1 : null;
  const plural = d => `${d} day${d === 1 ? '' : 's'}`;
  const showDays = () => { const d = daysBetween(from.value, to.value); hint.textContent = d ? `Activities will shift ${plural(d)}.` : ''; };
  from.addEventListener('change', showDays);
  to.addEventListener('change', showDays);

  // After any change the road table is rebuilt (panels close), so reopen this one.
  const afterChange = async (msg, isError) => {
    EXPANDED_ROAD = rn;
    await refreshLiveRoads();
    setLiveStatus(msg, isError, { roadNo: rn });
    uiToast(msg, isError ? 'error' : 'ok');
    const btn = document.querySelector(`[data-road-issue="${rn}"]`);
    if (btn) btn.click();
  };

  const loadList = async () => {
    const box = q('#issueList-' + rn);
    try {
      const rows = await dbGet(`/api/db/roads/${rn}/issues`);
      box.innerHTML = rows.length ? `
        <table class="data-table sub">
          <thead><tr><th>From</th><th>To</th><th>Days shifted</th>
            <th title="Number of activities whose planned dates were pushed later by this issue">Activities moved</th>
            <th>Issue</th><th>Logged</th><th></th></tr></thead>
          <tbody>${rows.map(r => `
            <tr data-issue-row="${r.issue_id}">
              <td>${fmtDate(r.from_date)}</td><td>${fmtDate(r.to_date)}</td>
              <td>${r.shift_days}</td><td>${r.activities_shifted}</td>
              <td class="wrap issue-text">${esc(r.reason) || '-'}</td><td>${fmtDateTime(r.created_at)}</td>
              <td class="no-print" style="white-space:nowrap;">${r.to_date < todayISO() ? '' : `
                <button type="button" class="btn-small btn-edit" data-issue-edit="${r.issue_id}">Edit</button>
                <button type="button" class="btn-small btn-danger" data-issue-del="${r.issue_id}">Delete</button>`}
              </td>
            </tr>`).join('')}</tbody>
        </table>` : '<p class="hint">No issues logged for this road.</p>';

      box.querySelectorAll('[data-issue-del]').forEach(btn => btn.addEventListener('click', async () => {
        const row = rows.find(r => String(r.issue_id) === btn.dataset.issueDel);
        if (!(await uiConfirm(`Delete this issue? The ${plural(row.shift_days)} it added will be taken back out of the road's activity dates.`,
                              { okLabel: 'Delete', danger: true }))) return;
        try {
          await dbSend('DELETE', `/api/db/roads/${rn}/issues/${row.issue_id}`);
          await afterChange(`Issue deleted; ${plural(row.shift_days)} taken back out of the schedule.`, false);
        } catch (err) { uiToast('Could not delete issue: ' + err.message, 'error'); }
      }));

      box.querySelectorAll('[data-issue-edit]').forEach(btn => btn.addEventListener('click', () => {
        const row = rows.find(r => String(r.issue_id) === btn.dataset.issueEdit);
        const tr = box.querySelector(`[data-issue-row="${row.issue_id}"]`);
        tr.innerHTML = `
          <td><input type="date" class="inline-input" data-e-from value="${row.from_date}"></td>
          <td><input type="date" class="inline-input" data-e-to value="${row.to_date}"></td>
          <td colspan="3"><input type="text" class="inline-input" data-e-reason value="${esc(row.reason || '')}" style="width:100%;" placeholder="Issue / reason"></td>
          <td colspan="2" style="white-space:nowrap;">
            <button type="button" class="btn-small btn-save" data-e-save>Save</button>
            <button type="button" class="btn-small btn-cancel" data-e-cancel>Cancel</button>
          </td>`;
        tr.querySelector('[data-e-cancel]').addEventListener('click', loadList);
        tr.querySelector('[data-e-save]').addEventListener('click', async () => {
          const f = tr.querySelector('[data-e-from]').value, t = tr.querySelector('[data-e-to]').value;
          if (!f || !t) { uiToast('Pick both From and To dates.', 'error'); return; }
          if (t < f) { uiToast('To must be on or after From.', 'error'); return; }
          try {
            const out = await dbSend('PUT', `/api/db/roads/${rn}/issues/${row.issue_id}`,
              { from_date: f, to_date: t, reason: tr.querySelector('[data-e-reason]').value });
            await afterChange(issueResultMessage(out, 'Issue updated'), false);
          } catch (err) { uiToast('Could not update issue: ' + err.message, 'error'); }
        });
      }));
    } catch (err) {
      box.innerHTML = `<p class="hint">Could not load issues: ${esc(err.message)}</p>`;
    }
  };
  loadList();

  q('#issueSave-' + rn).addEventListener('click', async () => {
    if (!from.value || !to.value) { uiToast('Pick both From and To dates.', 'error'); return; }
    if (to.value < from.value) { uiToast('To must be on or after From.', 'error'); return; }
    const d = daysBetween(from.value, to.value);
    if (!(await uiConfirm(`Shift all unfinished activities on road ${rn} by ${plural(d)}? This changes their planned dates (logged in the Edit Log).`,
                          { okLabel: 'Add issue' }))) return;
    try {
      const out = await dbSend('POST', `/api/db/roads/${rn}/issues`,
        { from_date: from.value, to_date: to.value, reason: q('#issueReason-' + rn).value });
      await afterChange(issueResultMessage(out, 'Issue logged'), false);
    } catch (err) { uiToast('Could not log issue: ' + err.message, 'error'); }
  });
}

// "View Past Data" panel: per-activity entry history, view + delete.
function renderRoadWeeklyData(road, wrapEl) {
  const acts = road.activities || [];
  wrapEl.innerHTML = `
    <div class="detail-box">
      <p class="hint">View the recorded entries for a single activity. Open one to see every submission and add or delete individual entries.</p>
      <div class="table-scroll">
      <table class="data-table sub">
        <thead><tr><th>Activity</th><th>Unit</th><th>Planned</th><th>Cum. Actual</th><th>% Complete</th><th></th></tr></thead>
        <tbody>${acts.map(liveActivityWeeklyRow).join('') || '<tr><td colspan="6">No activities.</td></tr>'}</tbody>
      </table>
      </div>
    </div>`;
  wrapEl.querySelectorAll('[data-weekly-act]').forEach(btn => btn.addEventListener('click', () => {
    const id = btn.dataset.weeklyAct;
    const row = wrapEl.querySelector('#act-weekly-' + id);
    row.hidden = !row.hidden;
    if (!row.hidden) renderWeeklyPanel(id, row.querySelector('.weekly-wrap'));
  }));
}

// "Bottlenecks" panel for a single road - same bottleneck feed as the global
// tab, filtered to this road, shown inline in the expanded road detail.
async function renderRoadBottlenecks(road, wrapEl) {
  wrapEl.innerHTML = '<div class="detail-box">Loading...</div>';
  try {
    const rows = (await dbGet('/api/db/bottlenecks')).filter(r => Number(r.road_no) === Number(road.road_no));
    if (!rows.length) {
      wrapEl.innerHTML = '<div class="detail-box"><p class="hint">No bottlenecks on this road right now.</p></div>';
      return;
    }
    wrapEl.innerHTML = `
      <div class="detail-box">
        <p class="hint">An activity is flagged if it is behind planned-to-date by more than 15 percentage, is holding up the next activity in sequence, or has had zero recorded progress for 3+ weeks since it started.</p>
        <div class="table-scroll">
        <table class="data-table sub">
          <thead><tr><th>Activity</th><th>Actual %</th><th>Expected %</th><th>Reasons</th></tr></thead>
          <tbody>${rows.map(r => `
            <tr class="issue-row">
              <td class="wrap">${esc(r.activity)}</td>
              <td>${r.actual_pct}%</td><td>${r.expected_pct}%</td>
              <td class="wrap">${esc((r.reasons || []).join('; '))}</td>
            </tr>`).join('')}</tbody>
        </table>
        </div>
      </div>`;
  } catch (err) {
    wrapEl.innerHTML = `<div class="detail-box"><p class="hint">Could not load bottlenecks: ${esc(err.message)}</p></div>`;
  }
}

// "Parallel Working" panel for a single road - the stacked pyramid (drains
// excluded), reusing the critical-path feed and csPyramid renderer.
async function renderRoadPyramid(road, wrapEl) {
  wrapEl.innerHTML = '<div class="detail-box">Loading...</div>';
  try {
    const data = await dbGet(`/api/db/roads/${road.road_no}/critical-path`);
    const svg = (data.path && data.path.length) ? csPyramid(data.path) : '';
    wrapEl.innerHTML = `
      <div class="detail-box">
        <p class="hint">Build activities stacked in sequence, foundation at the bottom. Each track fills by actual % complete.</p>
        ${svg || `<p class="hint">${esc(data.note || 'No sequenced activities for this road.')}</p>`}
      </div>`;
  } catch (err) {
    wrapEl.innerHTML = `<div class="detail-box"><p class="hint">Could not load the pyramid: ${esc(err.message)}</p></div>`;
  }
}

function liveRoadBlock(road) {
  const open = EXPANDED_ROAD === road.road_no;
  const acts = road.activities;
  const bodyHtml = acts == null
    ? '<tr><td colspan="8" class="hint">Loading activities...</td></tr>'
    : (acts.map(a => liveActivityRow(a)).join('') || '<tr><td colspan="8">No activities.</td></tr>');
  return `
    <tbody class="live-road-group">
      <tr class="road-head live-road-head${open ? ' open' : ''}" data-live-road="${road.road_no}">
        <td>${road.road_no}</td><td class="wrap">${esc(road.location)}</td><td class="wrap">${esc(road.vendor)}</td>
        <td class="wrap">${esc(road.line_manager)}</td><td>${esc(road.road_type)}</td>
        <td>${fmtDate(road.start_date)}</td><td>${fmtDate(road.end_date)}</td>
        <td>${road.progress_pct == null ? '-' : road.progress_pct + '%'}${roadStatusPill(road)}${roadBottleneckPill(road)}${roadOverPlannedPill(road)}${roadCompletionBar(road)}</td>
        <td>${road.drain_progress_pct == null ? '-' : road.drain_progress_pct + '%'}</td>
        <td><button class="btn-small btn-edit" data-edit-road="${road.road_no}">Edit Road</button></td>
      </tr>
      <tr class="road-detail" id="live-detail-${road.road_no}" ${open ? '' : 'hidden'}>
        <td colspan="10">
          <div class="detail-box">
            <form class="edit-form" id="road-edit-${road.road_no}" data-kind="road" data-id="${road.road_no}" hidden></form>
            <div class="road-status" id="road-status-${road.road_no}" hidden></div>
            <div class="form-row no-print" style="margin:0 0 12px;">
              <button class="btn-small btn-edit" data-road-weekly="${road.road_no}">Update (whole Road)</button>
              <button class="btn-small btn-edit" data-road-weeklydata="${road.road_no}">View Past Data</button>
              <button class="btn-small btn-edit" data-road-bottlenecks="${road.road_no}">Bottlenecks</button>
              <button class="btn-small btn-edit" data-road-pyramid="${road.road_no}">Parallel Working</button>
              <button class="btn-small btn-edit" data-road-editacts="${road.road_no}">Edit Activities</button>
              <button class="btn-small btn-edit btn-issue" data-road-issue="${road.road_no}">Issue</button>
            </div>
            <div class="road-weekly-wrap" id="road-weekly-${road.road_no}" hidden></div>
            <div class="road-weeklydata-wrap" id="road-weeklydata-${road.road_no}" hidden></div>
            <div class="road-bottlenecks-wrap" id="road-bottlenecks-${road.road_no}" hidden></div>
            <div class="road-pyramid-wrap" id="road-pyramid-${road.road_no}" hidden></div>
            <div class="road-editacts-wrap" id="road-editacts-${road.road_no}" hidden></div>
            <div class="road-issue-wrap" id="road-issue-${road.road_no}" hidden></div>
            <div class="form-row no-print act-date-filter" data-road="${road.road_no}" style="margin:0 0 8px;gap:6px;align-items:center;">
              <label class="hint">Activity dates</label>
              <span class="hint">from</span>
              <input type="date" class="act-filter-from" title="Show activities on or after this date">
              <span class="hint">to</span>
              <input type="date" class="act-filter-to" title="Show activities on or before this date">
              <button type="button" class="btn-small btn-edit act-filter-clear">Clear</button>
            </div>
            <div class="table-scroll">
            <table class="data-table sub">
              <thead><tr><th>Activity</th><th>Weight</th><th>Unit</th><th>Planned</th><th>Start</th><th>End</th><th>Cum. Actual</th><th>% Complete</th></tr></thead>
              <tbody>${bodyHtml}</tbody>
            </table>
            </div>
          </div>
        </td>
      </tr>
    </tbody>`;
}

async function ensureRoadActivities(roadNo) {
  const road = LIVE_ROADS.find(r => r.road_no === roadNo);
  if (!road || road.activities != null) return;
  try {
    const resp = await dbGet(`/api/db/roads/${roadNo}`);
    road.activities = resp.activities;
    renderLiveRoadsTable();
  } catch (err) {
    setLiveStatus('Could not load activities for road ' + roadNo + ': ' + err.message, true, { roadNo });
  }
}

function buildEditForm(formEl, kind, id, fields, sourceObj) {
  formEl.innerHTML = editFormRows(fields, sourceObj) +
    `<div class="form-row form-actions"><button type="button" class="btn-small btn-save">Save</button>
     <button type="button" class="btn-small btn-cancel">Cancel</button></div>`;
  formEl.classList.toggle('form-grid', kind === 'road');     // road form: same even grid as Add Road
  if (kind === 'activity') {                                  // weight only exists for a Major
    const maj = formEl.querySelector('[data-field="major"]'), wt = formEl.querySelector('[data-field="weight_pct"]');
    if (maj && wt) {
      syncWeightToMajor(maj, wt);
      maj.addEventListener('change', () => syncWeightToMajor(maj, wt));
    }
  }
  wireComboForm(formEl);
  formEl.hidden = false;
  formEl.querySelector('.btn-save').addEventListener('click', () => submitEdit(kind, id, fields, formEl));
  formEl.querySelector('.btn-cancel').addEventListener('click', () => { formEl.hidden = true; formEl.innerHTML = ''; });
}

function wireLiveRoadsEvents() {
  document.querySelectorAll('.live-road-head').forEach(h => h.addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    const rn = Number(h.dataset.liveRoad);
    const closing = EXPANDED_ROAD === rn;
    EXPANDED_ROAD = closing ? null : rn;
    // Closing this road's row clears any status message it was showing, so
    // reopening it later doesn't resurface a stale save/error message.
    if (closing && ROAD_STATUS && ROAD_STATUS.roadNo === rn) ROAD_STATUS = null;
    renderLiveRoadsTable();
    if (EXPANDED_ROAD === rn) ensureRoadActivities(rn);
  }));
  document.querySelectorAll('[data-edit-road]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.editRoad);
    EXPANDED_ROAD = rn;
    renderLiveRoadsTable();
    await ensureRoadActivities(rn);
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const formEl = document.getElementById('road-edit-' + rn);
    if (formEl && road) buildEditForm(formEl, 'road', rn, ROAD_EDIT_FIELDS, road);
  }));
  // Per-road from/to date filter: hide activity rows whose [start,end] range
  // does not overlap the picked window. Pure DOM filtering off the row's
  // data-start/data-end; undated activities always show.
  document.querySelectorAll('.act-date-filter').forEach(bar => {
    const rn = bar.dataset.road;
    const fromEl = bar.querySelector('.act-filter-from');
    const toEl = bar.querySelector('.act-filter-to');
    const apply = () => applyActivityDateFilter(rn, fromEl.value, toEl.value);
    fromEl.addEventListener('change', apply);
    toEl.addEventListener('change', apply);
    bar.querySelector('.act-filter-clear').addEventListener('click', () => {
      fromEl.value = ''; toEl.value = ''; apply();
    });
  });
  // The three road-detail panels (Update whole Road / View Past Data / Edit Activities)
  // are mutually exclusive - opening one closes the other two.
  const closeRoadPanels = (rn, except) => {
    [['road-weekly-', 'weekly'], ['road-weeklydata-', 'weeklydata'],
     ['road-bottlenecks-', 'bottlenecks'], ['road-pyramid-', 'pyramid'],
     ['road-editacts-', 'editacts'], ['road-issue-', 'issue']]
      .forEach(([prefix, key]) => {
        if (key === except) return;
        const el = document.getElementById(prefix + rn);
        if (el) { el.hidden = true; el.innerHTML = ''; }
      });
  };
  document.querySelectorAll('[data-road-weekly]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.roadWeekly);
    await ensureRoadActivities(rn); // may re-render the table if not already cached
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const wrap = document.getElementById('road-weekly-' + rn);
    if (!wrap) return;
    closeRoadPanels(rn, 'weekly');
    const willOpen = wrap.hidden;
    wrap.hidden = !willOpen;
    if (willOpen && road) renderRoadWeeklyPanel(road, wrap);
  }));
  document.querySelectorAll('[data-road-weeklydata]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.roadWeeklydata);
    await ensureRoadActivities(rn); // may re-render the table if not already cached
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const wrap = document.getElementById('road-weeklydata-' + rn);
    if (!wrap) return;
    closeRoadPanels(rn, 'weeklydata');
    const willOpen = wrap.hidden;
    wrap.hidden = !willOpen;
    if (willOpen && road) renderRoadWeeklyData(road, wrap);
  }));
  document.querySelectorAll('[data-road-bottlenecks]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.roadBottlenecks);
    await ensureRoadActivities(rn);
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const wrap = document.getElementById('road-bottlenecks-' + rn);
    if (!wrap) return;
    closeRoadPanels(rn, 'bottlenecks');
    const willOpen = wrap.hidden;
    wrap.hidden = !willOpen;
    if (willOpen && road) renderRoadBottlenecks(road, wrap);
  }));
  document.querySelectorAll('[data-road-pyramid]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.roadPyramid);
    await ensureRoadActivities(rn);
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const wrap = document.getElementById('road-pyramid-' + rn);
    if (!wrap) return;
    closeRoadPanels(rn, 'pyramid');
    const willOpen = wrap.hidden;
    wrap.hidden = !willOpen;
    if (willOpen && road) renderRoadPyramid(road, wrap);
  }));
  document.querySelectorAll('[data-road-editacts]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.roadEditacts);
    await ensureRoadActivities(rn); // may re-render the table if not already cached
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const wrap = document.getElementById('road-editacts-' + rn);
    if (!wrap) return;
    closeRoadPanels(rn, 'editacts');
    const willOpen = wrap.hidden;
    wrap.hidden = !willOpen;
    if (willOpen && road) renderRoadActivitiesEditor(road, wrap);
  }));
  document.querySelectorAll('[data-road-issue]').forEach(btn => btn.addEventListener('click', async () => {
    const rn = Number(btn.dataset.roadIssue);
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    const wrap = document.getElementById('road-issue-' + rn);
    if (!wrap) return;
    closeRoadPanels(rn, 'issue');
    const willOpen = wrap.hidden;
    wrap.hidden = !willOpen;
    if (willOpen && road) renderRoadIssuePanel(road, wrap);
  }));
  document.querySelectorAll('[data-goto-bottlenecks]').forEach(btn => btn.addEventListener('click', (e) => {
    e.stopPropagation();
    activateTab('bottlenecks');
  }));
}

/* ---------------------------------------------------------------------
   Live Progress filters (line manager / vendor / type). Purely client
   side: every road is already in LIVE_ROADS, so filtering just decides
   which rows renderLiveRoadsTable() draws. The selects are filled from
   the distinct values actually present, so nothing can be picked that
   returns an empty table unless the other filters exclude it.
   --------------------------------------------------------------------- */
const ROAD_FILTER_IDS = {
  line_manager: 'roadFilterLm',
  vendor: 'roadFilterVendor',
  road_type: 'roadFilterType',
};

function roadFilterValues() {
  const out = {};
  Object.entries(ROAD_FILTER_IDS).forEach(([field, id]) => {
    const el = document.getElementById(id);
    out[field] = el ? el.value : '';
  });
  return out;
}

function filteredLiveRoads() {
  const f = roadFilterValues();
  return LIVE_ROADS.filter(r =>
    Object.entries(f).every(([field, val]) => !val || String(r[field] || '') === val));
}

// A dropdown with exactly one real choice (besides "All"/blank) selects it
// automatically - there's nothing to decide. Returns true when it ends up
// holding that single value. Use after (re)building a filter's options.
function autoSelectSingle(sel) {
  if (!sel || sel.disabled) return false;
  const real = [...sel.options].filter(o => o.value !== '');
  if (real.length !== 1) return false;
  sel.value = real[0].value;
  return true;
}

function populateRoadFilters() {
  Object.entries(ROAD_FILTER_IDS).forEach(([field, id]) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const keep = sel.value;
    const vals = [...new Set(LIVE_ROADS.map(r => String(r[field] || '').trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b));
    sel.innerHTML = '<option value="">All</option>' +
      vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if (vals.includes(keep)) sel.value = keep;
    autoSelectSingle(sel);
  });
}

function wireRoadFilters() {
  const bar = document.getElementById('roadFilterBar');
  if (!bar || bar.dataset.bound) return;
  Object.values(ROAD_FILTER_IDS).forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', renderLiveRoadsTable);
  });
  const clear = document.getElementById('roadFilterClear');
  if (clear) clear.addEventListener('click', () => {
    Object.values(ROAD_FILTER_IDS).forEach(id => {
      const el = document.getElementById(id);
      if (el) { el.value = ''; autoSelectSingle(el); }   // a lone option stays selected
    });
    renderLiveRoadsTable();
  });
  bar.dataset.bound = '1';
}

/* ---------------------------------------------------------------------
   Phase groups. Roads are grouped by their `phase` value; every distinct
   phase found in the data gets its own collapsible section (a phase added
   later - Add Road with a new phase number, or editing a road's phase -
   appears automatically, nothing here is hard-coded to Phase 1). Collapsed
   phases are remembered across reloads/refreshes.
   --------------------------------------------------------------------- */
const PHASE_KEY = 'tata_phase_collapsed';
const PHASE_COLLAPSED = (() => {
  try { return new Set(JSON.parse(localStorage.getItem(PHASE_KEY) || '[]').map(String)); }
  catch (e) { return new Set(); }
})();
function savePhaseState() {
  try { localStorage.setItem(PHASE_KEY, JSON.stringify([...PHASE_COLLAPSED])); } catch (e) { /* storage unavailable */ }
}
const phaseOf = r => (r.phase == null || r.phase === '' ? 1 : Number(r.phase));

function phaseSection(phase, roads) {
  const collapsed = PHASE_COLLAPSED.has(String(phase));
  const len = roads.reduce((s, r) => s + (Number(r.length_m) || 0), 0);
  const pct = len ? roads.reduce((s, r) => s + (Number(r.progress_pct) || 0) * (Number(r.length_m) || 0), 0) / len : null;
  return `
    <section class="phase-group" data-phase="${phase}">
      <button type="button" class="phase-head${collapsed ? '' : ' open'}" data-phase-toggle="${phase}" aria-expanded="${!collapsed}">
        <span class="phase-title">Phase ${phase}</span>
        <span class="phase-meta">${roads.length} road${roads.length === 1 ? '' : 's'}${pct == null ? '' : ` &middot; ${Math.round(pct * 10) / 10}% complete`}</span>
      </button>
      <div class="phase-body"${collapsed ? ' hidden' : ''}>
        <div class="table-scroll">
        <table class="data-table live-roads-table">
          <thead><tr>
            <th>Road</th><th>Location</th><th>Vendor</th><th>Line Manager</th><th>Type</th>
            <th>Start Date</th><th>End Date</th>
            <th>% Complete</th><th>Drain %</th><th></th>
          </tr></thead>${roads.map(liveRoadBlock).join('')}
        </table>
        </div>
      </div>
    </section>`;
}

function renderLiveRoadsTable() {
  wireRoadFilters();
  const shown = filteredLiveRoads();
  const countEl = document.getElementById('roadFilterCount');
  if (countEl) {
    countEl.textContent = shown.length === LIVE_ROADS.length
      ? `Showing all ${LIVE_ROADS.length} roads`
      : `Showing ${shown.length} of ${LIVE_ROADS.length} roads`;
  }
  // A road that is open (e.g. jumped to from Weight Audit) must not hide inside a collapsed phase.
  if (EXPANDED_ROAD != null) {
    const open = LIVE_ROADS.find(r => r.road_no === EXPANDED_ROAD);
    if (open) PHASE_COLLAPSED.delete(String(phaseOf(open)));
  }
  const byPhase = new Map();
  shown.forEach(r => {
    const p = phaseOf(r);
    if (!byPhase.has(p)) byPhase.set(p, []);
    byPhase.get(p).push(r);
  });
  const phases = [...byPhase.keys()].sort((x, y) => x - y);
  document.getElementById('liveRoadsWrap').innerHTML = shown.length
    ? phases.map(p => phaseSection(p, byPhase.get(p))).join('')
    : '<p class="hint">No roads match the filters selected above.</p>';
  document.querySelectorAll('[data-phase-toggle]').forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.phaseToggle;
    const body = btn.nextElementSibling;
    const nowHidden = !body.hidden;
    body.hidden = nowHidden;
    btn.classList.toggle('open', !nowHidden);
    btn.setAttribute('aria-expanded', String(!nowHidden));
    if (nowHidden) PHASE_COLLAPSED.add(key); else PHASE_COLLAPSED.delete(key);
    savePhaseState();
  }));
  wireLiveRoadsEvents();
  reapplyRoadStatus();
}

/* =====================================================================
   Overall programme progress - a single roll-up of the whole phase out of
   its totals. Actual vs planned headline %, metre-equivalent completed of
   total road length and drain length, roads fully complete, and activities
   complete. All derived from the already-loaded overview + roads + the
   activity-status feed; no new backend endpoint.
   ===================================================================== */
function renderOverall(overview, roads, status) {
  const wrap = document.getElementById('overallWrap');
  if (!wrap) return;
  const detail = (overview && overview.roads) || [];

  // Length-weighted planned % across roads (actual overall comes from backend).
  const plannedOverall = detail.reduce((s, r) =>
    s + (Number(r.length_weight_pct) || 0) / 100 * (r.planned_pct == null ? 0 : Number(r.planned_pct)), 0);
  const actualOverall = Number(overview.overall_pct) || 0;

  // Metre-equivalent completed = sum(length * road %), against total length.
  const totalLen = roads.reduce((s, r) => s + (Number(r.length_m) || 0), 0);
  const roadPctByNo = new Map(detail.map(r => [r.road_no, Number(r.road_pct) || 0]));
  const doneLen = roads.reduce((s, r) =>
    s + (Number(r.length_m) || 0) * (roadPctByNo.get(r.road_no) || 0) / 100, 0);

  const totalDrain = roads.reduce((s, r) => s + (Number(r.drain_planned_m) || 0), 0);
  const doneDrain = roads.reduce((s, r) =>
    s + (Number(r.drain_planned_m) || 0) * (Number(r.drain_progress_pct) || 0) / 100, 0);

  const roadsComplete = detail.filter(r => (Number(r.road_pct) || 0) >= 100).length;
  const totalCost = roads.reduce((s, r) => s + (Number(r.cost_cr) || 0), 0);

  const st = status || {};
  const actsComplete = Number(st.complete) || 0;
  const actsTotal = actsComplete + (Number(st.in_progress) || 0) + (Number(st.not_started) || 0);

  const pct1 = v => (v == null ? '—' : Number(v).toFixed(1) + '%');
  const bar = (actual, planned) => {
    const a = Math.max(0, Math.min(100, actual));
    const tick = planned == null ? '' :
      `<b style="left:calc(${Math.max(0, Math.min(100, planned))}% - 1px)" title="Planned ${pct1(planned)}"></b>`;
    return `<span class="road-pb overall-pb" title="Actual ${pct1(actual)}"><i style="width:${a}%"></i>${tick}</span>`;
  };

  const tiles = [
    ['Overall progress (actual)', pct1(actualOverall)],
    ['Planned to date', pct1(plannedOverall)],
    ['Road length done', `${fmt(Math.round(doneLen))} / ${fmt(Math.round(totalLen))} m`],
    ['Drain done', `${fmt(Math.round(doneDrain))} / ${fmt(Math.round(totalDrain))} m`],
    ['Roads complete', `${roadsComplete} / ${roads.length}`],
    ['Activities complete', `${actsComplete} / ${actsTotal}`],
    ['Total cost (Cr)', Math.round(totalCost * 100) / 100],
  ];

  wrap.innerHTML = `
    <div class="overall-headline">
      <div class="overall-figure">
        <div class="overall-pct">${pct1(actualOverall)}</div>
        <div class="hint">of the whole programme complete (length-weighted), against ${pct1(plannedOverall)} planned to date</div>
        ${bar(actualOverall, plannedOverall)}
      </div>
    </div>
    <div class="overall-tiles">
      ${tiles.map(([l, v]) => `<div class="kpi-tile"><div class="kpi-value">${v}</div><div class="kpi-label">${l}</div></div>`).join('')}
    </div>`;
}

async function refreshLiveRoads() {
  try {
    // These three endpoints are independent, so fetch them concurrently rather
    // than one after another — each used to wait for the previous to finish,
    // stacking three server round-trips in series on every refresh. activity-
    // status is allowed to fail on its own (overall still renders with null).
    const [roads, overview, status] = await Promise.all([
      dbGet('/api/db/roads'),
      dbGet('/api/db/overview'),
      dbGet('/api/db/charts/activity-status').catch(() => null),
    ]);
    LIVE_ROADS = roads;
    populateRoadFilters();
    renderLiveRoadsTable();
    renderKpis(LIVE_ROADS);
    renderWeightChart(overview, LIVE_ROADS);
    renderOverall(overview, LIVE_ROADS, status);
    // The fresh road objects above have no cached `.activities`, so the
    // currently expanded road's default table would otherwise be stuck on
    // "Loading activities..." forever (nothing else re-triggers the fetch).
    // Re-populate it immediately so the table comes back to life on its own.
    if (EXPANDED_ROAD != null) await ensureRoadActivities(EXPANDED_ROAD);
  } catch (err) {
    document.getElementById('liveRoadsWrap').innerHTML =
      `<p class="hint">Could not load live roads from the database: ${esc(err.message)}` +
      ` (is the API/DB tier running?)</p>`;
  }
}

function weightAuditFixRow(rn) {
  return (btn) => btn.addEventListener('click', async () => {
    EXPANDED_ROAD = rn;
    renderLiveRoadsTable();
    activateTab('roads');
    await ensureRoadActivities(rn);
    const formEl = document.getElementById('road-edit-' + rn);
    const road = LIVE_ROADS.find(r => r.road_no === rn);
    if (formEl && road) buildEditForm(formEl, 'road', rn, ROAD_EDIT_FIELDS, road);
  });
}

async function renderWeightAudit() {
  const wrap = document.getElementById('weightAuditWrap');
  try {
    const resp = await dbGet('/api/db/weight-audit');
    if (resp.clean) { wrap.innerHTML = '<p>All roads total 100% weight. Nothing to fix.</p>'; return; }
    const notTotalling = resp.roads_not_totalling_100 || [];
    const dupMajors = resp.duplicate_major_rows || [];
    wrap.innerHTML = `
      ${notTotalling.length ? `
      <h3 class="hint">Roads not totalling 100% weight</h3>
      <table class="data-table">
        <thead><tr><th>Road</th><th>Location</th><th>Weight Total</th><th></th></tr></thead>
        <tbody>${notTotalling.map(r => `
          <tr class="worklist-row">
            <td>${r.road_no}</td><td class="wrap">${esc(r.location)}</td>
            <td>${r.weight_total}%</td>
            <td><button class="btn-small btn-edit" data-fix-road="${r.road_no}">Fix</button></td>
          </tr>`).join('')}</tbody>
      </table>` : ''}
      ${dupMajors.length ? `
      <h3 class="hint">Duplicate major rows</h3>
      <table class="data-table">
        <thead><tr><th>Road</th><th>Major</th><th>Rows</th><th>Summed Weight</th><th>Raw Names</th><th></th></tr></thead>
        <tbody>${dupMajors.map(r => `
          <tr class="worklist-row">
            <td>${r.road_no}</td><td>${esc(r.major)}</td>
            <td>${r.activity_rows}</td><td>${r.summed_weight}%</td>
            <td class="wrap">${esc((r.raw_names || []).join(', '))}</td>
            <td><button class="btn-small btn-edit" data-fix-road="${r.road_no}">Fix</button></td>
          </tr>`).join('')}</tbody>
      </table>` : ''}`;
    wrap.querySelectorAll('[data-fix-road]').forEach(btn =>
      weightAuditFixRow(Number(btn.dataset.fixRoad))(btn));
  } catch (err) {
    wrap.innerHTML = `<p class="hint">Could not load weight audit: ${esc(err.message)}</p>`;
  }
}

async function renderEditLog() {
  const wrap = document.getElementById('editLogWrap');
  try {
    const rows = await dbGet('/api/db/edit-log');
    if (!rows.length) { wrap.innerHTML = '<p>No edits logged yet.</p>'; return; }
    wrap.innerHTML = `
      <table class="data-table">
        <thead><tr><th>When</th><th>Table</th><th>Row</th><th>Field</th><th>Old</th><th>New</th></tr></thead>
        <tbody>${rows.map(r => `
          <tr>
            <td>${fmtDateTime(r.edited_at)}</td><td>${esc(r.table_name)}</td><td>${esc(r.row_pk)}</td>
            <td>${esc(r.field)}</td><td class="wrap">${esc(r.old_value)}</td><td class="wrap">${esc(r.new_value)}</td>
          </tr>`).join('')}</tbody>
      </table>`;
  } catch (err) {
    wrap.innerHTML = `<p class="hint">Could not load edit log: ${esc(err.message)}</p>`;
  }
}

/* =====================================================================
   CRITICAL PATH TAB - GET /api/db/roads/{road_no}/critical-path.
   Road select is populated from LIVE_ROADS (already loaded by init()).
   ===================================================================== */

// Horizontal Gantt bar per activity in canonical seq_no order, on a shared
// real-date time axis - so planned parallel/overlapping activities (e.g.
// soil disposal starting once excavation is partway done) visibly overlap
// instead of being buried in a text note. Same CHART_COL palette as the
// S-curve for visual consistency.
function csGantt(path) {
  const bars = (path || []).filter(p => p.start && p.end);
  if (!bars.length) return '<p class="hint">No dated activities to chart yet.</p>';
  const day = s => Date.parse(s + 'T00:00:00Z');
  const times = bars.flatMap(p => [day(p.start), day(p.end)]);
  const t0 = Math.min(...times), t1 = Math.max(...times), span = (t1 - t0) || 1;
  const rowH = 24, gap = 8, L = 190, R = 16, T = 10, B = 26;
  const W = 820, pw = W - L - R, H = T + bars.length * (rowH + gap) + B;
  const X = t => L + pw * (t - t0) / span;
  let g = '';
  const d0 = new Date(t0);
  for (let d = new Date(Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), 1)); d.getTime() <= t1; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
    const x = X(d.getTime());
    if (x < L || x > W - R) continue;
    g += `<line x1="${x}" x2="${x}" y1="${T}" y2="${T + bars.length * (rowH + gap)}" stroke="#F2F4F6"/>` +
         `<text x="${x}" y="${H - 10}" fill="#4A5560" font-size="9" text-anchor="middle">${d.toLocaleString('en', { month: 'short', year: '2-digit', timeZone: 'UTC' })}</text>`;
  }
  bars.forEach((p, i) => {
    const y = T + i * (rowH + gap);
    const x1 = X(day(p.start)), x2 = X(day(p.end));
    const overlapsPrev = i > 0 && day(p.start) < day(bars[i - 1].end);
    g += `<text x="${L - 8}" y="${y + rowH / 2 + 4}" font-size="10" fill="#1A1A1A" text-anchor="end">${esc((p.activity || '').slice(0, 30))}</text>` +
         `<rect x="${x1.toFixed(1)}" y="${y}" width="${Math.max(x2 - x1, 2).toFixed(1)}" height="${rowH}" rx="3" fill="${overlapsPrev ? CHART_COL.planned : CHART_COL.actual}"><title>${esc(p.activity)}: ${fmtDate(p.start)} to ${fmtDate(p.end)}</title></rect>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="gantt-svg" style="width:100%;height:auto;">${g}</svg>
    <div class="chart-legend"><span><i style="background:${CHART_COL.actual}"></i>Runs after predecessor finishes</span>` +
    `<span><i style="background:${CHART_COL.planned}"></i>Planned parallel working (overlaps predecessor)</span></div>`;
}

// "Planned parallel working" as a stacked PYRAMID - same visual idea as a
// road cross-section (foundation layer at the bottom, wearing course at the
// top), one band per activity in canonical seq_no order. Drains are not a
// road layer, so they are excluded. The foundation (first in sequence) is the
// widest band; each band above it steps in narrower, forming a centred,
// stepped pyramid. Bands sit flush on each other (no gaps). Each band is a
// progress track that fills left-to-right by its actual % complete.
function csPyramid(path) {
  const bands = (path || []).filter(p => p.start && p.end && !p.is_drain);
  if (!bands.length) return '';
  const n = bands.length;
  const rowH = 26, padTop = 10, padBottom = 10;
  const maxW = 340, minW = 96;   // bottom (foundation) widest, apex narrowest
  const L = 150, R = 48;
  const H = padTop + n * rowH + padBottom;
  const W = L + maxW + R;
  const cx = L + maxW / 2;        // horizontal centre the pyramid is built around
  const palette = ['#0A2A4A', '#1E8E3E', '#2F3A4A', '#5B7A6A', '#4A5560', '#5B6B82', '#2E4A2E', '#8A6B3A', '#B36B00', '#6B4A8A', '#1A5276', '#7D6608'];
  let g = '';
  bands.forEach((p, i) => {
    // i = 0 is the foundation and sits at the base; higher i stacks on top.
    const y = H - padBottom - (i + 1) * rowH;
    const w = n === 1 ? maxW : minW + (maxW - minW) * ((n - 1 - i) / (n - 1));
    const x = cx - w / 2;
    const pct = Math.max(0, Math.min(100, Number(p.actual_pct) || 0));
    const fillW = w * pct / 100;
    const color = palette[i % palette.length];
    g += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${rowH}" fill="#E8EBEE" stroke="#FFFFFF" stroke-width="1"><title>${esc(p.activity)}: ${fmtDate(p.start)} to ${fmtDate(p.end)}</title></rect>`;
    if (fillW > 0) g += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${fillW.toFixed(1)}" height="${rowH}" fill="${color}"><title>${esc(p.activity)}: ${pct}% complete</title></rect>`;
    g += `<text x="${(L - 10).toFixed(1)}" y="${(y + rowH / 2 + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#1A1A1A">${esc((p.activity || '').slice(0, 24))}</text>` +
         `<text x="${(L + maxW + 8).toFixed(1)}" y="${(y + rowH / 2 + 3).toFixed(1)}" text-anchor="start" font-size="10" font-weight="700" fill="#1A1A1A">${pct.toFixed(0)}%</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="pyramid-svg" style="width:100%;max-width:540px;height:auto;display:block;">${g}</svg>`;
}

function populatePyramidRoadSelect() {
  const sel = document.getElementById('pyramidRoadSelect');
  if (!sel) return;
  sel.innerHTML = LIVE_ROADS.map(r =>
    `<option value="${r.road_no}">R${r.road_no} ${esc(r.location)}</option>`).join('');
}

async function loadPyramidChart(roadNo) {
  const wrap = document.getElementById('pyramidWrap');
  if (!wrap) return;
  wrap.innerHTML = 'Loading...';
  try {
    const data = await dbGet(`/api/db/roads/${roadNo}/critical-path`);
    wrap.innerHTML = data.path && data.path.length
      ? csPyramid(data.path)
      : `<p class="hint">${esc(data.note || 'No critical path available for this road.')}</p>`;
  } catch (err) {
    wrap.innerHTML = `<p class="hint">Could not load chart: ${esc(err.message)}</p>`;
  }
}

function populateCpmRoadSelect() {
  const sel = document.getElementById('cpmRoadSelect');
  sel.innerHTML = LIVE_ROADS.map(r =>
    `<option value="${r.road_no}">R${r.road_no} ${esc(r.location)}</option>`).join('');
}

async function loadCriticalPath(roadNo) {
  const wrap = document.getElementById('cpmWrap');
  wrap.innerHTML = 'Loading critical path...';
  try {
    const data = await dbGet(`/api/db/roads/${roadNo}/critical-path`);
    if (!data.path || !data.path.length) {
      wrap.innerHTML = `<p class="hint">${esc(data.note || 'No critical path available for this road.')}</p>`;
      return;
    }
    wrap.innerHTML = `
      <p class="hint">${esc(data.method || '')}</p>
      <h3 class="hint">Schedule (Gantt)</h3>
      ${csGantt(data.path)}
      <table class="data-table">
        <thead><tr><th>#</th><th>Activity</th><th>Start</th><th>End</th></tr></thead>
        <tbody>${data.path.map(p => `
          <tr><td>${p.seq_no}</td><td class="wrap">${esc(p.activity)}</td>
            <td>${p.start ? fmtDate(p.start) : '-'}</td><td>${p.end ? fmtDate(p.end) : '-'}</td></tr>`).join('')}</tbody>
      </table>
      <p><strong>Total duration:</strong> ${fmt(data.duration_days)} days</p>`;
  } catch (err) {
    wrap.innerHTML = `<p class="hint">Could not load critical path: ${esc(err.message)}</p>`;
  }
}

function renderCriticalPathTab() {
  populateCpmRoadSelect();
  const sel = document.getElementById('cpmRoadSelect');
  sel.addEventListener('change', () => loadCriticalPath(sel.value));
  if (sel.options.length) loadCriticalPath(sel.value);
}

/* =====================================================================
   PERFORMANCE TAB - GET /api/db/performance/vendor and .../line-manager.
   Both return a list sorted by -progress_pct, same row shape.
   ===================================================================== */
// Click (or Enter/Space) a .sel-row to show/hide its road-wise detail row.
function wireSelectableRows(wrap) {
  const toggle = row => {
    const det = wrap.querySelector(`[data-sel-detail="${row.dataset.sel}"]`);
    if (!det) return;
    det.hidden = !det.hidden;
    row.classList.toggle('selected', !det.hidden);
    row.setAttribute('aria-expanded', String(!det.hidden));
  };
  wrap.querySelectorAll('tr.sel-row').forEach(row => {
    row.addEventListener('click', () => toggle(row));
    row.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(row); }
    });
  });
}

/* =====================================================================
   Shared building blocks for the Performance / Backlog / Targets tables.
   A table is described once as a list of column definitions
   ({ head, cell: row => html, cls? }) and rendered by expandableTable();
   the same definitions also drive the one-row summary on the group print.
   ===================================================================== */

// Quantity per unit (units aren't additive, so one line each).
function qtyByUnit(list, key) {
  const parts = (list || []).filter(q => q[key] > 0).map(q => `${fmt(q[key])} ${esc(q.unit)}`);
  return parts.length ? parts.join('<br>') : '-';
}

// Summary table whose rows expand (click / Enter) into `detail(row)`.
// opts.actions(row, i) -> html adds a trailing no-print cell (e.g. a Print button).
function expandableTable(rows, cols, opts) {
  opts = opts || {};
  if (!rows.length) return '<p class="hint">No data.</p>';
  const span = cols.length + (opts.actions ? 1 : 0);
  return `
    <table class="data-table sel-table">
      <thead><tr>${cols.map(c => `<th>${c.head}</th>`).join('')}${opts.actions ? '<th class="no-print"></th>' : ''}</tr></thead>
      <tbody>${rows.map((r, i) => `
        <tr class="sel-row" data-sel="${i}" tabindex="0" aria-expanded="false" title="${esc(opts.title || 'Click to see road-wise details')}">
          ${cols.map(c => `<td${c.cls ? ` class="${c.cls}"` : ''}>${c.cell(r)}</td>`).join('')}
          ${opts.actions ? `<td class="no-print">${opts.actions(r, i)}</td>` : ''}
        </tr>
        <tr class="sel-detail" data-sel-detail="${i}" hidden><td colspan="${span}">${opts.detail(r)}</td></tr>`).join('')}</tbody>
    </table>`;
}

// A one-row version of the same columns (used at the top of a group print).
function summaryRowHtml(cols, r, groupHead) {
  const heads = cols.map((c, i) => (i === 0 && groupHead ? groupHead : c.head));
  return `<table class="data-table">
    <thead><tr>${heads.map(h => `<th>${h}</th>`).join('')}</tr></thead>
    <tbody><tr>${cols.map(c => `<td${c.cls ? ` class="${c.cls}"` : ''}>${c.cell(r)}</td>`).join('')}</tr></tbody>
  </table>`;
}

// One road: a header line, then its activities as a table.
function roadBlock(headerHtml, cols, acts, emptyMsg) {
  return `
    <div class="detail-box">
      <p class="hint" style="margin:0 0 6px;">${headerHtml}</p>
      ${(acts || []).length ? `<div class="table-scroll">
      <table class="data-table sub">
        <thead><tr>${cols.map(c => `<th>${c.head}</th>`).join('')}</tr></thead>
        <tbody>${acts.map(a => `<tr>${cols.map(c => `<td${c.cls ? ` class="${c.cls}"` : ''}>${c.cell(a)}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>
      </div>` : `<p class="hint" style="margin:0;">${emptyMsg}</p>`}
    </div>`;
}

const roadsHtml = (roadRows, builder) =>
  (roadRows || []).map(builder).join('') || '<p class="hint">No roads in this group.</p>';

// ---- activity column sets -------------------------------------------------
const ACT_BASE = [
  { head: 'Activity', cell: a => esc(a.activity) + (a.no_planned_qty ? ' <span class="muted">(no planned qty)</span>' : ''), cls: 'wrap' },
  { head: 'Unit', cell: a => esc(a.unit) || '-' },
  { head: 'Planned Qty', cell: a => fmt(a.planned_qty) },
  { head: 'Actual Qty', cell: a => fmt(a.actual_qty) },
];
const ACT_COLS = {
  perf: [...ACT_BASE, { head: '% Complete', cell: a => `${a.actual_pct}%` }],
  backlog: [...ACT_BASE,
    { head: 'Backlog Qty', cell: a => `<b>${fmt(a.backlog_qty)}</b>` },
    { head: 'Behind %', cell: a => `${a.backlog_pct}%` }],
  target: [...ACT_BASE,
    { head: 'Backlog Qty', cell: a => fmt(a.backlog_qty) },
    { head: 'Next Planned Qty', cell: a => fmt(a.next_planned_qty) },
    { head: 'Target Qty', cell: a => `<b>${fmt(a.target_qty)}</b>` }],
};

// ---- Performance ---------------------------------------------------------
const PERF_COLS = [
  { head: 'Group', cell: r => esc(r.group) },
  { head: 'Roads', cell: r => fmt(r.roads) },
  { head: 'Progress %', cell: r => `${r.progress_pct}%` },
  { head: 'Planned Qty', cell: r => qtyByUnit(r.quantity_by_unit, 'planned_qty') },
  { head: 'Actual Qty', cell: r => qtyByUnit(r.quantity_by_unit, 'actual_qty') },
  { head: 'Activities Behind', cell: r => fmt(r.activities_behind) },
  { head: 'Status', cell: r => r.activities_behind > 0
    ? '<span class="pill pill-bad">Behind</span>' : '<span class="pill pill-ok">On / ahead</span>' },
];
const perfRoadDetail = r => roadBlock(
  `<b>R${esc(r.road_no)} ${esc(r.location)}</b> &mdash; progress ${r.progress_pct}%, planned to date ${r.planned_pct}%`,
  ACT_COLS.perf, r.activities, 'No activities.');
const performanceTable = rows => expandableTable(rows, PERF_COLS, { detail: r => roadsHtml(r.road_rows, perfRoadDetail) });

/* =====================================================================
   PRINT ONE GROUP - a clean sheet for a single vendor / line manager (Targets
   and Backlog). The sheet is a temporary #printSheet; while body has the
   .printing-single class the print CSS hides everything else, so the normal
   full-page print is unchanged.
   ===================================================================== */
function printSheet(title, html) {
  const old = document.getElementById('printSheet');
  if (old) old.remove();
  const el = document.createElement('div');
  el.id = 'printSheet';
  el.innerHTML = `
    <div class="print-banner">
      <span class="brand-mark">TATA STEEL</span>
      <span class="brand-sub">Road Infrastructure Programme</span>
    </div>
    <h2 class="print-title">${esc(title)}</h2>
    <p class="print-meta">Printed ${fmtDate(todayISO())}</p>
    ${html}`;
  document.body.appendChild(el);
  document.body.classList.add('printing-single');
  const prevTitle = document.title;
  document.title = title.replace(/[\\/:*?"<>|]+/g, '-');   // becomes the default PDF file name
  const cleanup = () => {
    document.body.classList.remove('printing-single');
    document.title = prevTitle;
    el.remove();
    window.removeEventListener('afterprint', cleanup);
  };
  window.addEventListener('afterprint', cleanup);
  window.print();
}

const GROUP_LABEL = { vendor: 'Vendor', line_manager: 'Line Manager' };
let TARGET_VIEW = {};     // group type -> { tg, rows } as currently shown on the Targets tab
let BACKLOG_VIEW = {};    // group type -> { bl, rows } as currently shown on the Performance tab

const printButton = (attr, i, groupKey) =>
  `<button type="button" class="btn-small btn-edit" ${attr}="${i}" data-print-group="${groupKey}">Print</button>`;

// ---- Targets ---------------------------------------------------------------
const TARGET_COLS = [
  { head: 'Group', cell: r => esc(r.group) },
  { head: 'Roads', cell: r => fmt(r.roads) },
  { head: 'Actual % Now', cell: r => `${r.actual_pct}%` },
  { head: 'Backlog %', cell: r => `${r.backlog_pct}%` },
  { head: 'Next Planned %', cell: r => `${r.next_planned_pct}%` },
  { head: 'Target %', cell: r => `<b>${r.target_pct}%</b>` },
  { head: 'Target Cumulative %', cell: r => `${r.target_cumulative_pct}%` },
  { head: 'Backlog Qty', cell: r => qtyByUnit(r.quantity_by_unit, 'backlog_qty') },
  { head: 'Next Planned Qty', cell: r => qtyByUnit(r.quantity_by_unit, 'next_planned_qty') },
  { head: 'Target Qty', cell: r => `<b>${qtyByUnit(r.quantity_by_unit, 'target_qty')}</b>` },
];
const targetRoadDetail = r => roadBlock(
  `<b>R${esc(r.road_no)} ${esc(r.location)}</b> &mdash; actual ${r.actual_pct}%, backlog ${r.backlog_pct}%, next planned ${r.next_planned_pct}%, target <b>${r.target_pct}%</b> (cumulative ${r.target_cumulative_pct}%)`,
  ACT_COLS.target, r.activities, 'No backlog or planned work on this road in the selected period.');
const targetTable = (rows, groupKey) => expandableTable(rows, TARGET_COLS, {
  detail: r => roadsHtml(r.road_rows, targetRoadDetail),
  actions: (r, i) => printButton('data-print-target', i, groupKey),
});

function printTargetGroup(groupKey, idx) {
  const v = TARGET_VIEW[groupKey];
  const r = v && v.rows[idx];
  if (!r) return;
  printSheet(`Target - ${GROUP_LABEL[groupKey]} ${r.group} - ${fmtDate(v.tg.from)} to ${fmtDate(v.tg.to)}`, `
    ${summaryRowHtml(TARGET_COLS, r, GROUP_LABEL[groupKey])}
    <h3 class="print-sub">Road-wise details</h3>
    ${roadsHtml(r.road_rows, targetRoadDetail)}`);
}

// ---- Backlog -----------------------------------------------------------------
const BACKLOG_COLS = [
  { head: 'Group', cell: r => esc(r.group) },
  { head: 'Roads', cell: r => fmt(r.roads) },
  { head: 'Planned %', cell: r => `${r.planned_pct}%` },
  { head: 'Actual %', cell: r => `${r.actual_pct}%` },
  { head: 'Backlog %', cell: r => r.backlog_pct > 0
    ? `<span class="pill pill-bad">${r.backlog_pct}%</span>` : '<span class="pill pill-ok">0%</span>' },
  { head: 'Backlog Qty', cell: r => qtyByUnit(r.quantity_by_unit, 'backlog_qty') },
  { head: 'Activities Behind', cell: r => fmt(r.activities_behind) },
];

// Only roads / activities that actually carry a backlog are listed.
// behind by quantity, or by % (an activity with no planned quantity can only be behind by %)
const isBacklogActivity = a => a.backlog_qty > 0 || a.backlog_pct > 0;
const backlogRoadsOnly = roadRows =>
  (roadRows || []).filter(r => (r.activities || []).some(isBacklogActivity) || r.backlog_pct > 0);
const backlogRoadDetail = r => roadBlock(
  `<b>R${esc(r.road_no)} ${esc(r.location)}</b> &mdash; actual ${r.actual_pct}%, planned ${r.planned_pct}%, backlog <b>${r.backlog_pct}%</b>`,
  ACT_COLS.backlog, (r.activities || []).filter(isBacklogActivity), 'No backlog on this road.');
const backlogRoadsHtml = r => backlogRoadsOnly(r.road_rows).map(backlogRoadDetail).join('')
  || '<p class="hint">No backlog on any road in this group.</p>';
const backlogTable = (rows, groupKey) => expandableTable(rows, BACKLOG_COLS, {
  title: 'Click to see road-wise backlog',
  detail: backlogRoadsHtml,
  actions: (r, i) => printButton('data-print-backlog', i, groupKey),
});

function printBacklogGroup(groupKey, idx) {
  const v = BACKLOG_VIEW[groupKey];
  const r = v && v.rows[idx];
  if (!r) return;
  const bl = v.bl;
  printSheet(`Backlog - ${GROUP_LABEL[groupKey]} ${r.group} - as at ${fmtDate(bl.as_of)}`, `
    <p class="print-meta">Backlog as at ${fmtDate(bl.as_of)} (${esc(monthLabel(bl.month))}): scheduled work not yet done.</p>
    ${summaryRowHtml(BACKLOG_COLS, r, GROUP_LABEL[groupKey])}
    <h3 class="print-sub">Road-wise backlog</h3>
    ${backlogRoadsHtml(r)}`);
}

function wirePrintButtons(wrap) {
  wrap.querySelectorAll('[data-print-target]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();          // don't also toggle the row
    printTargetGroup(b.dataset.printGroup, Number(b.dataset.printTarget));
  }));
  wrap.querySelectorAll('[data-print-backlog]').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    printBacklogGroup(b.dataset.printGroup, Number(b.dataset.printBacklog));
  }));
}

function monthLabel(ym) {
  const [y, m] = ym.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleString('en-GB', { month: 'long', year: 'numeric' });
}

/* Filter bars (Performance and Targets tabs share one implementation; the
   element ids are `${prefix}FilterVendor|LM|Status|Clear|Count`). */
function fbFilters(prefix) {
  return {
    vendor: document.getElementById(prefix + 'FilterVendor').value,
    lm: document.getElementById(prefix + 'FilterLM').value,
    status: document.getElementById(prefix + 'FilterStatus').value,
  };
}

function fbQuery(prefix, extra) {
  const f = fbFilters(prefix);
  const p = new URLSearchParams(extra || {});
  if (f.vendor) p.set('vendor', f.vendor);
  if (f.lm) p.set('line_manager', f.lm);
  if (prefix === 'tgt') {
    const from = document.getElementById('tgtFilterFrom').value;
    const to = document.getElementById('tgtFilterTo').value;
    if (from) p.set('date_from', from);
    if (to) p.set('date_to', to);
  }
  return p.toString();
}

// Status is applied client-side on whichever "behind" signal the table has.
function fbStatus(prefix, rows, behindFn) {
  const s = fbFilters(prefix).status;
  return s ? rows.filter(r => behindFn(r) === (s === 'behind')) : rows;
}

function fbSetCount(prefix, shown, total) {
  const el = document.getElementById(prefix + 'FilterCount');
  const f = fbFilters(prefix);
  el.textContent = (f.vendor || f.lm || f.status) ? `Showing ${shown} of ${total}` : '';
}

// Vendor and Line Manager filters narrow each other: once one is chosen, the
// other only offers values that actually occur together with it on a road, so
// an empty / contradictory combination can't be picked. A selection that is no
// longer valid is dropped. (Rebuilt from LIVE_ROADS, so no extra request.)
function refreshFilterOptions(prefix) {
  const vSel = document.getElementById(prefix + 'FilterVendor');
  const lSel = document.getElementById(prefix + 'FilterLM');
  const roads = LIVE_ROADS || [];
  const uniq = arr => [...new Set(arr.filter(Boolean))].sort();
  const fill = (sel, values) => {
    const keep = values.includes(sel.value) ? sel.value : '';
    sel.innerHTML = '<option value="">All</option>' +
      values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    sel.value = keep;
  };
  // two passes so a dropped selection widens the other list on the next pass
  for (let pass = 0; pass < 3; pass++) {
    const v = vSel.value, l = lSel.value;
    fill(vSel, uniq(roads.filter(r => !l || r.line_manager === l).map(r => r.vendor)));
    fill(lSel, uniq(roads.filter(r => !v || r.vendor === v).map(r => r.line_manager)));
    autoSelectSingle(vSel);      // only one choice -> pick it (may in turn narrow the other list)
    autoSelectSingle(lSel);
  }
}

function initFilterBar(prefix, onChange, onClear) {
  const bar = document.getElementById(prefix + 'FilterBar');
  refreshFilterOptions(prefix);
  if (bar.dataset.wired) return;
  bar.dataset.wired = '1';
  ['Vendor', 'LM'].forEach(k => document.getElementById(prefix + 'Filter' + k).addEventListener('change', () => {
    refreshFilterOptions(prefix);
    onChange();
  }));
  ['Status', 'From', 'To'].forEach(k => {
    const el = document.getElementById(prefix + 'Filter' + k);
    if (el) el.addEventListener('change', onChange);
  });
  document.getElementById(prefix + 'FilterClear').addEventListener('click', () => {
    ['Vendor', 'LM', 'Status'].forEach(k => { document.getElementById(prefix + 'Filter' + k).value = ''; });
    refreshFilterOptions(prefix);          // back to the full lists
    if (onClear) onClear();                // tab-specific extras (month / dates)
    onChange();
  });
}

let BACKLOG_SEQ = 0, PERF_SEQ = 0, TARGET_SEQ = 0;   // newest request wins; late replies are dropped

function resetBacklogMonth() {
  const sel = document.getElementById('backlogMonthSelect');
  const now = new Date();
  const cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
}

async function loadBacklogTables() {
  const seq = ++BACKLOG_SEQ;
  const sel = document.getElementById('backlogMonthSelect');
  for (const [group, wrapId, title] of [['vendor', 'vendorPlanWrap', 'Vendor'], ['line_manager', 'lmPlanWrap', 'Line Manager']]) {
    const wrap = document.getElementById(wrapId);
    wrap.innerHTML = 'Loading...';
    try {
      const bl = await dbGet('/api/db/performance/backlog?' + fbQuery('perf', { group, month: sel.value, detail: 'true' }));
      if (seq !== BACKLOG_SEQ) return;
      const rowsShown = fbStatus('perf', bl.rows, r => r.backlog_pct > 0);
      BACKLOG_VIEW[group] = { bl, rows: rowsShown };
      wrap.innerHTML = `
        <h3 class="hint" style="margin-top:16px;">${title} backlog - as at ${fmtDate(bl.as_of)} (${esc(monthLabel(bl.month))})</h3>
        ${backlogTable(rowsShown, group)}`;
      wireSelectableRows(wrap);
      wirePrintButtons(wrap);
    } catch (err) {
      if (seq === BACKLOG_SEQ) wrap.innerHTML = `<p class="hint">Could not load ${title.toLowerCase()} backlog: ${esc(err.message)}</p>`;
    }
  }
}

async function initBacklogMonth() {
  const sel = document.getElementById('backlogMonthSelect');
  if (sel.options.length) return;
  try {
    const months = await dbGet('/api/db/performance/months');
    const now = new Date();
    const cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    sel.innerHTML = months.map(m => `<option value="${m}">${esc(monthLabel(m))}</option>`).join('');
    sel.value = months.includes(cur) ? cur : months[months.length - 1];
    sel.addEventListener('change', loadBacklogTables);
  } catch (err) { /* tables below will surface the error */ }
}

async function loadPerformanceData() {
  const seq = ++PERF_SEQ;
  const vendorWrap = document.getElementById('vendorPerfWrap');
  const lmWrap = document.getElementById('lmPerfWrap');
  vendorWrap.innerHTML = 'Loading...';
  lmWrap.innerHTML = 'Loading...';
  const behind = r => r.activities_behind > 0;
  let shown = 0, total = 0;
  try {
    const vendors = await dbGet('/api/db/performance/vendor?' + fbQuery('perf'));
    if (seq !== PERF_SEQ) return;
    const rows = fbStatus('perf', vendors, behind);
    shown += rows.length; total += vendors.length;
    vendorWrap.innerHTML = performanceTable(rows);
    wireSelectableRows(vendorWrap);
  } catch (err) {
    vendorWrap.innerHTML = `<p class="hint">Could not load vendor performance: ${esc(err.message)}</p>`;
  }
  try {
    const lms = await dbGet('/api/db/performance/line-manager?' + fbQuery('perf'));
    if (seq !== PERF_SEQ) return;
    const rows = fbStatus('perf', lms, behind);
    shown += rows.length; total += lms.length;
    lmWrap.innerHTML = performanceTable(rows);
    wireSelectableRows(lmWrap);
  } catch (err) {
    lmWrap.innerHTML = `<p class="hint">Could not load line manager performance: ${esc(err.message)}</p>`;
  }
  if (seq !== PERF_SEQ) return;
  fbSetCount('perf', shown, total);
  loadBacklogTables();
}

async function renderPerformanceTab() {
  initFilterBar('perf', loadPerformanceData, resetBacklogMonth);
  await initBacklogMonth();
  loadPerformanceData();
}

/* =====================================================================
   TARGETS TAB - GET /api/db/performance/target?group=&vendor=&line_manager=.
   Next-month target per vendor / line manager = backlog today + planned
   work in the next 30 days.
   ===================================================================== */
// A target looks forward: neither date may be in the past.
function setTargetDateMins() {
  const t = todayISO();
  document.getElementById('tgtFilterFrom').min = t;
  document.getElementById('tgtFilterTo').min = t;
}

// Quick ranges: "N days from tomorrow" = From tomorrow, To tomorrow + N days.
function targetRangeDates(days) {
  const from = new Date(); from.setDate(from.getDate() + 1);
  const to = new Date(from); to.setDate(to.getDate() + days);
  return { from: toISO(from), to: toISO(to) };
}

// Highlight the quick-range button whose dates are currently selected (if any).
function syncRangeButtons() {
  const f = document.getElementById('tgtFilterFrom').value, t = document.getElementById('tgtFilterTo').value;
  document.querySelectorAll('[data-range-days]').forEach(b => {
    const r = targetRangeDates(Number(b.dataset.rangeDays));
    b.classList.toggle('active', r.from === f && r.to === t);
  });
}

function wireRangeButtons() {
  const bar = document.getElementById('tgtFilterBar');
  if (bar.dataset.rangeWired) return;
  bar.dataset.rangeWired = '1';
  document.querySelectorAll('[data-range-days]').forEach(b => b.addEventListener('click', () => {
    const r = targetRangeDates(Number(b.dataset.rangeDays));
    document.getElementById('tgtFilterFrom').value = r.from;
    document.getElementById('tgtFilterTo').value = r.to;
    loadTargetData();
  }));
  ['tgtFilterFrom', 'tgtFilterTo'].forEach(id =>
    document.getElementById(id).addEventListener('change', syncRangeButtons));
}

function setDefaultTargetDates() {
  const from = new Date();
  const to = new Date(from.getTime() + 30 * 86400000);
  document.getElementById('tgtFilterFrom').value = toISO(from);
  document.getElementById('tgtFilterTo').value = toISO(to);
  setTargetDateMins();
  syncRangeButtons();
}

async function loadTargetData() {
  const seq = ++TARGET_SEQ;
  syncRangeButtons();
  const fromEl = document.getElementById('tgtFilterFrom');
  const toEl = document.getElementById('tgtFilterTo');
  if ((fromEl.value && fromEl.value < todayISO()) || (toEl.value && toEl.value < todayISO())) {
    for (const id of ['vendorTargetWrap', 'lmTargetWrap'])
      document.getElementById(id).innerHTML = '<p class="hint">Targets are for today or future dates only. Pick a From and To that are not in the past.</p>';
    return;
  }
  if (fromEl.value && toEl.value && toEl.value <= fromEl.value) {
    for (const id of ['vendorTargetWrap', 'lmTargetWrap'])
      document.getElementById(id).innerHTML = '<p class="hint">The "To" date must be after the "From" date.</p>';
    return;
  }
  let shown = 0, total = 0;
  for (const [group, wrapId, title] of [['vendor', 'vendorTargetWrap', 'Vendor'], ['line_manager', 'lmTargetWrap', 'Line Manager']]) {
    const wrap = document.getElementById(wrapId);
    wrap.innerHTML = 'Loading...';
    try {
      const tg = await dbGet('/api/db/performance/target?' + fbQuery('tgt', { group }));
      if (seq !== TARGET_SEQ) return;
      const rows = fbStatus('tgt', tg.rows, r => r.backlog_pct > 0);
      shown += rows.length; total += tg.rows.length;
      wrap.innerHTML = `
        <p class="hint"><b>Window: ${fmtDate(tg.from)} to ${fmtDate(tg.to)}</b></p>
        ${targetTable(rows, group)}`;
      TARGET_VIEW[group] = { tg, rows };
      wireSelectableRows(wrap);
      wirePrintButtons(wrap);
    } catch (err) {
      wrap.innerHTML = `<p class="hint">Could not load ${title.toLowerCase()} targets: ${esc(err.message)}</p>`;
    }
  }
  if (seq === TARGET_SEQ) fbSetCount('tgt', shown, total);
}

function renderTargetsTab() {
  if (!document.getElementById('tgtFilterFrom').value) setDefaultTargetDates();
  setTargetDateMins();
  wireRangeButtons();
  initFilterBar('tgt', loadTargetData, setDefaultTargetDates);
  return loadTargetData();
}

/* =====================================================================
   BOTTLENECKS TAB - GET /api/db/bottlenecks?threshold_pct=&stale_weeks=.
   ===================================================================== */
async function renderBottlenecksTab() {
  const wrap = document.getElementById('bottlenecksWrap');
  wrap.innerHTML = 'Loading...';
  try {
    const [rows, manual] = await Promise.all([
      dbGet('/api/db/bottlenecks'),
      dbGet('/api/db/manual-bottlenecks'),
    ]);
    const auto = !rows.length
      ? '<p>No bottlenecks detected.</p>'
      : `
      <table class="data-table">
        <thead><tr><th>Road</th><th>Location</th><th>Line Manager</th><th>Activity</th><th>Actual %</th><th>Expected %</th><th>Reasons</th></tr></thead>
        <tbody>${rows.map(r => `
          <tr class="issue-row">
            <td>${esc(r.road_no)}</td><td class="wrap">${esc(r.location) || '-'}</td><td class="wrap">${esc(r.line_manager) || '-'}</td>
            <td class="wrap">${esc(r.activity)}</td>
            <td>${r.actual_pct}%</td><td>${r.expected_pct}%</td>
            <td class="wrap">${esc((r.reasons || []).join('; '))}</td>
          </tr>`).join('')}</tbody>
      </table>`;
    wrap.innerHTML = `
      <h3 class="hint">Auto-detected</h3>
      ${auto}
      <h3 class="hint" style="margin-top:20px;">Manually logged issues</h3>
      <div class="form-row no-print" style="margin-bottom:12px;">
        <button type="button" id="manualIssueToggle" class="btn-small">+ Log Issue</button>
      </div>
      <div id="manualIssueFormWrap" class="panel no-print" style="display:none; margin-bottom:16px;"></div>
      <div id="manualIssueTableWrap">${manualIssueTableHtml(manual)}</div>`;
    wireManualIssueForm();
    wireManualIssueTable();
  } catch (err) {
    wrap.innerHTML = `<p class="hint">Could not load bottlenecks: ${esc(err.message)}</p>`;
  }
}

function manualIssueTableHtml(rows) {
  if (!rows.length) return '<p>No manually logged issues.</p>';
  const statuses = ['Open', 'In progress', 'Closed'];
  return `
    <table class="data-table">
      <thead><tr><th>Road</th><th>Issue</th><th>Source</th><th>Status</th><th>Remarks</th><th class="no-print">Actions</th></tr></thead>
      <tbody>${rows.map(r => `
        <tr class="issue-row" data-issue-id="${r.issue_id}">
          <td>${r.road_no != null ? esc(r.road_no) : '-'}</td>
          <td class="wrap">${esc(r.issue)}</td>
          <td class="wrap">${esc(r.source) || '-'}</td>
          <td class="no-print">
            <select class="inline-input manual-issue-status" data-issue-id="${r.issue_id}">
              ${statuses.map(s => `<option value="${s}" ${s === r.status ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
          </td>
          <td class="print-only">${esc(r.status)}</td>
          <td class="wrap">
            <input type="text" class="inline-input manual-issue-remarks no-print" data-issue-id="${r.issue_id}" value="${esc(r.remarks) || ''}">
            <span class="print-only">${esc(r.remarks) || '-'}</span>
          </td>
          <td class="no-print">
            <button type="button" class="btn-small manual-issue-save" data-issue-id="${r.issue_id}">Save</button>
            <button type="button" class="btn-danger manual-issue-delete" data-issue-id="${r.issue_id}">Delete</button>
          </td>
        </tr>`).join('')}</tbody>
    </table>`;
}

function wireManualIssueTable() {
  const wrap = document.getElementById('manualIssueTableWrap');
  if (!wrap) return;
  wrap.querySelectorAll('.manual-issue-save').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.issueId;
      const status = wrap.querySelector(`.manual-issue-status[data-issue-id="${id}"]`).value;
      const remarks = wrap.querySelector(`.manual-issue-remarks[data-issue-id="${id}"]`).value;
      btn.disabled = true;
      try {
        await dbSend('PUT', `/api/db/manual-bottlenecks/${id}`, { status, remarks });
        renderBottlenecksTab();
      } catch (err) {
        uiToast('Could not save: ' + err.message, 'error');
        btn.disabled = false;
      }
    });
  });
  wrap.querySelectorAll('.manual-issue-delete').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!(await uiConfirm('Delete this issue record?', { okLabel: 'Delete', danger: true }))) return;
      try {
        await dbSend('DELETE', `/api/db/manual-bottlenecks/${btn.dataset.issueId}`);
        renderBottlenecksTab();
      } catch (err) {
        uiToast('Could not delete: ' + err.message, 'error');
      }
    });
  });
}

function wireManualIssueForm() {
  const toggle = document.getElementById('manualIssueToggle');
  if (!toggle || toggle.dataset.bound) return;
  toggle.dataset.bound = '1';
  toggle.addEventListener('click', () => {
    const formWrap = document.getElementById('manualIssueFormWrap');
    const show = formWrap.style.display === 'none';
    if (!show) { formWrap.style.display = 'none'; return; }
    const roadOptions = (LIVE_ROADS || [])
      .map(r => `<option value="${r.road_no}">Road ${r.road_no} - ${esc(r.location)}</option>`).join('');
    formWrap.innerHTML = `
      <div class="edit-form">
        <div class="form-row">
          <label for="manualIssueRoad">Road</label>
          <select id="manualIssueRoad" class="inline-input"><option value="">(none)</option>${roadOptions}</select>
        </div>
        <div class="form-row">
          <label for="manualIssueText">Issue</label>
          <input id="manualIssueText" type="text" class="inline-input" style="min-width:280px;">
        </div>
        <div class="form-row">
          <label for="manualIssueSource">Source</label>
          <input id="manualIssueSource" type="text" class="inline-input" value="Weekly review">
        </div>
      </div>
      <div style="margin-top:10px; display:flex; gap:8px;">
        <button type="button" id="manualIssueSubmit" class="btn-small">Save</button>
      </div>
      <div id="manualIssueFormHint" class="hint"></div>`;
    formWrap.style.display = '';
    document.getElementById('manualIssueSubmit').addEventListener('click', async () => {
      const hint = document.getElementById('manualIssueFormHint');
      const issue = document.getElementById('manualIssueText').value.trim();
      if (!issue) { hint.textContent = 'Issue text is required.'; return; }
      const roadVal = document.getElementById('manualIssueRoad').value;
      try {
        await dbSend('POST', '/api/db/manual-bottlenecks', {
          road_no: roadVal ? Number(roadVal) : null,
          issue,
          source: document.getElementById('manualIssueSource').value.trim() || 'Weekly review',
        });
        renderBottlenecksTab();
      } catch (err) {
        hint.textContent = 'Could not save: ' + err.message;
      }
    });
  });
}

/* =====================================================================
   MILESTONES TAB - GET /api/db/milestones?window_days=.
   Three sections: completed / planned_next_30_days / projected_next_30_days.
   ===================================================================== */
function milestoneSection(title, rows, cols) {
  if (!rows.length) return `<h3 class="hint">${esc(title)}</h3><p>None.</p>`;
  return `
    <h3 class="hint">${esc(title)}</h3>
    <table class="data-table">
      <thead><tr>${cols.map(([, label]) => `<th>${esc(label)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => `
        <tr>${cols.map(([key, , isDate]) => `<td class="wrap">${isDate ? fmtDate(r[key]) : esc(r[key])}</td>`).join('')}</tr>`).join('')}</tbody>
    </table>`;
}

async function renderMilestonesTab() {
  const wrap = document.getElementById('milestonesWrap');
  wrap.innerHTML = 'Loading...';
  try {
    const data = await dbGet('/api/db/milestones');
    wrap.innerHTML =
      milestoneSection('Completed milestones', data.completed || [], [
        ['road_no', 'Road'], ['location', 'Location'], ['activity', 'Activity'],
        ['mark_pct', 'Mark'], ['reached_week', 'Reached (week)', true],
      ]) +
      milestoneSection('Planned to finish in next 30 days', data.planned_next_30_days || [], [
        ['road_no', 'Road'], ['location', 'Location'], ['activity', 'Activity'], ['planned_end', 'Planned End', true],
      ]) +
      milestoneSection('Projected to finish in next 30 days', data.projected_next_30_days || [], [
        ['road_no', 'Road'], ['location', 'Location'], ['activity', 'Activity'], ['projected_completion', 'Projected Completion', true],
      ]);
  } catch (err) {
    wrap.innerHTML = `<p class="hint">Could not load milestones: ${esc(err.message)}</p>`;
  }
}

async function init() {
  renderSequence();
  // These five panels are independent of each other, so load them concurrently
  // instead of one after another — each used to wait for the previous to
  // finish, stacking five server round-trips in series on every page load.
  await Promise.all([
    refreshLiveRoads(),
    renderIssues(),
    renderWeightAudit(),
    renderNavBadges(),
    renderEditLog(),
  ]);
}

// Client-side only: re-fetch and re-render every DB-backed panel.
document.getElementById('refreshBtn').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Reloading...';
  try {
    await init();
    const active = document.querySelector('.nav-item.active');
    if (active) activateTab(active.dataset.tab);
    btn.textContent = 'Reloaded';
  } catch (err) {
    btn.textContent = 'Reload failed';
    setLiveStatus('Reload failed: ' + err.message, true, { global: true });
  } finally {
    setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1200);
  }
});

/* =====================================================================
   Bulk weekly-progress CSV import (parity with the legacy app's
   post_import_progress). Expected header row: activity_id,
   week_start_date, actual_qty[, note][, planned_qty_override].
   ===================================================================== */
function toggleImportCsvForm() {
  const wrap = document.getElementById('importCsvFormWrap');
  const toggleBtn = document.getElementById('importCsvToggle');
  if (!wrap) return;
  const show = wrap.style.display === 'none';
  wrap.style.display = show ? '' : 'none';
  if (toggleBtn) toggleBtn.textContent = show ? 'Cancel Import' : 'Import Weekly CSV';
  if (!show) return;
  wrap.innerHTML = `
    <div class="edit-form">
      <div class="form-row" style="flex-direction:column; align-items:flex-start;">
        <label for="importCsvText">CSV text (header: activity_id, week_start_date, actual_qty, note)</label>
        <textarea id="importCsvText" class="inline-input" style="min-width:420px; min-height:120px;"
          placeholder="activity_id,week_start_date,actual_qty,note&#10;101,2026-10-06,25,on schedule"></textarea>
      </div>
    </div>
    <div style="margin-top:10px; display:flex; gap:8px;">
      <button type="button" id="importCsvSubmit" class="btn-small">Import</button>
    </div>
    <div id="importCsvHint" class="hint"></div>`;
  document.getElementById('importCsvSubmit').addEventListener('click', async () => {
    const hint = document.getElementById('importCsvHint');
    const csv = document.getElementById('importCsvText').value;
    if (!csv.trim()) { hint.textContent = 'Paste CSV text first.'; return; }
    hint.textContent = 'Importing...';
    try {
      const result = await dbSend('POST', '/api/db/weekly/import-csv', { csv });
      hint.textContent = `Imported ${result.imported} row(s).`;
      await refreshLiveRoads();
    } catch (err) {
      hint.textContent = 'Could not import: ' + err.message;
    }
  });
}

document.getElementById('importCsvToggle').addEventListener('click', toggleImportCsvForm);

/* =====================================================================
   Export Summary CSV — column picker dialog. Only the summary export is
   wired to a dialog (history export was dropped from the UI per request);
   the user ticks which columns they want and the download is built with
   just those, via /api/db/export/summary?columns=...

   The two scopes show different shapes of data (one row per road, overall,
   vs one row per activity, for a single road), so each gets its OWN column
   list - the "Single road" picker is not just the overall columns filtered
   to one road, it is activity-level detail (mirrors
   calc.road_activity_rows / _ROAD_COLUMNS in db_routes.py).
   ===================================================================== */
const EXPORT_COLUMNS_BY_SCOPE = {
  all: [
    { key: 'road', label: 'Road' },
    { key: 'location', label: 'Location' },
    { key: 'vendor', label: 'Vendor' },
    { key: 'line_manager', label: 'Line Manager' },
    { key: 'activities', label: 'Activities' },
    { key: 'actual_pct', label: 'Actual %' },
    { key: 'planned_pct', label: 'Planned %' },
    { key: 'status', label: 'Status' },
  ],
  road: [
    { key: 'activity', label: 'Activity' },
    { key: 'major', label: 'Major' },
    { key: 'unit', label: 'Unit' },
    { key: 'planned_qty', label: 'Planned Qty' },
    { key: 'actual_qty', label: 'Actual Qty' },
    { key: 'actual_pct', label: 'Actual %' },
    { key: 'planned_pct', label: 'Planned %' },
    { key: 'start_date', label: 'Start Date' },
    { key: 'end_date', label: 'End Date' },
    { key: 'status', label: 'Status' },
  ],
};

// Belt-and-suspenders for the ".chip:has(input:checked)" CSS highlight -
// :has() is modern-browser-only, so mirror it with a plain class toggle too.
function syncExportChipClass(el) {
  el.closest('.export-col-chip, .export-scope-card')?.classList.toggle('is-checked', el.checked);
}

function renderExportColumns(scope) {
  const list = document.getElementById('exportColumnsList');
  list.innerHTML = EXPORT_COLUMNS_BY_SCOPE[scope].map(c => `
    <label class="export-col-chip">
      <input type="checkbox" class="export-col-chk" value="${c.key}" checked> ${c.label}
    </label>`).join('');
  list.querySelectorAll('.export-col-chk').forEach(cb => {
    syncExportChipClass(cb);
    cb.addEventListener('change', () => syncExportChipClass(cb));
  });
}

function openExportSummaryDialog() {
  const dlg = document.getElementById('exportColumnsDialog');
  if (!dlg) return;

  // Scope: overall (all roads) vs a single picked road. Default back to
  // "overall" and the road dropdown hidden every time the dialog opens, so
  // a previous single-road export doesn't quietly carry over.
  document.getElementById('exportScopeAll').checked = true;
  document.querySelectorAll('input[name="exportScope"]').forEach(syncExportChipClass);
  const roadSelect = document.getElementById('exportRoadSelect');
  roadSelect.innerHTML = (LIVE_ROADS || []).map(r =>
    `<option value="${r.road_no}">R${r.road_no} ${esc(r.location)}</option>`).join('');
  roadSelect.style.display = 'none';
  renderExportColumns('all');

  if (typeof dlg.showModal === 'function') dlg.showModal();
  else dlg.setAttribute('open', '');
}

document.getElementById('exportSummaryToggle').addEventListener('click', openExportSummaryDialog);

document.querySelectorAll('input[name="exportScope"]').forEach(r => r.addEventListener('change', () => {
  const isRoad = document.getElementById('exportScopeRoad').checked;
  document.getElementById('exportRoadSelect').style.display = isRoad ? '' : 'none';
  document.querySelectorAll('input[name="exportScope"]').forEach(syncExportChipClass);
  renderExportColumns(isRoad ? 'road' : 'all');
}));

document.getElementById('exportColumnsCancel').addEventListener('click', () => {
  document.getElementById('exportColumnsDialog').close();
});

document.getElementById('exportColumnsConfirm').addEventListener('click', () => {
  const dlg = document.getElementById('exportColumnsDialog');
  const chosen = [...document.querySelectorAll('.export-col-chk:checked')].map(c => c.value);
  if (!chosen.length) { uiToast('Pick at least one column.', 'error'); return; }
  let url = '/api/db/export/summary?columns=' + encodeURIComponent(chosen.join(','));
  if (document.getElementById('exportScopeRoad').checked) {
    const roadNo = document.getElementById('exportRoadSelect').value;
    if (!roadNo) { uiToast('Pick a road first.', 'error'); return; }
    url += '&road_no=' + encodeURIComponent(roadNo);
  }
  dlg.close();
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
});


document.getElementById('addRoadToggle').addEventListener('click', () => toggleAddRoadForm());

initSidenav();

/* =====================================================================
   LIVE REFRESH - wires live-refresh.js to this page. Server: markers in
   live_change_marker (db/05_live_change.sql) -> GET /api/db/live/changes.
   LIVE_TABLE_TABS: which tabs show data from which table. The Roads panels
   that are read-only sub-pages (View Past Data, Bottlenecks, Pyramid) are
   re-opened after a reload; form panels count as busy so a half-typed
   entry is never overwritten (a "new updates" banner is shown instead).
   ===================================================================== */
const LIVE_TABLE_TABS = {
  road: ['overview', 'charts', 'roads', 'critical-path', 'performance', 'targets', 'bottlenecks', 'milestones', 'audit'],
  activity: ['overview', 'charts', 'roads', 'critical-path', 'performance', 'targets', 'bottlenecks', 'milestones', 'audit'],
  weekly_entry: ['overview', 'charts', 'roads', 'critical-path', 'performance', 'targets', 'bottlenecks', 'milestones'],
  manual_issue: ['bottlenecks'],
  road_issue: ['roads'],
  data_issue: ['issues'],
  edit_log: ['editlog'],
};

// "Busy" = the user has something half-done that a silent reload would wipe:
// a modal open, or typed-into (changed from default) fields in an open form or
// panel, or the cursor in a text box. Merely having a panel open to look at it
// is NOT busy.
function liveEditBusy() {
  if (document.querySelector('dialog[open]')) return true;
  const changed = el => {
    if (el.type === 'button' || el.type === 'submit' || el.type === 'hidden') return false;
    if (el.tagName === 'SELECT') return [...el.options].some(o => o.selected !== o.defaultSelected);
    if (el.type === 'checkbox' || el.type === 'radio') return el.checked !== el.defaultChecked;
    return el.value !== el.defaultValue;
  };
  const roots = document.querySelectorAll(
    '.edit-form:not([hidden]), [id^="road-weekly-"]:not([hidden]), [id^="road-editacts-"]:not([hidden]), ' +
    '[id^="road-issue-"]:not([hidden]), #manualIssueFormWrap, #addRoadFormWrap, #importCsvFormWrap');
  for (const r of roots) {
    if (r.offsetParent === null) continue;               // not visible
    if ([...r.querySelectorAll('input, textarea, select')].some(changed)) return true;
  }
  const el = document.activeElement;
  return !!(el && /^(INPUT|TEXTAREA)$/.test(el.tagName) && !el.closest('.filter-bar') && el.closest('#app'));
}

const LIVE_REOPEN = {
  'road-weeklydata-': 'data-road-weeklydata',
  'road-bottlenecks-': 'data-road-bottlenecks',
  'road-pyramid-': 'data-road-pyramid',
};
async function liveReloadRoads() {
  const open = [];
  Object.entries(LIVE_REOPEN).forEach(([prefix, attr]) => {
    document.querySelectorAll(`[id^="${prefix}"]:not([hidden]):not(:empty)`).forEach(el =>
      open.push([attr, el.id.slice(prefix.length)]));
  });
  await refreshLiveRoads();
  for (const [attr, rn] of open) {
    const btn = document.querySelector(`[${attr}="${rn}"]`);
    if (btn) btn.click();
  }
}

window.LIVE_FEED = LiveRefresh.start({
  endpoint: '/api/db/live/changes',
  tableTabs: LIVE_TABLE_TABS,
  activeTab: () => (document.querySelector('.nav-item.active') || {}).dataset?.tab,
  tabs: {
    overview: { key: 'roads-data', reload: liveReloadRoads, busy: liveEditBusy },
    roads: { key: 'roads-data', reload: liveReloadRoads, busy: liveEditBusy },
    charts: { reload: renderChartsTab },
    'critical-path': { reload: () => loadCriticalPath(document.getElementById('cpmRoadSelect').value) },
    performance: { reload: loadPerformanceData },
    targets: { reload: loadTargetData },
    bottlenecks: { reload: renderBottlenecksTab, busy: liveEditBusy },
    milestones: { reload: renderMilestonesTab },
    issues: { reload: renderIssues },
    audit: { reload: renderWeightAudit },
    editlog: { reload: renderEditLog },
  },
  onChange: () => { renderNavBadges(); },
  // Feed is live -> the manual Reload button is redundant; hide it. If the
  // triggers aren't installed (enabled:false) the button stays as a fallback.
  onEnabled: on => { const b = document.getElementById('refreshBtn'); if (b) b.hidden = on; },
});

init()
  .then(() => restoreActiveTab())
  .catch(err => { document.getElementById('app').innerHTML = `<p style="color:red">${err.message}</p>`; });
