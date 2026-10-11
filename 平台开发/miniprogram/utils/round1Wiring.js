const REAGENT_FILTERS = ['', 'expired', 'expiring', 'low_volume'];

function positiveId(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function normalizeStationHubTarget(target) {
  const value = target && typeof target === 'object' ? target : null;
  if (!value) return null;
  if (value.view === 'stations') return { view: 'stations' };
  if (value.view !== 'reagents') return null;
  const filter = String(value.filter || '');
  return REAGENT_FILTERS.includes(filter) ? { view: 'reagents', filter } : null;
}

function reviewExecutionProjection(item) {
  const value = item || {};
  const inspectionType = value.source_type === 'inspection' || value.source_type === 'inspection_batch';
  const executionPlanId = positiveId(value.plan_id);
  const executionSiteId = positiveId(value.site_id);
  return {
    executionPlanId,
    executionSiteId,
    canViewInspection: !!(inspectionType && executionPlanId && executionSiteId),
  };
}

function attentionReason(reason) {
  const source = typeof reason === 'string' ? { type: reason } : (reason || {});
  const type = String(source.type || source.key || '').trim();
  const labels = {
    expired: '已过期', expiring: '临期', low_volume: '低余量',
    pending_qc: '待标定', failed_qc: '标定失败',
  };
  return labels[type] ? { type, label: String(source.label || labels[type]) } : null;
}

function projectReagentItem(item) {
  const value = item || {};
  const siteId = positiveId(value.site_id);
  const reagentId = positiveId(value.reagent_id || value.id);
  const rawVolume = value.current_qty !== undefined ? value.current_qty : value.volume;
  const volume = rawVolume === null || rawVolume === undefined || rawVolume === '' ? null : Number(rawVolume);
  const reasons = (Array.isArray(value.attention_reasons) ? value.attention_reasons : [])
    .map(attentionReason).filter(Boolean);
  const qcStatus = String(value.qc_status || '').trim().toLowerCase();
  const qcProjection = {
    not_required: { tone: 'unknown', label: '无需标定' },
    passed: { tone: 'normal', label: '已标定' },
    pending: { tone: 'pending', label: '待标定' },
    failed: { tone: 'failed', label: '标定异常' },
  }[qcStatus] || { tone: 'unknown', label: '状态未知' };
  return Object.assign({}, value, {
    id: reagentId,
    key: `${siteId || 'site'}-${reagentId || 'reagent'}`,
    site_id: siteId,
    reagent_id: reagentId,
    site_name: String(value.site_name || '站点名称暂不可用'),
    reagent_name: String(value.reagent_name || value.name || '试剂名称暂不可用'),
    unit: String(value.unit || ''),
    volume: Number.isFinite(volume) ? volume : rawVolume,
    expiryText: String(value.expiry_text || value.expiryText || value.status || '有效期信息暂不可用'),
    attention_reasons: reasons,
    qc_status: qcStatus,
    qc_status_tone: qcProjection.tone,
    qc_status_label: qcProjection.label,
    canReplace: value.can_replace === true,
    canCalibrate: value.can_calibrate === true,
  });
}

function projectReagentSiteGroups(response, filter, expansionState) {
  const items = projectReagentOverview(response, filter);
  const groups = [];
  const bySite = new Map();
  items.forEach(item => {
    const key = `${item.site_id || 'site'}:${item.site_name}`;
    let group = bySite.get(key);
    if (!group) {
      group = {
        site_id: item.site_id,
        site_name: item.site_name,
        reagents: [],
        attention_reasons: [],
        attention_count: 0,
        expanded: false,
      };
      bySite.set(key, group);
      groups.push(group);
    }
    group.reagents.push(item);
  });
  groups.forEach(group => {
    const reasonMap = new Map();
    group.reagents.forEach(item => item.attention_reasons.forEach(reason => reasonMap.set(reason.type, reason)));
    group.attention_reasons = Array.from(reasonMap.values());
    group.attention_count = group.reagents.filter(item => item.attention_reasons.length > 0).length;
    const saved = expansionState && Object.prototype.hasOwnProperty.call(expansionState, group.site_id)
      ? expansionState[group.site_id] : undefined;
    const attentionDefault = (!response || response.scope !== 'all') && group.attention_count > 0;
    group.expanded = saved === undefined ? attentionDefault : saved === true;
  });
  return groups;
}

function projectReagentScope(response) {
  const value = response || {};
  const scopes = Array.isArray(value.available_scopes) ? value.available_scopes.filter(scope => ['mine', 'all'].includes(scope)) : ['mine'];
  const counts = value.scope_counts && typeof value.scope_counts === 'object' ? value.scope_counts : {};
  return {
    scope: value.scope === 'all' && scopes.includes('all') ? 'all' : 'mine',
    availableScopes: scopes.length ? scopes : ['mine'],
    canViewAll: scopes.includes('all'),
    scopeCounts: {
      mine: Number.isFinite(Number(counts.mine)) ? Number(counts.mine) : 0,
      all: counts.all === null || counts.all === undefined ? null : Number(counts.all),
    },
  };
}

function projectReagentOverview(response, filter) {
  const items = (response && Array.isArray(response.items) ? response.items : []).map(projectReagentItem);
  const selected = REAGENT_FILTERS.includes(filter) ? filter : '';
  return selected ? items.filter(item => item.attention_reasons.some(reason => reason.type === selected)) : items;
}

function teamOverviewText(rows) {
  if (!Array.isArray(rows)) return '查看团队整体巡检进度';
  const count = rows.length;
  if (!count) return '团队当前暂无计划';
  const active = rows.filter(row => !['completed', 'cancelled', 'archived'].includes(String(row.status || '').toLowerCase())).length;
  return active ? `团队共 ${count} 个计划，${active} 个待推进` : `团队共 ${count} 个计划，均已闭环`;
}

function stableKey(prefix, seed) {
  const base = String(seed || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 48);
  return `${prefix}-${base || Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function padDatePart(value) {
  return String(value).padStart(2, '0');
}

function localBusinessDate(value) {
  const date = value instanceof Date ? value : new Date(value === undefined ? Date.now() : value);
  if (!Number.isFinite(date.getTime())) return '';
  return `${date.getFullYear()}-${padDatePart(date.getMonth() + 1)}-${padDatePart(date.getDate())}`;
}

function localBusinessDateTime(value) {
  const date = value instanceof Date ? value : new Date(value === undefined ? Date.now() : value);
  if (!Number.isFinite(date.getTime())) return '';
  return `${localBusinessDate(date)} ${padDatePart(date.getHours())}:${padDatePart(date.getMinutes())}:${padDatePart(date.getSeconds())}`;
}

module.exports = {
  REAGENT_FILTERS,
  normalizeStationHubTarget,
  positiveId,
  projectReagentItem,
  projectReagentOverview,
  projectReagentScope,
  projectReagentSiteGroups,
  reviewExecutionProjection,
  localBusinessDate,
  localBusinessDateTime,
  stableKey,
  teamOverviewText,
};
