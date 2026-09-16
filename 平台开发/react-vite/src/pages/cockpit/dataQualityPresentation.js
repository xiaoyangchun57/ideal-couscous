const RATE_DENOMINATORS = {
  completeness_rate: 'expected',
  validity_rate: 'actual',
};

export function qualityRate(record = {}, rateKey) {
  const denominatorKey = rateKey === 'timeliness_rate'
    ? (Object.hasOwn(record, 'sampled_metric_count') ? 'sampled_metric_count' : 'actual')
    : RATE_DENOMINATORS[rateKey];
  if (!denominatorKey || Number(record[denominatorKey] || 0) <= 0) return null;
  const rate = Number(record[rateKey]);
  return Number.isFinite(rate) ? rate : null;
}

export function completenessPresentation(record = {}) {
  const rate = qualityRate(record, 'completeness_rate');
  return { rate, hasSample: rate !== null, label: rate === null ? '无样本' : `${rate}%` };
}
