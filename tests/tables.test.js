// Run: node --test tests/  - the shared expandable-table builder and its three column sets
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const src = fs.readFileSync(require('path').join(__dirname, '../dashboard/static/dashboard.js'), 'utf8');
const body = src.slice(src.indexOf('// Quantity per unit (units aren'), src.indexOf('function wirePrintButtons'));
const stubs = "const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;');" +
  "const fmt=n=>(n===null||n===undefined||n==='')?'-':Number(n).toLocaleString();" +
  "const fmtDate=d=>d;const todayISO=()=>'x';const monthLabel=m=>m;";
const lib = new Function(stubs + body + ';return {performanceTable,targetTable,backlogTable,summaryRowHtml,BACKLOG_COLS};')();

const road = { road_no: 1, location: 'Gate', progress_pct: 40, planned_pct: 50, actual_pct: 10, backlog_pct: 20,
  next_planned_pct: 5, target_pct: 25, target_cumulative_pct: 35,
  activities: [{ activity: 'WMM', unit: 'm', planned_qty: 100, actual_qty: 10, backlog_qty: 20, next_planned_qty: 5, target_qty: 25, actual_pct: 10 }] };
const row = { group: 'V1', roads: 1, progress_pct: 40, activities_behind: 1, actual_pct: 10, planned_pct: 50, backlog_pct: 20,
  next_planned_pct: 5, target_pct: 25, target_cumulative_pct: 35,
  quantity_by_unit: [{ unit: 'm', planned_qty: 100, actual_qty: 10, backlog_qty: 20, next_planned_qty: 5, target_qty: 25 }], road_rows: [road] };

const cells = html => (html.split('<tr class="sel-row"')[1].split('</tr>')[0].match(/<td/g) || []).length;

test('each table has one expandable detail row per group, spanning every column', () => {
  for (const [html, dataCells] of [[lib.performanceTable([row]), 7], [lib.targetTable([row], 'vendor'), 11], [lib.backlogTable([row], 'vendor')  , 8]]) {
    assert.equal(cells(html), dataCells);
    assert.match(html, new RegExp(`colspan="${dataCells}"`));
    assert.match(html, /data-sel-detail="0" hidden/);
    assert.match(html, /WMM/);
  }
});

test('only Targets and Backlog carry a Print button; empty input gives a hint', () => {
  assert.doesNotMatch(lib.performanceTable([row]), /data-print/);
  assert.match(lib.targetTable([row], 'vendor'), /data-print-target="0" data-print-group="vendor"/);
  assert.match(lib.backlogTable([row], 'line_manager'), /data-print-backlog="0" data-print-group="line_manager"/);
  assert.match(lib.performanceTable([]), /No data/);
});

test('the print summary reuses the same columns, with the first heading renamed', () => {
  const html = lib.summaryRowHtml(lib.BACKLOG_COLS, row, 'Vendor');
  assert.match(html, /<th>Vendor<\/th><th>Roads<\/th>/);
  assert.match(html, /Backlog Qty/);
});
