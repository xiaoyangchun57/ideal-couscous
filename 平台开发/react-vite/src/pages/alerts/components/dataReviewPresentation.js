import { metricUnitMap } from '../../../services/constants.js';

export function dataReviewMetric(metric) {
  const key = String(metric || '').trim();
  const known = metricUnitMap[key];
  return known || { name: key ? `未识别因子（${key}）` : '未识别因子', unit: '' };
}

export function dataReviewValue(review) {
  const metric = dataReviewMetric(review?.metric);
  if (review?.value == null) return '未记录';
  return `${review.value}${metric.unit ? ` ${metric.unit}` : ''}`;
}

export function formatReviewTime(value) {
  if (!value) return '未记录';
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(String(value))
    ? String(value) : String(value).replace(' ', 'T');
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })}（北京时间）`;
}
