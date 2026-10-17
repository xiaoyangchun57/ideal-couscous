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
  return Object.assign({}, source, {
    arrived_count_display: countValue(arrivedSource),
    expected_count_display: countValue(source.expected_count),
    status_label: reasonLabel || CYCLE_STATUS_LABELS[source.status] || '结果状态待确认',
  });
}

function presentMonitoring(monitoring) {
  const source = monitoring && typeof monitoring === 'object' ? monitoring : {};
  return Object.assign({}, source, {
    latest_cycle: presentLatestCycle(source.latest_cycle),
    latest_values: Array.isArray(source.latest_values) ? source.latest_values : [],
  });
}

module.exports = { presentLatestCycle, presentMonitoring };
