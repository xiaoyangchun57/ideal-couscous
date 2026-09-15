const assert = require('node:assert/strict');
const api = require('../services/api.js');

let definition;
global.getApp = () => ({ globalData: {} });
global.Page = page => { definition = page; };
global.wx = { getNetworkType: ({ success }) => success({ networkType: 'wifi' }) };
require('../pages/responsible-sites/responsible-sites.js');

const page = Object.assign({}, definition, { data: { sites: [], loading: false, error: '' } });
page.setData = patch => Object.assign(page.data, patch);
const original = api.stationMonitoringSites;

(async () => {
  try {
    api.stationMonitoringSites = () => Promise.resolve({ items: [{ id: 1, name: '站点', monitoring_status_label: '等待首帧' }] });
    page.onShow();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(page.data.sites[0].monitoring_status_label, '等待首帧');
    api.stationMonitoringSites = () => Promise.reject(new Error('network'));
    page.onShow();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(page.data.sites[0].id, 1);
    assert.match(page.data.error, /重试/);
    console.log('responsible sites monitoring tests passed');
  } finally {
    api.stationMonitoringSites = original;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
