const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const api = require('../services/api.js');
const photos = require('../utils/photos.js');
const pagePath = require.resolve('../pages/workorder/workorder.js');

let pageDefinition;
let navigationCalls = [];
let uploadCalls = [];
let submitCalls = [];
let toastCalls = [];
let uploadImplementation;
let submitImplementation;

const originalUploadWorkorderImage = api.uploadWorkorderImage;
const originalSubmitWorkorderReview = api.submitWorkorderReview;
const originalFileToBase64 = photos.fileToBase64;

api.uploadWorkorderImage = (orderNo, image, idempotencyKey, metadata) => {
  const call = { orderNo, image, idempotencyKey, metadata };
  uploadCalls.push(call);
  return uploadImplementation(call);
};
api.submitWorkorderReview = (orderNo, resolutionNote) => {
  const call = { orderNo, resolutionNote };
  submitCalls.push(call);
  return submitImplementation(call);
};
photos.fileToBase64 = filePath => Promise.resolve('base64:' + filePath);

global.getApp = () => ({ globalData: { token: 'test-token' } });
global.Page = definition => { pageDefinition = definition; };
global.wx = {
  navigateTo(options) { navigationCalls.push(options); },
  showToast(options) { toastCalls.push(options); },
  showModal() {},
  showLoading() {},
  hideLoading() {},
  getNetworkType() {},
  reLaunch() {},
};

delete require.cache[pagePath];
require(pagePath);

function setByPath(target, key, value) {
  const parts = key.split('.');
  let cursor = target;
  while (parts.length > 1) {
    const part = parts.shift();
    if (!cursor[part] || typeof cursor[part] !== 'object') cursor[part] = {};
    cursor = cursor[part];
  }
  cursor[parts[0]] = value;
}

function makePage(orderNo = 'WO-20261012-1', siteId = 7) {
  const page = Object.assign({}, pageDefinition, {
    data: JSON.parse(JSON.stringify(pageDefinition.data)),
  });
  page.setData = (patch, done) => {
    Object.entries(patch).forEach(([key, value]) => setByPath(page.data, key, value));
    if (done) done();
  };
  page.onLoad({});
  page.data.sheet = {
    open: true,
    item: {
      order_no: orderNo,
      site_id: siteId,
      site_name: '测试站点',
      display_title: '水泵故障',
      images_arr: [],
      has_images: false,
    },
  };
  return page;
}

function captureFor(navigation, overrides) {
  const url = new URL('https://local.test' + navigation.url);
  return Object.assign({
    requestId: url.searchParams.get('request_id'),
    path: 'wxfile://workorder-photo.jpg',
    captureSession: 'trusted-workorder-session',
    gpsLat: 28.071303,
    gpsLng: 115.539684,
    frameOrientation: 'portrait',
    frameRatio: '3:4',
    frameMode: 'center_cover',
  }, overrides || {});
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setTimeout(resolve, 0));
}

