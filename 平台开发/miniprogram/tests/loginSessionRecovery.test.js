const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const storage = {};
const launches = [];
let wxLoginCalls = 0;
const app = { globalData: {} };
let definition;

global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  getStorageSync: key => storage[key] || '',
  setStorageSync: (key, value) => { storage[key] = value; },
  removeStorageSync: key => { delete storage[key]; },
  reLaunch: options => { launches.push(options.url); },
  login: () => { wxLoginCalls += 1; },
};

const api = require('../services/api.js');
const originalRestoreSession = api.restoreSession;
require('../pages/login/login.js');

const flush = () => new Promise(resolve => setImmediate(resolve));
const createPage = () => {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = patch => Object.assign(page.data, patch);
  return page;
};
const reset = () => {
  Object.keys(storage).forEach(key => delete storage[key]);
  Object.assign(app.globalData, { token: '', user: null, sites: [] });
  launches.length = 0;
  wxLoginCalls = 0;
};

(async () => {
  try {
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/login/login.wxml'), 'utf8');
    assert.match(wxml, /wx:if="\{\{restoring\}\}"/);
    assert.match(wxml, /正在恢复登录/);
    assert.match(wxml, /bindtap="onRetryRecovery"/);
    assert.match(wxml, /bindtap="onUseAnotherAccount"/);
    assert.match(wxml, /!restoring && !recoveryFailed && !mustChangePassword/);

    reset();
    storage.token = 'api-token';
    let restoreUrl = '';
    wx.request = options => {
      restoreUrl = options.url;
      options.success({ statusCode: 401, data: { code: 'SESSION_EXPIRED' } });
    };
    await assert.rejects(originalRestoreSession(), error => error.status === 401);
    assert.equal(restoreUrl, 'http://192.168.2.107:5000/api/auth/me');
    assert.equal(storage.token, 'api-token', 'the recovery API leaves 401 cleanup to the login page');

    reset();
    let calls = 0;
    api.restoreSession = () => { calls += 1; return Promise.resolve({}); };
    const noToken = createPage();
    noToken.onLoad();
    assert.equal(noToken.data.restoring, false);
    assert.equal(calls, 0, 'no token shows the normal login form without a recovery request');

    reset();
    storage.token = 'valid-token';
    storage.user = { id: 2, roles: ['operator'] };
    api.restoreSession = () => {
      calls += 1;
      return Promise.resolve({
        user: { id: 2, role: 'operator', roles: ['operator', 'reviewer'], capabilities: { station_monitoring_public: false } },
        site_ids: [11], sites: [{ id: 11, name: '最新站点' }],
      });
    };
    const valid = createPage();
    valid.onLoad();
    assert.equal(valid.data.restoring, true);
    await flush(); await flush();
    assert.deepEqual(storage.user.roles, ['operator', 'reviewer']);
    assert.deepEqual(storage.user.capabilities, { station_monitoring_public: false });
    assert.deepEqual(storage.user.site_ids, [11]);
    assert.deepEqual(storage.sites, [{ id: 11, name: '最新站点' }]);
    assert.deepEqual(launches, ['/pages/index/index']);
    assert.equal(wxLoginCalls, 0, 'session recovery must not bind WeChat again');

    reset();
    storage.token = 'expired-token';
    storage.user = { id: 2 };
    api.restoreSession = () => Promise.reject({ status: 401, code: 'SESSION_EXPIRED' });
    const expired = createPage();
    expired.onLoad();
    await flush(); await flush();
    assert.equal(storage.token, undefined);
    assert.equal(storage.user, undefined);
    assert.equal(expired.data.restoring, false);
    assert.equal(expired.data.recoveryFailed, false);
    assert.equal(expired.data.error, '登录已失效，请重新登录');

    reset();
    storage.token = 'retained-token';
    storage.user = { id: 2, capabilities: {} };
    api.restoreSession = () => Promise.reject({ status: 503, error: '服务暂不可用' });
    const unavailable = createPage();
    unavailable.onLoad();
    await flush(); await flush();
    assert.equal(storage.token, 'retained-token');
    assert.deepEqual(storage.user, { id: 2, capabilities: {} });
    assert.equal(unavailable.data.recoveryFailed, true);
    assert.equal(unavailable.data.error, '服务暂不可用');

    reset();
    storage.token = 'network-token';
    storage.user = { id: 2 };
    api.restoreSession = () => Promise.reject({ status: 0, network: true, error: '网络异常，请检查网络后重试' });
    const networkFailure = createPage();
    networkFailure.onLoad();
    await flush(); await flush();
    assert.equal(storage.token, 'network-token');
    assert.deepEqual(storage.user, { id: 2 });
    assert.equal(networkFailure.data.recoveryFailed, true);

    api.restoreSession = () => Promise.resolve({
      user: { id: 2, roles: ['operator'], capabilities: { station_monitoring_public: false } },
      site_ids: [], sites: [],
    });
    await networkFailure.onRetryRecovery();
    assert.deepEqual(launches, ['/pages/index/index']);

    reset();
    storage.token = 'pending-token';
    let resolvePending;
    calls = 0;
    api.restoreSession = () => {
      calls += 1;
      return new Promise(resolve => { resolvePending = resolve; });
    };
    const pending = createPage();
    pending.onLoad();
    pending.recoverSession();
    assert.equal(calls, 1, 'duplicate recovery triggers share one request');
    resolvePending({ user: { id: 2, roles: ['operator'], capabilities: {} }, site_ids: [], sites: [] });
    await flush(); await flush();

    reset();
    storage.token = 'switch-token';
    storage.user = { id: 2 };
    const switchAccount = createPage();
    switchAccount.onUseAnotherAccount();
    assert.equal(storage.token, undefined);
    assert.equal(switchAccount.data.recoveryFailed, false);
    assert.equal(switchAccount.data.restoring, false);

    console.log('login session recovery tests passed');
  } finally {
    api.restoreSession = originalRestoreSession;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
