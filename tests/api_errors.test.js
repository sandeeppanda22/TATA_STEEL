// Run: node --test tests/   - friendly API error text (pure function lifted out of dashboard.js)
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const src = fs.readFileSync(require('path').join(__dirname, '../dashboard/static/dashboard.js'), 'utf8');
const body = src.slice(src.indexOf('const API_FIELD_LABELS'), src.indexOf('function apiError('));
const apiErrorMessages = new Function(body + '; return apiErrorMessages;')();

test('the technical pydantic output becomes plain sentences', () => {
  const detail = [
    { type: 'float_type', loc: ['body', 'length_m'], msg: 'Input should be a valid number', input: null },
    { type: 'float_type', loc: ['body', 'drain_planned_m'], msg: 'Input should be a valid number', input: null },
  ];
  assert.deepEqual(apiErrorMessages(detail), ['Length is required', 'Drain Planned is required']);
});

test('activity rows are numbered, missing fields and bad numbers are explained', () => {
  const detail = [
    { type: 'missing', loc: ['body', 'activities', 1, 'planned_qty'], msg: 'Field required', input: {} },
    { type: 'float_parsing', loc: ['body', 'activities', 0, 'weight_pct'], msg: 'x', input: 'abc' },
    { type: 'date_from_datetime_parsing', loc: ['body', 'start_date'], msg: 'x', input: 'zz' },
  ];
  assert.deepEqual(apiErrorMessages(detail),
    ['Activity 2: Planned Qty is required', 'Activity 1: Weight % must be a number', 'Start Date must be a valid date']);
});

test('plain strings pass through; empty detail gets a generic sentence', () => {
  assert.deepEqual(apiErrorMessages('Road 5 already exists.'), ['Road 5 already exists.']);
  assert.match(apiErrorMessages(null)[0], /try again/i);
});
