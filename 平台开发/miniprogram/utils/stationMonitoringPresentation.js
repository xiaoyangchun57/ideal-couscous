const CYCLE_STATUS_LABELS = {
  complete: '结果完整',
  incomplete: '结果缺项',
  pending: '结果形成中',
  review_required: '结果待核对',
};

const CYCLE_REASON_LABELS = {
  expected_factors_unconfigured: '应监测因子未设置',
  time_configuration_missing: '时间配置缺失',
  no_evaluable_period: '暂无可评价时段',
};

function countValue(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

function presentLatestCycle(cycle) {
  const source = cycle && typeof cycle === 'object' ? cycle : {};
  const arrivedSource = source.arrived_count !== undefined && source.arrived_count !== null
    ? source.arrived_count
    : source.formed_count;
  const reasonLabel = CYCLE_REASON_LABELS[source.reason_code] || '';
  const status = source.status === 'pending' && countValue(arrivedSource) > 0
    ? 'incomplete' : source.status;
  return Object.assign({}, source, {
    arrived_count_display: countValue(arrivedSource),
    expected_count_display: countValue(source.expected_count),
    status_label: reasonLabel || CYCLE_STATUS_LABELS[status] || '结果状态待确认',
  });
}

function presentMonitoring(monitoring) {
  const source = monitoring && typeof monitoring === 'object' ? monitoring : {};
  const latestValues = Array.isArray(source.latest_values) ? source.latest_values : [];
  const factors = Array.isArray(source.factors) && source.factors.length
    ? source.factors : latestValues;
  const valueByMetric = new Map(latestValues.map(item => [item.business_metric, item]));
  const cycleByMetric = new Map(((source.latest_cycle || {}).factors || []).map(item => [item.business_metric, item]));
  const times = latestValues.map(item => item.observed_at);
  const commonTime = times.length && times.every(time => time && time === times[0]) ? times[0] : null;
  const latestResults = factors.map(factor => {
    const value = valueByMetric.get(factor.business_metric);
    const diagnostic = cycleByMetric.get(factor.business_metric) || {};
    const conflict = value && value.status === 'review_required';
    const hasValue = !!(value && !conflict && value.standard_value != null);
    const label = conflict ? '待核对' : factor.state === 'fault' ? (hasValue ? '故障 · 历史值' : '故障')
      : diagnostic.reason_code === 'stale_observation' ? '历史值'
        : !hasValue ? (factor.state === 'pending' ? '等待结果' : '缺失') : '';
    return Object.assign({}, factor, value, {
      status: conflict ? 'review_required' : hasValue ? 'value' : 'missing',
      has_value: hasValue, status_label: label,
      result_time_display: value && value.observed_at ? formatResultTime(value.observed_at) : '',
    });
  });
  return Object.assign({}, source, {
    latest_cycle: presentLatestCycle(source.latest_cycle),
    latest_values: latestValues,
    latest_result_at: commonTime,
    latest_result_time_display: commonTime ? formatResultTime(commonTime) : '',
    latest_results: latestResults,
  });
}

function formatResultTime(value) {
  if (!value) return '';
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return '';
  return new Date(timestamp + 8 * 3600000).toISOString().slice(0, 19).replace('T', ' ');
}

module.exports = { presentLatestCycle, presentMonitoring, formatResultTime };