async function main() {
  try {
    submitImplementation = () => new Promise(() => {});
    uploadImplementation = () => Promise.resolve({ url: '/uploads/workorder.jpg' });
    const cancelledPage = makePage();
    cancelledPage.onUploadImage();
    assert.equal(navigationCalls.length, 1);
    const firstNavigation = navigationCalls[0];
    assert.match(firstNavigation.url, /^\/pages\/trusted-camera\/trusted-camera\?/);
    assert.match(firstNavigation.url, /order_no=WO-20261012-1/);
    assert.match(firstNavigation.url, /site_id=7/);
    assert.doesNotMatch(firstNavigation.url, /plan_id=|item_id=/);
    firstNavigation.events.trustedCameraCancelled({});
    await flush();
    assert.equal(uploadCalls.length, 0, 'camera cancellation creates no business photo');
    assert.equal(cancelledPage.data.workorderPhotoDraft, null);

    const stalePage = makePage();
    stalePage.onUploadImage();
    const staleNavigation = navigationCalls[1];
    stalePage.data.sheet.item = Object.assign({}, stalePage.data.sheet.item, {
      order_no: 'WO-OTHER', site_id: 8,
    });
    staleNavigation.events.trustedCameraCaptured(captureFor(staleNavigation));
    await flush();
    assert.equal(uploadCalls.length, 0, 'late camera result cannot upload after workorder context changes');

    let attempts = 0;
    uploadImplementation = () => {
      attempts += 1;
      if (attempts === 1) return Promise.reject({ error: '模拟上传失败' });
      return Promise.resolve({ url: '/uploads/workorder.jpg' });
    };
    const retryPage = makePage();
    retryPage.onUploadImage();
    const retryNavigation = navigationCalls[2];
    retryNavigation.events.trustedCameraCaptured(captureFor(
      retryNavigation, { displayWatermark: false }));
    assert.equal(retryPage.data.workorderPhotoDraft.status, 'processing');
    await flush();
    assert.equal(retryPage.data.workorderPhotoDraft.status, 'failed');
    assert.match(retryPage.data.workorderPhotoDraft.error, /模拟上传失败/);
    assert.equal(uploadCalls.length, 1);
    const firstUpload = uploadCalls[0];
    assert.equal(firstUpload.orderNo, 'WO-20261012-1');
    assert.equal(firstUpload.metadata.capture_source, 'camera');
    assert.equal(firstUpload.metadata.capture_session, 'trusted-workorder-session');
    assert.equal(firstUpload.metadata.capture_client, 'trusted_camera_v1');
    assert.equal(firstUpload.metadata.capture_frame_orientation, 'portrait');
    assert.equal(firstUpload.metadata.capture_frame_ratio, '3:4');
    assert.equal(firstUpload.metadata.capture_frame_mode, 'center_cover');
    assert.equal(firstUpload.metadata.display_watermark, false);
    assert.equal(firstUpload.metadata.gps_lat, 28.071303);
    assert.equal(firstUpload.metadata.gps_lng, 115.539684);
    assert.ok(firstUpload.metadata.taken_at);
    retryPage.onRetryWorkorderPhoto();
    await flush();
    assert.equal(uploadCalls.length, 2);
    assert.equal(uploadCalls[1].idempotencyKey, firstUpload.idempotencyKey,
      'retry reuses the original idempotency key');
    assert.equal(uploadCalls[1].metadata.display_watermark, false,
      'retry preserves the original photo watermark choice');
    assert.equal(retryPage.data.workorderPhotoDraft, null);
    assert.equal(retryPage.data.sheet.item.images_arr.length, 1);
    assert.match(retryPage.data.sheet.item.images_arr[0], /workorder\.jpg$/);

    const processingReviewPage = makePage();
    processingReviewPage.data.sheet.item.has_images = true;
    processingReviewPage.data.resolutionNote = '已完成现场处置';
    processingReviewPage.data.workorderPhotoDraft = {
      orderNo: 'WO-20261012-1', siteId: '7', status: 'processing',
    };
    processingReviewPage.doReview();
    assert.equal(submitCalls.length, 0,
      'a processing draft for the current workorder blocks review submission');
    assert.equal(processingReviewPage.data.sheet.open, true);
    assert.equal(toastCalls.at(-1).title, '照片仍在处理中，请完成上传后提交');

    const failedReviewPage = makePage();
    failedReviewPage.data.sheet.item.has_images = true;
    failedReviewPage.data.resolutionNote = '已完成现场处置';
    failedReviewPage.data.workorderPhotoDraft = {
      orderNo: 'WO-20261012-1', siteId: '7', status: 'failed',
    };
    failedReviewPage.doReview();
    assert.equal(submitCalls.length, 0,
      'a failed draft for the current workorder blocks review submission');
    assert.equal(failedReviewPage.data.sheet.open, true);
    assert.equal(toastCalls.at(-1).title, '有照片上传失败，请先重试上传');

    retryPage.data.resolutionNote = '重试成功，提交核验';
    retryPage.doReview();
    assert.equal(submitCalls.length, 1,
      'a successful retry clears the draft and restores review submission');
    assert.deepEqual(submitCalls[0], {
      orderNo: 'WO-20261012-1', resolutionNote: '重试成功，提交核验',
    });

    const otherDraftPage = makePage();
    otherDraftPage.data.sheet.item.has_images = true;
    otherDraftPage.data.resolutionNote = '当前工单可以提交';
    otherDraftPage.data.workorderPhotoDraft = {
      orderNo: 'WO-OTHER', siteId: '8', status: 'failed',
    };
    otherDraftPage.doReview();
    assert.equal(submitCalls.length, 2,
      'a draft belonging to another workorder must not block the current workorder');

    let resolveUpload;
    uploadImplementation = () => new Promise(resolve => { resolveUpload = resolve; });
    const switchedPage = makePage('WO-ORIGINAL', 11);
    switchedPage.onUploadImage();
    const switchedNavigation = navigationCalls[3];
    switchedNavigation.events.trustedCameraCaptured(captureFor(switchedNavigation));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(typeof resolveUpload, 'function');
    switchedPage.data.sheet.item = {
      order_no: 'WO-NEW', site_id: 12, images_arr: [], has_images: false,
    };
    resolveUpload({ url: '/uploads/original.jpg' });
    await flush();
    assert.deepEqual(switchedPage.data.sheet.item.images_arr, [],
      'an in-flight success cannot append its URL to a newly opened workorder');

    const workorderSource = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.js'), 'utf8');
    const workorderTemplate = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.wxml'), 'utf8');
    assert.doesNotMatch(workorderSource, /chooseInspectionPhotos\([^)]*['"]camera['"]/,
      'workorder no longer calls the rejected legacy camera picker');
    assert.match(workorderSource, /\/pages\/trusted-camera\/trusted-camera/);
    assert.match(workorderTemplate, /workorderPhotoDraft/);
    assert.match(workorderTemplate, /重试上传/);

    console.log('workorder trusted camera tests passed');
  } finally {
    api.uploadWorkorderImage = originalUploadWorkorderImage;
    api.submitWorkorderReview = originalSubmitWorkorderReview;
    photos.fileToBase64 = originalFileToBase64;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
