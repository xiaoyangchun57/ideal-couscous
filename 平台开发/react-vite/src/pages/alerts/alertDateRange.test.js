import test from 'node:test';
import assert from 'node:assert/strict';
import { ALERT_DATE_RANGE_OPTIONS, alertListCoverage, isAlertInDateRange } from './alertDateRange.js';

test('alert ranges retain shortcuts and provide a loaded-history route', () => {
  assert.deepEqual(ALERT_DATE_RANGE_OPTIONS.map((item) => item.value), ['today', 'week', 'month', 'all']);
  const oldAlert = '2024-01-01T08:00:00+08:00';
  const now = new Date('2026-09-16T12:00:00+08:00');
  assert.equal(isAlertInDateRange(oldAlert, 'today', now), false);
  assert.equal(isAlertInDateRange(oldAlert, 'month', now), false);
  assert.equal(isAlertInDateRange(oldAlert, 'all', now), true);
});

test('alert list coverage exposes the 500-row truncation without claiming all history is loaded', () => {
  const coverage = alertListCoverage(500, 836);
  assert.deepEqual(
    { loaded: coverage.loaded, total: coverage.total, missing: coverage.missing, truncated: coverage.truncated },
    { loaded: 500, total: 836, missing: 336, truncated: true },
  );
  assert.match(coverage.summaryText, /全量共 836 条；列表已加载 500 条/);
  assert.match(coverage.truncationText, /另有 336 条暂不可在 Web 打开/);
  assert.doesNotMatch(ALERT_DATE_RANGE_OPTIONS.at(-1).label, /全部/);
});

test('alert list coverage does not warn when every reported row is loaded', () => {
  assert.deepEqual(alertListCoverage(2, 2), {
    loaded: 2,
    total: 2,
    missing: 0,
    truncated: false,
    summaryText: '汇总统计：全量共 2 条；列表已加载 2 条',
    truncationText: '',
  });
});
