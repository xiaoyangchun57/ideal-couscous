const maps = require('../services/maps.js');
const { canReview } = require('./reviewAccess.js');

const ALERT_LEVEL_TEXT = { red: 'I级', orange: 'II级', yellow: 'III级', blue: 'IV级' };
const ALERT_LEVEL_CLS = { red: 'red', orange: 'orange', yellow: 'yellow', blue: 'blue' };
const ALERT_LEVEL_TONE = { red: 'danger', orange: 'warning', yellow: 'warning', blue: 'info' };
const WORKORDER_STATUS_TONE = {
  pending: 'info', accepted: 'info', dispatched: 'info',
  in_progress: 'warning', reviewing: 'warning', resolved: 'success', closed: 'success'
};

function first(items) {
  return Array.isArray(items) && items.length ? items[0] : null;
}

function optionalCount(source, key) {
  if (!source || !Object.prototype.hasOwnProperty.call(source, key)) return null;
  const value = source[key];
  if (value === null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
}

function formatHomeDate(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return '';
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  return `${Number(match[1])}年${Number(match[2])}月${Number(match[3])}日 ${weekdays[date.getDay()]}`;
}

function greetingByHour(hour) {
  const value = Number(hour);
  if (value < 6) return '凌晨好';
  if (value < 9) return '早上好';
  if (value < 12) return '上午好';
  if (value < 14) return '中午好';
  if (value < 18) return '下午好';
  return '晚上好';
}

function projectIdentity(user, date, hour) {
  const value = user || {};
  const reviewVisible = canReview(value);
  return {
    displayName: String(value.real_name || '').trim() || '运维人员',
    greeting: greetingByHour(hour),
    dateLabel: formatHomeDate(date),
    canReview: reviewVisible,
    reviewVisible,
    reviewState: reviewVisible ? 'loading' : 'hidden',
    reviewCount: null,
    reviewDisplay: reviewVisible ? '加载中' : ''
  };
}

function projectWorkorder(item) {
  if (!item) return null;
  const value = maps.workorderCn(item);
  const objectId = String(value.order_no || '').trim();
  return {
    key: `workorder:${objectId || 'unavailable'}`,
    kind: 'workorder',
    typeLabel: '工单',
    statusLabel: value.status_cn,
    statusTone: WORKORDER_STATUS_TONE[value.status] || 'neutral',
    statusCls: value.status_cls,
    title: value.display_title,
    meta: [objectId, value.site_name].filter(Boolean),
    target: { kind: 'workorder', objectId: objectId || null }
  };
}

function projectAlert(item) {
  if (!item) return null;
  const objectId = item.id === null || item.id === undefined || item.id === '' ? null : item.id;
  const level = String(item.level || '').toLowerCase();
  const metric = String(item.metric_cn || maps.metricCn(item.metric) || '').trim();
  const siteName = String(item.site_name || '').trim();
  return {
    key: `alert:${objectId == null ? 'unavailable' : objectId}`,
    kind: 'alert',
    typeLabel: '告警',
    statusLabel: ALERT_LEVEL_TEXT[level] || '告警',
    statusTone: ALERT_LEVEL_TONE[level] || 'neutral',
    statusCls: ALERT_LEVEL_CLS[level] || 'gray',
    title: [siteName, metric].filter(Boolean).join(' · ') || '告警事项',
    meta: [item.message].filter(Boolean),
    target: { kind: 'alert', objectId }
  };
}

function projectRework(site) {
  if (!site) return null;
  const count = optionalCount(site, 'rework_items');
  if (count === null || count < 1) return null;
  const siteId = Number(site.site_id);
  if (!Number.isInteger(siteId) || siteId <= 0) return null;
  const planId = Number(site.target_plan_id);
  const itemId = Number(site.target_item_id);
  return {
    key: `inspection-rework:${siteId}`,
    kind: 'inspection_rework',
    typeLabel: '巡检整改',
    statusLabel: '需整改',
    statusTone: 'warning',
    statusCls: 'orange',
    title: String(site.site_name || '').trim() || '整改站点',
    meta: [`${count}项待整改`],
    target: {
      kind: 'inspection_rework',
      executionPlanId: Number.isInteger(planId) && planId > 0 ? planId : null,
      siteId,
      itemId: Number.isInteger(itemId) && itemId > 0 ? itemId : null,
      reworkOnly: true,
      source: 'home_rework'
    }
  };
}

function homeActions(workorders, alerts, sites) {
  const reworkActions = (Array.isArray(sites) ? sites : [])
    .map(projectRework).filter(Boolean);
  return [...reworkActions, projectWorkorder(first(workorders)), projectAlert(first(alerts))].filter(Boolean);
}

function homePackage(workPackage, summary) {
  const value = workPackage || {};
  const readiness = value.readiness || {};
  const sites = Array.isArray(value.sites) ? value.sites : [];
  const completed = optionalCount(summary, 'completed_items');
  const total = optionalCount(summary, 'total_items');
  const hasProgress = completed !== null && total !== null;
  const vehicleAssigned = readiness.vehicle_assigned === true;
  const departureConfirmed = readiness.departure_confirmed === true;
  const departurePending = optionalCount(readiness, 'departure_pending_count');
  return {
    hasPlan: value.has_plan === true,
    siteNames: sites.map(site => String(site && site.name || '').trim()).filter(Boolean),
    progress: hasProgress ? (() => {
      const percent = total > 0 ? Math.max(0, Math.min(100, Math.round(completed / total * 100))) : 0;
      return {
        completed,
        total,
        percent,
        label: `${completed}/${total} 项`,
        fillStyle: `width:${percent}%`,
      };
    })() : null,
    resources: {
      vehicleAssigned,
      departureConfirmed,
      departurePendingCount: departurePending,
      partsCount: optionalCount(readiness, 'parts_count'),
      linkedWorkorders: optionalCount(readiness, 'linked_workorders'),
      vehicleText: vehicleAssigned ? '车辆已安排' : '车辆待安排',
      vehicleStatusCls: vehicleAssigned ? 'green' : 'orange',
      departureText: departureConfirmed
        ? '出发资源已确认'
        : (departurePending === null ? '出发资源待确认' : `${departurePending}项出发资源待确认`),
      departureStatusCls: departureConfirmed ? 'green' : 'orange'
    }
  };
}

function projectHome(response) {
  const value = response || {};
  return {
    actions: homeActions(value.workorders, value.alerts, value.sites),
    workPackage: homePackage(value.work_package, value.summary)
  };
}

function projectUnread(response) {
  const value = optionalCount(response, 'count');
  if (value === null) return { notificationsState: 'unavailable', unreadCount: null, unreadDisplay: '' };
  return {
    notificationsState: 'ready',
    unreadCount: value,
    unreadDisplay: value > 99 ? '99+' : (value > 0 ? String(value) : '')
  };
}

function projectReview(rows) {
  if (!Array.isArray(rows)) {
    return { reviewState: 'unavailable', reviewCount: null, reviewDisplay: '数量暂不可用' };
  }
  const count = rows.length;
  return {
    reviewState: 'ready',
    reviewCount: count,
    reviewDisplay: count > 0 ? `${count}项待审核` : '暂无待审核'
  };
}

function projectStationSummary(response) {
  const summary = response && response.summary && typeof response.summary === 'object'
    ? response.summary : {};
  const normal = optionalCount(summary, 'normal') || 0;
  const attention = optionalCount(summary, 'attention') || 0;
  const total = optionalCount(summary, 'total') || 0;
  return {
    total,
    normal,
    attention,
    unavailable: Math.max(0, total - normal - attention),
  };
}

function projectReagentSummary(response) {
  const rows = response && Array.isArray(response.items) ? response.items : [];
  const count = optionalCount(response, 'concern_count');
  const concernCount = count === null ? rows.length : count;
  return {
    concernCount,
    display: concernCount > 0 ? `${concernCount}项需关注` : '暂无需处理',
    items: rows.slice(0, 2).map(item => ({
      key: `${item.site_id || ''}-${item.id || item.reagent_id || ''}`,
      text: [item.site_name, item.reagent_name, item.status].map(value => String(value || '').trim()).filter(Boolean).join(' · '),
    })).filter(item => item.text),
    remainingCount: Math.max(0, concernCount - 2),
  };
}

function errorMessage(error, fallback) {
  const value = error || {};
  return String(value.error || value.message || fallback);
}

module.exports = {
  errorMessage,
  formatHomeDate,
  homeActions,
  homePackage,
  projectHome,
  projectIdentity,
  projectReagentSummary,
  projectReview,
  projectStationSummary,
  projectUnread
};
