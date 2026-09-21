const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function setPath(target, key, value) {
  const parts = key.split('.');
  let current = target;
  while (parts.length > 1) {
    const part = parts.shift();
    if (!current[part] || typeof current[part] !== 'object') current[part] = {};
    current = current[part];
  }
  current[parts[0]] = value;
}

const storage = {};
const app = { globalData: { token: 'test', executionTarget: null, sites: [] } };
let definition;
let toasts = [];
let modals = [];
let scrollCalls = [];
let switchTabs = 0;
let navigationUrls = [];

global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getStorageSync: key => storage[key] || [],
  setStorageSync: (key, value) => { storage[key] = value; },
  removeStorageSync: key => { delete storage[key]; },
  showToast: options => { toasts.push(options); },
  showModal: options => {
    modals.push(options);
    if (options.success) options.success({ confirm: true, cancel: false });
  },
  showLoading: () => {}, hideLoading: () => {},
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
  authorize: ({ success }) => success(),
  getLocation: ({ success }) => success({ latitude: 28.6, longitude: 115.7 }),
  switchTab: options => { switchTabs += 1; if (options.success) options.success(); },
  navigateTo: options => { navigationUrls.push(options.url); }, reLaunch: () => {}, stopPullDownRefresh: () => {},
  setNavigationBarTitle: () => {}, pageScrollTo: options => { scrollCalls.push(options); },
  removeSavedFile: () => {},
};

const api = require('../services/api.js');
const localStore = require('../utils/localStore.js');
require('../pages/inspection/inspection.js');

function createPage() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = (patch, done) => {
    Object.keys(patch).forEach(key => setPath(page.data, key, patch[key]));
    if (done) done();
  };
  page.onLoad();
  return page;
}

function resetStorage() {
  Object.keys(storage).forEach(key => delete storage[key]);
  storage.user = { id: 2, real_name: '测试执行人' };
  storage.sites = [];
  toasts = [];
  modals = [];
  scrollCalls = [];
  switchTabs = 0;
  navigationUrls = [];
}

function seedStation(page, overrides) {
  const site = Object.assign({ id: 20, site_id: 20, name: '青云站', checked_in: true, checked_out: false }, overrides || {});
  page.data.viewPhase = 'inspection';
  page.data.selectedPlanId = 10;
  page.data.selSiteId = 20;
  page.data.site = site;
  page.data.selSite = site;
  page.data.sites = [site];
  page.data.currentPackage = {
    plan_id: 10, schedule_id: 100, work_date: '2026-08-31',
    site_order: [20], sites: [site], plan_display_name: '计划#100',
  };
}

const originals = {
  submitItem: api.submitItem,
  checkOutExecutionSite: api.checkOutExecutionSite,
  checkIn: api.checkIn,
  trackEvent: api.trackEvent,
  todayExecution: api.todayExecution,
  executionSiteTasks: api.executionSiteTasks,
  executionSiteReagents: api.executionSiteReagents,
  submitManualReport: api.submitManualReport,
};

