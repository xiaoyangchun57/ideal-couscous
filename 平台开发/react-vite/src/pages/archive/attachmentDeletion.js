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
  const values = [target?.item_name, target?.description, target?.category]
    .map((value) => String(value || '').trim())
    .filter((value) => value && !['检查项待确认', '未关联检查项'].includes(value));
  return values[0] || `影像 #${target?.id || '待确认'}`;
}

export function archiveSecondaryMeta(target) {
  return [target?.site_name, target?.category || target?.source_type,
    target?.taken_at ? `拍摄：${target.taken_at}` : '拍摄时间待确认',
    target?.created_at ? `上传：${target.created_at}` : '上传时间未记录']
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

export function archiveHistoryStatus(target, statusMap = {}) {
  if (Number(target?.is_deleted || 0) === 1) {
    return { label: '已移出', color: 'default', reason: target?.delete_reason || '' };
  }
  let materialRole = '';
  try {
    materialRole = JSON.parse(target?.extra_json || '{}').material_role || '';
  } catch {
    materialRole = '';
  }
  if (materialRole === 'supplement') {
    return {
      label: '补充材料',
      color: 'warning',
      reason: target?.evidence_reason || '该记录仅作为补充材料，不进入业务审核，也不计入当前有效档案。',
    };
  }
  if (target?.review_status === 'approved'
      && target?.evidence_qualification !== 'qualified') {
    return {
      label: '来源未通过当前规则',
      color: 'warning',
      reason: target?.evidence_reason || '该记录的来源未通过当前证据规则。',
    };
  }
  const status = statusMap[target?.review_status]
    || { label: target?.review_status_label || '待所属业务审核', color: 'processing' };
  const reason = target?.review_status === 'voided'
    ? (target?.void_reason || target?.reject_reason || target?.evidence_reason || '')
    : (target?.reject_reason || target?.evidence_reason || '');
  return { ...status, reason };
}

export function voidEligibility(target, user, submitting = false) {
  if (submitting) return { allowed: false, reason: '正在提交作废请求' };
  if (!hasReviewerRole(user)) return { allowed: false, reason: '当前账号没有影像管理权限' };
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

export function rejectedPurgeEligibility(target, user, submitting = false) {
  if (submitting) return { allowed: false, reason: '正在提交彻底删除请求' };
  if (!hasAdminRole(user)) return { allowed: false, reason: '仅管理员可彻底删除已驳回影像' };
  if (target?.source_type !== 'inspection' || target?.review_status !== 'rejected') {
    return { allowed: false, reason: '仅已驳回的巡检影像可以彻底删除' };
  }
  if (target?.association_status !== 'linked') {
    return { allowed: false, reason: '影像关联不完整，不能彻底删除' };
  }
  return { allowed: true, reason: '' };
}

export function archivePurgeEligibility(preview, user, submitting = false) {
  if (submitting) return { allowed: false, reason: '正在提交彻底清理请求' };
  if (!hasAdminRole(user)) return { allowed: false, reason: '仅管理员可彻底清理历史影像' };
  if (!preview) return { allowed: false, reason: '正在读取服务端清理资格' };
  return {
    allowed: preview.can_purge === true,
    reason: preview.block_reason || '',
  };
}
