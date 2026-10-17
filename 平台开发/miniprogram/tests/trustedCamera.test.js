const assert = require('node:assert/strict');

const api = require('../services/api.js');
const location = require('../utils/location.js');
const pagePath = require.resolve('../pages/trusted-camera/trusted-camera.js');

let pageDefinition;
let sessionResolve;
let locationResolve;
let sessionPayloads = [];
let locationCalls = 0;
let bindCalls = [];
let takePhotoCalls = [];
let emitted = [];
let navigateBackCalls = 0;

const originalCreateSession = api.createPhotoCaptureSession;
const originalBindLocation = api.bindPhotoCaptureSessionLocation;
const originalRequestLocation = location.requestLocation;

api.createPhotoCaptureSession = payload => new Promise(resolve => {
  sessionPayloads.push(payload);
  sessionResolve = () => resolve({ capture_session: 'prepared-token', payload });
});
api.bindPhotoCaptureSessionLocation = payload => {
  bindCalls.push(payload);
  return Promise.resolve({ location_verified: true, anti_counterfeit_code: 'WQ-TEST' });
};
location.requestLocation = () => new Promise(resolve => {
  locationCalls += 1;
  locationResolve = () => resolve({ lat: 28.071303, lng: 115.539684 });
});

global.Page = definition => { pageDefinition = definition; };
global.wx = {
  getWindowInfo: () => ({ windowWidth: 390, windowHeight: 844 }),
  createCameraContext: () => ({ takePhoto(options) { takePhotoCalls.push(options); } }),
  navigateBack: () => { navigateBackCalls += 1; },
  showToast() {},
  openSetting() {},
};

delete require.cache[pagePath];
const { trustedCameraFrameLayout, inspectionWatermarkDefault } = require(pagePath);

function makePage() {
  const page = Object.assign({}, pageDefinition, {
    data: Object.assign({}, pageDefinition.data),
  });
  page.setData = (patch, done) => {
    Object.assign(page.data, patch);
    if (done) done();
  };
  page.getOpenerEventChannel = () => ({
    emit(name, value) { emitted.push({ name, value }); },
  });
  return page;
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setTimeout(resolve, 0));
}

