import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AXIS_META,
  MONITORING_STATUS_META,
  axisView,
  hasAdminRole,
  mergeMonitoringSites,
  monitoringStatusView,
  monitoringSummaryItems,
  monitoringDefaultTrendMetric,
  monitoringCoverageLabel,
  monitoringTrendChartOption,
  monitoringTrendView,
  monitoringFactorName,
  formatMonitoringTime,
} from './stationMonitoring.js';

test('monitoring status metadata covers the eight product states', () => {
  assert.deepEqual(Object.keys(MONITORING_STATUS_META), [
    'not_connected',
    'awaiting_first_frame',
    'raw_received_config_pending',
    'waiting_first_valid',
    'interval_unconfigured',
    'normal',
    'attention',
    'data_unavailable',
  ]);
  assert.deepEqual(Object.keys(AXIS_META), ['communication', 'data']);
});

test('monitoring status never falls back to the legacy station status', () => {
  const view = monitoringStatusView({ status: 'normal', status_label: '旧状态' });
  assert.equal(view.contractMissing, true);
  assert.equal(view.label, '监测状态待确认');
});

test('monitoring merge preserves the legacy site status and retains last success on refresh failure', () => {
  const legacy = [{ id: 7, name: '一号站', status: 'maintenance' }];
  const first = mergeMonitoringSites(legacy, {
    items: [{ id: 7, monitoring_status: 'normal', monitoring_reason: '有效观测在周期内' }],
  });
  assert.equal(first[0].status, 'maintenance');
  assert.equal(first[0].monitoring_status, 'normal');

  const retained = mergeMonitoringSites(legacy, null, first);
  assert.equal(retained[0].monitoring_status, 'normal');
  assert.equal(retained[0].monitoring_reason, '有效观测在周期内');
});

test('summary and axes expose explicit unavailable and unknown states', () => {
  const summary = monitoringSummaryItems({ data_unavailable: 2 });
  assert.equal(summary.find((item) => item.key === 'data_unavailable').value, 2);
  assert.equal(axisView('communication', { state: 'reported' }).stateLabel, '已上报');
  assert.equal(axisView('data', { state: 'interval_unconfigured' }).stateLabel, '数据周期未配置');
  assert.equal(axisView('data', { state: 'waiting_first_valid' }).stateLabel, '等待首个有效观测');
});

test('admin detection evaluates the complete role set', () => {
  assert.equal(hasAdminRole({ role: 'reviewer', roles: ['reviewer', 'admin'] }), true);
  assert.equal(hasAdminRole({ role: 'admin', roles: ['reviewer'] }), false);
  assert.equal(hasAdminRole({ role: 'admin' }), true);
  assert.equal(hasAdminRole({ role: 'operator' }), false);
});

test('every server status renders its independent label and reason', () => {
  for (const [key, meta] of Object.entries(MONITORING_STATUS_META)) {
    const view = monitoringStatusView({
      monitoring_status: key, monitoring_reason: `服务端原因:${key}`, status: 'offline',
    });
    assert.equal(view.label, meta.label);
    assert.equal(view.reason, `服务端原因:${key}`);
    assert.equal(view.contractMissing, false);
  }
});

test('a successful response missing a site cannot reuse its previous monitoring facts', () => {
  const legacy = [{ id: 7, status: 'maintenance', last_communication_at: '旧通信时间' }];
  const previous = [{ id: 7, monitoring_status: 'normal', monitoring_reason: '旧原因' }];
  const next = mergeMonitoringSites(legacy, { items: [] }, previous);
  assert.equal(next[0].status, 'maintenance');
  assert.equal(next[0].monitoring_status, null);
  assert.equal(next[0].monitoring_reason, undefined);
  assert.equal(next[0].last_communication_at, undefined);
});

test('scope removal never restores previous rows on refresh failure', () => {
  const previous = [{ id: 7, monitoring_status: 'normal' }, { id: 8, monitoring_status: 'normal' }];
  assert.deepEqual(mergeMonitoringSites([], null, previous), []);
  assert.deepEqual(mergeMonitoringSites([{ id: 7 }], null, previous).map((item) => item.id), [7]);
});

