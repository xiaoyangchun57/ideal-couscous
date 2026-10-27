const assert = require('node:assert/strict');
const test = require('node:test');
const { presentLatestCycle, presentMonitoring } = require('../utils/stationMonitoringPresentation.js');

test('partial values use the existing missing-result summary without hiding values', () => {
  const result = presentMonitoring({
    latest_cycle: {
      status: 'pending', arrived_count: 1, expected_count: 2, pending_count: 1,
      factors: [
        { business_metric: 'ph', status: 'value' },
        { business_metric: 'water_temp', status: 'pending' },
      ],
    },
    factors: [
      { business_metric: 'ph', state: 'normal' },
      { business_metric: 'water_temp', state: 'pending' },
    ],
    latest_values: [{
      business_metric: 'ph', standard_value: 7.198, standard_value_display: '7.198',
      observed_at: '2026-10-27T00:00:00+00:00',
    }],
  });
  assert.equal(result.latest_cycle.status_label, '结果缺项');
  assert.equal(result.latest_results[0].has_value, true);
  assert.equal(result.latest_results[0].standard_value_display, '7.198');
  assert.equal(result.latest_results[1].status_label, '等待结果');
});

test('zero-value, complete, fault, and review states retain their distinct meaning', () => {
  assert.equal(presentLatestCycle({ status: 'pending', arrived_count: 0 }).status_label, '结果形成中');
  assert.equal(presentLatestCycle({ status: 'complete', arrived_count: 2 }).status_label, '结果完整');
  assert.equal(presentLatestCycle({ status: 'review_required', arrived_count: 1 }).status_label, '结果待核对');
  const fault = presentMonitoring({
    latest_cycle: { status: 'incomplete', arrived_count: 0, expected_count: 1 },
    factors: [{ business_metric: 'ph', state: 'fault' }], latest_values: [],
  });
  assert.equal(fault.latest_cycle.status_label, '结果缺项');
  assert.equal(fault.latest_results[0].status_label, '故障');
  const conflict = presentMonitoring({
    latest_cycle: { status: 'review_required', arrived_count: 1, expected_count: 1 },
    factors: [{ business_metric: 'ph', state: 'normal' }],
    latest_values: [{ business_metric: 'ph', status: 'review_required', standard_value: 7.1 }],
  });
  assert.equal(conflict.latest_results[0].has_value, false);
  assert.equal(conflict.latest_results[0].status_label, '待核对');
});
