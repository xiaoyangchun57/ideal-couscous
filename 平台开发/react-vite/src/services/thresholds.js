import { api } from './api';

// 阈值状态色阶：normal(正常绿色) / warning(预警黄色) / critical(超标红色)
export const THRESHOLD_COLORS = {
  normal: '#52c41a',
  warning: '#faad14',
  critical: '#f5222d',
};

export const THRESHOLD_LABELS = {
  normal: '正常',
  warning: '预警',
  critical: '超标',
};

/**
 * 根据指标阈值判断数值状态。
 * @param {string} metric - 指标编码（ph/ammonia/dissolved_oxygen/...）
 * @param {number} value - 采集值
 * @param {Array} thresholds - 后端 /api/thresholds 返回的阈值数组
 * @returns {{status:string,label:string,color:string}}
 */
export function classifyMetric(metric, value, thresholds = []) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return { status: 'unknown', label: '—', color: '#8c8c8c' };
  }
  const v = Number(value);
  const t = thresholds.find((x) => x.metric === metric);
  if (!t) return { status: 'unknown', label: '—', color: '#8c8c8c' };

  const low = t.low != null ? Number(t.low) : null;
  const high = t.high != null ? Number(t.high) : null;
  const criticalLow = t.critical_low != null ? Number(t.critical_low) : null;
  const criticalHigh = t.critical_high != null ? Number(t.critical_high) : null;

  // 严重区间优先
  if (criticalLow !== null && v <= criticalLow) {
    return { status: 'critical', label: THRESHOLD_LABELS.critical, color: THRESHOLD_COLORS.critical };
  }
  if (criticalHigh !== null && v >= criticalHigh) {
    return { status: 'critical', label: THRESHOLD_LABELS.critical, color: THRESHOLD_COLORS.critical };
  }
  // 预警区间
  if (low !== null && v < low) {
    return { status: 'warning', label: THRESHOLD_LABELS.warning, color: THRESHOLD_COLORS.warning };
  }
  if (high !== null && v > high) {
    return { status: 'warning', label: THRESHOLD_LABELS.warning, color: THRESHOLD_COLORS.warning };
  }
  return { status: 'normal', label: THRESHOLD_LABELS.normal, color: THRESHOLD_COLORS.normal };
}

let _thresholdCache = null;

/** 获取阈值配置（带内存缓存，登录态变化后重新获取） */
export async function getThresholds() {
  if (_thresholdCache) return _thresholdCache;
  _thresholdCache = await api.get('/thresholds');
  return _thresholdCache || [];
}

export function clearThresholdCache() {
  _thresholdCache = null;
}
