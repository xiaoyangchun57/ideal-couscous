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
    lastReceivedAt: axis?.last_received_at || null,
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

export function monitoringTrendView(capabilities = {}, monitoring = {}) {
  const items = Array.isArray(monitoring.trend) ? monitoring.trend : [];
  const available = capabilities.trend === true && items.length > 0;
  return {
    available,
    items: available ? items : [],
    emptyReason: capabilities.trend === true
      ? '服务端已声明趋势能力，但当前未返回聚合事实，趋势暂不可用'
      : '暂无服务端聚合事实，趋势暂不可用',
  };
}

export function hasAdminRole(user = {}) {
  const roles = Array.isArray(user.roles) && user.roles.length ? user.roles : [user.role];
  return roles.includes('admin');
}
