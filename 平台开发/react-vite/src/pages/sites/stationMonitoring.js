export const MONITORING_STATUS_META = {
  not_connected: { label: '未接入', color: 'default' },
  awaiting_first_frame: { label: '等待首帧', color: 'blue' },
  raw_received_config_pending: { label: '原文已收/档案待批准', color: 'gold' },
  waiting_first_valid: { label: '等待首个有效观测', color: 'blue' },
  interval_unconfigured: { label: '数据周期未配置', color: 'default' },
  normal: { label: '数据正常', color: 'green' },
  attention: { label: '数据需关注', color: 'orange' },
  data_unavailable: { label: '数据暂不可用', color: 'red' },
};

export const MONITORING_STATUS_ORDER = Object.keys(MONITORING_STATUS_META);

export const AXIS_META = {
  communication: '数据接收',
  data: '观测数据',
};

const MONITORING_FIELDS = [
  'monitoring_status',
  'monitoring_status_label',
  'monitoring_reason',
  'monitoring_reason_code',
  'reason_code',
  'last_received_at',
  'last_communication_at',
  'last_valid_observation_at',
  'latest_values',
  'published_factor_count',
];

export function monitoringStatusView(record = {}) {
  const key = record.monitoring_status;
  const meta = MONITORING_STATUS_META[key];
  if (!meta) {
    return {
      key: null,
      label: '监测状态待确认',
      color: 'default',
      reason: record.monitoring_reason || '服务端尚未返回监测状态字段',
      contractMissing: true,
    };
  }
  return {
    key,
    label: record.monitoring_status_label || meta.label,
    color: meta.color,
    reason: record.monitoring_reason || '服务端未提供状态主原因',
    contractMissing: false,
  };
}

export function mergeMonitoringSite(site, monitoring = {}) {
  const merged = { ...site };
  MONITORING_FIELDS.forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(monitoring, field)) merged[field] = monitoring[field];
  });
  if (!Object.prototype.hasOwnProperty.call(monitoring, 'monitoring_status')) {
    MONITORING_FIELDS.forEach((field) => { delete merged[field]; });
    merged.monitoring_status = null;
    merged.monitoring_contract_missing = true;
  } else {
    merged.monitoring_contract_missing = false;
  }
  return merged;
}

export function mergeMonitoringSites(siteRows, monitoringPayload, previousRows = []) {
  const items = Array.isArray(monitoringPayload)
    ? monitoringPayload
    : (monitoringPayload?.items || []);
  const byId = new Map(items.map((item) => [String(item.id ?? item.site_id), item]));
  const previousById = new Map(previousRows.map((item) => [String(item.id), item]));
  return (Array.isArray(siteRows) ? siteRows : []).map((site) => {
    const key = String(site.id);
    const current = byId.get(key);
    if (current) return mergeMonitoringSite(site, current);
    const previous = monitoringPayload == null ? previousById.get(key) : null;
    return previous ? mergeMonitoringSite(site, previous) : mergeMonitoringSite(site);
  });
}

export function monitoringSummaryItems(summary = {}) {
  return MONITORING_STATUS_ORDER.map((key) => ({
    key,
    label: MONITORING_STATUS_META[key].label,
    value: Number(summary[key] || 0),
    color: MONITORING_STATUS_META[key].color,
  }));
}

export function axisView(key, axis = {}) {
  const state = axis?.status || axis?.state;
  const labels = {
    received: '已收到',
    reported: '已上报',
    configured: '已配置',
    normal: '正常',
    attention: '需关注',
    not_configured: '未配置',
    unavailable: '暂不可用',
    unknown: '待确认',
    fresh: '在配置周期内',
    stale: '超出配置周期',
    has_valid_observation: '已有有效观测',
    no_valid_observation: '暂无有效观测',
    no_observation: '暂无观测',
    missing: '暂无记录',
    health_unknown: '健康状态未知',
  };
  const badgeStatuses = {
    normal: 'success', fresh: 'success', attention: 'warning', stale: 'warning',
    unavailable: 'error', data_unavailable: 'error',
  };
  return {
    key,
    label: AXIS_META[key] || key,
    state,
    stateLabel: axis?.status_label || labels[state] || MONITORING_STATUS_META[state]?.label || (state ? '服务端未提供状态名称' : '暂无分轴事实'),
    badgeStatus: badgeStatuses[state] || 'default',
    reason: axis?.reason || '',
    lastRecordAt: axis?.last_received_at || axis?.last_valid_observation_at || null,
    nextExpectedAt: axis?.next_expected_at || null,
  };
}

