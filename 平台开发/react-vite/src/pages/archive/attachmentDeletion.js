export function hasAdminRole(user) {
  const roles = Array.isArray(user?.roles) ? user.roles : [user?.role];
  return roles.includes('admin');
}

export function hasReviewerRole(user) {
  const roles = Array.isArray(user?.roles) ? user.roles : [user?.role];
  return roles.includes('admin') || roles.includes('reviewer');
}

export function isFormalAttachment(target) {
  const formalSources = new Set([
    'workorder', 'inspection', 'patrol', 'site_photo', 'calibration',
    'reagent', 'vehicle', 'maintenance', 'manual_report',
  ]);
  return Boolean(
    target?.association_status === 'linked'
      || formalSources.has(String(target?.source_type || '').toLowerCase())
      || target?.requirement_id,
  );
}

export function hasPositiveBusinessId(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0;
}

export function archivePrimaryTitle(target) {
  return target?.item_name || '检查项待确认';
}

export function archiveSecondaryMeta(target) {
  return [target?.site_name, target?.category || target?.source_type, target?.taken_at || target?.created_at]
    .filter(Boolean);
}

export function attachmentResourceState(target, failed = false) {
  return { failed: Boolean(failed), imageAlt: failed ? '' : (target?.filename || ''), downloadDisabled: Boolean(failed), label: failed ? '文件不可用' : '' };
}

export function archiveStatusLayout() {
  return {
    statusColumnWidth: 176,
    actionColumnWidth: 176,
    tableMinWidth: 1180,
    tagStyle: { whiteSpace: 'normal', overflowWrap: 'anywhere', height: 'auto', lineHeight: '18px', marginInlineEnd: 0 },
  };
}

export function voidEligibility(target, user, submitting = false) {
  if (submitting) return { allowed: false, reason: '正在提交作废请求' };
  if (!hasReviewerRole(user)) return { allowed: false, reason: '当前账号没有影像审核权限' };
  if (target?.review_status !== 'approved') return { allowed: false, reason: '仅已通过的正式证据可以作废' };
  if (target?.association_status !== 'linked') return { allowed: false, reason: '该影像未可信关联检查项，不能作废证据' };
  if (target?.archived === true || target?.archived === 1 || target?.archived === '1') return { allowed: false, reason: '已归档业务不能在此处作废' };
  return { allowed: true, reason: '' };
}

export function canVoidAttachment(target, user, submitting = false) {
  return voidEligibility(target, user, submitting).allowed;
}

export function attachmentDeletionDialogMode(target) {
  if (target?.can_delete === true) return 'ordinary';
  if (target?.can_delete === false) return isFormalAttachment(target) ? 'void' : 'blocked';
  return 'checking';
}

export function attachmentPrimaryStatus(target) {
  return target?.review_status || 'pending';
}

export function normalizeDeleteReason(value) {
  return typeof value === 'string' ? value.trim() : '';
}

export function canSubmitAttachmentDelete(target, reason, submitting = false) {
  return Boolean(target?.can_delete && !submitting && normalizeDeleteReason(reason));
}
