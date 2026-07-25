const API_BASE = '/api';

function getToken() {
  try { return localStorage.getItem('water_ops_token') || ''; } catch { return ''; }
}

function authHeaders() {
  const h = {};
  const t = getToken();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

function handle401() {
  try { localStorage.removeItem('water_ops_token'); } catch { /* ignore */ }
  if (window.location.pathname !== '/login') window.location.assign('/login');
}

async function request(url, options = {}) {
  const { method = 'GET', body, timeout = 30000 } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const headers = { ...authHeaders() };
    if (body && typeof body === 'object') {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(`${API_BASE}${url}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    if (res.status === 401) {
      handle401();
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

// 埋点：对齐小程序 api.trackEvent 契约（/api/telemetry/events）。
// 本地时间格式 YYYY-MM-DD HH:MM:SS，与 baseline 查询窗口一致。
function _localStamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export const api = {
  get: (url, timeout) => request(url, { timeout }),
  post: (url, data, timeout) => request(url, { method: 'POST', body: data, timeout }),
  put: (url, data, timeout) => request(url, { method: 'PUT', body: data, timeout }),
  delete: (url, timeout) => request(url, { method: 'DELETE', timeout }),
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
