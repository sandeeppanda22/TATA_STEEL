// Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const { diffChanged, tabsForTables, pollDelay } = require('../dashboard/static/live-refresh.js');

const rows = (o) => Object.entries(o).map(([table_name, change_seq]) => ({ table_name, change_seq }));

test('first reply is a baseline and reloads nothing', () => {
  const d = diffChanged(null, rows({ road: 1, activity: 2 }));
  assert.equal(d.baseline, true);
  assert.deepEqual(d.changed, []);
});

test('only tables whose change_seq differs are reported (per table, no global cursor)', () => {
  const d = diffChanged({ road: 1, activity: 2, weekly_entry: 5 }, rows({ road: 1, activity: 3, weekly_entry: 5 }));
  assert.deepEqual(d.changed, ['activity']);
  // a lower number than before is still a change (slower txn committing later)
  assert.deepEqual(diffChanged({ road: 9 }, rows({ road: 4 })).changed, ['road']);
});

test('tabsForTables unions and de-duplicates tabs', () => {
  const map = { road: ['overview', 'roads'], weekly_entry: ['roads', 'charts'] };
  assert.deepEqual(tabsForTables(map, ['road', 'weekly_entry']).sort(), ['charts', 'overview', 'roads']);
  assert.deepEqual(tabsForTables(map, ['nope']), []);
});

test('poll delay policy', () => {
  const now = 1_000_000;
  assert.equal(pollDelay({ errors: 0, now, lastChangeAt: now - 1000 }), 5000);
  assert.equal(pollDelay({ errors: 0, now, lastChangeAt: now - 61000 }), 15000);
  assert.deepEqual([1, 2, 3, 4, 9].map(e => pollDelay({ errors: e, now, lastChangeAt: now })), [4000, 8000, 16000, 30000, 30000]);
  assert.equal(pollDelay({ errors: 0, retryAfterMs: 45000, now, lastChangeAt: now }), 45000);
});
