export const PLAN_PURGE_AUDIT_PAGE_SIZE = 20;

function recordedText(value) {
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return '未记录';
}

export function planCancellationPresentation(schedule) {
  if (schedule?.status !== 'cancelled') return null;
  const cancellation = schedule?.cancellation && typeof schedule.cancellation === 'object'
    ? schedule.cancellation
    : {};
  return {
    reason: recordedText(cancellation.reason),
    operatorName: typeof cancellation.operator_name === 'string' && cancellation.operator_name.trim()
      ? cancellation.operator_name.trim()
      : cancellation.operator_id !== null && cancellation.operator_id !== undefined
        ? `用户 #${cancellation.operator_id}`
        : '未记录',
    occurredAt: recordedText(cancellation.occurred_at),
  };
}

export function planPurgeAuditPath(page, pageSize = PLAN_PURGE_AUDIT_PAGE_SIZE) {
  const normalizedPage = Number.isSafeInteger(Number(page)) && Number(page) > 0 ? Number(page) : 1;
  const normalizedPageSize = Number.isSafeInteger(Number(pageSize)) && Number(pageSize) > 0
    ? Number(pageSize)
    : PLAN_PURGE_AUDIT_PAGE_SIZE;
  const params = new URLSearchParams({ page: String(normalizedPage), page_size: String(normalizedPageSize) });
  return `/plan-schedules/purge-audits?${params.toString()}`;
}

export function normalizePlanPurgeAudits(payload) {
  if (!payload || !Array.isArray(payload.items)
    || !Number.isSafeInteger(payload.total) || payload.total < 0
    || !Number.isSafeInteger(payload.page) || payload.page < 1
    || !Number.isSafeInteger(payload.page_size) || payload.page_size < 1) {
    throw new Error('删除记录返回格式异常，请重试');
  }
  return {
    items: payload.items,
    total: payload.total,
    page: payload.page,
    pageSize: payload.page_size,
  };
}

export function planPurgeAuditPresentation(record = {}) {
  const siteIds = Array.isArray(record.site_ids) ? record.site_ids : [];
  return {
    key: `${record.plan_id ?? 'unknown'}-${record.purged_at ?? 'unknown'}`,
    planId: recordedText(record.plan_id),
    planName: recordedText(record.plan_name),
    statusBeforeDelete: recordedText(record.status_before_delete),
    ownerName: recordedText(record.owner_name),
    period: record.period_start || record.period_end
      ? `${recordedText(record.period_start)} ~ ${recordedText(record.period_end)}`
      : '未记录',
    siteCount: siteIds.length,
    reason: recordedText(record.reason),
    operatorName: recordedText(record.operator_name),
    purgedAt: recordedText(record.purged_at),
  };
}
