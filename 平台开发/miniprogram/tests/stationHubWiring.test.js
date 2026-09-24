const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../services/api.js');

const app = { globalData: { stationHubTarget: null } };
let definition;
global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = { getStorageSync: key => key === 'user' ? { capabilities: { station_monitoring_public: true } } : null,
  navigateTo: () => {}, stopPullDownRefresh: () => {} };
require('../pages/responsible-sites/responsible-sites.js');

const originals = {
  stationMonitoringSites: api.stationMonitoringSites, responsibleSites: api.responsibleSites,
  reagentOverview: api.reagentOverview, reagentReplacement: api.reagentReplacement,
  reagentCalibration: api.reagentCalibration
};
const flush = () => new Promise(resolve => setImmediate(resolve));
function page() {
  const instance = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  instance.setData = patch => Object.assign(instance.data, patch);
  instance.onLoad();
  return instance;
}
const reagent = (siteId, reagentId, reasons, extra = {}) => Object.assign({
  site_id: siteId, reagent_id: reagentId, site_name: '站点', reagent_name: '试剂',
  current_qty: 0, unit: '瓶', attention_reasons: reasons,
  can_replace: true, can_calibrate: true, qc_status: 'pending', remaining_days: 0
}, extra);
const summary = { total: 3, concern_count: 3, status_counts: {
  expired: 1, expiring: 0, low_volume: 2, pending_qc: 1, failed_qc: 1
}, items: [
  reagent(1, 7, ['expired', 'low_volume', 'pending_qc']),
  reagent(2, 8, ['low_volume', 'failed_qc'], { can_replace: false, can_calibrate: false }),
  reagent(3, 9, ['pending_qc'], { current_qty: 2 })
] };

