export const MONITORING_STATUS_META = {
  not_connected: { label: '未接入', color: 'default' },
  awaiting_first_frame: { label: '等待首帧', color: 'blue' },
  raw_received_config_pending: { label: '应监测因子未设置', color: 'gold' },
  waiting_first_valid: { label: '等待首个有效观测', color: 'blue' },
  interval_unconfigured: { label: '数据周期未配置', color: 'default' },
  normal: { label: '数据正常', color: 'green' },
  attention: { label: '数据需关注', color: 'orange' },
  data_unavailable: { label: '数据暂不可用', color: 'red' },
};

export const MONITORING_STATUS_ORDER = Object.keys(MONITORING_STATUS_META);

export const SERVICE_STATUS_META = {
  active: { label: '在服务', color: 'green' },
  ended: { label: '服务结束', color: 'default' },
};

export const REPORTING_CONDITION_META = {
  normal: { label: '正常到报要求', color: 'blue' },
  temporary_stop: { label: '临时停站', color: 'orange' },
  no_upload_condition: { label: '暂无上传条件', color: 'gold' },
};

export function stationServicePolicyView(value = {}) {
  const policy = value.service_policy || value;
  const service = SERVICE_STATUS_META[policy.service_status] || SERVICE_STATUS_META.active;
  const reporting = REPORTING_CONDITION_META[policy.reporting_condition]
    || REPORTING_CONDITION_META.normal;
  const ended = policy.service_status === 'ended';
  const showReportingCondition = !ended && Boolean(policy.reporting_condition)
    && policy.reporting_condition !== 'normal';
  let notice = null;
  if (!ended && policy.reporting_condition === 'no_upload_condition') {
    notice = {
      type: 'warning',
      message: '当前暂无上传条件',
      description: '站点仍在服务，但当前不要求上传；等待首帧或暂无接收记录不作为当前催报依据。',
    };
  } else if (!ended && policy.reporting_condition === 'temporary_stop') {
    notice = {
      type: 'warning',
      message: '当前临时停站',
      description: '站点仍在服务范围内，但停站期间不参与当前到报评价。',
    };
  }
  return {
    service: { ...service, label: policy.service_status_label || service.label },
    reporting: { ...reporting, label: policy.reporting_condition_label || reporting.label },
    ended,
    showReportingCondition,
    notice,
  };
}

export function monitoringMissingCount(cycle) {
  const count = cycle?.missing_count;
  return Number.isInteger(count) && count > 0 ? count : 0;
}

const SITE_DIRECTORY_SNAPSHOT_LIMIT = 8;
const siteDirectorySnapshots = new Map();

function normalizedRoles(user = {}) {
  const roles = Array.isArray(user.roles) && user.roles.length ? user.roles : [user.role];
  return [...new Set(roles.filter(Boolean).map(String))].sort();
}

function normalizedCapabilities(user = {}) {
  return Object.entries(user.capabilities || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => [key, value]);
}

export function stationDirectorySnapshotKey(user = {}, scope, sessionKey = '', serviceScope = 'current') {
  const identity = user.id ?? user.username;
  if (identity == null || !scope || !sessionKey) return null;
  return JSON.stringify({
    sessionKey,
    identity: String(identity),
    username: String(user.username || ''),
    roles: normalizedRoles(user),
    capabilities: normalizedCapabilities(user),
    scope,
    serviceScope,
  });
}

function copySiteDirectorySnapshot(snapshot) {
  if (!snapshot) return null;
  return {
    ...snapshot,
    rows: Array.isArray(snapshot.rows) ? [...snapshot.rows] : [],
    summary: { ...(snapshot.summary || {}) },
    scopeFacts: snapshot.scopeFacts ? {
      ...snapshot.scopeFacts,
      availableScopes: [...(snapshot.scopeFacts.availableScopes || [])],
      availableServiceScopes: [...(snapshot.scopeFacts.availableServiceScopes || [])],
    } : null,
  };
}

export function readSiteDirectorySnapshot(key) {
  return key ? copySiteDirectorySnapshot(siteDirectorySnapshots.get(key)) : null;
}

export function writeSiteDirectorySnapshot(key, snapshot) {
  if (!key || !snapshot?.ready) return null;
  const stored = copySiteDirectorySnapshot({ ...snapshot, key, savedAt: Date.now() });
  siteDirectorySnapshots.delete(key);
  siteDirectorySnapshots.set(key, stored);
  while (siteDirectorySnapshots.size > SITE_DIRECTORY_SNAPSHOT_LIMIT) {
    siteDirectorySnapshots.delete(siteDirectorySnapshots.keys().next().value);
  }
  return copySiteDirectorySnapshot(stored);
}

