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
  monitoringTrendView,
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
  assert.deepEqual(Object.keys(AXIS_META), ['communication', 'data', 'rtu', 'instrument']);
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
  assert.equal(axisView('rtu', { state: 'unknown' }).stateLabel, '待确认');
  assert.equal(axisView('communication', { state: 'reported' }).stateLabel, '已上报');
  assert.equal(axisView('data', { state: 'interval_unconfigured' }).stateLabel, '周期未配置');
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

test('trend availability requires both server capability and returned aggregate facts', () => {
  assert.equal(monitoringTrendView({ trend: true }, {}).available, false);
  assert.equal(monitoringTrendView({ trend: false }, { trend: [{ value: 3 }] }).available, false);
  const available = monitoringTrendView({ trend: true }, { trend: [{ value: 0 }] });
  assert.equal(available.available, true);
  assert.equal(available.items[0].value, 0);
});
