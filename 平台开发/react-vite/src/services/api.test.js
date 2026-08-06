import test from 'node:test';
import assert from 'node:assert/strict';
import { api, ApiError } from './api.js';
import {
  buildLoginUrl,
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

test('getStrict handles a non-JSON server error as retryable', async () => {
  globalThis.fetch = async () => response('service unavailable', { status: 503, contentType: 'text/plain' });
  await assert.rejects(api.getStrict('/example'), (error) => {
    assert.equal(error.message, 'service unavailable');
    assert.equal(error.status, 503);
    assert.equal(error.retryable, true);
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
  assert.match(getLoginReasonMessage('?reason=session_expired'), /登录已过期/);
  assert.match(getLoginReasonMessage('?reason=session_revoked'), /账号状态或密码/);
  assert.equal(sessionReasonFromCode('SESSION_EXPIRED'), 'session_expired');
  assert.equal(sessionReasonFromCode('SESSION_REVOKED'), 'session_revoked');
});
