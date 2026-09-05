const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  chooseInspectionPhotos,
  isPhotoSelectionCancelled,
  photoCaptureErrorMessage,
  captureSourceNeedsLocationSession,
  requestCaptureSessionWithLocation,
  shouldOpenCameraSettings,
  collectInspectionPhotoUploadResults,
  processPhotoUploadIssues,
  photoActionOpeningTitle,
  runPhotoActionOnce,
  validateReportPhotoPaths,
  handlePhotoActionFailure,
  deletePendingReportPhotoOnce,
  inspectionUploadTaskResult,
  deletePendingPhotoOnce,
  inspectionPhotoIssueFeedback,
  inspectionPhotoIssueMessage,
  setInspectionPhotoIssueMessage,
} = require('../utils/photos.js');
const { locationErrorMessage } = require('../utils/location.js');

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

test('photo upload results classify every selected photo without silent gaps', () => {
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: { url: '/uploads/ok.jpg' } },
    { status: 'fulfilled', value: { localPath: 'wxfile://usr/offline.jpg', metadata: { source: 'album' } } },
    { status: 'fulfilled', value: { rejected: {
      reason: '水印时间无法确认', next_action: '请重新选择', can_keep_as_supplement: true,
    } } },
    { status: 'fulfilled', value: { rejected: {
      reason: '照片来源不可信', next_action: '请重新拍摄', can_keep_as_supplement: false,
    } } },
    { status: 'rejected', reason: { error: '网络上传失败' } },
    { status: 'fulfilled', value: {} },
    { status: 'fulfilled', value: { url: '' } },
    { status: 'fulfilled', value: { rejected: { can_keep_as_supplement: true } } },
  ]);
  assert.deepEqual(summary.urls, ['/uploads/ok.jpg']);
  assert.deepEqual(summary.localPaths, ['wxfile://usr/offline.jpg']);
  assert.deepEqual(summary.localMetadata, [{ source: 'album' }]);
  assert.deepEqual(
    summary.issues.map(issue => issue.kind),
    ['rejected', 'rejected', 'failed', 'failed', 'failed', 'rejected'],
  );
  assert.deepEqual(
    summary.issues.filter(issue => issue.kind === 'failed').map(issue => issue.error.error),
    [
      '网络上传失败', '服务响应异常，请重试',
      '服务响应异常，请重试',
    ],
  );
});

test('real upload response rejection reaches the page issue chain exactly once', async () => {
  const rejected = inspectionUploadTaskResult({
    accepted_for_review: false,
    code: 'PHOTO_SOURCE_NOT_ACCEPTED',
    reason: '与影像#109重复',
    next_action: '请重新拍摄',
  }, { path: 'wxfile://tmp/a.jpg', image: 'base64', metadata: {}, idempotencyKey: 'one' });
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: rejected },
    { status: 'fulfilled', value: inspectionUploadTaskResult({}, {}) },
  ]);
  const messages = [];
  await processPhotoUploadIssues(summary.issues, issue => {
    messages.push(issue.kind === 'rejected'
      ? `${issue.value.rejected.reason}。${issue.value.rejected.next_action}`
      : photoCaptureErrorMessage(issue.error));
    return Promise.resolve('retry');
  }, () => Promise.resolve());
  assert.deepEqual(messages, ['与影像#109重复。请重新拍摄', '服务响应异常，请重试']);
});

