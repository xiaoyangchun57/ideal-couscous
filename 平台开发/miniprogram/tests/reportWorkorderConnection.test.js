const assert = require('node:assert/strict');

const definitions = {};
const previews = [];
const toasts = [];
const modals = [];
const navigations = [];
let chosenPaths = [];
let chosenSourceTypes = [];
global.Page = page => { definitions.current = page; };
global.getApp = () => ({ globalData: {} });
global.wx = {
  previewImage: options => previews.push(options),
  navigateTo: options => navigations.push(options),
  showToast: options => toasts.push(options),
  showModal: options => { modals.push(options); if (options.success) options.success({ confirm: true }); },
  showLoading() {}, hideLoading() {}, reLaunch() {},
  getStorageSync: () => '',
  chooseMedia: options => {
    chosenSourceTypes.push(options.sourceType);
    options.success({ tempFiles: chosenPaths.map(tempFilePath => ({ tempFilePath })) });
  },
  compressImage: options => options.success({ tempFilePath: options.src }),
  saveFile: options => options.success({
    savedFilePath: 'wxfile://usr/' + String(options.tempFilePath).split('/').pop(),
  }),
  removeSavedFile: options => { if (options.success) options.success({}); },
  getFileSystemManager: () => ({ readFile: options => options.success({ data: 'image' }) }),
  authorize: options => options.success(),
  getLocation: options => options.success({ latitude: 28.68, longitude: 115.86 }),
};

const api = require('../services/api.js');
require('../pages/reports/reports.js');
const reportsPage = definitions.current;
require('../pages/workorder/workorder.js');
const workorderPage = definitions.current;

function pageInstance(definition, data) {
  const page = Object.assign({}, definition, {
    data: Object.assign(JSON.parse(JSON.stringify(definition.data)), data || {}),
  });
  page.setData = updates => Object.entries(updates).forEach(([key, value]) => {
    const parts = key.split('.');
    let target = page.data;
    while (parts.length > 1) {
      const part = parts.shift();
      target = target[part];
    }
    target[parts[0]] = value;
  });
  return page;
}

function reportSheet(sites, overrides) {
  return Object.assign({
    open: true, sites, siteId: sites[0] && sites[0].id, siteName: sites[0] && sites[0].name,
    siteIndex: 0, typeIndex: 0, description: '', photos: [], photoItems: [], photoSiteId: null,
    uploading: false, submitting: false,
  }, overrides || {});
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setImmediate(resolve));
}

