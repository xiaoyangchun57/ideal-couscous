const PLAN_EXECUTION_STATUS = {
  pending: { label: '待执行', color: 'default' },
  partial: { label: '部分完成', color: 'processing' },
  completed: { label: '已完成', color: 'success' },
  change_pending: { label: '变更待审', color: 'warning' },
  rework: { label: '需整改', color: 'warning' },
  modifying: { label: '变更中，暂缓执行', color: 'warning' },
  cancelled: { label: '已取消', color: 'default' },
};

function userRoles(user = {}) {
  const actor = user || {};
  const roles = Array.isArray(actor.roles) ? actor.roles.filter(Boolean) : [];
  if (roles.length) return roles;
  return actor.role ? [actor.role] : [];
}

export function canCancelPlanSchedule(plan = {}, user = {}) {
  const schedule = plan || {};
  const actor = user || {};
  if (!['approved', 'modifying'].includes(schedule.status)) return false;
  return Number(schedule.user_id) === Number(actor.id) || userRoles(actor).includes('admin');
}

export function normalizePlanCancelReason(rawReason) {
  const value = String(rawReason || '').trim();
  if (!value) return { value, error: '请填写取消原因' };
  if (value.length > 500) return { value, error: '取消原因不能超过500字' };
  return { value, error: '' };
}

export function normalizePlanPurgeReason(rawReason) {
  const value = String(rawReason || '').trim();
  if (!value) return { value, error: '请填写彻底删除原因' };
  if (value.length > 500) return { value, error: '彻底删除原因不能超过500字' };
  return { value, error: '' };
}

export function planExecutionPresentation(plan = {}) {
  if (plan.status === 'cancelled') return { key: 'cancelled', ...PLAN_EXECUTION_STATUS.cancelled };
  if (plan.status === 'modifying') return { key: 'modifying', ...PLAN_EXECUTION_STATUS.modifying };
  if (plan.status === 'change_submitted') return { key: 'change_pending', ...PLAN_EXECUTION_STATUS.change_pending };
  let key = PLAN_EXECUTION_STATUS[plan.execution_status] ? plan.execution_status : '';
  if (!key && plan.field_status === 'completed') key = 'completed';
  if (!key && plan.field_status === 'rework') key = 'rework';
  if (!key) key = 'pending';
  const presentation = PLAN_EXECUTION_STATUS[key];
  return {
    key,
    label: plan.execution_status_cn || presentation.label,
    color: presentation.color,
  };
}

export function groupExecutionPackagesByDate(tasks = []) {
  const byDate = {};
  tasks.forEach(task => {
    const date = task.date || '';
    byDate[date] ||= new Map();
    if (!byDate[date].has(task.plan_id)) byDate[date].set(task.plan_id, []);
    byDate[date].get(task.plan_id).push(task);
  });
  return Object.fromEntries(Object.entries(byDate).map(([date, packages]) => [
    date,
    [...packages.entries()].map(([planId, packageTasks]) => ({
      ...packageTasks[0],
      plan_id: planId,
      site_count: new Set(packageTasks.map(task => task.site_id)).size,
      can_handle_overdue: packageTasks.some(task =>
        ['pending', 'partial'].includes(task.status) && task.overdue),
    })),
  ]));
}

export function planDetailItineraryRows(planData = {}, generatedTasks = []) {
  const tasksByDate = new Map();
  (Array.isArray(generatedTasks) ? generatedTasks : []).forEach(task => {
    const date = task?.date || '';
    if (!date) return;
    if (!tasksByDate.has(date)) tasksByDate.set(date, new Map());
    const siteKey = task.site_id === undefined || task.site_id === null
      ? `name:${task.site_name || ''}`
      : `id:${task.site_id}`;
    if (!tasksByDate.get(date).has(siteKey)) tasksByDate.get(date).set(siteKey, task);
  });

  const dates = new Set([
    ...Object.keys(planData || {}),
    ...tasksByDate.keys(),
  ]);
  return [...dates].sort().map(date => {
    const day = planData?.[date] || {};
    const tasks = [...(tasksByDate.get(date)?.values() || [])];
    const plannedSiteIds = tasks.length > 0
      ? []
      : [...new Set(Array.isArray(day.sites) ? day.sites : [])];
    return {
      date,
      notes: day.notes || '',
      planned_site_ids: plannedSiteIds,
      tasks,
    };
  }).filter(row => row.tasks.length > 0 || row.planned_site_ids.length > 0);
}

export function shouldShowPreExecutionRisks(plan = {}) {
  if (['archived', 'completed'].includes(plan.status)) return false;
  return planExecutionPresentation(plan).key !== 'completed';
}

export function hasMeaningfulSitePriority(score, reasons = []) {
  return Number(score || 0) > 0
    || (Array.isArray(reasons) && reasons.some(reason => String(reason || '').trim()));
}

export function shouldShowSitePriority(showPreExecutionRisks, score, reasons = []) {
  return Boolean(showPreExecutionRisks) && hasMeaningfulSitePriority(score, reasons);
}

export function sitePriorityPresentation(showPreExecutionRisks, score, reasons = []) {
  const numericScore = Number(score || 0);
  const realReasons = Array.isArray(reasons)
    ? reasons.map(reason => String(reason || '').trim()).filter(Boolean)
    : [];
  if (!shouldShowSitePriority(showPreExecutionRisks, numericScore, realReasons)) return null;
  if (numericScore > 0) {
    const level = numericScore >= 30 ? '高' : numericScore >= 15 ? '中' : '低';
    return {
      label: `优先级${level}`,
      tone: 'priority',
      score: numericScore,
      reasons: realReasons,
      tooltip: `优先级评分 ${numericScore}${realReasons.length ? `：${realReasons.join('；')}` : ''}`,
    };
  }
  return {
    label: '有关注项',
    tone: 'warning',
    score: 0,
    reasons: realReasons,
    tooltip: realReasons.join('；'),
  };
}

export function itineraryRowSiteIds(row = {}) {
  const values = Array.isArray(row.tasks) && row.tasks.length > 0
    ? row.tasks.map(task => task?.site_id)
    : (Array.isArray(row.planned_site_ids) ? row.planned_site_ids : []);
  const unique = new Map();
  values.forEach(siteId => {
    if (siteId === undefined || siteId === null || siteId === '') return;
    if (!unique.has(String(siteId))) unique.set(String(siteId), siteId);
  });
  return [...unique.values()];
}
