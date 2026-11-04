import { metricUnitMap } from '../../services/constants.js';

export function analysisMetricPresentation(metric) {
  const key = String(metric || '').trim();
  const known = metricUnitMap[key];
  if (known) return { name: known.name, unit: known.unit || '' };
  return {
    name: key ? `未识别因子（${key}）` : '未识别因子',
    unit: '',
  };
}