test('inspection photo issues persist before modal feedback and keep actionable fallbacks', async () => {
  const owner = {
    data: { sheet: { photos: ['/uploads/ok.jpg'], remark: '保留现场输入' } },
    setData(values) {
      Object.keys(values).forEach(key => {
        this.data.sheet[key.replace('sheet.', '')] = values[key];
      });
    },
  };
  const rejected = { kind: 'rejected', value: { rejected: {
    reason: '与影像#116重复，不能作为新的正式证据', next_action: '请现场拍摄不同照片',
  } } };
  assert.equal(setInspectionPhotoIssueMessage(owner, rejected),
    '与已有照片重复，请改拍其他照片。');
  await new Promise(resolve => {
    const modal = { fail: () => resolve('retry') };
    modal.fail();
  });
  assert.equal(owner.data.sheet.photoResultTitle, '照片未采用');
  assert.equal(owner.data.sheet.photoResultMessage, '与已有照片重复，请改拍其他照片。');
  assert.deepEqual(owner.data.sheet.photos, ['/uploads/ok.jpg']);
  assert.equal(owner.data.sheet.remark, '保留现场输入');
  assert.equal(inspectionPhotoIssueMessage({ kind: 'rejected', value: { rejected: {} } }),
    '无法确认拍摄信息。请重新拍摄或重新选择');
  assert.equal(inspectionPhotoIssueMessage({ kind: 'failed', error: { error: '网络上传失败' } }),
    '网络上传失败');
  assert.equal(inspectionPhotoIssueMessage({ kind: 'failed', error: { error: '服务响应异常，请重试' } }),
    '服务响应异常，请重试');
  assert.deepEqual(inspectionPhotoIssueFeedback({
    kind: 'rejected', value: { rejected: {
      reason: '照片拍摄时间超出允许范围', next_action: '请重新选择近期拍摄的原图',
    } },
  }), {
    title: '照片未采用',
    message: '照片拍摄时间超出允许范围。请重新选择近期拍摄的原图',
  });
  assert.deepEqual(inspectionPhotoIssueFeedback({
    kind: 'failed', error: { error: '网络上传失败' },
  }), { title: '上传未完成', message: '网络上传失败' });
});

test('location capture failure uses location wording while other photo failures keep photo wording', () => {
  const locationFailure = { capturePhase: 'location', errMsg: 'getLocation:fail auth deny' };
  const locationMessage = locationErrorMessage(locationFailure);
  assert.match(locationMessage, /定位权限/);
  assert.doesNotMatch(locationMessage, /相机权限/);
  assert.match(inspectionPhotoIssueMessage({
    kind: 'failed', error: { errMsg: 'chooseMedia:fail auth deny' },
  }), /相机权限/);
  assert.equal(inspectionPhotoIssueMessage({
    kind: 'failed', error: { error: '水印相册读取失败，请重新选择' },
  }), '水印相册读取失败，请重新选择');

  const pageSource = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
  assert.match(pageSource,
    /const locationFailure = err && err\.capturePhase === 'location';[\s\S]{0,180}photoResultTitle': '无法获取位置'[\s\S]{0,180}photoResultMessage': locationErrorMessage\(err\)[\s\S]{0,180}setInspectionPhotoIssueMessage/);
});

test('inspection pending photo is removed only after matching server receipt', async () => {
  const photos = ['/uploads/site_photos/pending.jpg', '/uploads/formal.jpg'];
  await assert.rejects(deletePendingPhotoOnce({}, '/uploads/site_photos/pending.jpg',
    '/uploads/site_photos/pending.jpg',
    path => Promise.reject({ error: `删除失败:${path}` }), () => photos));
  assert.deepEqual(photos, ['/uploads/site_photos/pending.jpg', '/uploads/formal.jpg']);
  const removed = await deletePendingPhotoOnce({}, '/uploads/site_photos/pending.jpg',
    '/uploads/site_photos/pending.jpg',
    path => Promise.resolve({ success: true, deleted: true, path }), () => photos);
  assert.deepEqual(removed, ['/uploads/formal.jpg']);
});

test('mixed photo upload issues are complete and processed exactly once serially', async () => {
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: { url: '/uploads/ok.jpg' } },
    { status: 'fulfilled', value: { rejected: {
      reason: '水印时间无法确认', next_action: '请重新选择', can_keep_as_supplement: true,
    }, idempotencyKey: 'r1' } },
    { status: 'fulfilled', value: { url: '' } },
    { status: 'rejected', reason: { error: '网络上传失败' } },
  ]);
  assert.deepEqual(summary.urls, ['/uploads/ok.jpg']);
  assert.deepEqual(summary.issues.map(issue => issue.kind), ['rejected', 'failed', 'failed']);

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
  assert.deepEqual(handled, ['rejected', 'failed', 'failed']);
});

