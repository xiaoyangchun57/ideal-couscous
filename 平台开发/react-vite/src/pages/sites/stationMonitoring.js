export const MONITORING_STATUS_META = {
  not_connected: { label: '未接入', color: 'default' },
  awaiting_first_frame: { label: '等待首帧', color: 'blue' },
  raw_received_config_pending: { label: '原文已收/档案待批准', color: 'gold' },
  waiting_first_valid: { label: '等待首个有效观测', color: 'blue' },
  interval_unconfigured: { label: '周期未配置', color: 'default' },
  normal: { label: '正常', color: 'green' },
  attention: { label: '需关注', color: 'orange' },
  data_unavailable: { label: '数据暂不可用', color: 'red' },
};

export const MONITORING_STATUS_ORDER = Object.keys(MONITORING_STATUS_META);

export const AXIS_META = {
  communication: '通信',
  data: '数据',
  rtu: 'RTU',
  instrument: '仪器',
};

const MONITORING_FIELDS = [
  'monitoring_status',
  'monitoring_status_label',
  'monitoring_reason',
  'reason_code',
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
    const previous = previousById.get(key);
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
  const state = axis?.state;
  const labels = {
    received: '已收到',
    reported: '已上报',
    configured: '已配置',
    normal: '正常',
    attention: '需关注',
    not_configured: '未配置',
    unavailable: '暂不可用',
    unknown: '待确认',
  };
  return {
    key,
    label: AXIS_META[key] || key,
    state,
    stateLabel: labels[state] || '待确认',
    reason: axis?.reason || '',
    lastReceivedAt: axis?.last_received_at || null,
  };
}

export function formatMonitoringTime(value) {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}

export function capabilityLabel(enabled) {
  return enabled ? '可用' : '暂无服务端事实';
}

export function hasAdminRole(user = {}) {
  const roles = Array.isArray(user.roles) && user.roles.length ? user.roles : [user.role];
  return roles.includes('admin');
}
