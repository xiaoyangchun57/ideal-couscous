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
});

test('admin detection evaluates the complete role set', () => {
  assert.equal(hasAdminRole({ role: 'reviewer', roles: ['reviewer', 'admin'] }), true);
  assert.equal(hasAdminRole({ role: 'admin', roles: ['reviewer'] }), false);
  assert.equal(hasAdminRole({ role: 'admin' }), true);
  assert.equal(hasAdminRole({ role: 'operator' }), false);
});
