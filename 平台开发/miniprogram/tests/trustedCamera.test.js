const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

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
  sessionResolve = () => resolve(payload.capture_context === 'manual_report'
    ? { capture_session: 'prepared-token', payload, location_verified: false }
    : {
        capture_session: 'prepared-token', payload, location_verified: true,
        gps_lat: 28.071303, gps_lng: 115.539684,
        location_source: payload.order_no ? 'workorder_checkin' : 'inspection_checkin',
        issued_at: '2026-11-02 15:01:02', anti_counterfeit_code: 'WQ-PREVIEW-TEST',
      });
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
const {
  trustedCameraFrameLayout, inspectionWatermarkDefault, normalizeCameraFlashMode,
  captureWatermarkPreview, trustedCameraWatermarkPreviewLayout,
  wrapWatermarkText,
} = require(pagePath);

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
    assert.equal(normalizeCameraFlashMode('torch'), 'torch');
    assert.equal(normalizeCameraFlashMode('unsupported'), 'auto');
    for (const mode of ['on', 'off', 'auto', 'torch']) {
      const retryPage = makePage();
      retryPage._alive = true;
      retryPage._session = { capture_session: 'existing-session' };
      retryPage._verifiedLocation = { lat: 28, lng: 115 };
      Object.assign(retryPage.data, { preparing: false, previewReady: true,
        sessionReady: true, ready: true, flashMode: mode });
      retryPage.onTakePhoto();
      const attempt = takePhotoCalls.pop();
      assert.ok(attempt, mode + ' reaches the native shutter');
      attempt.fail({ errMsg: 'takePhoto:fail native capture failed' });
      assert.equal(retryPage.data.ready, false);
      const sessionsBeforeRetry = sessionPayloads.length;
      retryPage.onRetryPreparation();
      assert.equal(retryPage.data.ready, true, mode + ' recovers after capture failure');
      assert.equal(retryPage.data.flashMode, mode, 'retry never silently disables flash');
      assert.equal(sessionPayloads.length, sessionsBeforeRetry, 'retry reuses confirmed check-in and session');
      assert.equal(locationCalls, 0);
      retryPage.onTakePhoto();
      assert.ok(takePhotoCalls.pop());
    }
    const switchingPage = makePage();
    switchingPage._alive = true;
    switchingPage._session = { capture_session: 'existing-session' };
    switchingPage._verifiedLocation = { lat: 28, lng: 115 };
    Object.assign(switchingPage.data, { previewReady: true, sessionReady: true, ready: true });
    let flashApplied;
    switchingPage.setData = (patch, done) => {
      Object.assign(switchingPage.data, patch);
      if (patch.flashMode) flashApplied = done;
      else if (done) done();
    };
    switchingPage.onFlashModeChange({ detail: { value: '1' } });
    switchingPage.onTakePhoto();
    assert.equal(takePhotoCalls.length, 0, 'shutter waits for the selected flash mode to reach the view');
    flashApplied();
    assert.equal(switchingPage.data.ready, true);
    const shortWatermarkLayout = trustedCameraWatermarkPreviewLayout(1080);
    assert.equal(shortWatermarkLayout.siteStyle, 'font-size:60px;line-height:70px;');
    assert.equal(shortWatermarkLayout.bandHeight, 150);
    assert.deepEqual(wrapWatermarkText('昌南水厂', 180, 60, '700',
      (text, size) => Array.from(text).length * size), ['昌南水', '厂']);
    const longWatermark = {
      watermarkSiteName: '昌南航空产业园区污水处理厂深度处理站',
      watermarkPhotoName: '二期深度处理车间反渗透装置进水压力表现场照片',
      watermarkCoordinates: '28.071303,115.539684',
      watermarkCode: 'WQ-20261103-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789',
    };
    const measuredWidth = (text, size) => Array.from(text).length * size;
    const longWatermarkLayout = trustedCameraWatermarkPreviewLayout(
      1080, longWatermark, measuredWidth);
    assert.ok(longWatermarkLayout.bandHeight > 150,
      'long fields increase the preview band instead of being clipped');
    assert.equal(longWatermarkLayout.siteLines.join(''), longWatermark.watermarkSiteName);
    assert.equal(longWatermarkLayout.photoLines.join(''),
      `照片名称：${longWatermark.watermarkPhotoName}`);
    assert.equal(longWatermarkLayout.coordinateLines.join(''),
      `位置坐标：${longWatermark.watermarkCoordinates}`);
    assert.equal(longWatermarkLayout.codeLines.join(''),
      `防伪码：${longWatermark.watermarkCode}`);
    longWatermarkLayout.siteLines.forEach(line => assert.ok(
      measuredWidth(line, 60) <= longWatermarkLayout.contentWidths.left));
    [longWatermarkLayout.photoLines, longWatermarkLayout.coordinateLines,
      longWatermarkLayout.codeLines].flat().forEach(line => assert.ok(
      measuredWidth(line, 27) <= longWatermarkLayout.contentWidths.right));
    assert.deepEqual(captureWatermarkPreview(
      { issued_at: '2026-11-02 15:01:02', anti_counterfeit_code: 'WQ-PREVIEW' },
      { lat: 28.071303, lng: 115.539684 }, '测试站', '浊度仪表读数'), {
      watermarkSiteName: '测试站',
      watermarkCaptureTime: '拍摄后生成',
      watermarkPhotoName: '浊度仪表读数',
      watermarkCoordinates: '28.071303,115.539684',
      watermarkCode: 'WQ-PREVIEW',
    }, 'preview uses server facts but never presents issued_at as the shutter time');

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
    assert.equal(locationCalls, 0, 'inspection preparation does not request device location');
    assert.equal(page.data.previewReady, false);
    page.onCameraInit();
    assert.equal(page.data.previewReady, true, 'preview can become visible before location/session finish');
    assert.equal(page.data.ready, false, 'shutter stays gated while trusted facts are incomplete');

    sessionResolve();
    await flush();
    assert.equal(locationCalls, 0, 'server-confirmed inspection check-in remains the only location source');
    assert.equal(bindCalls.length, 0, 'inspection check-in location does not call the bind endpoint');
    assert.equal(page.data.ready, true);
    assert.equal(page.data.displayWatermark, false,
      'structured log-book categories default to no visible watermark');
    assert.equal(page.data.watermarkCaptureTime, '拍摄后生成');
    assert.equal(page.data.watermarkCode, 'WQ-PREVIEW-TEST');
    assert.equal(page.data.watermarkCoordinates, '28.071303,115.539684');
    assert.equal(page.data.flashMode, 'auto');
    page.onFlashModeChange({ detail: { value: '2' } });
    assert.equal(page.data.flashMode, 'torch', 'users can explicitly enable continuous fill light');
    assert.equal(page.data.flashModeLabel, '常亮');

    page.onReady();
    page.onDisplayWatermarkChange({ detail: { value: false } });
    assert.equal(page.data.displayWatermark, false);
    page.onTakePhoto();
    page.onFlashModeChange({ currentTarget: { dataset: { mode: 'off' } } });
    assert.equal(page.data.flashMode, 'torch', 'flash mode is locked while the photo is being captured');
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
    await flush();
    assert.equal(locationCalls, 0, 'workorder check-in location also skips device location');
    assert.equal(bindCalls.length, 0);
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
    await flush();
    assert.equal(locationCalls, 1, 'independent manual report keeps its device-location flow');
    assert.equal(typeof locationResolve, 'function');
    locationResolve();
    await flush();
    assert.deepEqual(bindCalls[0], {
      capture_session: 'prepared-token', gps_lat: 28.071303, gps_lng: 115.539684,
    });
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

    const template = fs.readFileSync(
      path.join(__dirname, '../pages/trusted-camera/trusted-camera.wxml'), 'utf8');
    assert.doesNotMatch(template, /camera-context|frame-border|最终照片范围/);
    assert.match(template, /<picker[^>]+range="\{\{flashOptions\}\}"[^>]+bindchange="onFlashModeChange"/);
    assert.match(template, /<cover-view wx:if="\{\{displayWatermark\}\}" class="camera-watermark-preview"/);
    assert.match(template, /\{\{watermarkCaptureTime\}\}/);
    assert.match(template, /wx:for="\{\{watermarkSiteLines\}\}"/);
    assert.match(template, /class="camera-shutter-slot"/);
    const styles = fs.readFileSync(
      path.join(__dirname, '../pages/trusted-camera/trusted-camera.wxss'), 'utf8');
    assert.match(styles, /\.camera-shutter\s*\{[^}]*width:\s*104rpx;[^}]*min-width:\s*104rpx;[^}]*max-width:\s*104rpx;[^}]*height:\s*104rpx;[^}]*min-height:\s*104rpx;[^}]*max-height:\s*104rpx;/s);
    assert.match(styles, /\.camera-tool-slot\s*\{[^}]*flex:\s*1 1 0;/s);
    assert.match(styles, /\.camera-shutter-slot\s*\{[^}]*flex:\s*0 0 112rpx;/s);

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