export function monitoringFactorName(item = {}) {
  return item.factor_name_cn || item.business_name || item.label || item.business_metric || '未命名业务因子';
}

export function formatMonitoringTime(value) {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}

export function capabilityLabel(enabled) {
  return enabled ? '可用' : '暂无服务端事实';
}

export function monitoringDefaultTrendMetric(factors = [], latestValues = []) {
  const available = new Set(factors.map((item) => item.business_metric).filter(Boolean));
  const latest = latestValues.find((item) => available.has(item.business_metric));
  return latest?.business_metric || factors.find((item) => item.business_metric)?.business_metric || '';
}

export function monitoringTrendView(payload, { loading = false, error = '' } = {}) {
  const items = Array.isArray(payload?.points) ? payload.points : [];
  const coverage = payload?.coverage || {};
  const available = items.length > 0;
  return {
    available,
    items: available ? items : [],
    loading,
    error,
    unit: payload?.standard_unit || items.find((item) => item.unit)?.unit || '',
    factorName: payload?.factor_name_cn || '',
    windowStart: coverage.window_start || null,
    windowEnd: coverage.window_end || null,
    coverageRate: Number.isFinite(coverage.coverage_rate) ? coverage.coverage_rate : null,
    validPoints: Number.isFinite(coverage.valid_points) ? coverage.valid_points : null,
    displayedPoints: Number.isFinite(coverage.displayed_points) ? coverage.displayed_points : items.length,
    expectedPoints: Number.isFinite(coverage.expected_points) ? coverage.expected_points : null,
    gapCount: Number.isFinite(coverage.gap_count) ? coverage.gap_count : null,
    missingPoints: Number.isFinite(coverage.missing_points) ? coverage.missing_points : null,
    latePoints: Number.isFinite(coverage.late_points) ? coverage.late_points : 0,
    suspectPoints: Number.isFinite(coverage.suspect_points) ? coverage.suspect_points : 0,
    duplicateRecords: Number.isFinite(coverage.duplicate_records) ? coverage.duplicate_records : 0,
    conflictSlots: Number.isFinite(coverage.conflict_slots) ? coverage.conflict_slots : 0,
    emptyReason: '当前因子在最近24小时内暂无有效观测',
  };
}

export function monitoringCoverageLabel(trend) {
  if (trend.coverageRate == null || trend.validPoints == null || trend.expectedPoints == null) {
    return '周期未配置';
  }
  return `${(trend.coverageRate * 100).toFixed(1)}% (${trend.validPoints}/${trend.expectedPoints})`;
}

export function monitoringTrendChartOption(trend) {
  return {
    animation: false,
    grid: { left: 56, right: 20, top: 24, bottom: 48 },
    tooltip: { trigger: 'axis', valueFormatter: (value) => `${value}${trend.unit ? ` ${trend.unit}` : ''}` },
    xAxis: {
      type: 'time', name: '正式业务时点', nameLocation: 'middle', nameGap: 32,
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: 'value', name: trend.unit || '数值', scale: true },
    series: [{
      type: 'line', name: trend.factorName || '有效观测', showSymbol: trend.items.length < 80,
      connectNulls: false, data: trend.items.map((item) => [item.scheduled_at || item.observed_at, item.value]),
    }],
  };
}

export function hasAdminRole(user = {}) {
  const roles = Array.isArray(user.roles) && user.roles.length ? user.roles : [user.role];
  return roles.includes('admin');
}
