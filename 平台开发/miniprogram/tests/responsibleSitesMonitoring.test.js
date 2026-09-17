const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../services/api.js');

let definition;
global.getApp = () => ({ globalData: {} });
global.Page = page => { definition = page; };
let cachedUser = { capabilities: { station_monitoring_public: true } };
global.wx = { navigateTo: () => {}, getStorageSync: key => key === 'user' ? cachedUser : null };
require('../pages/responsible-sites/responsible-sites.js');

const originals = { stationMonitoringSites: api.stationMonitoringSites, responsibleSites: api.responsibleSites };
const flush = () => new Promise(resolve => setImmediate(resolve));
const makePage = () => {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = patch => Object.assign(page.data, patch);
  page.onLoad();
  return page;
};

(async () => {
  try {
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxml'), 'utf8');
    assert.match(wxml, /最后收到报文/);
    assert.match(wxml, /wx:if="\{\{monitoringPublic\}\}"/);
    assert.doesNotMatch(wxml, /RTU|仪器状态|最后通信/);
    const calls = [];
    api.stationMonitoringSites = options => {
      calls.push(options);
      return Promise.resolve({
        scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 },
        items: [{ id: 1, site_id: 1, name: '本人站', is_responsible: true, monitoring_status_label: '等待首帧' }]
      });
    };
    const page = makePage();
    page.onShow();
    await flush();
    assert.deepEqual(calls[0], { scope: 'mine', keyword: '' }, 'first request must use mine');
    assert.equal(page.data.canViewAll, true, 'scope switch follows server capability');
    assert.equal(page.data.sites[0].is_responsible, true);

    let staticCalls = 0;
    let closedMonitoringCalls = 0;
    cachedUser = { capabilities: { station_monitoring_public: false } };
    api.responsibleSites = options => {
      staticCalls += 1;
      return Promise.resolve({ scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 }, items: [{ id: 5, name: '静态站点' }] });
    };
    api.stationMonitoringSites = () => { closedMonitoringCalls += 1; return Promise.resolve({ items: [] }); };
    const closed = makePage();
    closed.onShow();
    await flush();
    assert.equal(closed.data.monitoringPublic, false);
    assert.equal(staticCalls, 1);
    assert.equal(closedMonitoringCalls, 0, 'closed capability must make zero monitoring requests');
    cachedUser = { capabilities: { station_monitoring_public: true } };
    api.stationMonitoringSites = options => {
      calls.push(options);
      return Promise.resolve({
        scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 },
        items: [{ id: 1, site_id: 1, name: '本人站', is_responsible: true, monitoring_status_label: '等待首帧' }]
      });
    };
    page.onScopeAll();
    await flush();
    assert.deepEqual(calls[1], { scope: 'all', keyword: '' });
    assert.equal(page.data.scope, 'all');
    page.onKeywordInput({ detail: { value: ' 水站 A&B ' } });
    page.onSearch();
    await flush();
    assert.deepEqual(calls[2], { scope: 'all', keyword: '水站 A&B' });
    assert.equal(page.data.keyword, '水站 A&B');
    page.onClearSearch();
    await flush();
    assert.deepEqual(calls[3], { scope: 'all', keyword: '' });

    api.stationMonitoringSites = () => Promise.resolve({
      scope: 'mine', available_scopes: ['mine'], scope_counts: { mine: 0, all: null }, items: []
    });
    const ordinary = makePage();
    ordinary.onShow();
    await flush();
    assert.equal(ordinary.data.canViewAll, false);
    assert.equal(ordinary.data.emptyTitle, '暂未分配负责站点');

    api.stationMonitoringSites = options => Promise.resolve({
      scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 0, all: 2 }, items: []
    });
    const empty = makePage();
    empty.data.canViewAll = true;
    empty.data.scope = 'all';
    empty.data.keyword = '不存在';
    empty.onShow();
    await flush();
    assert.equal(empty.data.emptyTitle, '未找到匹配站点');

    const retained = makePage();
    retained.data.sites = [{ id: 8, name: '最近成功结果' }];
    retained.data.scope = 'all';
    api.stationMonitoringSites = () => Promise.reject(new Error('network'));
    retained.onShow();
    await flush();
    assert.equal(retained.data.sites[0].id, 8);
    assert.match(retained.data.error, /重试/);

    const failedCalls = [];
    api.stationMonitoringSites = options => {
      failedCalls.push(options);
      return Promise.reject(new Error('network'));
    };
    retained.loadSites('all', '失败关键词');
    await flush();
    retained.onRetry();
    await flush();
    assert.deepEqual(failedCalls.slice(-2), [
      { scope: 'all', keyword: '失败关键词' }, { scope: 'all', keyword: '失败关键词' }
    ], 'retry keeps the failed scope and keyword');

    const pending = [];
    api.stationMonitoringSites = () => new Promise(resolve => pending.push(resolve));
    const racing = makePage();
    racing.onShow();
    racing.loadSites('all', 'new');
    pending[1]({ scope: 'all', available_scopes: ['mine', 'all'], scope_counts: {}, items: [{ id: 2, name: '新结果' }] });
    await flush();
    pending[0]({ scope: 'mine', available_scopes: ['mine'], scope_counts: {}, items: [{ id: 3, name: '旧结果' }] });
    await flush();
    assert.equal(racing.data.sites[0].id, 2, 'older response cannot overwrite current scope or keyword');

    const leaving = makePage();
    let resolveLeaving;
    api.stationMonitoringSites = () => new Promise(resolve => { resolveLeaving = resolve; });
    leaving.onShow();
    leaving.onHide();
    resolveLeaving({ scope: 'mine', available_scopes: ['mine'], scope_counts: {}, items: [{ id: 9 }] });
    await flush();
    assert.equal(leaving.data.sites.length, 0, 'response after leaving cannot write page state');

    const requestPath = require.resolve('../utils/request.js');
    const apiPath = require.resolve('../services/api.js');
    const requestModule = require(requestPath);
    const originalRequest = requestModule.request;
    const requestedUrls = [];
    requestModule.request = url => { requestedUrls.push(url); return Promise.resolve({}); };
    delete require.cache[apiPath];
    const isolatedApi = require(apiPath);
    await isolatedApi.stationMonitoringSites({ scope: 'all', keyword: '水站 A&B' });
    await isolatedApi.responsibleSites({ scope: 'all', keyword: '水站 A&B' });
    await isolatedApi.siteProfile(20);
    assert.deepEqual(requestedUrls, [
      '/api/station-monitoring/sites?scope=all&keyword=%E6%B0%B4%E7%AB%99%20A%26B',
      '/api/mobile/responsible-sites?scope=all&keyword=%E6%B0%B4%E7%AB%99%20A%26B',
      '/api/mobile/site-profile/20'
    ]);
    requestModule.request = originalRequest;
    delete require.cache[apiPath];
    console.log('responsible sites monitoring tests passed');
  } finally {
    Object.assign(api, originals);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
