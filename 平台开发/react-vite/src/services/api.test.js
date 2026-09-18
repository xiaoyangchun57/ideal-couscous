import test from 'node:test';
import assert from 'node:assert/strict';
import { api, ApiError } from './api.js';
import {
  buildLoginUrl,
  buildProtectedLoginUrl,
  getLoginReasonMessage,
  getSafeReturnTo,
  isSafeReturnTo,
  sessionReasonFromCode,
} from '../utils/authNavigation.js';

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
const originalLocalStorage = globalThis.localStorage;

function response(body, { status = 200, contentType = 'application/json', headers = {} } = {}) {
  return new Response(contentType.includes('json') && typeof body !== 'string' ? JSON.stringify(body) : body, {
    status,
    headers: { 'content-type': contentType, ...headers },
  });
}

test.beforeEach(() => {
  const values = new Map([['water_ops_token', 'test-token']]);
  globalThis.localStorage = {
    getItem: (key) => values.get(key) || null,
    removeItem: (key) => values.delete(key),
  };
  globalThis.window = { location: { pathname: '/sites', search: '?q=青云', hash: '', assign() {} } };
});

test.after(() => {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
  globalThis.localStorage = originalLocalStorage;
});

test('getStrict returns JSON and sends the bearer token', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/example');
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    return response({ value: 1 });
  };
  assert.deepEqual(await api.getStrict('/example'), { value: 1 });
});

test('getStrict exposes backend error metadata', async () => {
  globalThis.fetch = async () => response(
    { error: '参数无效', code: 'INVALID_INPUT', request_id: 'body-id' },
    { status: 400, headers: { 'x-request-id': 'header-id' } },
  );
  await assert.rejects(api.getStrict('/example'), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, '参数无效');
    assert.equal(error.status, 400);
    assert.equal(error.code, 'INVALID_INPUT');
    assert.equal(error.requestId, 'body-id');
    assert.equal(error.retryable, false);
    return true;
  });
});

test('getStrict hides non-JSON server bodies while preserving diagnostic metadata', async () => {
  const html = '<!doctype html><title>405 Method Not Allowed</title><pre>C:\\internal\\server.py</pre>';
  globalThis.fetch = async () => response(html, {
    status: 405,
    contentType: 'text/html; charset=utf-8',
    headers: { 'x-request-id': 'preview-405-request' },
  });
  await assert.rejects(api.getStrict('/example'), (error) => {
    assert.equal(error.message, '服务接口暂不可用，请刷新页面后重试（HTTP 405）');
    assert.doesNotMatch(error.message, /doctype|internal|server\.py|Method Not Allowed/i);
    assert.equal(error.status, 405);
    assert.equal(error.requestId, 'preview-405-request');
    assert.equal(error.retryable, false);
    return true;
  });
});

test('getStrict keeps a JSON business block reason unchanged', async () => {
  globalThis.fetch = async () => response(
    { error: '所选影像中有当前有效证据，不可彻底清理', code: 'BATCH_PURGE_BLOCKED' },
    { status: 409 },
  );
  await assert.rejects(api.postStrict('/attachments/purge-batch/preview', { attachment_ids: [7] }), (error) => {
    assert.equal(error.message, '所选影像中有当前有效证据，不可彻底清理');
    assert.equal(error.code, 'BATCH_PURGE_BLOCKED');
    assert.equal(error.status, 409);
    return true;
  });
});

test('getStrict rejects malformed successful JSON', async () => {
  globalThis.fetch = async () => response('{broken', { contentType: 'application/json' });
  await assert.rejects(api.getStrict('/example'), (error) => {
    assert.equal(error.code, 'INVALID_JSON_RESPONSE');
    assert.equal(error.status, 200);
    assert.equal(error.retryable, true);
    return true;
  });
});

test('getStrict distinguishes a network failure', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
  await assert.rejects(api.getStrict('/example'), (error) => {
    assert.equal(error.code, 'NETWORK_ERROR');
    assert.equal(error.retryable, true);
    return true;
  });
});

test('getStrict distinguishes a timeout', async () => {
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    });
  });
  await assert.rejects(api.getStrict('/example', 5), (error) => {
    assert.equal(error.code, 'REQUEST_TIMEOUT');
    assert.equal(error.retryable, true);
    return true;
  });
});

test('monitoring directory encodes scope without losing caller cancellation', async () => {
  for (const scope of ['all', 'mine', 'scope & injected=all']) {
    const controller = new AbortController();
    let requested;
    globalThis.fetch = (url, { signal }) => {
      requested = url;
      return new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
        const error = new Error('cancelled');
        error.name = 'AbortError';
        reject(error);
      }));
    };
    const pending = api.stationMonitoringSites({ scope, signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, (error) => error.code === 'REQUEST_ABORTED');
    const query = new URL(requested, 'http://web-test.invalid').searchParams;
    assert.equal(query.get('scope'), scope);
    assert.equal(query.has('injected'), false);
  }
});