async function run() {
  const detailDisplayUrl = 'http://127.0.0.1:5000/uploads/site_photos/old.jpg?ticket=detail-ticket';
  let detailReads = 0;
  api.manualReports = () => {
    detailReads += 1;
    return Promise.resolve([{
      id: 1,
      report_type: 'equipment',
      status: 'open',
      photo_urls: JSON.stringify(['/uploads/site_photos/old.jpg']),
      photo_media: [{
        id: 7,
        stored_path: '/uploads/site_photos/old.jpg',
        display_url: 'http://127.0.0.1:5000/uploads/site_photos/old.jpg?ticket=fresh-' + detailReads,
      }],
    }]);
  };
  const report = pageInstance(reportsPage, { reports: [{
    id: 1,
    photo_urls: JSON.stringify(['/uploads/site_photos/old.jpg', 'bad-path', 'https://other.example/x.jpg']),
    photo_media: [{ id: 7, stored_path: '/uploads/site_photos/old.jpg', display_url: detailDisplayUrl }],
  }] });
  await report.onOpenDetail({ currentTarget: { dataset: { id: 1 } } });
  assert.equal(detailReads, 1, 'opening a submitted report rereads current photo media');
  assert.equal(report.data.detail.photos.length, 1);
  assert.equal(report.data.detail.photos[0].storedPath, '/uploads/site_photos/old.jpg');
  assert.match(report.data.detail.photos[0].url, /ticket=fresh-1$/);
  await report.onDetailPhotoMediaError();
  assert.equal(detailReads, 2, 'the first image failure performs one automatic detail refresh');
  await report.onDetailPhotoMediaError();
  assert.equal(detailReads, 2, 'the same detail does not loop automatic refreshes');
  assert.equal(report.data.detail.mediaError, '照片加载失败，点击重试');
  await report.onRetryDetailMedia();
  assert.equal(detailReads, 3, 'the visible retry action can explicitly refresh again');
  await report.onPreviewPhoto({ currentTarget: { dataset: { storedPath: '/uploads/site_photos/old.jpg' } } });
  assert.equal(detailReads, 4, 'preview rereads current media before using a ticket');
  assert.deepEqual(previews.at(-1).urls, [
    'http://127.0.0.1:5000/uploads/site_photos/old.jpg?ticket=fresh-4',
  ]);

  api.workorderDetail = orderNo => Promise.resolve({ order_no: orderNo, status: 'reviewing', flow_events: [{ type: 'accepted', label: '接单' }] });
  api.workorderRelated = () => Promise.resolve({});
  const workorder = pageInstance(workorderPage);
  workorder.openWorkorderSheet({ order_no: 'WO-1', status: 'reviewing' });
  await settle();
  assert.equal(workorder.data.sheet.item.flowUnavailable, false);
  assert.deepEqual(workorder.data.sheet.item.flowEvents.map(event => event.type), ['accepted']);
  api.workorderDetail = orderNo => Promise.resolve({ order_no: orderNo, status: 'reviewing' });
  workorder.openWorkorderSheet({ order_no: 'WO-2', status: 'reviewing' });
  await settle();
  assert.equal(workorder.data.sheet.item.flowUnavailable, true);

  let checkinWrites = 0;
  let checkinDetailReads = 0;
  api.checkIn = () => { checkinWrites += 1; return Promise.resolve({ success: true }); };
  api.workorderRelated = () => Promise.resolve({ parts: [], recycles: [], vehicles: [] });
  api.workorderDetail = orderNo => {
    checkinDetailReads += 1;
    return Promise.resolve({
      order_no: orderNo, site_id: 7, site_name: '签到站', status: 'in_progress',
      checked_in: true, check_in_time: '2026-10-14 17:00:00',
      actions: { primary: 'submit_review', can_apply_resources: true },
      flow_events: [],
    });
  };
  const checkedIn = pageInstance(workorderPage, { sheet: { open: true, item: {
    order_no: 'WO-CHECKIN-1', site_id: 7, site_name: '签到站', status: 'in_progress',
    actions: { primary: 'check_in' }, detailStale: false,
  } } });
  await checkedIn.onCheckIn();
  await settle();
  assert.equal(checkinWrites, 1);
  assert.equal(checkinDetailReads, 1, 'successful check-in rereads the authoritative detail');
  assert.equal(checkedIn.data.sheet.item.actions.primary, 'submit_review');
  assert.equal(checkedIn.data.sheet.item.detailStale, false);

  api.workorderDetail = () => Promise.reject({ error: '详情服务超时' });
  const staleAfterCheckin = pageInstance(workorderPage, { sheet: { open: true, item: {
    order_no: 'WO-CHECKIN-2', site_id: 7, site_name: '签到站', status: 'in_progress',
    actions: { primary: 'check_in' }, detailStale: false,
  } } });
  await staleAfterCheckin.onCheckIn();
  await settle();
  assert.equal(staleAfterCheckin.data.sheet.item.checked_in, true,
    'a detail read failure must not erase the successful check-in');
  assert.equal(staleAfterCheckin.data.sheet.item.detailStale, true);
  assert.match(staleAfterCheckin.data.detailError, /签到已成功/);
  assert.equal(toasts.at(-1).title, '签到成功，请刷新详情');
  api.workorderDetail = orderNo => Promise.resolve({
    order_no: orderNo, site_id: 7, status: 'in_progress', checked_in: true,
    actions: { primary: 'submit_review', can_apply_resources: true }, flow_events: [],
  });
  await staleAfterCheckin.onRefreshWorkorderDetail();
  assert.equal(staleAfterCheckin.data.sheet.item.detailStale, false);
  assert.equal(staleAfterCheckin.data.sheet.item.actions.primary, 'submit_review');

  let vehiclePayload;
  api.applyVehicle = payload => { vehiclePayload = payload; return Promise.resolve({ id: 91 }); };
  api.workorderRelated = () => Promise.resolve({
    parts: [], recycles: [], vehicles: [{ id: 91, status: 'pending', plate_no: '赣A测试' }],
  });
  staleAfterCheckin.data.vehicleOptions = [{ id: 5, label: '赣A测试' }];
  staleAfterCheckin.onApplyVehicle();
  staleAfterCheckin.onCloseVehicle();
  assert.equal(vehiclePayload, undefined, 'closing the optional vehicle sheet creates no record');
  assert.equal(staleAfterCheckin.data.vehicleApply.open, false);
  staleAfterCheckin.onApplyVehicle();
  staleAfterCheckin.onVehicleRequirementChange({ detail: { value: true } });
  staleAfterCheckin.onVehicleReason({ detail: { value: '工单现场处置' } });
  const vehicleRequestKey = staleAfterCheckin.data.vehicleApply.requestKey;
  await staleAfterCheckin.submitVehicle();
  assert.equal(vehiclePayload._idempotency_key, vehicleRequestKey);
  assert.equal(vehiclePayload.vehicle_id, 5);
  assert.equal(Object.hasOwn(vehiclePayload, 'no_vehicle_required'), false);
  assert.equal(Object.hasOwn(vehiclePayload, 'vehicle_exception_reason'), false);
  assert.equal(staleAfterCheckin.data.vehicleApply.open, false);
  assert.equal(staleAfterCheckin.data.sheet.item.related.vehicles.length, 1,
    'successful resource requests are visible after the related-resource reread');

  let partsPayload;
  api.applyParts = payload => { partsPayload = payload; return Promise.resolve({ id: 92 }); };
  api.workorderRelated = () => Promise.resolve({
    parts: [{ id: 92, status: 'pending', part_name: '临时接头' }], recycles: [],
    vehicles: [{ id: 91, status: 'pending', plate_no: '赣A测试' }],
  });
  staleAfterCheckin.data.partsApply = {
    open: true, fulfillmentIndex: 1, fulfillment_type: 'local_purchase',
    part_name: '临时接头', specification: '6mm', estimated_amount: '30',
    quantity: 1, reason: '现场更换', index: 0, submitting: false, requestKey: 'parts-workorder-92',
  };
  staleAfterCheckin.data.partsInventoryStatus = 'ready';
  await staleAfterCheckin.submitParts();
  assert.equal(partsPayload._idempotency_key, 'parts-workorder-92');
  assert.equal(staleAfterCheckin.data.partsApply.open, false);
  assert.equal(staleAfterCheckin.data.sheet.item.related.parts.length, 1,
    'successful parts requests are visible after the related-resource reread');

  api.workorderRelated = () => Promise.reject({ error: '关联资源服务超时' });
  staleAfterCheckin.onApplyVehicle();
  staleAfterCheckin.onVehicleRequirementChange({ detail: { value: true } });
  staleAfterCheckin.onVehicleReason({ detail: { value: '再次申请车辆' } });
  await staleAfterCheckin.submitVehicle();
  assert.equal(staleAfterCheckin.data.vehicleApply.open, false,
    'a completed write is not presented as a failed application');
  assert.equal(staleAfterCheckin.data.sheet.item.relatedUnavailable, true);
  assert.equal(toasts.at(-1).title, '申请成功，关联资源刷新失败');
  api.workorderRelated = () => Promise.resolve({ parts: [], recycles: [], vehicles: [{ id: 93 }] });
  await staleAfterCheckin.onRefreshRelated();
  assert.equal(staleAfterCheckin.data.sheet.item.relatedUnavailable, false);
  assert.equal(staleAfterCheckin.data.sheet.item.related.vehicles.length, 1);

  const workorderWxml = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'pages/workorder/workorder.wxml'), 'utf8');
  assert.match(workorderWxml, /bindtap="onRefreshWorkorderDetail"/);
  assert.match(workorderWxml, /bindtap="onRefreshRelated"/);
  assert.doesNotMatch(workorderWxml, /本工单无需用车|无需用车原因|onNoVehicleRequired|onVehicleExceptionReason/);
  const workorderSource = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'pages/workorder/workorder.js'), 'utf8');
  assert.doesNotMatch(workorderSource,
    /noVehicleRequired|exceptionReason|no_vehicle_required|vehicle_exception_reason/);
  assert.doesNotMatch(workorderWxml, /车辆关联尚未接入/);

  // A single current server-authorized site opens the form directly.
  api.sites = () => Promise.resolve([{ id: 21, name: '单站' }]);
  const single = pageInstance(reportsPage);
  await single.onGoReport();
  assert.equal(single.data.reportSheet.open, true);
  assert.equal(single.data.reportSheet.siteId, 21);

  // Several sites deliberately require an explicit choice, not a guessed first site.
  api.sites = () => Promise.resolve([{ id: 21, name: '甲站' }, { id: 22, name: '乙站' }]);
  const multiple = pageInstance(reportsPage);
  await multiple.onGoReport();
  assert.equal(multiple.data.reportSheet.open, true);
  assert.equal(multiple.data.reportSheet.siteId, null);
  multiple.onReportSite({ detail: { value: '1' } });
  assert.equal(multiple.data.reportSheet.siteId, 22);

  // No sites never opens a fake form, and a failed request can be retried in place.
  api.sites = () => Promise.resolve([]);
  const empty = pageInstance(reportsPage);
  await empty.onGoReport();
  assert.equal(empty.data.reportSheet.open, false);
  let siteAttempts = 0;
  api.sites = () => {
    siteAttempts += 1;
    return siteAttempts === 1 ? Promise.reject({ error: '网络异常' }) : Promise.resolve([{ id: 23, name: '重试站' }]);
  };
  const retry = pageInstance(reportsPage);
  await retry.onGoReport();
  assert.equal(retry.data.reportSiteLoadError, '网络异常');
  await retry.onRetryReportSites();
  assert.equal(retry.data.reportSheet.siteId, 23);

  // Repeated taps share one site read; an unloaded page ignores that old response.
  const pendingSites = deferred();
  let siteReads = 0;
  api.sites = () => { siteReads += 1; return pendingSites.promise; };
  const repeated = pageInstance(reportsPage);
  const firstOpen = repeated.onGoReport();
  const secondOpen = repeated.onGoReport();
  assert.equal(siteReads, 1);
  assert.equal(firstOpen, secondOpen);
  pendingSites.resolve([{ id: 24, name: '去重站' }]);
  await firstOpen;
  assert.equal(repeated.data.reportSheet.open, true);
  const staleSites = deferred();
  api.sites = () => staleSites.promise;
  const stale = pageInstance(reportsPage);
  const staleOpen = stale.onGoReport();
  stale.onHide();
  staleSites.resolve([{ id: 25, name: '过期站' }]);
  await staleOpen;
  assert.equal(stale.data.reportSheet.open, false);

  // Upload is bound to the selected site and a photo-bearing draft cannot switch site.
  const siteA = { id: 31, name: '甲站' };
  const siteB = { id: 32, name: '乙站' };
  const uploadCalls = [];
  api.uploadSitePhoto = (siteId, image) => {
    uploadCalls.push({ siteId, image });
    return Promise.resolve({
      id: 31, url: '/uploads/site_photos/31-a.jpg',
      display_url: 'http://127.0.0.1:5000/uploads/site_photos/31-a.jpg?ticket=album-ticket',
    });
  };
  chosenPaths = ['/tmp/31-a.jpg'];
  const photos = pageInstance(reportsPage, { reportSheet: reportSheet([siteA, siteB]) });
  await photos.onAddReportPhoto();
  assert.deepEqual(uploadCalls.map(call => call.siteId), [31]);
  assert.deepEqual(chosenSourceTypes.at(-1), ['album']);
  assert.equal(photos.data.reportSheet.photoSiteId, 31);
  photos.onReportSite({ detail: { value: '1' } });
  assert.equal(photos.data.reportSheet.siteId, 31);

  let attachmentReads = 0;
  api.attachmentDetail = attachmentId => {
    attachmentReads += 1;
    assert.equal(attachmentId, 31);
    return Promise.resolve({
      id: 31,
      stored_path: '/uploads/site_photos/31-a.jpg',
      display_url: 'http://127.0.0.1:5000/uploads/site_photos/31-a.jpg?ticket=refreshed-' + attachmentReads,
    });
  };
  const uploadedKey = photos.data.reportSheet.photoItems[0].key;
  await photos.onReportPhotoMediaError({ currentTarget: { dataset: { key: uploadedKey } } });
  assert.equal(attachmentReads, 1);
  assert.match(photos.data.reportSheet.photoItems[0].displayUrl, /ticket=refreshed-1$/);
  await photos.onReportPhotoMediaError({ currentTarget: { dataset: { key: uploadedKey } } });
  assert.equal(attachmentReads, 1, 'a draft photo automatically refreshes at most once');
  assert.equal(photos.data.reportSheet.photoItems[0].mediaError, '照片加载失败，点击重试');
  await photos.onRetryReportPhotoMedia({ currentTarget: { dataset: { key: uploadedKey } } });
  assert.equal(attachmentReads, 2, 'the user can explicitly retry a draft media refresh');

  const failedSiteItem = {
    key: 'failed-site-photo', status: 'failed', storedPath: '', displayUrl: 'wxfile://usr/failed.jpg',
    localPath: 'wxfile://usr/failed.jpg', idempotencyKey: 'stable-failed-key',
    metadata: { capture_session: 'stable-session', _idempotency_key: 'stable-failed-key' },
    error: '网络响应丢失', nextAction: '', failureCode: '', requiresNewCapture: false,
  };
  const protectedDraft = pageInstance(reportsPage, { reportSheet: reportSheet([siteA, siteB], {
    description: '保留失败照片', photoItems: [failedSiteItem], photos: [], photoSiteId: 31,
  }) });
  protectedDraft.onReportSite({ detail: { value: '0' } });
  assert.equal(protectedDraft.data.reportSheet.photoItems[0].metadata.capture_session, 'stable-session',
    'selecting the current site preserves the full failed-photo draft');
  protectedDraft.onReportSite({ detail: { value: '1' } });
  assert.equal(protectedDraft.data.reportSheet.siteId, 31);
  assert.equal(protectedDraft.data.reportSheet.description, '保留失败照片');
  assert.equal(protectedDraft.data.reportSheet.photoItems[0].idempotencyKey, 'stable-failed-key');
  assert.equal(protectedDraft.data.reportSheet.photoItems[0].metadata.capture_session, 'stable-session');
  const backgroundUpload = deferred();
  api.uploadSitePhoto = () => backgroundUpload.promise;
  chosenPaths = ['/tmp/31-background.jpg'];
  const hiddenUpload = pageInstance(reportsPage, { reportSheet: reportSheet([siteA]) });
  const uploadWhileHidden = hiddenUpload.onAddReportPhoto();
  await settle();
  hiddenUpload.onHide();
  backgroundUpload.resolve({
    id: 32, url: '/uploads/site_photos/31-background.jpg',
    display_url: 'http://127.0.0.1:5000/uploads/site_photos/31-background.jpg?ticket=background-ticket',
  });
  await uploadWhileHidden;
  assert.equal(hiddenUpload.data.reportSheet.uploading, false);
  assert.equal(hiddenUpload.data.reportSheet.photos[0].endsWith('/uploads/site_photos/31-background.jpg'), true);

  // Formal report capture uses the shared trusted 3:4 camera and uploads one site-only draft photo.
  const trustedUploads = [];
  api.uploadSitePhoto = (siteId, image, key, metadata) => {
    trustedUploads.push({ siteId, image, key, metadata });
    return Promise.resolve({
      id: 33, url: '/uploads/site_photos/31-trusted.jpg',
      display_url: 'http://127.0.0.1:5000/uploads/site_photos/31-trusted.jpg?ticket=trusted-ticket',
    });
  };
  const trusted = pageInstance(reportsPage, { reportSheet: reportSheet([siteA]) });
  const trustedTask = trusted.onTakeReportPhoto();
  await settle();
  const trustedNavigation = navigations.at(-1);
  assert.match(trustedNavigation.url, /^\/pages\/trusted-camera\/trusted-camera\?/);
  assert.match(trustedNavigation.url, /capture_context=manual_report/);
  trustedNavigation.events.trustedCameraCaptured({
    requestId: new URLSearchParams(trustedNavigation.url.split('?')[1]).get('request_id'),
    path: '/tmp/31-trusted.jpg', captureSession: 'manual-report-session',
    gpsLat: 28.68, gpsLng: 115.86,
    frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
  });
  await trustedTask;
  assert.equal(trustedUploads.length, 1);
  assert.equal(trustedUploads[0].siteId, 31);
  assert.equal(trustedUploads[0].metadata.capture_context, 'manual_report');
  assert.equal(trustedUploads[0].metadata.capture_session, 'manual-report-session');
  assert.equal(trustedUploads[0].metadata.capture_frame_ratio, '3:4');
  assert.equal(trusted.data.reportSheet.photos[0].endsWith('/uploads/site_photos/31-trusted.jpg'), true);
  assert.match(trusted.data.reportSheet.photoItems[0].displayUrl, /\?ticket=trusted-ticket$/);

  // A lost upload response keeps the original capture and retries with the same identity.
  const retryUploads = [];
  api.uploadSitePhoto = (siteId, image, key, metadata) => {
    retryUploads.push({ siteId, image, key, metadata });
    if (retryUploads.length === 1) return Promise.reject({ error: '响应丢失' });
    return Promise.resolve({
      id: 34, url: '/uploads/site_photos/31-retry.jpg',
      display_url: 'http://127.0.0.1:5000/uploads/site_photos/31-retry.jpg?ticket=retry-ticket',
    });
  };
  const retryPhotoPage = pageInstance(reportsPage, { reportSheet: reportSheet([siteA]) });
  const retryCapture = retryPhotoPage.onTakeReportPhoto();
  await settle();
  const retryNavigation = navigations.at(-1);
  retryNavigation.events.trustedCameraCaptured({
    requestId: new URLSearchParams(retryNavigation.url.split('?')[1]).get('request_id'),
    path: '/tmp/31-retry.jpg', captureSession: 'retry-session',
    gpsLat: 28.68, gpsLng: 115.86,
    frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
  });
  await retryCapture;
  const failedPhoto = retryPhotoPage.data.reportSheet.photoItems[0];
  assert.equal(failedPhoto.status, 'failed');
  assert.equal(failedPhoto.localPath, 'wxfile://usr/31-retry.jpg');
  assert.equal(failedPhoto.metadata.capture_session, 'retry-session');
  assert.equal(failedPhoto.metadata._idempotency_key, failedPhoto.idempotencyKey);
  retryPhotoPage.setData({ 'reportSheet.description': '上传响应丢失' });
  let blockedSubmitCalls = 0;
  api.submitManualReport = () => { blockedSubmitCalls += 1; return Promise.resolve({}); };
  await retryPhotoPage.onSubmitReport();
  assert.equal(blockedSubmitCalls, 0);
  await retryPhotoPage.onRetryReportPhoto({ currentTarget: { dataset: { key: failedPhoto.key } } });
  assert.equal(retryUploads[1].key, retryUploads[0].key);
  assert.equal(retryUploads[1].metadata.capture_session, 'retry-session');
  assert.equal(retryPhotoPage.data.reportSheet.photoItems[0].status, 'uploaded');
  assert.equal(retryPhotoPage.data.reportSheet.photoItems[0].displayUrl.endsWith('?ticket=retry-ticket'), true);
  assert.deepEqual(retryPhotoPage.data.reportSheet.photos, ['/uploads/site_photos/31-retry.jpg']);

  // A HTTP 200 capture-session rejection keeps the server reason and requires a fresh camera session.
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
      id: 35,
      url: '/uploads/site_photos/31-recaptured.jpg',
      display_url: 'http://127.0.0.1:5000/uploads/site_photos/31-recaptured.jpg?ticket=recaptured-ticket',
    });
  };
  const expiredPage = pageInstance(reportsPage, { reportSheet: reportSheet([siteA]) });
  const expiredCapture = expiredPage.onTakeReportPhoto();
  await settle();
  const expiredNavigation = navigations.at(-1);
  expiredNavigation.events.trustedCameraCaptured({
    requestId: new URLSearchParams(expiredNavigation.url.split('?')[1]).get('request_id'),
    path: '/tmp/31-expired.jpg', captureSession: 'expired-session',
    gpsLat: 28.68, gpsLng: 115.86,
    frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
  });
  await expiredCapture;
  const expiredItem = expiredPage.data.reportSheet.photoItems[0];
  assert.equal(expiredItem.requiresNewCapture, true);
  assert.equal(expiredItem.failureCode, 'CAPTURE_SESSION_REQUIRED');
  assert.equal(expiredItem.error, '异常上报现场拍摄授权无效、已过期或与当前站点不一致');
  assert.equal(expiredItem.nextAction, '请返回异常上报重新发起现场拍摄');
  const recaptureTask = expiredPage.onRetryReportPhoto({
    currentTarget: { dataset: { key: expiredItem.key } },
  });
  await settle();
  assert.equal(expiredUploads.length, 1, 'the rejected capture session is never uploaded again');
  assert.match(modals.at(-1).content, /请返回异常上报重新发起现场拍摄/);
  const recaptureNavigation = navigations.at(-1);
  recaptureNavigation.events.trustedCameraCaptured({
    requestId: new URLSearchParams(recaptureNavigation.url.split('?')[1]).get('request_id'),
    path: '/tmp/31-recaptured.jpg', captureSession: 'fresh-session',
    gpsLat: 28.68, gpsLng: 115.86,
    frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
  });
  await recaptureTask;
  assert.equal(expiredUploads.length, 2);
  assert.equal(expiredUploads[1].metadata.capture_session, 'fresh-session');
  assert.notEqual(expiredUploads[1].key, expiredUploads[0].key);
  assert.equal(expiredPage.data.reportSheet.photoItems.length, 1);
  assert.equal(expiredPage.data.reportSheet.photoItems[0].status, 'uploaded');
  assert.deepEqual(expiredPage.data.reportSheet.photos, ['/uploads/site_photos/31-recaptured.jpg']);

  const cancelled = pageInstance(reportsPage, { reportSheet: reportSheet([siteA]) });
  const cancelledTask = cancelled.onTakeReportPhoto();
  await settle();
  const cancelledNavigation = navigations.at(-1);
  cancelledNavigation.events.trustedCameraCancelled({
    requestId: new URLSearchParams(cancelledNavigation.url.split('?')[1]).get('request_id'),
  });
  await cancelledTask;
  assert.deepEqual(cancelled.data.reportSheet.photos, []);
  assert.equal(cancelled.data.reportSheet.uploading, false);

  const staleCapture = pageInstance(reportsPage, { reportSheet: reportSheet([siteA, siteB]) });
  const staleCaptureTask = staleCapture.onTakeReportPhoto();
  await settle();
  const staleNavigation = navigations.at(-1);
  staleCapture.setData({ 'reportSheet.siteId': 32, 'reportSheet.siteName': '乙站' });
  staleNavigation.events.trustedCameraCaptured({
    requestId: new URLSearchParams(staleNavigation.url.split('?')[1]).get('request_id'),
    path: '/tmp/stale.jpg', captureSession: 'stale-session',
    gpsLat: 28.68, gpsLng: 115.86,
    frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'center_cover',
  });
  await staleCaptureTask;
  assert.equal(trustedUploads.length, 1, 'stale camera result performs no upload');
  assert.deepEqual(staleCapture.data.reportSheet.photos, []);

  // Client validation fails before any write, including a mismatched photo-site binding.
  let submitCalls = 0;
  api.submitManualReport = () => { submitCalls += 1; return Promise.resolve({ order_no: 'MR-ignored' }); };
  const invalid = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], { description: '', photos: [] }) });
  await invalid.onSubmitReport();
  invalid.setData({ 'reportSheet.description': '现场异味', 'reportSheet.photos': ['http://invalid/photo.jpg'], 'reportSheet.photoSiteId': 31 });
  await invalid.onSubmitReport();
  assert.equal(submitCalls, 0);

  // One valid draft writes once despite repeated submit; a failure keeps all user input for retry.
  const submitDeferred = deferred();
  let submittedPayload = null;
  api.submitManualReport = payload => { submittedPayload = payload; submitCalls += 1; return submitDeferred.promise; };
  const validDraft = reportSheet([siteA], {
    description: '现场异味', photos: ['/uploads/site_photos/31-a.jpg'], photoSiteId: 31,
  });
  const duplicate = pageInstance(reportsPage, { reportSheet: validDraft });
  const firstSubmit = duplicate.onSubmitReport();
  const secondSubmit = duplicate.onSubmitReport();
  await settle();
  assert.equal(submitCalls, 1);
  assert.equal(firstSubmit, secondSubmit);
  assert.equal(submittedPayload.site_id, 31);
  assert.deepEqual(submittedPayload.photo_urls, ['/uploads/site_photos/31-a.jpg']);
  submitDeferred.resolve({ order_no: 'MR202609030001' });
  api.manualReports = () => Promise.resolve([]);
  await firstSubmit;
  assert.equal(duplicate.data.reportSheet.open, false);
  assert.equal(modals.at(-1).content.includes('MR202609030001'), true);
  const backgroundSubmit = deferred();
  api.submitManualReport = () => backgroundSubmit.promise;
  const hiddenSubmit = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], {
    description: '后台返回测试', photos: ['/uploads/site_photos/31-background.jpg'], photoSiteId: 31,
  }) });
  const submitWhileHidden = hiddenSubmit.onSubmitReport();
  await settle();
  hiddenSubmit.onHide();
  backgroundSubmit.resolve({ order_no: 'MR202609030002' });
  await submitWhileHidden;
  assert.equal(hiddenSubmit.data.reportSheet.open, false);
  assert.equal(hiddenSubmit.data.reportSheet.submitting, false);
  assert.equal(hiddenSubmit.data.reportSheet.description, '');
  assert.equal(hiddenSubmit._reportSuccessNotice.orderNo, 'MR202609030002');
  api.manualReports = () => Promise.resolve([]);
  hiddenSubmit.onShow();
  assert.equal(modals.at(-1).content.includes('MR202609030002'), true);
  const backgroundFailure = deferred();
  api.submitManualReport = () => backgroundFailure.promise;
  const hiddenFailure = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], {
    description: '后台失败测试', photos: ['/uploads/site_photos/31-background.jpg'], photoSiteId: 31,
  }) });
  const failedWhileHidden = hiddenFailure.onSubmitReport();
  await settle();
  hiddenFailure.onHide();
  backgroundFailure.reject({ error: '网络中断' });
  await failedWhileHidden;
  assert.equal(hiddenFailure.data.reportSheet.submitting, false);
  assert.equal(hiddenFailure.data.reportSheet.description, '后台失败测试');
  const failed = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], {
    description: '泵房异响', photos: ['/uploads/site_photos/31-b.jpg'], photoSiteId: 31,
  }) });
  api.submitManualReport = () => Promise.reject({ error: '网络中断' });
  await failed.onSubmitReport();
  assert.equal(failed.data.reportSheet.description, '泵房异响');
  assert.equal(failed.data.reportSheet.photos.length, 1);
  assert.equal(failed.data.reportSheet.submitting, false);
  api.submitManualReport = () => Promise.resolve({});
  await failed.onSubmitReport();
  assert.equal(failed.data.reportSheet.open, true);
  assert.equal(failed.data.reportSheet.description, '泵房异响');
  assert.equal(navigations.some(item => /inspection/.test(item.url || '')), false);

  console.log('reportWorkorderConnection tests passed');
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