(async () => {
  try {
    const requests = [];
    wx.request = options => {
      requests.push(options);
      options.success({ statusCode: 200, data: { ok: true } });
    };
    await originals.reagentReplacement({ _idempotency_key: 'fixture-replace' });
    await originals.reagentCalibration({ _idempotency_key: 'fixture-calibrate' });
    assert.match(requests[0].url, /\/api\/reagent-inventory\/replacement$/);
    assert.match(requests[1].url, /\/api\/reagent-qc$/);
    assert.equal(requests[0].data._idempotency_key, 'fixture-replace');
    assert.equal(requests[1].data._idempotency_key, 'fixture-calibrate');
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxml'), 'utf8');
    assert.match(wxml, /custom-navbar/);
    assert.match(wxml, /catchtap="onCloseReplaceSheet"/);
    assert.match(wxml, /catchtap="onCloseCalibrateSheet"/);
    assert.match(wxml, /wx:if="\{\{monitoringPublic\}\}"/);
    assert.match(wxml, /\{\{replaceSheet\.unit\}\}/, 'inventory amount retains its stock unit');
    assert.doesNotMatch(wxml, /\{\{calibrateSheet\.unit\}\}/,
      'calibration numbers cannot inherit the inventory unit');
    assert.match(wxml, /已进入待标定状态，请继续完成标定/);
    assert.match(wxml, /标定已通过；其他关注项以列表为准/);
    assert.match(wxml, /已记录需报修/);
    assert.doesNotMatch(wxml, /试剂状态已恢复正常|已提交报修|标定完成后恢复正常/);
    const siteQueries = [];
    api.stationMonitoringSites = options => {
      siteQueries.push(options);
      return Promise.resolve({ scope: options.scope, items: [] });
    };
    api.responsibleSites = () => Promise.resolve({ scope: 'mine', items: [] });
    let overviewCalls = 0;
    api.reagentOverview = () => { overviewCalls++; return Promise.resolve(summary); };
    const p = page();
    const target = { view: 'reagents', filter: 'low_volume' };
    app.globalData.stationHubTarget = target;
    p.onShow();
    assert.equal(app.globalData.stationHubTarget, null, 'valid target consumed immediately');
    assert.equal(p.data.activeTab, 'reagents');
    assert.equal(p.data.activeFilter, 'low_volume');
    await flush();
    assert.equal(p.data.reagentItems.length, 2, 'filter uses multi-reason union');
    assert.equal(p.data.reagentItems[0].volume, 0, 'zero is not treated as missing');
    assert.equal(p.data.reagentItems[0].attention_reasons.length, 3);
    assert.deepEqual(p.data.reagentItems[0].attention_reasons.map(x => x.type),
      ['expired', 'low_volume', 'pending_qc']);
    p.onReagentFilterTap({ currentTarget: { dataset: { filter: '' } } });
    assert.equal(p.data.reagentItems.length, 3, 'pending QC appears in all filter');
    p.onReagentFilterTap({ currentTarget: { dataset: { filter: 'expired' } } });
    assert.equal(p.data.reagentItems.length, 1);
    p.onShow();
    await flush();
    assert.equal(p.data.activeFilter, 'expired', 'return to tab retains filter');
    assert.ok(overviewCalls >= 2);

    app.globalData.stationHubTarget = { view: 'reagents', filter: 'invalid' };
    p.onShow();
    await flush();
    assert.equal(app.globalData.stationHubTarget, null, 'invalid target discarded');
    assert.equal(p.data.activeFilter, 'expired');
    app.globalData.stationHubTarget = { view: 'stations' };
    p.onShow();
    await flush();
    assert.equal(p.data.activeTab, 'stations');
    p.onTabReagent();
    await flush();
    assert.equal(p.data.activeFilter, 'expired', 'mode switch keeps previous filter');
    p.setData({ scope: 'all', keyword: '坝上' });
    p.onTabSites();
    await flush();
    assert.deepEqual(siteQueries.at(-1), { scope: 'all', keyword: '坝上' },
      'switching back preserves station scope and search');
    p.onTabReagent();
    await flush();

    p.onReagentFilterTap({ currentTarget: { dataset: { filter: '' } } });
    p.onOpenReplaceSheet({ currentTarget: { dataset: { id: '2:8' } } });
    assert.equal(p.data.replaceSheetVisible, false, 'server action capability blocks maintenance');
    p.onOpenReplaceSheet({ currentTarget: { dataset: { id: '1:7' } } });
    assert.equal(p.data.replaceSheetVisible, true);
    p.onSubmitReplace();
    assert.ok(p.data.replaceSheet.errors.newVolume);
    p.onReplaceVolumeInput({ detail: { value: '4' } });
    p.onReplaceTimeChange({ detail: { value: '2026-09-23' } });
    p.onReplaceEstDaysInput({ detail: { value: '30' } });
    const replaceRequests = [];
    let rejectReplace;
    api.reagentReplacement = payload => {
      replaceRequests.push(payload);
      return replaceRequests.length === 1
        ? new Promise((resolve, reject) => { rejectReplace = reject; }) : Promise.resolve({ ok: true });
    };
    const pending = p.onSubmitReplace();
    assert.equal(p.data.replaceSheet.submitting, true);
    p.onCloseReplaceSheet();
    assert.equal(p.data.replaceSheetVisible, true, 'cannot close while saving');
    p.onSubmitReplace();
    assert.equal(replaceRequests.length, 1, 'double tap does not duplicate request');
    rejectReplace({ status: 503, error: '请重试' });
    await pending;
    assert.equal(p.data.replaceSheet.serverError, '请重试');
    await p.onSubmitReplace();
    assert.equal(replaceRequests[1]._idempotency_key, replaceRequests[0]._idempotency_key);
    assert.deepEqual(replaceRequests[1], replaceRequests[0], 'failed retry repeats exact payload');
    assert.equal(p.data.replaceSheet.success, true);
    p.onCloseReplaceSheet();
    p.onOpenReplaceSheet({ currentTarget: { dataset: { id: '1:7' } } });
    p.onReplaceVolumeInput({ detail: { value: '4' } });
    p.onReplaceTimeChange({ detail: { value: '2026-09-23' } });
    p.onReplaceEstDaysInput({ detail: { value: '30' } });
    p.onReplaceVolumeInput({ detail: { value: '5' } });
    await p.onSubmitReplace();
    assert.notEqual(replaceRequests[2]._idempotency_key, replaceRequests[0]._idempotency_key,
      'a new submission uses a new key');
    p.onCloseReplaceSheet();
    p.onOpenCalibrateSheet({ currentTarget: { dataset: { id: '1:7' } } });
    p.onCalibrateStandardInput({ detail: { value: '0' } });
    p.onCalibrateMeasuredInput({ detail: { value: '1.5' } });
    p.onCalibrateResultTap({ currentTarget: { dataset: { result: 'fail' } } });
    p.onSubmitCalibrate();
    assert.ok(p.data.calibrateSheet.errors.followUp);
    p.onCalibrateFollowUpTap({ currentTarget: { dataset: { follow: 'recalibrate' } } });
    let calibration;
    api.reagentCalibration = payload => { calibration = payload; return Promise.resolve({ ok: true }); };
    await p.onSubmitCalibrate();
    assert.equal(calibration.passed, 0);
    assert.equal(calibration.fail_action, 'calibrate');
    assert.equal(calibration.standard_value, 0);
    assert.equal(p.data.calibrateSheet.success, true);

    p.onCloseCalibrateSheet();
    const stillAttention = { items: [reagent(1, 7, ['expired', 'low_volume'],
      { unit: '盒', qc_status: 'passed' })] };
    api.reagentOverview = () => Promise.resolve(stillAttention);
    await p.loadReagents();
    p.onOpenCalibrateSheet({ currentTarget: { dataset: { id: '1:7' } } });
    assert.equal(p.data.calibrateSheet.unit, '', 'a stock unit is not a calibration unit');
    p.onCalibrateStandardInput({ detail: { value: '10' } });
    p.onCalibrateMeasuredInput({ detail: { value: '10' } });
    p.onCalibrateResultTap({ currentTarget: { dataset: { result: 'pass' } } });
    await p.onSubmitCalibrate();
    await flush();
    assert.equal(calibration.passed, 1);
    assert.deepEqual(p.data.reagentItems[0].attention_reasons.map(reason => reason.type),
      ['expired', 'low_volume'], 'QC pass cannot erase other server attention reasons');

    p.onCloseCalibrateSheet();
    p.onOpenCalibrateSheet({ currentTarget: { dataset: { id: '1:7' } } });
    p.onCalibrateStandardInput({ detail: { value: '10' } });
    p.onCalibrateMeasuredInput({ detail: { value: '12' } });
    p.onCalibrateResultTap({ currentTarget: { dataset: { result: 'fail' } } });
    p.onCalibrateFollowUpTap({ currentTarget: { dataset: { follow: 'repair' } } });
    await p.onSubmitCalibrate();
    assert.equal(calibration.passed, 0);
    assert.equal(calibration.fail_action, 'repair', 'record a repair need, not a created workorder');
    p.onCloseCalibrateSheet();
    api.reagentOverview = () => Promise.resolve(summary);
    await p.loadReagents();

    p.onOpenReplaceSheet({ currentTarget: { dataset: { id: '1:7' } } });
    assert.equal(p.data.replaceSheetVisible, true);
    api.reagentOverview = () => Promise.reject({ status: 403, error: '禁止访问' });
    await p.loadReagents();
    assert.equal(p.data.reagentNoViewPermission, true);
    assert.equal(p.data.reagentItems.length, 0, 'permission loss removes stale data');
    assert.equal(p.data.replaceSheetVisible, false, 'permission loss closes stale maintenance form');
    assert.equal(p.data.replaceSheet.siteId, null, 'permission loss removes stale form target');
    p.onSubmitReplace();
    assert.equal(replaceRequests.length, 3, 'stale maintenance form cannot submit after permission loss');
    api.reagentOverview = () => Promise.reject({ status: 0, error: '网络断开' });
    await p.loadReagents();
    assert.equal(p.data.reagentError, '网络断开');
    assert.equal(p.data.reagentNoViewPermission, false, 'network failure remains visibly retryable');
    api.reagentOverview = () => Promise.resolve(summary);
    await p.onReagentRetry();
    assert.equal(p.data.reagentNoViewPermission, false);
    assert.equal(p.data.reagentItems.length, 3);

    p.onOpenCalibrateSheet({ currentTarget: { dataset: { id: '1:7' } } });
    assert.equal(p.data.calibrateSheetVisible, true);
    api.reagentOverview = () => Promise.reject({ status: 401, error: '登录已失效' });
    await p.loadReagents();
    assert.equal(p.data.calibrateSheetVisible, false, 'expired session closes stale calibration form');
    assert.equal(p.data.calibrateSheet.siteId, null);
    assert.equal(p.data.reagentItems.length, 0);
    api.reagentOverview = () => Promise.resolve(summary);
    await p.onReagentRetry();

    api.reagentOverview = () => Promise.reject({ status: 0, error: '暂时离线' });
    await p.loadReagents();
    assert.equal(p.data.reagentItems.length, 3, 'network refresh retains previous authorized items');
    assert.equal(p.data.reagentError, '暂时离线');

    let resolveOld;
    api.reagentOverview = () => new Promise(resolve => { resolveOld = resolve; });
    p.loadReagents();
    p.onHide();
    resolveOld({ items: [] });
    await flush();
    assert.equal(p.data.reagentItems.length, 3, 'hidden page ignores stale overview');
    console.log('station hub navigation and reagent contract tests passed');
  } finally {
    Object.assign(api, originals);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
