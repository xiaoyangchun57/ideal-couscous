const assert = require('node:assert/strict');

const api = require('../services/api.js');
const location = require('../utils/location.js');
const pagePath = require.resolve('../pages/trusted-camera/trusted-camera.js');

let pageDefinition;
let sessionResolve;
let locationResolve;
let bindCalls = [];
let takePhotoCalls = [];
let emitted = [];
let navigateBackCalls = 0;

const originalCreateSession = api.createPhotoCaptureSession;
const originalBindLocation = api.bindPhotoCaptureSessionLocation;
const originalRequestLocation = location.requestLocation;

api.createPhotoCaptureSession = payload => new Promise(resolve => {
  sessionResolve = () => resolve({ capture_session: 'prepared-token', payload });
});
api.bindPhotoCaptureSessionLocation = payload => {
  bindCalls.push(payload);
  return Promise.resolve({ location_verified: true, anti_counterfeit_code: 'WQ-TEST' });
};
location.requestLocation = () => new Promise(resolve => {
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
const { trustedCameraFrameLayout } = require(pagePath);

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

    const page = makePage();
    page.onLoad({
      request_id: 'request-1', site_id: '1', plan_id: '10', item_id: '100',
      site_name: encodeURIComponent('测试站'), item_name: encodeURIComponent('浊度仪表读数'),
    });
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

    page.onReady();
    page.onTakePhoto();
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
      previewElapsedMs: 0,
    });
    page.onUnload();
    assert.equal(emitted.length, 1, 'successful capture cannot emit a late cancellation');

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
