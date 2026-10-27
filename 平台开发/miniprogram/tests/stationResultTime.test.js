const assert = require('node:assert/strict');
const { presentMonitoring, formatResultTime } = require('../utils/stationMonitoringPresentation');
const values = [
  { business_metric: 'ammonia', observed_at: '2026-10-22T06:07:00+00:00', standard_value: 0.105853, standard_value_display: '0.105853' },
  { business_metric: 'total_phosphorus', observed_at: '2026-10-22T08:00:00+00:00', standard_value: 0.03 },
];
const result = presentMonitoring({ latest_values: values,
  factors: [{ business_metric: 'ammonia', state: 'fault' }, { business_metric: 'total_phosphorus' }],
  latest_cycle: { factors: [{ business_metric: 'total_phosphorus', reason_code: 'stale_observation' }] } });
assert.equal(result.latest_result_at, null);
assert.equal(result.latest_results[0].result_time_display, '2026-10-22 14:07:00');
assert.equal(result.latest_results[1].result_time_display, '2026-10-22 16:00:00');
assert.equal(result.latest_results[0].status_label, '故障 · 历史值');
assert.equal(result.latest_results[0].standard_value_display, '0.105853');
assert.equal(result.latest_results[1].status_label, '历史值');
assert.equal(presentMonitoring({ latest_values: values.map(v => Object.assign({}, v, { observed_at: values[0].observed_at })) }).latest_result_at, values[0].observed_at);
assert.equal(presentMonitoring({ factors: [{ business_metric: 'ph' }] }).latest_results[0].has_value, false);
assert.equal(formatResultTime(null), '');
