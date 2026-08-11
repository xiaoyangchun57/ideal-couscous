export function hasAdminRole(user) {
  const roles = Array.isArray(user?.roles) ? user.roles : [user?.role];
  return roles.includes('admin');
}

export function normalizeDeleteReason(value) {
  return typeof value === 'string' ? value.trim() : '';
}

export function canSubmitAttachmentDelete(target, reason, submitting = false) {
  return Boolean(target?.can_delete && !submitting && normalizeDeleteReason(reason));
}
