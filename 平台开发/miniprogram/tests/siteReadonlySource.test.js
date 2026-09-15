const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

const app = { globalData: { selSiteId: null } };
let definition;
global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
  getStorageSync: () => null,
  showToast: () => {}, showLoading: () => {}, hideLoading: () => {},
};

const api = require('../services/api.js');
const originals = { siteTasks: api.siteTasks, stationMonitoringOverview: api.stationMonitoringOverview, partsInventory: api.partsInventory };
require('../pages/site/site.js');

function createPage() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = patch => Object.assign(page.data, patch);
  return page;
}

(async () => {
  try {
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/site/site.wxml'), 'utf8');
    assert.match(wxml, /wx:if="\{\{!readOnlySource\}\}" class="task-status/);
    assert.match(wxml, /wx:if="\{\{!readOnlySource\}\}" class="btn-primary checkin-btn/);
    assert.match(wxml, /wx:if="\{\{!readOnlySource && site\.can_calibrate\}\}"/);
    assert.match(wxml, /wx:if="\{\{!readOnlySource\}\}" class="btn-ghost parts-apply-btn/);
    assert.match(wxml, /bindtap="onNavigate">导航到站/);
    assert.match(wxml, /!site && monitoringError/);
    assert.match(wxml, /monitoringError.*bindtap="onRetryMonitoring"/s);
    const responsibleWxml = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxml'), 'utf8');
    assert.match(responsibleWxml, /!sites\.length && error/);
    assert.match(responsibleWxml, /sites\.length && error/);
    assert.match(responsibleWxml, /bindtap="onRetry"/);

    let siteTaskCalls = 0;
    let monitoringCalls = 0;
    let inventoryCalls = 0;
    api.siteTasks = id => {
      siteTaskCalls += 1;
      return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01' } });
    };
    api.stationMonitoringOverview = id => { monitoringCalls += 1; return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01', status_label: '未接入' }, monitoring: { latest_values: [] } }); };
    api.partsInventory = () => {
      inventoryCalls += 1;
      return Promise.resolve([]);
    };

    const readonlyPage = createPage();
    readonlyPage.onLoad({ site_id: '20', source: 'inspection_readonly' });
    await flush();
    assert.equal(readonlyPage.data.readOnlySource, true);
    assert.equal(readonlyPage.data.site.id, 20);
    assert.equal(siteTaskCalls, 0);
    assert.equal(monitoringCalls, 1);
    assert.equal(inventoryCalls, 0, 'inspection source does not load parts-application data');

    api.stationMonitoringOverview = () => Promise.reject(new Error('network'));
    readonlyPage.loadSite(20);
    await flush();
    assert.equal(readonlyPage.data.site.id, 20, 'refresh failure keeps the last successful detail');
    assert.match(readonlyPage.data.monitoringError, /重试/);
    api.stationMonitoringOverview = id => { monitoringCalls += 1; return Promise.resolve({ site: { id: Number(id), name: '万松站', code: 'WS-01', monitoring_status_label: '未接入' }, monitoring: { latest_values: [] } }); };

    const standardPage = createPage();
    standardPage.onLoad({ site_id: '20' });
    await flush();
    assert.equal(standardPage.data.readOnlySource, false);
    assert.equal(standardPage.data.site.id, 20);
    assert.equal(siteTaskCalls, 1);
    assert.equal(inventoryCalls, 1, 'existing site entry preserves parts-application loading');

    const inspection = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
    assert.match(inspection, /\/pages\/site\/site\?site_id=' \+ this\.data\.selSiteId \+ '&source=inspection_readonly'/);
    console.log('siteReadonlySource tests passed');
  } finally {
    Object.assign(api, originals);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