async function main() {
  try {
    assert.equal(trustedCameraFrameLayout({ windowWidth: 390, windowHeight: 844 }).ratio, '3:4');
    assert.equal(trustedCameraFrameLayout({ windowWidth: 844, windowHeight: 390 }).ratio, '3:4');
    assert.equal(trustedCameraFrameLayout({ windowWidth: 844, windowHeight: 390 }).orientation, 'portrait');
    assert.equal(inspectionWatermarkDefault('log_books'), false);
    assert.equal(inspectionWatermarkDefault('台账登记'), false);
    assert.equal(inspectionWatermarkDefault('equipment_ops'), true);
    assert.equal(inspectionWatermarkDefault(''), true);

    const page = makePage();
    page.onLoad({
      request_id: 'request-1', site_id: '1', plan_id: '10', item_id: '100',
      site_name: encodeURIComponent('测试站'), item_name: encodeURIComponent('浊度仪表读数'),
      item_category: encodeURIComponent('log_books'),
    });
    assert.deepEqual(sessionPayloads[0], {
      site_id: 1, plan_id: 10, item_id: 100,
      capture_source: 'camera', prepare_only: true,
    }, 'inspection mode keeps its existing trusted-session contract');
    assert.equal(typeof sessionResolve, 'function', 'session preparation starts immediately');
    assert.equal(typeof locationResolve, 'function', 'location starts in parallel with session preparation');
    assert.equal(page.data.previewReady, false);
    page.onCameraInit();
    assert.equal(page.data.previewReady, true, 'preview can become visible before location/session finish');
    assert.equal(page.data.ready, false, 'shutter stays gated while trusted facts are incomplete');

    sessionResolve();
    await flush();
    assert.equal(bindCalls.length, 0, 'location binding waits only for the parallel location result');
    locationResolve();
    await flush();
    assert.deepEqual(bindCalls[0], {
      capture_session: 'prepared-token', gps_lat: 28.071303, gps_lng: 115.539684,
    });
    assert.equal(page.data.ready, true);
    assert.equal(page.data.displayWatermark, false,
      'structured log-book categories default to no visible watermark');

    page.onReady();
    page.onDisplayWatermarkChange({ detail: { value: false } });
    assert.equal(page.data.displayWatermark, false);
    page.onTakePhoto();
    page.onDisplayWatermarkChange({ detail: { value: true } });
    assert.equal(page.data.displayWatermark, false,
      'the selected value is locked while the current photo is being captured');
    page.onTakePhoto();
    assert.equal(takePhotoCalls.length, 1, 'repeated shutter taps create one photo');
    takePhotoCalls[0].success({ tempImagePath: 'wxfile://trusted-camera.jpg' });
    assert.equal(navigateBackCalls, 1);
    assert.equal(emitted.length, 1);
    assert.equal(emitted[0].name, 'trustedCameraCaptured');
    assert.deepEqual(Object.assign({}, emitted[0].value, { previewElapsedMs: 0 }), {
      requestId: 'request-1',
      path: 'wxfile://trusted-camera.jpg',
      captureSession: 'prepared-token',
      gpsLat: 28.071303,
      gpsLng: 115.539684,
      frameOrientation: 'portrait',
      frameRatio: '3:4',
      frameMode: 'center_cover',
      displayWatermark: false,
      previewElapsedMs: 0,
    });
    page.onUnload();
    assert.equal(emitted.length, 1, 'successful capture cannot emit a late cancellation');

    sessionResolve = null;
    locationResolve = null;
    const workorderPage = makePage();
    workorderPage.onLoad({
      request_id: 'request-workorder', site_id: '2', order_no: 'WO-20261012-1',
      site_name: encodeURIComponent('工单站点'), item_name: encodeURIComponent('工单 WO-20261012-1 · 水泵故障'),
    });
    assert.equal(workorderPage.data.displayWatermark, true,
      're-entering the shared camera does not retain the previous photo preference');
    assert.deepEqual(sessionPayloads[1], {
      site_id: 2, order_no: 'WO-20261012-1',
      capture_source: 'camera', prepare_only: true,
    }, 'workorder mode binds the trusted session to order_no');
    assert.equal(Object.hasOwn(sessionPayloads[1], 'plan_id'), false);
    assert.equal(Object.hasOwn(sessionPayloads[1], 'item_id'), false);
    sessionResolve();
    locationResolve();
    await flush();
    assert.equal(workorderPage.data.sessionReady, true);
    workorderPage.onUnload();

    sessionResolve = null;
    locationResolve = null;
    const manualReportPage = makePage();
    manualReportPage.onLoad({
      request_id: 'request-manual-report', site_id: '3', capture_context: 'manual_report',
      site_name: encodeURIComponent('异常上报站点'), item_name: encodeURIComponent('异常上报现场'),
    });
    assert.deepEqual(sessionPayloads[2], {
      site_id: 3, capture_context: 'manual_report',
      capture_source: 'camera', prepare_only: true,
    }, 'manual report mode binds only the authorized site and explicit report context');
    assert.equal(Object.hasOwn(sessionPayloads[2], 'plan_id'), false);
    assert.equal(Object.hasOwn(sessionPayloads[2], 'item_id'), false);
    assert.equal(Object.hasOwn(sessionPayloads[2], 'order_no'), false);
    sessionResolve();
    locationResolve();
    await flush();
    assert.equal(manualReportPage.data.sessionReady, true);
    manualReportPage.onUnload();

    const sessionCount = sessionPayloads.length;
    const locationCount = locationCalls;
    const invalidPage = makePage();
    invalidPage.onLoad({
      request_id: 'invalid', site_id: '2', order_no: 'WO-1', plan_id: '10', item_id: '100',
    });
    assert.equal(sessionPayloads.length, sessionCount, 'mixed workorder and inspection context creates no session');
    assert.equal(locationCalls, locationCount, 'invalid context does not start location');
    assert.match(invalidPage.data.errorText, /业务信息不完整/);
    invalidPage.onUnload();

    console.log('trusted camera tests passed');
  } finally {
    api.createPhotoCaptureSession = originalCreateSession;
    api.bindPhotoCaptureSessionLocation = originalBindLocation;
    location.requestLocation = originalRequestLocation;
    delete global.Page;
    delete global.wx;
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
