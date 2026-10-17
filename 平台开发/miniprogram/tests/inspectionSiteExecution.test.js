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
let navigationCalls = [];
let chosenPaths = [];
let chosenSourceTypes = [];

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
  navigateTo: options => { navigationUrls.push(options.url); navigationCalls.push(options); },
  chooseMedia: options => {
    chosenSourceTypes.push(options.sourceType);
    options.success({ tempFiles: chosenPaths.map(tempFilePath => ({ tempFilePath })) });
  },
  compressImage: options => options.success({ tempFilePath: options.src }),
  saveFile: options => options.success({
    savedFilePath: 'wxfile://usr/' + String(options.tempFilePath).split('/').pop(),
  }),
  getFileSystemManager: () => ({ readFile: options => options.success({ data: 'image' }) }),
  previewImage: () => {}, reLaunch: () => {}, stopPullDownRefresh: () => {},
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
  navigationCalls = [];
  chosenPaths = [];
  chosenSourceTypes = [];
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
  uploadSitePhoto: api.uploadSitePhoto,
  attachmentDetail: api.attachmentDetail,
  deletePendingSitePhoto: api.deletePendingSitePhoto,
  deletePhoto: api.deletePhoto,
  correctInspectionItemPhotos: api.correctInspectionItemPhotos,
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
    assert.equal((wxml.match(/bindtap="onTakeReportPhoto"/g) || []).length, 1,
      'the inspection report sheet exposes the trusted camera action');
    assert.equal((wxml.match(/bindtap="onAddReportPhoto"/g) || []).length, 1,
      'the inspection report sheet keeps a separate album-material action');
    assert.match(wxml, /wx:elif="\{\{stationStage\.code === 'local_pending'\}\}"[\s\S]{0,180}bindtap="onSyncNow"/);
    assert.doesNotMatch(wxml, /stationStage\.code === 'local_pending'[\s\S]{0,320}bindtap="onCheckOut"/);
    assert.match(wxml, /<!-- 校准信息 -->\s*<view class="ip-fp-section" wx:if="\{\{sheet\.requiresCalibration\}\}">/);
    assert.match(wxml, /本次补拍[\s\S]{0,120}sheet\.photoInfo\.required/);
    assert.doesNotMatch(wxml, /原照片待审核/);
    assert.doesNotMatch(wxml, /replacementPhotoStatus\s*!==\s*'pending_review'/);
    assert.doesNotMatch(wxml, /整改要求：\{\{sheet\.item\.supplement_reason/);

    resetStorage();
    const cameraReportPage = createPage();
    seedStation(cameraReportPage);
    cameraReportPage.onOpenReport();
    const reportUploads = [];
    api.uploadSitePhoto = (siteId, image, key, metadata) => {
      reportUploads.push({ siteId, image, key, metadata });
      return Promise.resolve({
        id: 51, url: '/uploads/site_photos/report-camera.jpg',
        display_url: 'http://127.0.0.1:5000/uploads/site_photos/report-camera.jpg?ticket=camera-ticket',
      });
    };
    const cameraTask = cameraReportPage.onTakeReportPhoto();
    await flush();
    const cameraNavigation = navigationCalls.at(-1);
    assert.match(cameraNavigation.url, /capture_context=manual_report/);
    cameraNavigation.events.trustedCameraCaptured({
      requestId: new URLSearchParams(cameraNavigation.url.split('?')[1]).get('request_id'),
      path: '/tmp/report-camera.jpg', captureSession: 'report-camera-session',
      gpsLat: 28.6, gpsLng: 115.7,
      frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
    });
    await cameraTask;
    assert.equal(reportUploads.length, 1);
    assert.equal(reportUploads[0].siteId, 20);
    assert.equal(reportUploads[0].metadata.capture_context, 'manual_report');
    assert.equal(reportUploads[0].metadata.capture_session, 'report-camera-session');
    assert.equal(reportUploads[0].metadata.capture_frame_ratio, '3:4');
    assert.equal(reportUploads[0].metadata.display_watermark, true,
      'older camera results without the field keep the visible watermark default');
    assert.equal(cameraReportPage.data.reportSheet.photoSiteId, 20);
    assert.deepEqual(cameraReportPage.data.reportSheet.photos, ['/uploads/site_photos/report-camera.jpg']);
    assert.match(cameraReportPage.data.reportSheet.photoItems[0].displayUrl, /\?ticket=camera-ticket$/);

    let attachmentReads = 0;
    api.attachmentDetail = attachmentId => {
      attachmentReads += 1;
      assert.equal(attachmentId, 51);
      return Promise.resolve({
        id: 51,
        stored_path: '/uploads/site_photos/report-camera.jpg',
        display_url: 'http://127.0.0.1:5000/uploads/site_photos/report-camera.jpg?ticket=refreshed-' + attachmentReads,
      });
    };
    const cameraPhotoKey = cameraReportPage.data.reportSheet.photoItems[0].key;
    await cameraReportPage.onReportPhotoMediaError({ currentTarget: { dataset: { key: cameraPhotoKey } } });
    assert.equal(attachmentReads, 1);
    assert.match(cameraReportPage.data.reportSheet.photoItems[0].displayUrl, /ticket=refreshed-1$/);
    await cameraReportPage.onReportPhotoMediaError({ currentTarget: { dataset: { key: cameraPhotoKey } } });
    assert.equal(attachmentReads, 1, 'inspection report media automatically refreshes at most once');
    assert.equal(cameraReportPage.data.reportSheet.photoItems[0].mediaError, '照片加载失败，点击重试');
    await cameraReportPage.onRetryReportPhotoMedia({ currentTarget: { dataset: { key: cameraPhotoKey } } });
    assert.equal(attachmentReads, 2, 'inspection report media exposes an explicit retry');

    const albumReportPage = createPage();
    seedStation(albumReportPage);
    albumReportPage.onOpenReport();
    chosenPaths = ['/tmp/report-album.jpg'];
    api.uploadSitePhoto = (siteId, image, key, metadata) => {
      reportUploads.push({ siteId, image, key, metadata });
      return Promise.resolve({
        id: 52, url: '/uploads/site_photos/report-album.jpg',
        display_url: 'http://127.0.0.1:5000/uploads/site_photos/report-album.jpg?ticket=album-ticket',
      });
    };
    await albumReportPage.onAddReportPhoto();
    assert.deepEqual(chosenSourceTypes.at(-1), ['album']);
    assert.equal(reportUploads.at(-1).metadata.capture_source, 'unknown');
    assert.equal(reportUploads.at(-1).metadata.capture_context, undefined);

    // Failed trusted-camera uploads retain the capture session and retry identity.
    resetStorage();
    const failedUploadPage = createPage();
    seedStation(failedUploadPage);
    failedUploadPage.onOpenReport();
    const retryUploads = [];
    api.uploadSitePhoto = (siteId, image, key, metadata) => {
      retryUploads.push({ siteId, image, key, metadata });
      if (retryUploads.length === 1) return Promise.reject({ error: '网络响应丢失' });
      return Promise.resolve({
        id: 53, url: '/uploads/site_photos/report-retry.jpg',
        display_url: 'http://127.0.0.1:5000/uploads/site_photos/report-retry.jpg?ticket=retry-ticket',
      });
    };
    const failedCaptureTask = failedUploadPage.onTakeReportPhoto();
    await flush();
    const failedNavigation = navigationCalls.at(-1);
    failedNavigation.events.trustedCameraCaptured({
      requestId: new URLSearchParams(failedNavigation.url.split('?')[1]).get('request_id'),
      path: '/tmp/report-retry.jpg', captureSession: 'report-retry-session',
      gpsLat: 28.6, gpsLng: 115.7,
      frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
      displayWatermark: false,
    });
    await failedCaptureTask;
    const failedPhoto = failedUploadPage.data.reportSheet.photoItems[0];
    assert.equal(failedPhoto.status, 'failed');
    assert.equal(failedPhoto.localPath, 'wxfile://usr/report-retry.jpg');
    assert.equal(failedPhoto.metadata.capture_session, 'report-retry-session');
    assert.equal(failedPhoto.metadata.display_watermark, false);
    assert.equal(failedPhoto.metadata._idempotency_key, failedPhoto.idempotencyKey);
    failedUploadPage.data.reportSheet.description = '泵房异响';
    let blockedReportWrites = 0;
    api.submitManualReport = () => { blockedReportWrites += 1; return Promise.resolve({}); };
    await failedUploadPage.onSubmitReport();
    assert.equal(blockedReportWrites, 0);
    await failedUploadPage.onRetryReportPhoto({ currentTarget: { dataset: { key: failedPhoto.key } } });
    assert.equal(retryUploads[1].key, retryUploads[0].key);
    assert.equal(retryUploads[1].metadata.capture_session, 'report-retry-session');
    assert.equal(retryUploads[1].metadata.display_watermark, false,
      'manual report retry preserves the original photo watermark choice');
    assert.equal(failedUploadPage.data.reportSheet.photoItems[0].status, 'uploaded');
    assert.match(failedUploadPage.data.reportSheet.photoItems[0].displayUrl, /\?ticket=retry-ticket$/);
    assert.deepEqual(failedUploadPage.data.reportSheet.photos, ['/uploads/site_photos/report-retry.jpg']);

    // A capture-session rejection cannot reuse the old upload identity and requires a fresh camera capture.
    resetStorage();
    const expiredCapturePage = createPage();
    seedStation(expiredCapturePage);
    expiredCapturePage.onOpenReport();
    const expiredUploads = [];
    api.uploadSitePhoto = (siteId, image, key, metadata) => {
      expiredUploads.push({ siteId, image, key, metadata });
      if (expiredUploads.length === 1) {
        return Promise.resolve({
          success: true,
          accepted_for_review: false,
          code: 'CAPTURE_SESSION_REQUIRED',
          reason: '异常上报现场拍摄授权无效、已过期或与当前站点不一致',
          next_action: '请返回异常上报重新发起现场拍摄',
        });
      }
      return Promise.resolve({
        id: 54,
        url: '/uploads/site_photos/report-recaptured.jpg',
        display_url: 'http://127.0.0.1:5000/uploads/site_photos/report-recaptured.jpg?ticket=recaptured-ticket',
      });
    };
    const expiredTask = expiredCapturePage.onTakeReportPhoto();
    await flush();
    const expiredNavigation = navigationCalls.at(-1);
    expiredNavigation.events.trustedCameraCaptured({
      requestId: new URLSearchParams(expiredNavigation.url.split('?')[1]).get('request_id'),
      path: '/tmp/report-expired.jpg', captureSession: 'expired-report-session',
      gpsLat: 28.6, gpsLng: 115.7,
      frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
    });
    await expiredTask;
    const expiredPhoto = expiredCapturePage.data.reportSheet.photoItems[0];
    assert.equal(expiredPhoto.requiresNewCapture, true);
    assert.equal(expiredPhoto.failureCode, 'CAPTURE_SESSION_REQUIRED');
    assert.equal(expiredPhoto.error, '异常上报现场拍摄授权无效、已过期或与当前站点不一致');
    assert.equal(expiredPhoto.nextAction, '请返回异常上报重新发起现场拍摄');
    const recaptureTask = expiredCapturePage.onRetryReportPhoto({
      currentTarget: { dataset: { key: expiredPhoto.key } },
    });
    await flush();
    assert.equal(expiredUploads.length, 1, 'inspection never retries the rejected capture session');
    assert.match(modals.at(-1).content, /请返回异常上报重新发起现场拍摄/);
    const recaptureNavigation = navigationCalls.at(-1);
    recaptureNavigation.events.trustedCameraCaptured({
      requestId: new URLSearchParams(recaptureNavigation.url.split('?')[1]).get('request_id'),
      path: '/tmp/report-recaptured.jpg', captureSession: 'fresh-report-session',
      gpsLat: 28.6, gpsLng: 115.7,
      frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
    });
    await recaptureTask;
    assert.equal(expiredUploads.length, 2);
    assert.equal(expiredUploads[1].metadata.capture_session, 'fresh-report-session');
    assert.notEqual(expiredUploads[1].key, expiredUploads[0].key);
    assert.equal(expiredCapturePage.data.reportSheet.photoItems.length, 1);
    assert.equal(expiredCapturePage.data.reportSheet.photoItems[0].status, 'uploaded');
    assert.deepEqual(expiredCapturePage.data.reportSheet.photos, ['/uploads/site_photos/report-recaptured.jpg']);

    resetStorage();
    const reportPage = createPage();
    seedStation(reportPage);
    reportPage.data.reportSheet = {
      open: true, siteId: 20, siteName: '青云站', typeIndex: 1, description: '',
      photos: [], photoSiteId: null, uploading: false, submitting: false
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
    reportPage.data.reportSheet.photoSiteId = 20;
    const immediateSetData = reportPage.setData;
    reportPage.setData = (patch, done) => {
      if (patch['reportSheet.submitting'] === true) return;
      immediateSetData(patch, done);
    };
    reportPage.onSubmitReport();
    reportPage.onSubmitReport();
    await flush();
    await flush();
    assert.equal(reportWrites, 1, 'double taps issue one report request before async setData is visible');
    reportPage.setData = immediateSetData;
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
      open: true, siteId: 20, siteName: '青云站', typeIndex: 1, description: '水泵异响',
      photos: ['/uploads/site_photos/equipment.jpg'], photoSiteId: 20,
      uploading: false, submitting: false
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
    const submittedAwayPage = createPage();
    seedStation(submittedAwayPage, { checked_in: false, checked_out: true });
    submittedAwayPage.data.categories = [{ items: [{
      item_id: 311, plan_id: 10, item_name: '离站后已提交项', result: 'normal',
      review_status: 2, evidence_status: 'effective', required_photos: 0,
      photo_urls: '[]', evidence_attachments: [], requiresCalibration: false,
    }, {
      item_id: 312, plan_id: 10, item_name: '未重新签到的整改项', result: 'abnormal',
      review_status: 3, evidence_status: 'supplement_required', required_photos: 1,
      current_photo_urls: ['/uploads/original.jpg'], evidence_attachments: [],
      requiresCalibration: false,
    }] }];
    submittedAwayPage.onOpenItem({ currentTarget: { dataset: { id: 311 } } });
    assert.equal(submittedAwayPage.data.sheet.open, true,
      'submitted items remain available after checkout');
    assert.equal(submittedAwayPage.data.sheet.readOnly, true);
    submittedAwayPage.onCloseSheet();
    submittedAwayPage.onOpenItem({ currentTarget: { dataset: { id: 312 } } });
    assert.equal(submittedAwayPage.data.sheet.open, true,
      'a submitted rework item remains available before the next check-in');
    assert.equal(submittedAwayPage.data.sheet.readOnly, true,
      'rework editing still requires an active site check-in');
    assert.equal(modals.some(item => item.title === '请先到站打卡'), false,
      'read-only submitted-item review never opens the check-in action modal');

    resetStorage();
    const cameraItemPage = createPage();
    seedStation(cameraItemPage);
    cameraItemPage.data.categories = [{ items: [{
      item_id: 308, plan_id: 10, item_name: '相机水印契约', result: null,
      category: 'log_books',
      required_photos: 1, evidence_attachments: [], requiresCalibration: false,
    }] }];
    cameraItemPage.onOpenItem({ currentTarget: { dataset: { id: 308 } } });
    const inspectionUploads = [];
    api.uploadSitePhoto = (siteId, image, key, metadata) => {
      inspectionUploads.push({ siteId, image, key, metadata });
      return Promise.resolve({
        id: 61, url: '/uploads/site_photos/inspection-hidden-watermark.jpg',
        display_url: '/uploads/site_photos/inspection-hidden-watermark.jpg',
        accepted_for_review: true, supplemental: false,
      });
    };
    const inspectionCaptureTask = cameraItemPage.onAddPhoto({
      currentTarget: { dataset: { source: 'camera' } },
    });
    await flush();
    const inspectionNavigation = navigationCalls.at(-1);
    assert.equal(new URLSearchParams(inspectionNavigation.url.split('?')[1]).get('item_category'),
      'log_books', 'inspection category reaches the trusted camera entry');
    inspectionNavigation.events.trustedCameraCaptured({
      requestId: new URLSearchParams(inspectionNavigation.url.split('?')[1]).get('request_id'),
      path: '/tmp/inspection-hidden-watermark.jpg',
      captureSession: 'inspection-hidden-watermark-session',
      gpsLat: 28.6, gpsLng: 115.7,
      frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
      displayWatermark: false,
    });
    await inspectionCaptureTask;
    assert.equal(inspectionUploads.length, 1);
    assert.equal(inspectionUploads[0].metadata.display_watermark, false);
    assert.equal(inspectionUploads[0].metadata.capture_session,
      'inspection-hidden-watermark-session');

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
    const legacyPendingReviewPage = createPage();
    seedStation(legacyPendingReviewPage);
    legacyPendingReviewPage.data.categories = [{ items: [{
      item_id: 306, plan_id: 10, item_name: '待审核照片', result: 'normal', review_status: 3,
      evidence_status: 'supplement_required', replacement_photo_status: 'pending_review',
      replacement_submission_allowed: false,
      replacement_block_reason: '原照片待审核，审核完成后才能补拍',
      replacement_required_photos: null, current_photo_urls: [], evidence_attachments: [],
      requiresCalibration: false,
    }] }];
    legacyPendingReviewPage.onOpenItem({ currentTarget: { dataset: { id: 306 } } });
    assert.equal(legacyPendingReviewPage.data.sheet.replacementPhotoStatus, 'pending_review');
    assert.equal(legacyPendingReviewPage.data.sheet.photoInfo.blocked === true, false,
      'legacy pending-review data no longer blocks replacement capture');
    let pendingReviewWrites = 0;
    api.submitItem = () => { pendingReviewWrites += 1; return Promise.resolve({}); };
    legacyPendingReviewPage.onSubmitItem();
    assert.equal(pendingReviewWrites, 0, 'replacement still requires a newly accepted photo');
    assert.equal(toasts.at(-1).title, '请先补充新的现场照片');
    let photoPickerCalls = 0;
    wx.chooseMedia = options => {
      photoPickerCalls += 1;
      options.fail({ errMsg: 'chooseMedia:fail cancel' });
    };
    await legacyPendingReviewPage.onAddPhoto({ currentTarget: { dataset: { source: 'album' } } });
    delete wx.chooseMedia;
    assert.equal(photoPickerCalls, 1, 'legacy pending-review data permits a new photo selection');
    assert.equal(toasts.some(item => /原照片待审核/.test(item.title || '')), false);

    resetStorage();
    const replacementSubmittedPage = createPage();
    seedStation(replacementSubmittedPage);
    replacementSubmittedPage.data.categories = [{ items: [{
      item_id: 307, plan_id: 10, item_name: '补拍已提交', result: 'normal', review_status: 1,
      evidence_status: 'replacement_submitted', replacement_photo_status: 'replacement_pending_review',
      replacement_submission_allowed: false,
      replacement_block_reason: '补拍照片已提交业务审核，审核完成前不能重复编辑',
      replacement_required_photos: null, current_photo_urls: ['/uploads/replacement.jpg'],
      pending_photo_urls: ['/uploads/replacement.jpg'], evidence_attachments: [],
      requiresCalibration: false,
    }] }];
    replacementSubmittedPage.onOpenItem({ currentTarget: { dataset: { id: 307 } } });
    assert.equal(replacementSubmittedPage.data.sheet.photoInfo.blocked, true);
    let lockedPickerCalls = 0;
    wx.chooseMedia = () => { lockedPickerCalls += 1; };
    replacementSubmittedPage.onAddPhoto({ currentTarget: { dataset: { source: 'album' } } });
    delete wx.chooseMedia;
    assert.equal(lockedPickerCalls, 0, 'submitted replacement cannot start another photo selection');
    assert.equal(toasts.at(-1).title, '补拍照片已提交业务审核，审核完成前不能重复编辑');
    const lockedPhotos = replacementSubmittedPage.data.sheet.photos.slice();
    replacementSubmittedPage.onDelPhoto({ currentTarget: { dataset: { idx: 0 } } });
    assert.deepEqual(replacementSubmittedPage.data.sheet.photos, lockedPhotos,
      'submitted replacement cannot delete photos while business review is pending');
    let replacementSubmitWrites = 0;
    api.submitItem = () => { replacementSubmitWrites += 1; return Promise.resolve({}); };
    replacementSubmittedPage.onSubmitItem();
    assert.equal(replacementSubmitWrites, 0, 'submitted replacement cannot be submitted twice');

    resetStorage();
    const correctionPage = createPage();
    seedStation(correctionPage);
    correctionPage.data.categories = [{ items: [{
      item_id: 309, plan_id: 10, item_name: '已提交检查项', result: 'abnormal',
      remark: '原说明', requiresCalibration: true, calibrator: '原校准人',
      calibration_values: '7.00', review_status: 1, evidence_status: 'accepted',
      photo_correction_allowed: true, photo_correction_block_reason: '', required_photos: 1,
      photo_urls: JSON.stringify(['/uploads/formal-a.jpg', '/uploads/formal-b.jpg']),
      pending_photo_urls: [], evidence_attachments: [],
    }] }];
    correctionPage.onOpenItem({ currentTarget: { dataset: { id: 309 } } });
    assert.equal(correctionPage.data.sheet.open, true);
    assert.equal(correctionPage.data.sheet.readOnly, true,
      'a submitted item always opens in read-only mode');
    assert.equal(correctionPage.data.sheet.photoCorrection, false);
    correctionPage.onStartPhotoCorrection();
    assert.equal(correctionPage.data.sheet.readOnly, false);
    assert.equal(correctionPage.data.sheet.photoCorrection, true,
      'the explicit action enters photo-only correction mode');
    correctionPage.onSetResult({ currentTarget: { dataset: { r: 'normal' } } });
    correctionPage.onRemark({ detail: { value: '被篡改说明' } });
    correctionPage.onCalibrator({ detail: { value: '被篡改校准人' } });
    correctionPage.onCalValues({ detail: { value: '9.99' } });
    assert.equal(correctionPage.data.sheet.result, 'abnormal');
    assert.equal(correctionPage.data.sheet.remark, '原说明');
    assert.equal(correctionPage.data.sheet.calibrator, '原校准人');
    assert.equal(correctionPage.data.sheet.calValues, '7.00');

    let legacyFormalDeletes = 0;
    api.deletePhoto = () => { legacyFormalDeletes += 1; return Promise.resolve({}); };
    const formalDisplayUrl = correctionPage.data.sheet.photos[0];
    correctionPage.data.sheet.correctionIdempotencyKey = 'stale-formal-key';
    correctionPage.onDelPhoto({ currentTarget: { dataset: { idx: 1 } } });
    assert.equal(legacyFormalDeletes, 0,
      'formal photo correction never calls the legacy immediate-delete endpoint');
    assert.deepEqual(correctionPage.data.sheet.photos, [formalDisplayUrl]);
    assert.equal(correctionPage.data.sheet.correctionIdempotencyKey, '',
      'changing the formal photo selection rotates the correction idempotency key');

    correctionPage.setData({
      'sheet.photos': [formalDisplayUrl, '/uploads/pending-new.jpg'],
      'sheet.pendingPhotos': ['/uploads/pending-new.jpg'],
      'sheet.correctionIdempotencyKey': 'stale-pending-key',
    });
    let pendingDeletes = 0;
    api.deletePendingSitePhoto = path => {
      pendingDeletes += 1;
      return Promise.resolve({ success: true, deleted: true, path });
    };
    await correctionPage.onDelPhoto({ currentTarget: { dataset: { idx: 1 } } });
    assert.equal(pendingDeletes, 1);
    assert.deepEqual(correctionPage.data.sheet.photos, [formalDisplayUrl]);
    assert.deepEqual(correctionPage.data.sheet.pendingPhotos, []);
    assert.equal(correctionPage.data.sheet.correctionIdempotencyKey, '',
      'removing a pending upload also rotates the correction idempotency key');

    let correctionPayload = null;
    let correctionCalls = 0;
    api.correctInspectionItemPhotos = (itemId, payload) => {
      correctionCalls += 1;
      correctionPayload = { itemId, payload };
      return Promise.resolve({ photo_urls: ['/uploads/formal-a.jpg'] });
    };
    correctionPage.loadTasks = () => {};
    await correctionPage.onSavePhotoCorrection();
    assert.equal(correctionCalls, 1);
    assert.equal(correctionPayload.itemId, 309);
    assert.deepEqual(correctionPayload.payload.photo_urls, ['/uploads/formal-a.jpg']);
    assert.match(correctionPayload.payload._idempotency_key,
      /^inspection-photo-correction-309-/);
    assert.equal(Object.hasOwn(correctionPayload.payload, 'result'), false);
    assert.equal(Object.hasOwn(correctionPayload.payload, 'remark'), false);
    assert.equal(Object.hasOwn(correctionPayload.payload, 'calibrator'), false);
    assert.equal(Object.hasOwn(correctionPayload.payload, 'calibration_values'), false);
    assert.equal(correctionPage.data.sheet.open, false);

    resetStorage();
    const blockedCorrectionPage = createPage();
    seedStation(blockedCorrectionPage);
    blockedCorrectionPage.data.categories = [{ items: [{
      item_id: 310, plan_id: 10, item_name: '人工审核后检查项', result: 'normal',
      review_status: 2, evidence_status: 'accepted', photo_correction_allowed: false,
      photo_correction_block_reason: '审核人已形成结论，不能再修改照片',
      required_photos: 0, photo_urls: '[]', evidence_attachments: [],
      requiresCalibration: false,
    }] }];
    blockedCorrectionPage.onOpenItem({ currentTarget: { dataset: { id: 310 } } });
    assert.equal(blockedCorrectionPage.data.sheet.readOnly, true);
    blockedCorrectionPage.onStartPhotoCorrection();
    assert.equal(blockedCorrectionPage.data.sheet.photoCorrection, false,
      'the client cannot enter correction mode without server capability');
    assert.equal(toasts.at(-1).title, '审核人已形成结论，不能再修改照片');

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