(async () => {
  try {
    const wxml = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
    const staticHandlers = Array.from(wxml.matchAll(/\b(?:bindtap|catchtap|bindinput|bindchange)="([^"]+)"/g))
      .map(match => match[1]).filter(name => !name.includes('{{'));
    for (const handler of new Set(staticHandlers)) {
      assert.equal(typeof definition[handler], 'function', 'visible WXML event ' + handler + ' must be callable');
    }
    for (const handler of ['onBack', 'onEntryViewCompletedPackage', 'onSyncNow', 'onCheckOut', 'onGoToNextSite', 'onCloseSheet']) {
      assert.equal(typeof definition[handler], 'function', handler + ' must be a real Page method');
    }
    assert.equal((wxml.match(/bindtap="onSubmitReport"/g) || []).length, 1,
      'the report sheet has one visible submit action bound only to onSubmitReport');
    assert.match(wxml, /wx:elif="\{\{stationStage\.code === 'local_pending'\}\}"[\s\S]{0,180}bindtap="onSyncNow"/);
    assert.doesNotMatch(wxml, /stationStage\.code === 'local_pending'[\s\S]{0,320}bindtap="onCheckOut"/);
    assert.match(wxml, /<!-- 校准信息 -->\s*<view class="ip-fp-section" wx:if="\{\{sheet\.requiresCalibration\}\}">/);
    assert.match(wxml, /本次补拍[\s\S]{0,120}sheet\.photoInfo\.required/);
    assert.match(wxml, /原照片待审核/);
    assert.doesNotMatch(wxml, /整改要求：\{\{sheet\.item\.supplement_reason/);

    resetStorage();
    const reportPage = createPage();
    seedStation(reportPage);
    reportPage.data.reportSheet = {
      open: true, typeIndex: 1, description: '', photos: [], submitting: false
    };
    let reportWrites = 0;
    let reportRequest = deferred();
    let reportRefreshes = 0;
    api.submitManualReport = payload => {
      reportWrites += 1;
      assert.deepEqual(payload, {
        site_id: 20, report_type: 'equipment', description: '水泵异响',
        photo_urls: ['/uploads/site_photos/equipment.jpg'], gps_lat: 28.6, gps_lng: 115.7,
      });
      return reportRequest.promise;
    };
    reportPage.loadTasks = siteId => { assert.equal(siteId, 20); reportRefreshes += 1; };
    reportPage.onSubmitReport();
    assert.equal(reportWrites, 0, 'blank reports do not call the write API');
    reportPage.data.reportSheet.description = '水泵异响';
    reportPage.onSubmitReport();
    assert.equal(reportWrites, 0, 'reports without a ready photo do not call the write API');
    reportPage.data.reportSheet.photos = ['/uploads/site_photos/equipment.jpg'];
    reportPage.onSubmitReport();
    reportPage.onSubmitReport();
    await flush();
    await flush();
    assert.equal(reportWrites, 1, 'double taps issue one report request');
    reportRequest.reject({ error: '网络异常' });
    await flush();
    await flush();
    assert.equal(reportPage.data.reportSheet.open, true, 'failed reports retain the sheet for retry');
    assert.equal(reportPage.data.reportSheet.description, '水泵异响');
    assert.deepEqual(reportPage.data.reportSheet.photos, ['/uploads/site_photos/equipment.jpg']);
    assert.equal(reportPage.data.reportSheet.submitting, false);
    assert.equal(modals.at(-1).title, '上报失败');

    reportRequest = deferred();
    reportPage.onSubmitReport();
    await flush();
    await flush();
    assert.equal(reportWrites, 2, 'a failed report remains retryable');
    reportRequest.resolve({ order_no: 'MR202609020001' });
    await flush();
    await flush();
    assert.equal(reportPage.data.reportSheet.open, false);
    assert.equal(modals.at(-1).content, '已生成工单：MR202609020001');
    assert.equal(reportRefreshes, 1, 'only a confirmed work order refreshes the station data');

    reportPage.data.reportSheet = {
      open: true, typeIndex: 1, description: '水泵异响',
      photos: ['/uploads/site_photos/equipment.jpg'], submitting: false
    };
    api.submitManualReport = () => Promise.resolve({});
    reportPage.onSubmitReport();
    await flush();
    await flush();
    assert.equal(reportPage.data.reportSheet.open, true, 'a response without the authoritative work order number is retryable');
    assert.equal(reportPage.data.reportSheet.submitting, false);
    assert.equal(modals.at(-1).title, '上报失败');

    resetStorage();
    const pending = createPage();
    seedStation(pending);
    pending.data.categories = [{ items: [{ item_id: 301, plan_id: 10, result: 'normal', sync_pending: true }] }];
    storage[localStore.KEY] = [{
      id: 'submit-pending', type: 'submit', ownerUserId: '2', syncStatus: 'pending', createdAt: 1,
      data: { item_id: 301, plan_id: 10, siteId: 20 },
    }];
    pending.refreshStationStage(20);
    assert.equal(pending.data.stationStage.code, 'local_pending');
    let checkoutWrites = 0;
    api.checkOutExecutionSite = () => { checkoutWrites += 1; return Promise.resolve({}); };
    pending.onCheckOut();
    assert.equal(checkoutWrites, 0, 'pending item sync is the only permitted station action');
    assert.match(toasts.at(-1).title, /待同步/);

    resetStorage();
    const checkin = createPage();
    seedStation(checkin, { checked_in: false });
    let postCheckinRefreshes = 0;
    checkin.loadTasks = siteId => {
      assert.equal(siteId, 20);
      postCheckinRefreshes += 1;
    };
    api.trackEvent = () => {};
    api.checkIn = () => Promise.resolve({ success: true });
    checkin.onCheckIn();
    await flush();
    await flush();
    assert.equal(postCheckinRefreshes, 1, 'successful arrival refreshes the exact site from server facts');

    resetStorage();
    const remoteCheckin = createPage();
    seedStation(remoteCheckin, { checked_in: false });
    remoteCheckin.refreshStationStage = () => {};
    api.trackEvent = () => {};
    api.checkIn = () => Promise.reject({
      code: 'SITE_GEOFENCE_EXCEEDED',
      error: '距站点约 900m，超出 300m 到场范围，无法打卡',
    });
    remoteCheckin.onCheckIn();
    await flush();
    await flush();
    assert.equal(modals.at(-1).confirmText, '去校准');
    assert.equal(modals.at(-1).cancelText, '暂不校准');
    assert.match(modals.at(-1).content, /定位不准？去校准/);
    assert.equal(navigationUrls.at(-1),
      '/pages/site/site?site_id=20&source=inspection_calibration&action=calibrate');

    navigationUrls = [];
    api.checkIn = () => Promise.reject({ code: 'PLAN_NOT_EXECUTABLE', error: '任务状态已变化' });
    remoteCheckin.onCheckIn();
    await flush();
    await flush();
    assert.equal(modals.at(-1).showCancel, false);
    assert.equal(navigationUrls.length, 0, 'non-geofence failures never offer calibration navigation');

    resetStorage();
    const itemPage = createPage();
    seedStation(itemPage);
    itemPage.data.categories = [{ items: [{
      item_id: 302, plan_id: 10, item_name: '常规检查', result: null,
      required_photos: 0, evidence_attachments: [], requiresCalibration: false,
    }] }];
    itemPage.onPageScroll({ scrollTop: 187 });
    itemPage.onOpenItem({ currentTarget: { dataset: { id: 302 } } });
    assert.equal(itemPage.data.sheet.open, true);
    assert.equal(itemPage.data.sheet.requiresCalibration, false, 'normal items do not acquire a calibration form');
    itemPage.data.sheet.calibrator = '历史脏数据';
    itemPage.data.sheet.calValues = '7.00';
    let normalSubmitWrites = 0;
    api.submitItem = () => { normalSubmitWrites += 1; return Promise.resolve({ photo_urls: [] }); };
    itemPage.onSubmitItem();
    await flush();
    assert.equal(normalSubmitWrites, 0, 'hidden calibration values cannot complete a normal item');
    assert.equal(itemPage.data.sheet.open, true, 'rejected normal submission preserves the item panel');
    assert.equal(toasts.at(-1).title, '请填写现场说明或拍摄照片');
    itemPage.onCloseSheet();
    assert.deepEqual(scrollCalls.at(-1), { scrollTop: 187, duration: 0 });

    resetStorage();
    const supplementPage = createPage();
    seedStation(supplementPage);
    supplementPage.data.categories = [{ items: [{
      item_id: 305, plan_id: 10, item_name: '站房照片', result: 'normal', review_status: 3,
      evidence_status: 'supplement_required', required_photos: 4,
      replacement_required_photos: 1,
      current_photo_urls: ['/uploads/keep-a.jpg', '/uploads/keep-b.jpg', '/uploads/keep-c.jpg'],
      pending_photo_urls: ['/uploads/replacement.jpg'], evidence_attachments: [],
      requiresCalibration: false,
    }] }];
    supplementPage.onOpenItem({ currentTarget: { dataset: { id: 305 } } });
    assert.equal(supplementPage.data.sheet.originalPhotoCount, 3);
    assert.equal(supplementPage.data.sheet.replacementRequiredPhotos, 1);
    assert.deepEqual(supplementPage.data.sheet.photoInfo,
      { required: 1, captured: 1, missing: 0, ready: true });

    resetStorage();
    const pendingReviewPage = createPage();
    seedStation(pendingReviewPage);
    pendingReviewPage.data.categories = [{ items: [{
      item_id: 306, plan_id: 10, item_name: '待审核照片', result: 'normal', review_status: 3,
      evidence_status: 'supplement_required', replacement_photo_status: 'pending_review',
      replacement_block_reason: '原照片待审核，审核完成后才能补拍',
      replacement_required_photos: null, current_photo_urls: [], evidence_attachments: [],
      requiresCalibration: false,
    }] }];
    pendingReviewPage.onOpenItem({ currentTarget: { dataset: { id: 306 } } });
    assert.equal(pendingReviewPage.data.sheet.replacementPhotoStatus, 'pending_review');
    assert.equal(pendingReviewPage.data.sheet.photoInfo.blocked, true);
    let pendingReviewWrites = 0;
    api.submitItem = () => { pendingReviewWrites += 1; return Promise.resolve({}); };
    pendingReviewPage.onSubmitItem();
    assert.equal(pendingReviewWrites, 0, 'pending original review cannot submit a replacement');
    assert.equal(toasts.at(-1).title, '原照片待审核，审核完成后才能补拍');
    pendingReviewPage.onAddPhoto({ currentTarget: { dataset: { source: 'camera' } } });
    assert.equal(toasts.at(-1).title, '原照片待审核，审核完成后才能补拍');

    resetStorage();
    const normalWithRemark = createPage();
    seedStation(normalWithRemark);
    normalWithRemark.data.categories = [{ items: [{
      item_id: 304, plan_id: 10, item_name: '常规检查', result: null,
      required_photos: 0, evidence_attachments: [], requiresCalibration: false,
      calibrator: '历史脏数据', calibration_values: '7.00',
    }] }];
    normalWithRemark.onOpenItem({ currentTarget: { dataset: { id: 304 } } });
    normalWithRemark.data.sheet.remark = '现场读数正常';
    let normalPayload;
    api.submitItem = payload => { normalPayload = payload; return Promise.resolve({ photo_urls: [] }); };
    normalWithRemark.loadTasks = () => {};
    normalWithRemark.onSubmitItem();
    await flush();
    await flush();
    assert.equal(Object.hasOwn(normalPayload, 'calibrator'), false);
    assert.equal(Object.hasOwn(normalPayload, 'calibration_values'), false);

    resetStorage();
    const calibrationPage = createPage();
    seedStation(calibrationPage);
    const calibration = Object.assign({}, itemPage.data.categories[0].items[0], {
      item_id: 303, item_name: '仪器质控', requiresCalibration: true,
    });
    calibrationPage.data.categories = [{ items: [calibration] }];
    calibrationPage.onOpenItem({ currentTarget: { dataset: { id: 303 } } });
    assert.equal(calibrationPage.data.sheet.requiresCalibration, true);
    calibrationPage.data.sheet.remark = '已核对标准液';
    calibrationPage.data.sheet.calibrator = '王工';
    calibrationPage.data.sheet.calValues = '7.00';
    let submittedPayload;
    api.submitItem = payload => { submittedPayload = payload; return Promise.resolve({ photo_urls: [] }); };
    calibrationPage.loadTasks = () => {};
    calibrationPage.onSubmitItem();
    await flush();
    await flush();
    assert.equal(submittedPayload.calibrator, '王工');
    assert.equal(submittedPayload.calibration_values, '7.00');

    resetStorage();
    const checkout = createPage();
    const first = { id: 20, site_id: 20, name: '第一站', checked_in: true, checked_out: false };
    const second = { id: 21, site_id: 21, name: '第二站', checked_in: false, checked_out: false };
    seedStation(checkout, first);
    checkout.data.sites = [first, second];
    checkout.data.currentPackage = { plan_id: 10, schedule_id: 100, work_date: '2026-08-31', site_order: [20, 21], sites: [first, second] };
    checkout.data.categories = [{ items: [{ item_id: 304, plan_id: 10, result: 'normal' }] }];
    checkout.data.total = 1;
    checkout.data.completed = 1;
    checkout.refreshStationStage(20);
    api.checkOutExecutionSite = () => Promise.resolve({ check_out_time: '2026-08-31 10:00:00' });
    api.todayExecution = () => Promise.resolve({ packages: [checkout.data.currentPackage] });
    api.executionSiteTasks = () => Promise.resolve({
      site: Object.assign({}, first, { checked_out: true }), categories: [{ items: [] }],
    });
    api.executionSiteReagents = () => Promise.resolve({ items: [] });
    checkout.onCheckOut();
    await flush();
    await flush();
    assert.equal(checkout.data.stationStage.code, 'checked_out');
    assert.deepEqual(checkout.data.stationOutcome, { type: 'next_site', siteId: 21, siteName: '第二站' });
    assert.equal(toasts.some(item => /巡检项已完成/.test(item.title || '')), false,
      'the final item leaves a stable summary rather than a second checkout modal');
    checkout.onGoToNextSite();
    await flush();
    assert.equal(checkout.data.viewPhase, 'inspection', 'next station remains in field execution, not departure preparation');
    assert.equal(checkout.data.selSiteId, 21);

    checkout.data.sheet.open = false;
    checkout.onBack();
    assert.equal(switchTabs, 1, 'visible field-page return uses a concrete navigation action');

    console.log('inspectionSiteExecution tests passed');
  } finally {
    Object.assign(api, originals);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