test('trend view preserves the server window, unit, coverage and zero values', () => {
  assert.equal(monitoringTrendView(null).available, false);
  const available = monitoringTrendView({
    factor_name_cn: '酸碱度', standard_unit: 'pH',
    points: [{ scheduled_at: '2026-09-18T00:00:00+00:00', observed_at: '2026-09-18T00:00:00+00:00', value: 0 }],
    coverage: {
      window_start: '2026-09-18T00:00:00+00:00', window_end: '2026-09-18T01:00:00+00:00',
      coverage_rate: 1, valid_points: 6, displayed_points: 6, expected_points: 6, gap_count: 0, missing_points: 0,
      late_points: 1, suspect_points: 4, duplicate_records: 2, conflict_slots: 3,
    },
  });
  assert.equal(available.available, true);
  assert.equal(available.items[0].value, 0);
  assert.equal(available.unit, 'pH');
  assert.equal(available.coverageRate, 1);
  assert.equal(available.validPoints, 6);
  assert.equal(available.gapCount, 0);
  assert.equal(available.latePoints, 1);
  assert.equal(available.suspectPoints, 4);
  assert.equal(available.duplicateRecords, 2);
  assert.equal(available.conflictSlots, 3);
  assert.deepEqual(monitoringTrendChartOption(available).series[0].data, [
    ['2026-09-18T00:00:00+00:00', 0],
  ]);
  const suspectOnly = monitoringTrendView({
    points: [{ scheduled_at: '2026-09-18T00:00:00+00:00', value: 7.1, quality: 'suspect' }],
    coverage: {
      coverage_rate: 0, valid_points: 0, displayed_points: 1, expected_points: 6,
      suspect_points: 1,
    },
  });
  assert.equal(monitoringCoverageLabel(suspectOnly), '0.0% (0/6)');
  assert.equal(suspectOnly.suspectPoints, 1);
});

test('observation axis exposes the next authoritative business slot', () => {
  const view = axisView('data', {
    state: 'fresh', last_valid_observation_at: '2026-09-18T00:00:00+00:00',
    next_expected_at: '2026-09-18T04:00:00+00:00',
  });
  assert.equal(view.lastRecordAt, '2026-09-18T00:00:00+00:00');
  assert.equal(view.nextExpectedAt, '2026-09-18T04:00:00+00:00');
});

test('trend defaults to a configured factor with a latest valid value', () => {
  const factors = [{ business_metric: 'ph' }, { business_metric: 'ammonia' }];
  assert.equal(monitoringDefaultTrendMetric(factors, [{ business_metric: 'ammonia' }]), 'ammonia');
  assert.equal(monitoringDefaultTrendMetric(factors, []), 'ph');
  assert.equal(monitoringDefaultTrendMetric([], [{ business_metric: 'ph' }]), '');
});

test('data reception and observation axes preserve server labels and status visual semantics', () => {
  for (const key of Object.keys(AXIS_META)) {
    for (const [status, badge] of [['normal', 'success'], ['attention', 'warning'], ['missing', 'default'], ['unavailable', 'error']]) {
      const view = axisView(key, { status, status_label: `服务端自定义:${key}:${status}`, state: 'unknown' });
      assert.equal(view.label, AXIS_META[key]);
      assert.equal(view.state, status);
      assert.equal(view.stateLabel, `服务端自定义:${key}:${status}`);
      assert.equal(view.badgeStatus, badge);
    }
  }
});

test('known data fact states are never collapsed to unknown or device health', () => {
  for (const [status, label, badge] of [
    ['fresh', '在配置周期内', 'success'], ['stale', '超出配置周期', 'warning'],
    ['has_valid_observation', '已有有效观测', 'default'], ['no_valid_observation', '暂无有效观测', 'default'],
  ]) {
    const view = axisView('data', { state: status });
    assert.equal(view.stateLabel, label);
    assert.equal(view.badgeStatus, badge);
  }
  assert.equal(axisView('data').stateLabel, '暂无分轴事实');
  assert.equal(axisView('data', { status: 'server_new_status' }).stateLabel, '服务端未提供状态名称');
});

test('Chinese factor names precede business display names and internal metric identifiers', () => {
  assert.equal(monitoringFactorName({ factor_name_cn: '酸碱度', business_name: '业务名称', business_metric: 'ph_internal' }), '酸碱度');
  assert.equal(monitoringFactorName({ business_name: '业务名称', label: '业务标签', business_metric: 'ph_internal' }), '业务名称');
  assert.equal(monitoringFactorName({ label: '业务标签', business_metric: 'ph_internal' }), '业务标签');
  assert.equal(monitoringFactorName({ business_metric: 'ph_internal' }), 'ph_internal');
});

test('monitoring timestamps render as local user-facing time instead of ISO source text', () => {
  const source = '2026-09-16T06:51:11+00:00';
  const formatted = formatMonitoringTime(source);
  assert.doesNotMatch(formatted, /T|\+00:00|Z$/);
  assert.match(formatted, /2026/);
});