export function clearSiteDirectorySnapshots() {
  siteDirectorySnapshots.clear();
}

export function siteDirectoryLoadMode(snapshot) {
  return snapshot?.ready ? 'refresh' : 'initial';
}

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
  'monitoring_load_state',
  'communication',
  'latest_cycle',
];

export const CYCLE_STATUS_META = {
  pending: { label: '按因子展示', color: 'default' },
  complete: { label: '按因子展示', color: 'default' },
  incomplete: { label: '按因子展示', color: 'default' },
  review_required: { label: '按因子展示', color: 'default' },
  not_configured: { label: '监测因子未配置', color: 'default' },
};

const CYCLE_UNAVAILABLE_META = {
  expected_factors_unconfigured: { label: '应监测因子未设置', color: 'gold' },
  time_configuration_missing: {
    label: '时间配置未完成',
    color: 'default',
    description: '不影响已有有效测量值展示',
  },
  no_evaluable_period: { label: '暂无可评价时段', color: 'default' },
};

export const COMMUNICATION_STATUS_META = {
  normal: { label: '接收正常', color: 'green' },
  stale: { label: '接收中断', color: 'red' },
  awaiting: { label: '等待首帧', color: 'blue' },
  not_connected: { label: '未接入', color: 'default' },
};

export function cycleStatusView(cycle = {}) {
  if (cycle.status === 'not_configured' && CYCLE_UNAVAILABLE_META[cycle.reason_code]) {
    return CYCLE_UNAVAILABLE_META[cycle.reason_code];
  }
  return CYCLE_STATUS_META[cycle.status] || { label: '按因子展示', color: 'default' };
}

export function communicationStatusView(communication = {}) {
  return COMMUNICATION_STATUS_META[communication.status] || { label: '接收状态待确认', color: 'default' };
}

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
  const baseRows = Array.isArray(siteRows) ? siteRows : [];
  const baseById = new Map(baseRows.map((item) => [String(item.id), item]));
  const byId = new Map(items.map((item) => [String(item.id ?? item.site_id), item]));
  const previousById = new Map(previousRows.map((item) => [String(item.id), item]));
  if (monitoringPayload != null) {
    const merged = items.map((monitoring) => {
      const key = String(monitoring.id ?? monitoring.site_id);
      const site = baseById.get(key) || { ...monitoring, id: monitoring.id ?? monitoring.site_id };
      return mergeMonitoringSite(site, monitoring);
    });
    baseRows.forEach((site) => {
      if (!byId.has(String(site.id))) merged.push(mergeMonitoringSite(site));
    });
    return merged;
  }
  return baseRows.map((site) => {
    const key = String(site.id);
    const previous = previousById.get(key);
    return previous ? mergeMonitoringSite(site, previous) : mergeMonitoringSite(site);
  });
}

