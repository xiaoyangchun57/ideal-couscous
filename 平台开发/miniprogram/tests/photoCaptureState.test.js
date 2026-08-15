const assert = require('node:assert/strict');
const test = require('node:test');

const {
  isPhotoSelectionCancelled,
  photoCaptureErrorMessage,
  captureSourceNeedsLocationSession,
  requestCaptureSessionWithLocation,
  shouldOpenCameraSettings,
  collectInspectionPhotoUploadResults,
  processPhotoUploadIssues,
} = require('../utils/photos.js');

test('camera cancellation stays silent', () => {
  assert.equal(isPhotoSelectionCancelled({ errMsg: 'chooseMedia:fail cancel' }), true);
  assert.equal(isPhotoSelectionCancelled({ error: '请先到站签到' }), false);
});

test('capture preflight and permission failures keep actionable reasons', () => {
  assert.equal(photoCaptureErrorMessage({ error: '请先完成本站到站签到' }), '请先完成本站到站签到');
  assert.match(
    photoCaptureErrorMessage({ errMsg: 'chooseMedia:fail auth deny' }),
    /相机权限/,
  );
  assert.equal(shouldOpenCameraSettings({ errMsg: 'chooseMedia:fail auth deny' }), true);
  assert.equal(shouldOpenCameraSettings({ error: '请先到站签到' }), false);
});

test('only location lookup failures are classified as location failures', async () => {
  const locationFailure = { errMsg: 'getLocation:fail auth deny' };
  await assert.rejects(
    requestCaptureSessionWithLocation(
      () => Promise.reject(locationFailure),
      () => Promise.resolve({ capture_session: 'unused' }),
    ),
    error => error.capturePhase === 'location',
  );

  const businessFailure = { error: '当前位置不在站点500米范围内', code: 'CAPTURE_LOCATION_OUT_OF_RANGE' };
  await assert.rejects(
    requestCaptureSessionWithLocation(
      () => Promise.resolve({ lat: 28.1, lng: 115.1 }),
      () => Promise.reject(businessFailure),
    ),
    error => error === businessFailure && !error.capturePhase
      && photoCaptureErrorMessage(error) === businessFailure.error,
  );
});

test('watermark album bypasses current location and capture-session preflight', () => {
  assert.equal(captureSourceNeedsLocationSession('camera'), true);
  assert.equal(captureSourceNeedsLocationSession('watermark_album'), false);
});

test('mixed photo upload issues are complete and processed serially', async () => {
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: { url: '/uploads/ok.jpg' } },
    { status: 'fulfilled', value: { rejected: { can_keep_as_supplement: true }, idempotencyKey: 'r1' } },
    { status: 'rejected', reason: { error: '网络上传失败' } },
  ]);
  assert.deepEqual(summary.urls, ['/uploads/ok.jpg']);
  assert.deepEqual(summary.issues.map(issue => issue.kind), ['rejected', 'failed']);

  let activePrompts = 0;
  let maxActivePrompts = 0;
  const handled = [];
  await processPhotoUploadIssues(summary.issues, issue => new Promise(resolve => {
    activePrompts += 1;
    maxActivePrompts = Math.max(maxActivePrompts, activePrompts);
    setTimeout(() => {
      handled.push(issue.kind);
      activePrompts -= 1;
      resolve('retry');
    }, 5);
  }), () => Promise.resolve());
  assert.equal(maxActivePrompts, 1);
  assert.deepEqual(handled, ['rejected', 'failed']);
});

test('each supplement-capable rejection requires and applies its own choice', async () => {
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: { rejected: { can_keep_as_supplement: true }, idempotencyKey: 'a' } },
    { status: 'fulfilled', value: { rejected: { can_keep_as_supplement: true }, idempotencyKey: 'b' } },
  ]);
  const prompted = [];
  const retained = [];
  await processPhotoUploadIssues(summary.issues, issue => {
    prompted.push(issue.value.idempotencyKey);
    return Promise.resolve('supplement');
  }, value => {
    retained.push(value.idempotencyKey);
    return Promise.resolve();
  });
  assert.deepEqual(prompted, ['a', 'b']);
  assert.deepEqual(retained, ['a', 'b']);
});