test('monitoring services propagate scope denial and missing sites without a fallback request', async () => {
  for (const status of [403, 404]) {
    const urls = [];
    globalThis.fetch = async (url) => {
      urls.push(url);
      return response({ error: '站点不可访问', code: `MONITORING_${status}` }, { status });
    };
    await assert.rejects(api.stationMonitoringOverview(7), (error) => error.status === status);
    assert.deepEqual(urls, ['/api/station-monitoring/sites/7/overview']);
  }
});

test('monitoring trend encodes the opaque metric and preserves caller cancellation', async () => {
  const controller = new AbortController();
  let requested;
  globalThis.fetch = (url, { signal }) => {
    requested = url;
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
      const error = new Error('cancelled');
      error.name = 'AbortError';
      reject(error);
    }));
  };
  const pending = api.stationMonitoringTrend(7, 'ph & injected=other', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (error) => error.code === 'REQUEST_ABORTED');
  const url = new URL(requested, 'http://web-test.invalid');
  assert.equal(url.pathname, '/api/station-monitoring/sites/7/trend');
  assert.equal(url.searchParams.get('metric'), 'ph & injected=other');
  assert.equal(url.searchParams.has('injected'), false);
});

test('getStrict distinguishes caller cancellation from timeout', async () => {
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    });
  });
  const controller = new AbortController();
  const pending = api.getStrict('/example', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (error) => {
    assert.equal(error.code, 'REQUEST_ABORTED');
    assert.equal(error.retryable, false);
    return true;
  });
});

test('getStrict clears authentication and redirects on 401', async () => {
  let redirectedTo = '';
  globalThis.window.location.assign = (path) => { redirectedTo = path; };
  globalThis.fetch = async () => response({ error: '登录已过期', code: 'SESSION_EXPIRED' }, { status: 401 });
  await assert.rejects(api.getStrict('/example'), (error) => error.status === 401);
  assert.equal(localStorage.getItem('water_ops_token'), null);
  assert.equal(redirectedTo, '/login?reason=session_expired&returnTo=%2Fsites%3Fq%3D%E9%9D%92%E4%BA%91');
});

test('downloadStrict returns the blob and server filename after an authenticated success', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/export/report');
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    return response('xlsx-data', {
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers: { 'content-disposition': "attachment; filename*=UTF-8''%E8%BF%90%E7%BB%B4%E6%8A%A5%E5%91%8A.xlsx" },
    });
  };
  const result = await api.downloadStrict('/export/report');
  assert.equal(result.filename, '运维报告.xlsx');
  assert.equal(await result.blob.text(), 'xlsx-data');
});

test('downloadStrict rejects an error response instead of creating a file', async () => {
  globalThis.fetch = async () => response({ error: '当前没有可导出的记录' }, { status: 409 });
  await assert.rejects(api.downloadStrict('/export/report'), (error) => {
    assert.equal(error.message, '当前没有可导出的记录');
    assert.equal(error.status, 409);
    return true;
  });
});

test('401 without an existing session does not redirect the login request', async () => {
  localStorage.removeItem('water_ops_token');
  let redirectedTo = '';
  globalThis.window.location.pathname = '/login';
  globalThis.window.location.search = '';
  globalThis.window.location.assign = (path) => { redirectedTo = path; };
  globalThis.fetch = async () => response({ error: '用户名或密码错误' }, { status: 401 });
  assert.equal(await api.post('/auth/login', { username: 'x', password: 'bad' }), null);
  assert.equal(redirectedTo, '');
});

test('return paths accept only local non-login routes', () => {
  assert.equal(isSafeReturnTo('/audit?tab=workorder'), true);
  assert.equal(isSafeReturnTo('//evil.example/path'), false);
  assert.equal(isSafeReturnTo('https://evil.example/path'), false);
  assert.equal(isSafeReturnTo('/login?returnTo=/sites'), false);
  assert.equal(getSafeReturnTo('?returnTo=%2Fsites%3Fq%3Dtest'), '/sites?q=test');
  assert.equal(getSafeReturnTo('?returnTo=https%3A%2F%2Fevil.example'), '/');
  assert.equal(buildLoginUrl('/sites?q=test', 'session_revoked'), '/login?reason=session_revoked&returnTo=%2Fsites%3Fq%3Dtest');
  assert.equal(buildLoginUrl('', ''), '/login');
  assert.equal(buildProtectedLoginUrl('/'), '/login');
  assert.equal(buildProtectedLoginUrl('/audit?tab=workorder'), '/login?reason=authentication_required&returnTo=%2Faudit%3Ftab%3Dworkorder');
  assert.equal(getLoginReasonMessage(''), '');
  assert.match(getLoginReasonMessage('?reason=session_expired'), /登录已过期/);
  assert.match(getLoginReasonMessage('?reason=session_revoked'), /账号状态或密码/);
  assert.equal(sessionReasonFromCode('SESSION_EXPIRED'), 'session_expired');
  assert.equal(sessionReasonFromCode('SESSION_REVOKED'), 'session_revoked');
});
