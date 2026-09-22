const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../services/api.js');

let definition;
global.getApp = () => ({ globalData: {} });
global.Page = page => { definition = page; };
let cachedUser = { capabilities: { station_monitoring_public: true } };
const navigations = [];
global.wx = { navigateTo: options => navigations.push(options.url), getStorageSync: key => key === 'user' ? cachedUser : null };
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
    assert.match(wxml, /监测能力未启用/);
    assert.doesNotMatch(wxml, /状态未知/);
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
    assert.equal(page.data.activeTab, 'stations', 'the approved station mode is visible by default');
    page.onShow();
    await flush();
    assert.deepEqual(calls[0], { scope: 'mine', keyword: '' }, 'first request must use mine');
    assert.equal(page.data.canViewAll, true, 'scope switch follows server capability');
    assert.equal(page.data.sites[0].is_responsible, true);
    assert.equal(page.data.monitoringEnabled, true);
    assert.equal(page.data.sites[0].monitoring_status_label, '等待首帧');

    api.stationMonitoringSites = () => Promise.resolve({
      scope: 'mine', available_scopes: ['mine'], scope_counts: { mine: 1, all: null },
      items: [{ id: 10, site_id: 10, name: '门禁前成功站点', monitoring_status_label: '等待首帧' }]
    });
    const gatedAfterSuccess = makePage();
    gatedAfterSuccess.onShow();
    await flush();
    assert.equal(gatedAfterSuccess.data.monitoringPublic, true);

    let failedGateDirectoryCalls = 0;
    api.stationMonitoringSites = () => Promise.reject({ status: 403, code: 'STATION_MONITORING_ADMIN_ONLY' });
    api.responsibleSites = () => {
      failedGateDirectoryCalls += 1;
      return new Promise((resolve, reject) => { rejectDirectoryFallback = reject; });
    };
    let rejectDirectoryFallback;
    gatedAfterSuccess.loadSites('mine', '');
    await flush();
    assert.equal(failedGateDirectoryCalls, 1, 'a monitoring gate response still attempts the authorized directory');
    assert.equal(gatedAfterSuccess.data.monitoringPublic, false, 'a gate response hides retained monitoring fields before fallback completes');
    assert.equal(gatedAfterSuccess.data.monitoringEnabled, false);
    rejectDirectoryFallback(new Error('directory unavailable'));
    await flush();
    assert.equal(gatedAfterSuccess.data.sites[0].monitoring_status_label, '等待首帧', 'the last successful directory remains available');
    assert.equal(gatedAfterSuccess.data.error, '站点目录加载失败，请重试');
    gatedAfterSuccess.openSite({ currentTarget: { dataset: { id: 10 } } });
    assert.equal(navigations.at(-1), '/pages/site/site?site_id=10&source=responsible_sites_profile');

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
    assert.equal(closed.data.monitoringEnabled, false);
    assert.equal(staticCalls, 1);
    assert.equal(closedMonitoringCalls, 0, 'closed capability must make zero monitoring requests');

    let staleCapabilityMonitoringCalls = 0;
    let staleCapabilityDirectoryCalls = 0;
    cachedUser = { capabilities: { station_monitoring_public: true } };
    api.stationMonitoringSites = () => {
      staleCapabilityMonitoringCalls += 1;
      return Promise.reject({ status: 403, code: 'STATION_MONITORING_PUBLIC_DISABLED' });
    };
    api.responsibleSites = options => {
      staleCapabilityDirectoryCalls += 1;
      return Promise.resolve({
        scope: options.scope, available_scopes: ['mine'], scope_counts: { mine: 1, all: null },
        items: [{ site_id: 6, name: '回退站点', monitoring_status_label: '不应展示' }]
      });
    };
    const staleCapability = makePage();
    staleCapability.onShow();
    assert.equal(staleCapability.data.monitoringPublic, false, 'monitoring fields stay hidden until the endpoint confirms availability');
    await flush();
    await flush();
    assert.equal(staleCapabilityMonitoringCalls, 1);
    assert.equal(staleCapabilityDirectoryCalls, 1, 'an explicit monitoring gate response falls back to the authorized directory');
    assert.equal(staleCapability.data.monitoringPublic, false);
    assert.equal(staleCapability.data.monitoringEnabled, false);
    assert.equal(staleCapability.data.sites[0].id, 6, 'site_id is normalized for list keys and navigation');
    assert.equal(staleCapability.data.sites[0].site_id, 6);
    staleCapability.openSite({ currentTarget: { dataset: { id: 6 } } });
    assert.equal(navigations.at(-1), '/pages/site/site?site_id=6&source=responsible_sites_profile');

    let networkFallbackCalls = 0;
    api.stationMonitoringSites = () => Promise.reject({ status: 0, network: true });
    api.responsibleSites = () => { networkFallbackCalls += 1; return Promise.resolve({ items: [] }); };
    const networkFailure = makePage();
    networkFailure.onShow();
    await flush();
    assert.equal(networkFallbackCalls, 0, 'network failures must stay visible instead of silently changing data sources');
    assert.equal(networkFailure.data.sites.length, 0);
    assert.equal(networkFailure.data.error, '站点监测信息加载失败，请重试');

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

    const blockedScopeCallCount = calls.length;
    const blockedScope = makePage();
    blockedScope.data.canViewAll = false;
    blockedScope.onScopeAll();
    await flush();
    assert.equal(calls.length, blockedScopeCallCount, 'a non-admin view cannot request the all scope');

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

    api.stationMonitoringSites = options => Promise.resolve({
      scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 1, all: 2 },
      items: [{ site_id: 12, name: '状态保持站点' }]
    });
    const preserved = makePage();
    preserved.data.scope = 'all';
    preserved.data.keyword = ' 保留条件 ';
    preserved.onShow();
    await flush();
    assert.equal(preserved.data.scope, 'all');
    assert.equal(preserved.data.keyword, '保留条件');
    preserved.onHide();
    preserved.onShow();
    await flush();
    assert.equal(preserved.data.scope, 'all');
    assert.equal(preserved.data.keyword, '保留条件', 'returning from detail keeps the confirmed scope and search');
    preserved.openSite({ currentTarget: { dataset: { id: 12 } } });
    assert.equal(navigations.at(-1), '/pages/site/site?site_id=12&source=responsible_sites_monitoring');

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