test('only supplement-capable rejection can be retained', async () => {
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: { rejected: {
      reason: '水印不完整', next_action: '请重新选择', can_keep_as_supplement: true,
    }, idempotencyKey: 'a' } },
    { status: 'fulfilled', value: { rejected: {
      reason: '照片来源不可信', next_action: '请重新拍摄', can_keep_as_supplement: false,
    }, idempotencyKey: 'b' } },
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
  assert.deepEqual(retained, ['a']);
});

test('empty successful selection is actionable while user cancellation stays silent', async () => {
  global.wx = {
    chooseMedia(options) {
      options.success({ tempFiles: [{ tempFilePath: '' }, { tempFilePath: {} }, {}] });
    },
  };
  await assert.rejects(
    chooseInspectionPhotos(6, 'watermark_album'),
    error => photoCaptureErrorMessage(error) === '未获取到有效照片，请重新选择'
      && !isPhotoSelectionCancelled(error),
  );

  global.wx.chooseMedia = options => options.fail({ errMsg: 'chooseMedia:fail cancel' });
  await assert.rejects(
    chooseInspectionPhotos(6, 'watermark_album'),
    error => isPhotoSelectionCancelled(error),
  );
  delete global.wx;
});

test('camera, album and report picker expose clear opening feedback', () => {
  assert.equal(photoActionOpeningTitle('camera'), '正在打开相机');
  assert.equal(photoActionOpeningTitle('watermark_album'), '正在打开相册');
  assert.equal(photoActionOpeningTitle('media_picker'), '正在打开相机/相册');
});

test('one page allows only one photo action and permits the next after success', async () => {
  const owner = {};
  const starts = [];
  let finishes = 0;
  let actionCalls = 0;
  let releaseFirst;
  const first = runPhotoActionOnce(owner, 'camera', () => {
    actionCalls += 1;
    return new Promise(resolve => { releaseFirst = resolve; });
  }, title => starts.push(title), () => { finishes += 1; });
  const duplicate = runPhotoActionOnce(owner, 'watermark_album', () => {
    actionCalls += 1;
  }, title => starts.push(title), () => { finishes += 1; });

  assert.strictEqual(duplicate, first);
  assert.equal(actionCalls, 0, 'action begins asynchronously after the lock is installed');
  await Promise.resolve();
  assert.equal(actionCalls, 1);
  assert.deepEqual(starts, ['正在打开相机']);
  releaseFirst();
  await first;
  assert.equal(finishes, 1);

  await runPhotoActionOnce(owner, 'watermark_album', () => {
    actionCalls += 1;
  }, title => starts.push(title), () => { finishes += 1; });
  assert.equal(actionCalls, 2);
  assert.deepEqual(starts, ['正在打开相机', '正在打开相册']);
  assert.equal(finishes, 2);
});

test('cancel and every startup or upload failure release the shared photo action', async () => {
  const failures = [
    { phase: 'camera-cancel', source: 'camera', error: { errMsg: 'chooseMedia:fail cancel' } },
    { phase: 'album-cancel', source: 'watermark_album', error: { errMsg: 'chooseMedia:fail cancel' } },
    { phase: 'location', source: 'camera', error: { error: '定位失败' } },
    { phase: 'session', source: 'camera', error: { error: '会话创建失败' } },
    { phase: 'chooseMedia', source: 'watermark_album', error: { error: '选图失败' } },
    { phase: 'upload', source: 'watermark_album', error: { error: '上传失败' } },
  ];
  for (const item of failures) {
    const owner = { sheet: { photos: ['/uploads/existing.jpg'], remark: '已填写说明' } };
    let finishes = 0;
    let prompts = 0;
    await runPhotoActionOnce(owner, item.source, () => Promise.reject(item.error), null,
      () => { finishes += 1; })
      .catch(error => {
        if (!isPhotoSelectionCancelled(error)) prompts += 1;
      });
    assert.equal(finishes, 1, `${item.phase} must restore the entry`);
    assert.equal(prompts, item.phase.endsWith('cancel') ? 0 : 1);
    let retried = 0;
    await runPhotoActionOnce(owner, item.source, () => { retried += 1; });
    assert.equal(retried, 1, `${item.phase} must allow a later action`);
    assert.deepEqual(owner.sheet, {
      photos: ['/uploads/existing.jpg'], remark: '已填写说明',
    }, `${item.phase} must preserve current input and successful photos`);
  }
});

