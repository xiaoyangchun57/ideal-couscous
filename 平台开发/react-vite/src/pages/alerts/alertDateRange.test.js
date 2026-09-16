import test from 'node:test';
import assert from 'node:assert/strict';
import { ALERT_DATE_RANGE_OPTIONS, isAlertInDateRange } from './alertDateRange.js';

test('alert ranges retain shortcuts and provide an all-history route', () => {
  assert.deepEqual(ALERT_DATE_RANGE_OPTIONS.map((item) => item.value), ['today', 'week', 'month', 'all']);
  const oldAlert = '2024-01-01T08:00:00+08:00';
  const now = new Date('2026-09-16T12:00:00+08:00');
  assert.equal(isAlertInDateRange(oldAlert, 'today', now), false);
  assert.equal(isAlertInDateRange(oldAlert, 'month', now), false);
  assert.equal(isAlertInDateRange(oldAlert, 'all', now), true);
});