export function validateMonitoringDirectoryPayload(
  siteRows, payload, { scope, serviceScope, allowMonitoringSuperset = false } = {},
) {
  const items = Array.isArray(payload?.items) ? payload.items : null;
  const availableScopes = Array.isArray(payload?.available_scopes)
    ? payload.available_scopes : [];
  const availableServiceScopes = Array.isArray(payload?.available_service_scopes)
    ? payload.available_service_scopes : [];
  if (!items || payload?.scope !== scope || payload?.service_scope !== serviceScope
      || !availableScopes.includes(scope)
      || !availableServiceScopes.includes(serviceScope)) {
    return { ok: false, error: '监测范围与当前站点列表不一致，请重新加载' };
  }
  const siteIds = (Array.isArray(siteRows) ? siteRows : []).map((item) => String(item.id));
  const monitoringIds = items.map((item) => String(item.id ?? item.site_id));
  const uniqueSiteIds = new Set(siteIds);
  const uniqueMonitoringIds = new Set(monitoringIds);
  const summaryTotal = Number(payload?.summary?.total);
  const sameIds = uniqueSiteIds.size === uniqueMonitoringIds.size
    && [...uniqueSiteIds].every((id) => uniqueMonitoringIds.has(id));
  const baseIsSubset = [...uniqueSiteIds].every((id) => uniqueMonitoringIds.has(id));
  if (uniqueSiteIds.size !== siteIds.length
      || uniqueMonitoringIds.size !== monitoringIds.length
      || !Number.isInteger(summaryTotal)
      || summaryTotal !== items.length
      || (allowMonitoringSuperset ? !baseIsSubset : !sameIds)) {
    return { ok: false, error: '监测汇总与当前站点列表数量不一致，请重新加载' };
  }
  return { ok: true };
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

const STATION_TREND_METRICS = new Set([
  'dissolved_oxygen', 'ph', 'turbidity', 'water_temp', 'conductivity',
  'codmn', 'total_nitrogen', 'total_phosphorus', 'ammonia',
]);

export function monitoringTrendFactors(factors = [], selectedMetrics = [], configured = false) {
  const selected = new Set(configured ? selectedMetrics : STATION_TREND_METRICS);
  const seen = new Set();
  return factors.filter((item) => {
    const metric = item?.business_metric;
    if (!STATION_TREND_METRICS.has(metric) || !selected.has(metric) || seen.has(metric)) return false;
    seen.add(metric);
    return true;
  });
}

export function formatMonitoringTime(value) {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
}

export function monitoringLatestResults(factors = [], values = []) {
  const byMetric = new Map(values.map(item => [item.business_metric, item]));
  const times = values.map(item => item.observed_at);
  const commonTime = times.length && times.every(time => time && time === times[0]) ? times[0] : null;
  return { commonTime, items: factors.map(factor => {
    const value = byMetric.get(factor.business_metric);
    const fault = factor.state === 'fault'
      && factor.reason_code !== 'business_time_outside_schedule';
    const hasValue = Boolean(value && value.standard_value != null);
    const statusLabel = fault
      ? (hasValue ? '存在故障值 · 最近有效值' : '存在故障值')
      : !hasValue ? '暂无有效观测' : '';
    return {
      ...factor,
      ...value,
      hasValue,
      statusLabel,
      statusReason: fault ? (factor.reason || '') : '',
      statusObservedAt: fault ? (factor.issue_observed_at || null) : null,
    };
  }) };
}

function latestTimestamp(values, field) {
  const timestamps = values
    .map((item) => ({ raw: item?.[field], time: new Date(item?.[field]).getTime() }))
    .filter((item) => item.raw && Number.isFinite(item.time));
  return timestamps.sort((left, right) => right.time - left.time)[0] || null;
}

export function monitoringResultSummary({
  latestValues = [], latestCycle = {}, reportingCondition = 'normal', lastReceivedAt = null,
} = {}) {
  const hasValues = latestValues.some((item) => item?.standard_value != null);
  if (reportingCondition === 'no_upload_condition') {
    return {
      cycleLabel: hasValues ? '历史结果' : '',
      cycleColor: 'default',
      notice: null,
      emptyDescription: hasValues ? '' : '当前不要求上传，且暂无历史有效测量结果',
      showingHistoricalValues: hasValues,
    };
  }
  const latestValueReceipt = latestTimestamp(latestValues, 'received_at');
  const latestCommunication = lastReceivedAt
    ? { raw: lastReceivedAt, time: new Date(lastReceivedAt).getTime() } : null;
  const newerFramePending = hasValues && latestValueReceipt && latestCommunication
    && Number.isFinite(latestCommunication.time)
    && latestCommunication.time > latestValueReceipt.time;
  if (newerFramePending) {
    return {
      cycleLabel: '当前显示历史值',
      cycleColor: 'gold',
      notice: {
        type: 'info',
        message: '已收到新报文，当前显示历史测量结果',
        description: `最新报文接收于 ${formatMonitoringTime(lastReceivedAt)}；当前有效值对应的接收时间为 ${formatMonitoringTime(latestValueReceipt.raw)}。`,
      },
      emptyDescription: '',
      showingHistoricalValues: true,
    };
  }
  if (!hasValues && lastReceivedAt && latestCycle.status !== 'not_configured') {
    return {
      cycleLabel: '尚无有效测量值',
      cycleColor: 'blue',
      notice: {
        type: 'info',
        message: '已收到报文，尚无有效测量值',
        description: `最新报文接收于 ${formatMonitoringTime(lastReceivedAt)}；当前没有可作为测量结果展示的有效值。`,
      },
      emptyDescription: '已收到报文，但尚无可展示的有效测量结果',
      showingHistoricalValues: false,
    };
  }
  return {
    cycleLabel: '',
    cycleColor: 'default',
    notice: null,
    emptyDescription: '当前站点暂无可展示的有效测量结果',
    showingHistoricalValues: false,
  };
}

export function capabilityLabel(enabled) {
  return enabled ? '可用' : '暂无服务端事实';
}

export function monitoringDefaultTrendMetric(factors = [], latestValues = []) {
  const available = new Set(factors.map((item) => item.business_metric).filter(Boolean));
  const latest = latestValues.find((item) => available.has(item.business_metric));
  return latest?.business_metric || factors.find((item) => item.business_metric)?.business_metric || '';
}

function monitoringTrendPointKey(item = {}) {
  return [
    item.business_metric || '', item.observed_at || item.scheduled_at || '',
    item.endpoint_id ?? '', item.instrument_asset_code ?? '',
  ].join('|');
}

export function mergeMonitoringTrendPages(current, next) {
  if (!current || current.site_id !== next?.site_id || current.metric !== next?.metric) return next;
  const points = new Map();
  [...(current.points || []), ...(next.points || [])].forEach((item) => {
    points.set(monitoringTrendPointKey(item), item);
  });
  return {
    ...current,
    ...next,
    points: [...points.values()].sort((left, right) => (
      new Date(left.observed_at || left.scheduled_at).getTime()
      - new Date(right.observed_at || right.scheduled_at).getTime()
    )),
    window: current.window || next.window,
    pagination: next.pagination || {},
  };
}

export function monitoringTrendView(payload, { loading = false, error = '' } = {}) {
  const items = Array.isArray(payload?.points) ? payload.points : [];
  const coverage = payload?.coverage || {};
  const pagination = payload?.pagination || {};
  const available = items.length > 0;
  return {
    available,
    items: available ? items : [],
    loading,
    error,
    unit: payload?.standard_unit || items.find((item) => item.unit)?.unit || '',
    factorName: payload?.factor_name_cn || '',
    aggregationSource: coverage.aggregation_source || '',
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
    hasMore: pagination.has_more === true,
    nextCursor: pagination.next_cursor || null,
    emptyReason: '当前因子在所选时间内暂无小时取样值',
  };
}

export function monitoringCoverageLabel(trend) {
  return trend?.aggregationSource === 'hourly_sample' ? '小时取样值' : '按测量时间展示';
}

export function monitoringHourlySeriesData(items = []) {
  const ordered = [...items].sort((left, right) => (
    new Date(left.observed_at || left.scheduled_at).getTime()
    - new Date(right.observed_at || right.scheduled_at).getTime()
  ));
  const result = [];
  let previousHour = null;
  ordered.forEach((item) => {
    const hour = new Date(item.sample_hour || item.hour_start || item.scheduled_at).getTime();
    if (Number.isFinite(previousHour) && Number.isFinite(hour) && hour - previousHour > 60 * 60 * 1000) {
      result.push([new Date(previousHour + 60 * 60 * 1000).toISOString(), null]);
    }
    const point = [
      item.observed_at || item.scheduled_at,
      !item.status || item.status === 'value' ? item.value : null,
    ];
    if (item.received_at) point.push(item.received_at);
    result.push(point);
    previousHour = Number.isFinite(hour) ? hour : previousHour;
  });
  return result;
}

export function monitoringTrendChartOption(trend) {
  return {
    animation: false,
    grid: { left: 56, right: 20, top: 24, bottom: 48 },
    tooltip: {
      trigger: 'axis',
      formatter: (params = []) => {
        const point = params[0];
        if (!point?.data) return '';
        const time = formatMonitoringTime(point.data[0]);
        const value = point.data[1] == null ? '缺失' : `${point.data[1]}${trend.unit ? ` ${trend.unit}` : ''}`;
        const received = point.data[2] ? `<br/>接收时间：${formatMonitoringTime(point.data[2])}` : '';
        return `测量时间：${time}${received}<br/>${trend.factorName || '监测结果'}小时取样值：${value}`;
      },
    },
    xAxis: {
      type: 'time', name: '测量时间', nameLocation: 'middle', nameGap: 32,
      axisLabel: { hideOverlap: true, formatter: '{MM}-{dd} {HH}:{mm}' },
    },
    yAxis: { type: 'value', name: trend.unit || '数值', scale: false },
    series: [{
      type: 'line', name: `${trend.factorName || '监测结果'}小时取样值`, showSymbol: trend.items.length < 80,
      connectNulls: false, data: monitoringHourlySeriesData(trend.items),
    }],
  };
}

export function hasAdminRole(user = {}) {
  return normalizedRoles(user).includes('admin');
}
