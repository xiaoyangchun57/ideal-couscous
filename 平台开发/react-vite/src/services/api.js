import { buildLoginUrl, sessionReasonFromCode } from '../utils/authNavigation.js';

const API_BASE = '/api';

export class ApiError extends Error {
  constructor(message, {
    status = 0,
    code = '',
    requestId = '',
    retryable = false,
    cause,
  } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}

function getToken() {
  try { return localStorage.getItem('water_ops_token') || ''; } catch { return ''; }
}

function authHeaders() {
  const h = {};
  const t = getToken();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

function handle401(code = '') {
  const hadSession = !!getToken();
  try { localStorage.removeItem('water_ops_token'); } catch { /* ignore */ }
  if (!hadSession || window.location.pathname === '/login') return;
  const returnTo = `${window.location.pathname}${window.location.search || ''}${window.location.hash || ''}`;
  window.location.assign(buildLoginUrl(returnTo, sessionReasonFromCode(code)));
}

async function request(url, options = {}) {
  const { method = 'GET', body, timeout = 30000 } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const headers = { ...authHeaders() };
    if (body && typeof body === 'object' && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(`${API_BASE}${url}`, {
      method,
      headers,
      body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined,
      signal: controller.signal,
    });

    if (res.status === 401) {
      let payload = null;
      try { payload = await res.json(); } catch { /* use generic session reason */ }
      handle401(payload?.code);
      return null;
    }

    const ct = (res.headers.get('content-type') || '').toLowerCase();
    if (ct.includes('application/json')) {
      return await res.json();
    }
    const text = await res.text();
    return res.ok ? { success: true } : { error: text.substring(0, 100) };
  } catch (e) {
    console.error(`API ${method} ${url}:`, e);
    return method === 'GET' ? null : { error: String(e) };
  } finally {
    clearTimeout(timer);
  }
}

async function strictRequest(url, options = {}) {
  const { method = 'GET', body, timeout = 30000 } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const headers = { ...authHeaders() };
    if (body && typeof body === 'object' && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(`${API_BASE}${url}`, {
      method,
      headers,
      body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined,
      signal: controller.signal,
    });
    const contentType = (res.headers.get('content-type') || '').toLowerCase();
    const requestId = res.headers.get('x-request-id') || res.headers.get('x-correlation-id') || '';
    let payload;
    let invalidJson = false;

    if (contentType.includes('application/json')) {
      try {
        payload = await res.json();
      } catch {
        payload = null;
        invalidJson = true;
      }
    } else {
      const text = await res.text();
      payload = text ? { error: text.substring(0, 200) } : null;
    }

    if (res.status === 401) handle401(payload?.code);

    if (!res.ok) {
      const message = payload?.error || payload?.message || `请求失败（HTTP ${res.status}）`;
      throw new ApiError(message, {
        status: res.status,
        code: payload?.code || '',
        requestId: payload?.request_id || requestId,
        retryable: res.status === 408 || res.status === 429 || res.status >= 500,
      });
    }

    if (invalidJson) {
      throw new ApiError('服务器返回数据格式异常，请稍后重试', {
        status: res.status,
        code: 'INVALID_JSON_RESPONSE',
        requestId,
        retryable: true,
      });
    }

    if (payload !== null && payload !== undefined) return payload;
    return { success: true };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    const timedOut = error?.name === 'AbortError';
    throw new ApiError(
      timedOut ? '请求超时，请检查网络后重试' : '网络连接失败，请检查网络后重试',
      { code: timedOut ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR', retryable: true, cause: error },
    );
  } finally {
    clearTimeout(timer);
  }
}

async function strictDownload(url, timeout = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(`${API_BASE}${url}`, {
      headers: authHeaders(),
      signal: controller.signal,
    });
    const requestId = res.headers.get('x-request-id') || res.headers.get('x-correlation-id') || '';
    if (res.status === 401) handle401();
    if (!res.ok) {
      let message = `下载失败（HTTP ${res.status}）`;
      try {
        const payload = await res.json();
        message = payload?.error || payload?.message || message;
      } catch { /* keep HTTP message */ }
      throw new ApiError(message, {
        status: res.status,
        requestId,
        retryable: res.status === 408 || res.status === 429 || res.status >= 500,
      });
    }
    const disposition = res.headers.get('content-disposition') || '';
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
    return {
      blob: await res.blob(),
      filename: encoded ? decodeURIComponent(encoded) : plain || '',
    };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    const timedOut = error?.name === 'AbortError';
    throw new ApiError(
      timedOut ? '下载超时，请检查网络后重试' : '网络连接失败，文件未下载',
      { code: timedOut ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR', retryable: true, cause: error },
    );
  } finally {
    clearTimeout(timer);
  }
}

// 埋点：对齐小程序 api.trackEvent 契约（/api/telemetry/events）。
// 本地时间格式 YYYY-MM-DD HH:MM:SS，与 baseline 查询窗口一致。
function _localStamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export const api = {
  get: (url, timeout) => request(url, { timeout }),
  post: (url, data, timeout) => request(url, { method: 'POST', body: data, timeout }),
  postForm: (url, data, timeout) => request(url, { method: 'POST', body: data, timeout }),
  put: (url, data, timeout) => request(url, { method: 'PUT', body: data, timeout }),
  delete: (url, timeout) => request(url, { method: 'DELETE', timeout }),
  getStrict: (url, timeout) => strictRequest(url, { timeout }),
  postStrict: (url, data, timeout) => strictRequest(url, { method: 'POST', body: data, timeout }),
  postFormStrict: (url, data, timeout) => strictRequest(url, { method: 'POST', body: data, timeout }),
  putStrict: (url, data, timeout) => strictRequest(url, { method: 'PUT', body: data, timeout }),
  deleteStrict: (url, timeout) => strictRequest(url, { method: 'DELETE', timeout }),
  downloadStrict: (url, timeout) => strictDownload(url, timeout),
  track: (eventName, context = {}, eventId = null) => request('/telemetry/events', {
    method: 'POST',
    body: {
      event_id: eventId || ('evt_' + Date.now() + '_' + Math.floor(Math.random() * 1e6)),
      event_name: eventName,
      occurred_at: _localStamp(),
      context: context || {},
    },
  }),
  trackEvent: (eventName, context) => api.track(eventName, context),
};
