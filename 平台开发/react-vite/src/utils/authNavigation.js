const LOGIN_REASONS = {
  authentication_required: '请先登录。登录后将返回刚才的页面。',
  session_expired: '登录已过期，请重新登录。登录后将返回刚才的页面。',
  session_revoked: '当前登录已失效，可能因为账号状态或密码发生变化。请重新登录；如仍无法登录，请联系管理员。',
  session_invalid: '登录状态已失效，请重新登录。登录后将返回刚才的页面。',
};

export function isSafeReturnTo(value) {
  return typeof value === 'string'
    && value.startsWith('/')
    && !value.startsWith('//')
    && !value.startsWith('/login');
}

export function getSafeReturnTo(search, fallback = '/') {
  const value = new URLSearchParams(search || '').get('returnTo');
  return isSafeReturnTo(value) ? value : fallback;
}

export function getLoginReasonMessage(search) {
  const reason = new URLSearchParams(search || '').get('reason');
  return LOGIN_REASONS[reason] || '';
}

export function buildLoginUrl(returnTo, reason = 'authentication_required') {
  const params = new URLSearchParams();
  if (reason) params.set('reason', reason);
  if (isSafeReturnTo(returnTo)) params.set('returnTo', returnTo);
  return `/login?${params.toString()}`;
}

export function sessionReasonFromCode(code) {
  if (code === 'SESSION_EXPIRED') return 'session_expired';
  if (code === 'SESSION_REVOKED') return 'session_revoked';
  return 'session_invalid';
}
