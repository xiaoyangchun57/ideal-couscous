import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('./ReportsPage.jsx', import.meta.url), 'utf8');

test('dispatched admin actions distinguish continue handling from dismissal', () => {
  assert.match(source, /record\.status === 'dispatched'.*确认需处置/s);
  assert.match(source, /record\.status === 'dispatched'.*核实消除/s);
  assert.match(source, /roles\.includes\('admin'\)/);
  assert.doesNotMatch(source, />核实<\/Button>/);
});

test('verified shows only dismissal while resolved keeps only archive', () => {
  assert.match(source, /record\.status === 'dispatched'.*确认需处置/s);
  assert.match(source, /\['dispatched', 'verified'\]\.includes\(record\.status\).*核实消除/s);
  assert.match(source, /record\.status === 'resolved'.*归档/s);
  assert.doesNotMatch(source, /record\.status === 'verified'.*确认需处置/s);
  assert.match(source, /canManage \? \[\{/);
});

test('dismissal requires a bounded reason and sends the trimmed value', () => {
  assert.match(source, /name="reason"[\s\S]*required: true, whitespace: true/);
  assert.match(source, /max: 500/);
  assert.match(source, /\/manual-reports\/\$\{dismissTarget\.id\}\/dismiss/);
  assert.match(source, /reason: values\.reason\.trim\(\)/);
});

test('dismissal failure preserves target and form for retry', () => {
  const handler = source.slice(source.indexOf('const submitDismiss'), source.indexOf('const archive'));
  assert.match(handler, /setDismissTarget\(null\)[\s\S]*await load\(\)/);
  const failure = handler.slice(handler.indexOf('catch (error)'));
  assert.doesNotMatch(failure, /setDismissTarget\(null\)|resetFields/);
  assert.match(handler, /if \(!dismissTarget \|\| dismissSaving\) return/);
});
