const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

const app = { globalData: { selSiteId: null } };
let cachedUser = null;
let definition;
global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
  getStorageSync: key => key === 'user' ? cachedUser : null,
  showToast: () => {}, showLoading: () => {}, hideLoading: () => {},
};

const api = require('../services/api.js');
const originals = { siteTasks: api.siteTasks, siteProfile: api.siteProfile, stationMonitoringOverview: api.stationMonitoringOverview, partsInventory: api.partsInventory };
require('../pages/site/site.js');

function createPage() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = (patch, done) => {
    Object.assign(page.data, patch);
    if (done) done();
  };
  return page;
}

(async () => {
  try {
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/site/site.wxml'), 'utf8');
    assert.match(wxml, /wx:if="\{\{!readOnlySource\}\}" class="task-status/);
    assert.match(wxml, /wx:if="\{\{!readOnlySource\}\}" class="btn-primary checkin-btn/);
    assert.match(wxml, /wx:if="\{\{site\.can_calibrate\}\}"/);
    assert.match(wxml, /wx:if="\{\{monitoringPublic && monitoringSource\}\}"/);
    assert.match(wxml, /wx:if="\{\{!readOnlySource\}\}" class="btn-ghost parts-apply-btn/);
    assert.match(wxml, /bindtap="onNavigate">导航到站/);
    assert.match(wxml, /!site && monitoringError/);
    assert.match(wxml, /monitoringError.*bindtap="onRetryMonitoring"/s);
    assert.match(wxml, /item\.factor_name_cn \|\| '监测因子'/);
    assert.doesNotMatch(wxml, /item\.factor_name\s*\|\||item\.standard_factor/);
    assert.match(wxml, /site\.monitoring\.axes\.communication\.status_label/);
    assert.match(wxml, /site\.monitoring\.axes\.data\.status_label/);
    assert.doesNotMatch(wxml, /site\.monitoring\.axes\.(?:rtu|instrument)/);
    assert.match(wxml, /最后收到报文/);
    const responsibleWxml = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxml'), 'utf8');
    assert.match(responsibleWxml, /!sites\.length && error/);
    assert.match(responsibleWxml, /sites\.length && error/);
    assert.match(responsibleWxml, /bindtap="onRetry"/);

    let siteTaskCalls = 0;
    let profileCalls = 0;
    let monitoringCalls = 0;
    let inventoryCalls = 0;
    api.siteTasks = id => {
      siteTaskCalls += 1;
      return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01', can_calibrate: true } });
    };
    api.siteProfile = id => {
      profileCalls += 1;
      return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01', can_calibrate: true } });
    };
    api.stationMonitoringOverview = id => { monitoringCalls += 1; return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01', monitoring_status_label: '未接入', can_calibrate: true }, monitoring: { latest_values: [], axes: {} } }); };
    api.partsInventory = () => {
      inventoryCalls += 1;
      return Promise.resolve([]);
    };

    const readonlyPage = createPage();
    readonlyPage.onLoad({ site_id: '20', source: 'inspection_readonly' });
    await flush();
    assert.equal(readonlyPage.data.readOnlySource, true);
    assert.equal(readonlyPage.data.monitoringSource, false);
    assert.equal(readonlyPage.data.site.id, 20);
    assert.equal(siteTaskCalls, 0);
    assert.equal(profileCalls, 1);
    assert.equal(monitoringCalls, 0, 'inspection source must not request monitoring');
    assert.equal(inventoryCalls, 0, 'inspection source does not load parts-application data');

    const calibrationPage = createPage();
    let calibrationCalls = 0;
    calibrationPage.onCalibrate = () => { calibrationCalls += 1; };
    calibrationPage.onLoad({ site_id: '20', source: 'inspection_calibration', action: 'calibrate' });
    await flush();
    assert.equal(calibrationPage.data.readOnlySource, false);
    assert.equal(calibrationCalls, 1, 'check-in calibration route opens the existing calibration action once');

    const staleSourcePage = createPage();
    staleSourcePage.onLoad({ site_id: '20', source: 'responsible_sites_monitoring' });
    await flush();
    assert.equal(staleSourcePage.data.monitoringSource, false);
    assert.equal(profileCalls, 2);
    assert.equal(monitoringCalls, 0, 'old source without an enabled capability cannot bypass the gate');

    cachedUser = { capabilities: { station_monitoring_public: true } };
    const monitoringPage = createPage();
    monitoringPage.onLoad({ site_id: '20', source: 'responsible_sites_monitoring' });
    await flush();
    assert.equal(monitoringPage.data.readOnlySource, true);
    assert.equal(monitoringPage.data.monitoringSource, true);
    assert.equal(monitoringPage.data.site.can_calibrate, true);

    const pending = [];
    api.stationMonitoringOverview = () => new Promise(resolve => pending.push(resolve));
    const racePage = createPage();
    racePage.onLoad({ site_id: '20', source: 'responsible_sites_monitoring' });
    racePage.loadSite(20);
    pending[1]({ site: { id: 20, name: '新结果' }, monitoring: { latest_values: [], axes: {} } });
    await flush();
    pending[0]({ site: { id: 20, name: '旧结果' }, monitoring: { latest_values: [], axes: {} } });
    await flush();
    assert.equal(racePage.data.site.name, '新结果', 'older detail response cannot overwrite the latest request');

    const hiddenPending = [];
    api.stationMonitoringOverview = () => new Promise(resolve => hiddenPending.push(resolve));
    const hiddenPage = createPage();
    hiddenPage.onLoad({ site_id: '20', source: 'responsible_sites_monitoring' });
    hiddenPage.onHide();
    hiddenPending[0]({ site: { id: 20, name: '离页结果' }, monitoring: { latest_values: [], axes: {} } });
    await flush();
    assert.equal(hiddenPage.data.site, null, 'a response arriving after page hide is ignored');

    cachedUser = null;
    api.siteProfile = () => Promise.reject(new Error('network'));
    readonlyPage.loadSite(20);
    await flush();
    assert.equal(readonlyPage.data.site.id, 20, 'refresh failure keeps the last successful detail');
    assert.match(readonlyPage.data.monitoringError, /重试/);
    api.siteProfile = id => { profileCalls += 1; return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01' } }); };
    api.stationMonitoringOverview = id => { monitoringCalls += 1; return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01', monitoring_status_label: '未接入' }, monitoring: { latest_values: [] } }); };

    const standardPage = createPage();
    standardPage.onLoad({ site_id: '20' });
    await flush();
    assert.equal(standardPage.data.readOnlySource, false);
    assert.equal(standardPage.data.site.id, 20);
    assert.equal(siteTaskCalls, 2);
    assert.equal(inventoryCalls, 2, 'calibration and existing site entries preserve normal site loading');

    const standardPending = [];
    api.siteTasks = () => new Promise(resolve => standardPending.push(resolve));
    const hiddenStandardPage = createPage();
    hiddenStandardPage.onLoad({ site_id: '20' });
    hiddenStandardPage.onShow();
    hiddenStandardPage.onHide();
    standardPending[0]({ site: { id: 20, name: '已失效结果' } });
    await flush();
    assert.equal(hiddenStandardPage.data.site, null);
    hiddenStandardPage.onShow();
    standardPending[1]({ site: { id: 20, name: '返回后结果' } });
    await flush();
    assert.equal(hiddenStandardPage.data.site.name, '返回后结果', 'standard entry reloads if its first response was invalidated while hidden');

    const inspection = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
    assert.match(inspection, /\/pages\/site\/site\?site_id=' \+ this\.data\.selSiteId \+ '&source=inspection_readonly'/);
    console.log('siteReadonlySource tests passed');
  } finally {
    Object.assign(api, originals);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
