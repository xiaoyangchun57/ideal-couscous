const assert = require('node:assert/strict');
const test = require('node:test');

const configPath = require.resolve('../utils/config.js');
const authPath = require.resolve('../utils/auth.js');
const requestPath = require.resolve('../utils/request.js');

test('local API failure is actionable, never retries against production, and never queues writes', async () => {
  const urls = [];
  const storage = {
    token: 'local-only-token',
    user: { id: 7 },
  };
  global.getApp = () => ({ globalData: {} });
  global.wx = {
    getSystemInfoSync: () => ({ platform: 'devtools' }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: key => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value; },
    removeStorageSync: key => { delete storage[key]; },
    request(options) {
      urls.push(options.url);
      options.fail({ errMsg: 'request:fail connect ECONNREFUSED' });
    },
  };
  delete require.cache[configPath];
  delete require.cache[authPath];
  delete require.cache[requestPath];
  const { request, getAllQueue } = require(requestPath);

  await assert.rejects(
    request('/api/mobile/my-today', 'GET', null, { retry: 0, queue: false }),
    error => error.code === 'LOCAL_API_UNAVAILABLE'
      && /\u542f\u52a8\u672c\u5730\u670d\u52a1/.test(error.error),
  );
  assert.deepEqual(urls, ['http://192.168.2.107:5000/api/mobile/my-today']);
  await assert.rejects(
    request('/api/mobile/check-in', 'POST', { site_id: 1 }, { retry: 0 }),
    error => error.code === 'LOCAL_API_UNAVAILABLE' && error.queued === false,
  );
  assert.deepEqual(getAllQueue(), []);

  delete require.cache[configPath];
  delete require.cache[authPath];
  delete require.cache[requestPath];
  delete global.getApp;
  delete global.wx;
});

test('API profiles isolate legacy and cross-profile queue tasks', async () => {
  const urls = [];
  const storage = {
    token: 'online-token', user: { id: 7 },
    fail_queue: [
      { ownerUserId: '7', url: '/api/local-write', method: 'POST', data: {}, api_profile: 'local' },
      { ownerUserId: '7', url: '/api/legacy-write', method: 'POST', data: {} },
      { ownerUserId: '7', url: '/api/online-write', method: 'POST', data: {}, api_profile: 'online' },
    ],
  };
  global.getApp = () => ({ globalData: {} });
  global.wx = {
    getSystemInfoSync: () => ({ platform: 'devtools' }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'trial' } }),
    getStorageSync: key => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value; },
    removeStorageSync: key => { delete storage[key]; },
    request(options) {
      urls.push(options.url);
      options.success({ statusCode: 200, data: { success: true } });
    },
  };
  delete require.cache[configPath]; delete require.cache[authPath]; delete require.cache[requestPath];
  const { flushQueue, getQueue } = require(requestPath);

  const result = await flushQueue();

  assert.deepEqual(urls, ['https://ops.hhyc-tec.cn/api/online-write']);
  assert.equal(result.synced, 1);
  assert.equal(result.isolated.length, 2);
  assert.deepEqual(getQueue().map(task => task.url).sort(), ['/api/legacy-write', '/api/local-write']);
  delete require.cache[configPath]; delete require.cache[authPath]; delete require.cache[requestPath];
  delete global.getApp; delete global.wx;
});