test('inspection empty photo card is a camera entry and existing source buttons remain', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
  assert.match(wxml, /class="ip-photo-source-row"[\s\S]{0,300}?bindtap="onAddPhoto"[\s\S]{0,100}?data-source="camera">现场拍照<\/button>/);
  assert.match(wxml, /data-source="watermark_album">水印相册<\/button>/);
  assert.match(wxml, /class="report-photo-item photo-add-item" bindtap="onAddReportPhoto"/);
  const pageSource = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
  assert.match(pageSource, /onAddReportPhoto\(\)[\s\S]*?runPhotoActionOnce\(this, 'media_picker'/);
  assert.match(pageSource, /validateReportPhotoPaths\(paths\)/);
  assert.match(pageSource, /handlePhotoActionFailure\(error, message => new Promise/);
  assert.match(pageSource, /onAddPhoto\(e\)[\s\S]*?runPhotoActionOnce\(this, captureSource/);
  assert.match(pageSource, /inspectionUploadTaskResult\(result/);
  assert.match(pageSource, /addPendingInspectionPhotos\(/);
  assert.match(pageSource, /setInspectionPhotoIssueMessage\(this, issue\)[\s\S]{0,160}wx\.showModal/);
  assert.match(pageSource, /if \(!uploadResult\.issues\.length\)[\s\S]{0,140}photoResultTitle': ''[\s\S]{0,80}photoResultMessage': ''/);
  assert.match(wxml, /class="ip-photo-result"[\s\S]{0,180}class="ip-photo-result-title"[\s\S]{0,120}class="ip-photo-result-message"/);
  assert.doesNotMatch(wxml, /class="ip-form-error"[^>]*wx:if="\{\{sheet\.photoResultMessage\}\}"/);
  const wxss = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxss'), 'utf8');
  assert.match(wxss, /\.ip-photo-result-title[^}]*font-size:\s*30rpx/);
  assert.match(wxss, /\.ip-photo-result-message[^}]*font-size:\s*24rpx/);
  assert.match(pageSource, /onDelPhoto\(e\)[\s\S]*?deletePendingPhotoOnce/);
});

test('report photo selection rejects every empty or non-string path with an actionable reason', () => {
  assert.deepEqual(
    validateReportPhotoPaths([' wxfile://tmp/a.jpg ', 'wxfile://tmp/b.jpg']),
    ['wxfile://tmp/a.jpg', 'wxfile://tmp/b.jpg'],
  );
  for (const paths of [[], [''], ['   '], ['wxfile://tmp/a.jpg', null], [42]]) {
    assert.throws(
      () => validateReportPhotoPaths(paths),
      error => error && error.error === '未获取到有效照片，请重新拍摄或选择',
    );
  }
});

test('report errors keep their reason and hold the lock until the prompt finishes', async () => {
  const cases = [
    { error: { errMsg: 'chooseMedia:fail auth deny' }, expected: /相机权限/ },
    { error: { error: '文件读取失败，请重新选择原图' }, expected: /^文件读取失败，请重新选择原图$/ },
  ];
  for (const item of cases) {
    const owner = {};
    let releasePrompt;
    let shownMessage = '';
    let finishes = 0;
    let retryCalls = 0;
    const first = runPhotoActionOnce(owner, 'media_picker', () => Promise.reject(item.error)
      .catch(error => handlePhotoActionFailure(error, message => new Promise(resolve => {
        shownMessage = message;
        releasePrompt = resolve;
      }))), null, () => { finishes += 1; });
    await new Promise(resolve => setImmediate(resolve));
    assert.match(shownMessage, item.expected);
    const duplicate = runPhotoActionOnce(owner, 'camera', () => { retryCalls += 1; });
    assert.strictEqual(duplicate, first);
    assert.equal(retryCalls, 0);
    assert.equal(finishes, 0, 'modal acknowledgement must precede lock release');
    releasePrompt();
    await first;
    assert.equal(finishes, 1);
    await runPhotoActionOnce(owner, 'camera', () => { retryCalls += 1; });
    assert.equal(retryCalls, 1);
  }

  let cancelPrompts = 0;
  const cancelOwner = {};
  await runPhotoActionOnce(cancelOwner, 'media_picker', () => handlePhotoActionFailure(
    { errMsg: 'chooseMedia:fail cancel' },
    () => { cancelPrompts += 1; },
  ));
  assert.equal(cancelPrompts, 0);
  let retried = 0;
  await runPhotoActionOnce(cancelOwner, 'media_picker', () => { retried += 1; });
  assert.equal(retried, 1);
});

test('report photo deletion requires a matching server confirmation', async () => {
  const displayUrl = 'http://192.168.2.103:5000/uploads/site_photos/pending.jpg';
  const storagePath = '/uploads/site_photos/pending.jpg';
  const original = [displayUrl, '/uploads/site_photos/keep.jpg'];
  for (const response of [undefined, {}, { success: true }, {
    success: true, deleted: true, path: '/uploads/site_photos/other.jpg',
  }]) {
    await assert.rejects(
      deletePendingReportPhotoOnce({}, displayUrl, storagePath,
        () => Promise.resolve(response), () => original),
      error => error && error.error === '服务响应异常，照片仍保留，请重试',
    );
    assert.deepEqual(original, [displayUrl, '/uploads/site_photos/keep.jpg']);
  }

  const removed = await deletePendingReportPhotoOnce({}, displayUrl, storagePath,
    () => Promise.resolve({
      success: true, deleted: true, already_deleted: false, path: storagePath,
    }), () => original);
  assert.deepEqual(removed, ['/uploads/site_photos/keep.jpg']);

  const replayed = await deletePendingReportPhotoOnce({}, displayUrl, storagePath,
    () => Promise.resolve({
      success: true, deleted: false, already_deleted: true, path: storagePath,
    }), () => original);
  assert.deepEqual(replayed, ['/uploads/site_photos/keep.jpg']);
});

test('report photo delete failures preserve input and repeated taps do not overlap', async () => {
  const displayUrl = 'http://192.168.2.103:5000/uploads/site_photos/pending.jpg';
  const storagePath = '/uploads/site_photos/pending.jpg';
  const original = [displayUrl];
  const failures = [400, 404, 409, 500, 0];
  for (const status of failures) {
    await assert.rejects(
      deletePendingReportPhotoOnce({}, displayUrl, storagePath,
        () => Promise.reject({ status, error: `删除失败-${status}` }), () => original),
      error => error && error.error === `删除失败-${status}`,
    );
    assert.deepEqual(original, [displayUrl]);
  }

  const owner = {};
  let calls = 0;
  let resolveDelete;
  const requestDelete = () => {
    calls += 1;
    return new Promise(resolve => { resolveDelete = resolve; });
  };
  const first = deletePendingReportPhotoOnce(
    owner, displayUrl, storagePath, requestDelete, () => original);
  const duplicate = deletePendingReportPhotoOnce(
    owner, displayUrl, storagePath, requestDelete, () => original);
  assert.strictEqual(duplicate, first);
  await Promise.resolve();
  assert.equal(calls, 1);
  resolveDelete({ success: true, deleted: true, path: storagePath });
  assert.deepEqual(await first, []);

  await deletePendingReportPhotoOnce(owner, displayUrl, storagePath,
    () => {
      calls += 1;
      return Promise.resolve({ success: true, already_deleted: true, path: storagePath });
    }, () => original);
  assert.equal(calls, 2, 'the lock must release after completion so retry is possible');
});

test('pending report photo delete explicitly bypasses the offline write queue', async () => {
  const requestPath = require.resolve('../utils/request.js');
  const apiPath = require.resolve('../services/api.js');
  const originalRequest = require.cache[requestPath];
  const calls = [];
  require.cache[requestPath] = {
    id: requestPath, filename: requestPath, loaded: true,
    exports: { request: (...args) => { calls.push(args); return Promise.resolve({}); } },
  };
  delete require.cache[apiPath];
  try {
    const api = require('../services/api.js');
    await api.deletePendingSitePhoto('/uploads/site_photos/pending.jpg');
    assert.deepEqual(calls[0], [
      '/api/mobile/site-photos/delete', 'POST',
      { url: '/uploads/site_photos/pending.jpg' }, { queue: false },
    ]);
  } finally {
    delete require.cache[apiPath];
    if (originalRequest) require.cache[requestPath] = originalRequest;
    else delete require.cache[requestPath];
  }
});

test('rejected inspection purge uses an online request and the page preserves retry context', async () => {
  const requestPath = require.resolve('../utils/request.js');
  const apiPath = require.resolve('../services/api.js');
  const originalRequest = require.cache[requestPath];
  const calls = [];
  require.cache[requestPath] = {
    id: requestPath, filename: requestPath, loaded: true,
    exports: { request: (...args) => { calls.push(args); return Promise.resolve({}); } },
  };
  delete require.cache[apiPath];
  try {
    const api = require('../services/api.js');
    await api.purgeRejectedInspectionPhoto(110);
    await api.getRejectedInspectionPurgeBatch(110);
    await api.purgeRejectedInspectionPhotoBatch(110);
    assert.deepEqual(calls[0], [
      '/api/attachments/110/purge-rejected', 'POST', {}, { queue: false },
    ]);
    assert.deepEqual(calls[1], [
      '/api/attachments/110/purge-rejected-batch', 'GET', {}, { queue: false },
    ]);
    assert.deepEqual(calls[2], [
      '/api/attachments/110/purge-rejected-batch', 'POST', {}, { queue: false },
    ]);
  } finally {
    delete require.cache[apiPath];
    if (originalRequest) require.cache[requestPath] = originalRequest;
    else delete require.cache[requestPath];
  }
  const page = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
  const template = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
  assert.equal((page.match(/rejectedInspectionEvidence\(/g) || []).length, 2,
    'opening and rebuilding must share the same rejected evidence projection');
  assert.match(template, /src="\{\{evidence\.display_url\}\}"/);
  assert.doesNotMatch(page, /purgeReason|onRejectedPurgeReason/);
  assert.doesNotMatch(template, /请填写彻底删除原因|textarea[^>]*purgeReason/);
  assert.match(template, /不可恢复，仅保留文字删除摘要/);
  assert.match(template, /清理本次整改全部 \{\{sheet\.purgeCount\}\} 张/);
  assert.doesNotMatch(template, /catchtap="onOpenRejectedPurge"[^>]*>×<\/text>/);
  assert.match(page, /itemResults = new Map/);
  assert.match(page, /evidence_attachments:[\s\S]*deletedIds/);
  assert.doesNotMatch(page, /already_deleted[\s\S]{0,160}(return|reload|loadTasks)/,
    'first success and idempotent replay must use the same authoritative item projection');
  assert.match(page, /result\.attachment_ids[\s\S]*result\.items[\s\S]*itemResults\.get/,
    'batch replay snapshots must clear every deleted attachment and project every affected item');
  assert.match(page, /catch\(error => \{[\s\S]*sheet\.purgeLoading'[\s\S]*sheet\.purgeError/);
});
