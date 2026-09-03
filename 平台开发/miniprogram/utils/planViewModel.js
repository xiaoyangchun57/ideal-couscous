const TONE_CLS = {
  neutral: 'gray',
  info: 'blue',
  warning: 'orange',
  success: 'green',
  danger: 'red'
};

const TYPE_LABEL = {
  weekly: '周检',
  monthly: '月检',
  quarterly: '季检',
  yearly: '年检'
};

function optionalCount(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
}

function validId(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function planTypeLabel(value) {
  return TYPE_LABEL[String(value || '').toLowerCase()] || '巡检';
}

function planStatusInfo(plan) {
  const value = plan || {};
  const status = String(value.status || '').toLowerCase();
  const executionStatus = resolveExecutionStatus(value);

  // Plan terminal states take precedence over any stale execution projection.
  if (status === 'archived') return { label: '已归档', tone: 'neutral', group: 'done' };
  if (status === 'cancelled') return { label: '已取消', tone: 'neutral', group: 'done' };
  if (status === 'draft') return { label: '草稿', tone: 'neutral', group: 'draft' };
  if (status === 'submitted') return { label: '待审批', tone: 'warning', group: 'active' };
  if (status === 'rejected') return { label: '已退回', tone: 'warning', group: 'active' };
  if (status === 'modifying') return { label: '变更中', tone: 'warning', group: 'active' };
  if (status === 'change_submitted' || executionStatus === 'change_pending') {
    return { label: '变更待审', tone: 'warning', group: 'active' };
  }
  if (status === 'approved') {
    if (executionStatus === 'completed') return { label: '已完成', tone: 'success', group: 'done' };
    if (executionStatus === 'rework') return { label: '需整改', tone: 'warning', group: 'active' };
    if (executionStatus === 'partial') return { label: '执行中', tone: 'info', group: 'active' };
    return { label: '待执行', tone: 'info', group: 'active' };
  }
  return { label: '状态待确认', tone: 'neutral', group: 'active' };
}

function planDisplayName(plan, typeLabel) {
  const value = plan || {};
  const explicitName = String(value.plan_name || '').trim();
  if (explicitName) return explicitName;
  const owner = String(value.user_name || '').trim() || '负责人未记录';
  const start = String(value.period_start || '').replace(/-/g, '');
  const end = String(value.period_end || '').replace(/-/g, '');
  const period = start && end && start !== end ? `${start}~${end}` : (start || end || '未定周期');
  return `${owner}·${typeLabel || planTypeLabel(value.schedule_type)}-${period}`;
}

function planPeriodText(plan) {
  const value = plan || {};
  const start = String(value.period_start || '').trim();
  const end = String(value.period_end || '').trim();
  if (start && end && start !== end) return `${start} ~ ${end}`;
  return start || end || '周期未记录';
}

function statusNoteContext(plan) {
  const value = plan || {};
  const summary = value.execution_summary || {};
  const totalTasks = optionalCount(summary.total_tasks);
  const completedTasks = optionalCount(summary.completed_tasks);
  return {
    value,
    summary,
    totalTasks,
    completedTasks,
    supplementRequiredItems: optionalCount(summary.supplement_required_items),
    hasTrustedProgress: summary.available === true
      && totalTasks !== null && totalTasks > 0
      && completedTasks !== null && completedTasks <= totalTasks,
    hasTrustedRework: summary.available === true
      && optionalCount(summary.supplement_required_items) !== null
  };
}

const APPROVED_EXECUTION_STATUS_NOTES = {
  pending: () => ({ text: '等待现场执行', tone: 'info' }),
  partial: context => (context.hasTrustedProgress
    ? { text: `${context.completedTasks}/${context.totalTasks}个站点任务已完成`, tone: 'info' }
    : { text: '现场执行中', tone: 'info' }),
  rework: context => (context.hasTrustedRework && context.supplementRequiredItems > 0
    ? { text: `${context.supplementRequiredItems}项需补拍`, tone: 'warning' }
    : { text: '请按审核意见完成整改', tone: 'warning' }),
  completed: () => ({ text: '全部站点任务已完成', tone: 'success' }),
  change_pending: () => ({ text: '等待变更审批', tone: 'info' })
};

const SCHEDULE_STATUS_NOTES = {
  draft: () => ({ text: '尚未提交审批', tone: 'neutral' }),
  submitted: () => ({ text: '等待管理员审批', tone: 'info' }),
  rejected: context => {
    const reason = String(context.value.reject_reason || '').trim();
    return reason
      ? { text: `退回原因：${reason}`, tone: 'warning' }
      : { text: '退回原因待补充，请联系管理员', tone: 'warning' };
  },
  modifying: () => ({ text: '计划变更编辑中', tone: 'info' }),
  change_submitted: () => ({ text: '等待变更审批', tone: 'info' }),
  cancelled: context => {
    const reason = String(context.value.cancel_reason || context.value.cancellation_reason || '').trim();
    return reason
      ? { text: `取消原因：${reason}`, tone: 'neutral' }
      : { text: '计划已取消，不再安排执行', tone: 'neutral' };
  },
  archived: () => ({ text: '已归档，可在历史记录中查看', tone: 'neutral' }),
  approved: (context, executionStatus) => {
    const rule = APPROVED_EXECUTION_STATUS_NOTES[executionStatus];
    return rule ? rule(context) : { text: '状态信息暂不可用', tone: 'neutral' };
  }
};

function resolveExecutionStatus(plan) {
  const value = plan || {};
  const executionStatus = String(value.execution_status || '').toLowerCase();
  if (executionStatus) return executionStatus;
  if (value.execution_completed === true) return 'completed';
  // 兜底：用 execution_summary 推导
  const summary = value.execution_summary || {};
  if (summary.available === true) {
    const total = optionalCount(summary.total_tasks);
    const completed = optionalCount(summary.completed_tasks);
    const rework = optionalCount(summary.supplement_required_items);
    if (total !== null && total > 0 && completed !== null) {
      if (rework !== null && rework > 0) return 'rework';
      if (completed >= total) return 'completed';
      if (completed > 0) return 'partial';
    }
  }
  return 'pending';
}

function statusNoteInfo(plan) {
  const context = statusNoteContext(plan);
  const status = String(context.value.status || '').toLowerCase();
  const executionStatus = resolveExecutionStatus(context.value);
  const rule = SCHEDULE_STATUS_NOTES[status];
  return rule ? rule(context, executionStatus) : { text: '状态信息暂不可用', tone: 'neutral' };
}

function projectPlanCard(plan) {
  const value = plan || {};
  const status = planStatusInfo(value);
  const statusNote = statusNoteInfo(value);
  const id = validId(value.id);
  const typeLabel = planTypeLabel(value.schedule_type);
  const isDraft = String(value.status || '').toLowerCase() === 'draft';
  const isRejected = String(value.status || '').toLowerCase() === 'rejected';
  const isModifying = String(value.status || '').toLowerCase() === 'modifying';
  return {
    id,
    ownerId: validId(value.user_id),
    ownerName: String(value.user_name || '').trim() || '负责人未记录',
    title: planDisplayName(value, typeLabel),
    typeLabel,
    statusLabel: status.label,
    statusTone: status.tone,
    statusCls: TONE_CLS[status.tone],
    statusNote: statusNote.text,
    statusNoteTone: statusNote.tone,
    filterGroup: status.group,
    periodLabel: planPeriodText(value),
    dayCount: optionalCount(value.day_count),
    siteCount: optionalCount(value.site_count),
    reason: isRejected ? (String(value.reject_reason || '').trim() || null) : null,
    primaryTarget: { kind: id && isDraft ? 'edit' : (id ? 'detail' : 'none'), objectId: id },
    secondaryAction: id && isDraft ? 'delete_draft' : (id && isRejected ? 'edit' : (id && isModifying ? 'continue_change' : null))
  };
}

function projectPlanCards(rows) {
  return (Array.isArray(rows) ? rows : []).map(projectPlanCard);
}

function filterPlans(plans, filter) {
  const rows = Array.isArray(plans) ? plans : [];
  return filter === 'all' ? rows : rows.filter(item => item.filterGroup === filter);
}

function projectFavoritePlans(rows) {
  return (Array.isArray(rows) ? rows : []).map(item => ({
    id: validId(item && item.id),
    name: String(item && item.name || '').trim() || '常用计划',
    typeLabel: planTypeLabel(item && item.schedule_type),
    suggestedPeriodStart: String(item && item.suggested_period_start || '').trim() || null
  })).filter(item => item.id);
}

module.exports = {
  filterPlans,
  planDisplayName,
  planPeriodText,
  statusNoteInfo,
  planStatusInfo,
  planTypeLabel,
  projectFavoritePlans,
  projectPlanCard,
  projectPlanCards
};
