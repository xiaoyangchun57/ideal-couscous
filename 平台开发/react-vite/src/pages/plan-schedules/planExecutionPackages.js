const PLAN_EXECUTION_STATUS = {
  pending: { label: '待执行', color: 'default' },
  partial: { label: '部分完成', color: 'processing' },
  completed: { label: '已完成', color: 'success' },
  change_pending: { label: '变更待审', color: 'warning' },
  rework: { label: '需整改', color: 'warning' },
};

export function planExecutionPresentation(plan = {}) {
  let key = PLAN_EXECUTION_STATUS[plan.execution_status] ? plan.execution_status : '';
  if (!key && ['modifying', 'change_submitted'].includes(plan.status)) key = 'change_pending';
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
