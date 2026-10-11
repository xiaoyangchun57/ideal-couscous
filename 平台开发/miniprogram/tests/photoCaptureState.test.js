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
  retainRejectedPhotoAsSupplement,
  persistFileStrict,
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

test('supplement material bypasses current location and capture-session preflight', () => {
  assert.equal(captureSourceNeedsLocationSession('camera'), true);
  assert.equal(captureSourceNeedsLocationSession('supplement'), false);
});

test('photo upload results classify every selected photo without silent gaps', () => {
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: { url: '/uploads/ok.jpg' } },
    { status: 'fulfilled', value: { localPath: 'wxfile://usr/offline.jpg', metadata: { source: 'album' } } },
    { status: 'fulfilled', value: {
      localPath: 'wxfile://usr/supplement.jpg', metadata: { capture_source: 'supplement' },
      supplemental: true,
    } },
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
  assert.deepEqual(summary.supplementLocalPaths, ['wxfile://usr/supplement.jpg']);
  assert.deepEqual(summary.supplementLocalMetadata, [{ capture_source: 'supplement' }]);
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

test('supplement response is saved separately and never enters required-photo issues', () => {
  const supplement = inspectionUploadTaskResult({
    accepted_for_review: false,
    saved_original: true,
    pending_verification: true,
    supplemental: true,
    id: 41,
    url: '/uploads/site_photos/preserved.jpg',
    display_url: '/uploads/site_photos/preserved.jpg?ticket=signed',
    reason: '拍摄时间缺少可确认依据，不计入有效证据',
    next_action: '请重新选择可自动核验的原图或现场重拍',
  }, { idempotencyKey: 'preserved' }, value => `http://local${value}`);
  const summary = collectInspectionPhotoUploadResults([
    { status: 'fulfilled', value: supplement },
  ]);
  assert.deepEqual(summary.urls, []);
  assert.deepEqual(summary.supplementUrls,
    ['http://local/uploads/site_photos/preserved.jpg?ticket=signed']);
  assert.deepEqual(summary.supplementAttachments, [{
    id: 41,
    url: 'http://local/uploads/site_photos/preserved.jpg?ticket=signed',
    storagePath: '/uploads/site_photos/preserved.jpg',
  }]);
  assert.equal(summary.issues.length, 0);
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

test('failed supplement retention persists a retryable draft before the second upload', async () => {
  const events = [];
  let visibleDraft = null;
  const result = await retainRejectedPhotoAsSupplement({
    path: 'wxfile://tmp/rejected.jpg',
    image: 'data:image/jpeg;base64,rejected',
    idempotencyKey: 'capture-key',
    metadata: {
      _idempotency_key: 'capture-key', capture_source: 'camera',
      capture_session: 'session-token', gps_lat: 28.1, gps_lng: 115.1,
      taken_at: '2026-10-09 10:00:00', plan_id: 10, item_id: 100,
    },
  }, {
    persist: path => {
      events.push(['persist', path]);
      return Promise.resolve('wxfile://usr/rejected.jpg');
    },
    onPersisted: draft => {
      events.push(['draft', draft.localPath]);
      visibleDraft = draft;
    },
    upload: (image, key, metadata) => {
      events.push(['upload', key, metadata]);
      return Promise.reject({ status: 503, error: 'network unavailable' });
    },
    cleanup: path => events.push(['cleanup', path]),
  });

  assert.deepEqual(events.map(event => event[0]), ['persist', 'draft', 'upload']);
  assert.equal(result.localPath, 'wxfile://usr/rejected.jpg');
  assert.equal(result.supplemental, true);
  assert.equal(result.metadata._idempotency_key, 'capture-key:supplement');
  assert.equal(result.metadata.capture_source, 'supplement');
  assert.equal(result.metadata.plan_id, 10);
  assert.equal(result.metadata.item_id, 100);
  assert.equal('capture_session' in result.metadata, false);
  assert.equal('gps_lat' in result.metadata, false);
  assert.equal('gps_lng' in result.metadata, false);
  assert.equal('taken_at' in result.metadata, false);
  assert.deepEqual(visibleDraft, {
    localPath: 'wxfile://usr/rejected.jpg',
    metadata: result.metadata,
    supplemental: true,
  });
});

test('strict supplement retention stops when wx.saveFile fails', async () => {
  let persistedCalls = 0;
  let uploadCalls = 0;
  global.wx = {
    saveFile(options) {
      options.fail({ errMsg: 'saveFile:fail quota exceeded' });
    },
  };
  try {
    await assert.rejects(
      retainRejectedPhotoAsSupplement({
        path: 'wxfile://tmp/rejected.jpg', image: 'base64', idempotencyKey: 'capture-key',
        metadata: { capture_source: 'camera', capture_session: 'session-token' },
      }, {
        persist: persistFileStrict,
        onPersisted: () => { persistedCalls += 1; },
        upload: () => { uploadCalls += 1; return Promise.resolve({}); },
      }),
      error => error && error.error === '原照片保存失败，请立即重新选择补充材料'
        && error.cause && error.cause.errMsg === 'saveFile:fail quota exceeded',
    );
    assert.equal(persistedCalls, 0);
    assert.equal(uploadCalls, 0);
  } finally {
    delete global.wx;
  }
});

test('successful supplement retention removes the saved retry file after server receipt', async () => {
  const events = [];
  const result = await retainRejectedPhotoAsSupplement({
    path: 'wxfile://tmp/rejected.jpg', image: 'base64', idempotencyKey: 'capture-key',
    metadata: { capture_source: 'camera', capture_session: 'session-token' },
  }, {
    persist: () => Promise.resolve('wxfile://usr/rejected.jpg'),
    onPersisted: draft => events.push(['draft', draft.localPath]),
    upload: (image, key) => {
      events.push(['upload', key]);
      return Promise.resolve({
        url: '/uploads/site_photos/supplement.jpg', supplemental: true,
      });
    },
    cleanup: path => events.push(['cleanup', path]),
  });
  assert.equal(result.supplementUrl, '/uploads/site_photos/supplement.jpg');
  assert.deepEqual(events, [
    ['draft', 'wxfile://usr/rejected.jpg'],
    ['upload', 'capture-key:supplement'],
    ['cleanup', 'wxfile://usr/rejected.jpg'],
  ]);
});

test('supplement retention keeps the draft visible when local cleanup fails after upload', async () => {
  const result = await retainRejectedPhotoAsSupplement({
    path: 'wxfile://tmp/rejected.jpg', image: 'base64', idempotencyKey: 'capture-key',
    metadata: { capture_source: 'camera', capture_session: 'session-token' },
  }, {
    persist: () => Promise.resolve('wxfile://usr/rejected.jpg'),
    onPersisted: () => undefined,
    upload: () => Promise.resolve({
      url: '/uploads/site_photos/supplement.jpg', supplemental: true,
    }),
    cleanup: () => Promise.reject({ error: 'saved file busy' }),
  });
  assert.equal(result.localPath, 'wxfile://usr/rejected.jpg');
  assert.equal(result.supplemental, true);
  assert.equal(result.supplementUrl, undefined);
  assert.equal(result.uploadError.error, 'saved file busy');
});

test('empty successful selection is actionable while user cancellation stays silent', async () => {
  global.wx = {
    chooseMedia(options) {
      options.success({ tempFiles: [{ tempFilePath: '' }, { tempFilePath: {} }, {}] });
    },
  };
  await assert.rejects(
    chooseInspectionPhotos(6, 'supplement'),
    error => photoCaptureErrorMessage(error) === '未获取到有效照片，请重新选择'
      && !isPhotoSelectionCancelled(error),
  );

  global.wx.chooseMedia = options => options.fail({ errMsg: 'chooseMedia:fail cancel' });
  await assert.rejects(
    chooseInspectionPhotos(6, 'supplement'),
    error => isPhotoSelectionCancelled(error),
  );
  delete global.wx;
});

test('formal capture cannot fall back to chooseMedia while supplement stays album-only', async () => {
  const sourceTypes = [];
  global.wx = {
    chooseMedia(options) {
      sourceTypes.push(options.sourceType);
      options.success({ tempFiles: [{ tempFilePath: `wxfile://tmp/${sourceTypes.length}.jpg` }] });
    },
  };
  await assert.rejects(chooseInspectionPhotos(1, 'camera'),
    error => error.code === 'TRUSTED_CAMERA_PAGE_REQUIRED');
  await chooseInspectionPhotos(1, 'supplement');
  assert.deepEqual(sourceTypes, [['album']]);
  delete global.wx;
});

test('camera, supplement and report picker expose clear opening feedback', () => {
  assert.equal(photoActionOpeningTitle('camera'), '正在打开相机');
  assert.equal(photoActionOpeningTitle('supplement'), '正在打开补充材料');
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
  const duplicate = runPhotoActionOnce(owner, 'supplement', () => {
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

  await runPhotoActionOnce(owner, 'supplement', () => {
    actionCalls += 1;
  }, title => starts.push(title), () => { finishes += 1; });
  assert.equal(actionCalls, 2);
  assert.deepEqual(starts, ['正在打开相机', '正在打开补充材料']);
  assert.equal(finishes, 2);
});

test('cancel and every startup or upload failure release the shared photo action', async () => {
  const failures = [
    { phase: 'camera-cancel', source: 'camera', error: { errMsg: 'chooseMedia:fail cancel' } },
    { phase: 'album-cancel', source: 'supplement', error: { errMsg: 'chooseMedia:fail cancel' } },
    { phase: 'location', source: 'camera', error: { error: '定位失败' } },
    { phase: 'session', source: 'camera', error: { error: '会话创建失败' } },
    { phase: 'chooseMedia', source: 'supplement', error: { error: '选图失败' } },
    { phase: 'upload', source: 'supplement', error: { error: '上传失败' } },
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

test('inspection photo card separates camera evidence and supplement materials', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
  assert.match(wxml, /class="ip-photo-source-row"[\s\S]{0,300}?bindtap="onAddPhoto"[\s\S]{0,100}?data-source="camera">现场拍照<\/button>/);
  assert.match(wxml, /data-source="supplement">补充材料<\/button>/);
  assert.match(wxml, /补充材料（不计入必需照片）/);
  assert.match(wxml, /补充材料[^<]*不能替代现场拍照/);
  assert.match(wxml, /catchtap="onDelSupplementPhoto"[^>]*data-id="\{\{p\.businessId\}\}"/);
  assert.match(wxml, /catchtap="onRetryLocalPhoto"[^>]*data-kind="required"/);
  assert.match(wxml, /catchtap="onSaveLocalPhotoAsSupplement"/);
  assert.doesNotMatch(wxml, /data-source="watermark_album"/);
  assert.match(wxml, /class="report-photo-item photo-add-item" bindtap="onAddReportPhoto"/);
  const pageSource = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
  assert.match(pageSource, /onDelSupplementPhoto\(e\)[\s\S]{0,900}attachment_id: attachmentId/);
  assert.match(pageSource, /onAddReportPhoto\(\)[\s\S]*?runPhotoActionOnce\(this, 'media_picker'/);
  assert.match(pageSource, /validateReportPhotoPaths\(paths\)/);
  assert.match(pageSource, /handlePhotoActionFailure\(error, message => new Promise/);
  assert.match(pageSource, /onAddPhoto\(e\)[\s\S]*?runPhotoActionOnce\(this, captureSource/);
  assert.match(pageSource, /_openTrustedCamera\(sheet\)[\s\S]{0,1600}wx\.navigateTo\(/);
  assert.match(pageSource, /capture_client = 'trusted_camera_v1'/);
  assert.match(pageSource, /capture_frame_mode = capture\.frameMode/);
  assert.doesNotMatch(pageSource,
    /chooseInspectionPhotos\([^)]*['"]camera['"]/,
    'formal capture must not call the system chooseMedia camera entry');
  assert.match(pageSource, /inspectionUploadTaskResult\(result/);
  assert.match(pageSource, /addPendingInspectionPhotos\(/);
  assert.match(pageSource, /let idempotencyKey = metadata\._idempotency_key/);
  assert.match(pageSource, /api\.uploadSitePhoto\([\s\S]{0,100}idempotencyKey, metadata/);
  assert.match(pageSource, /delete metadata\.capture_session/);
  assert.match(pageSource, /businessResponse: result/);
  assert.match(pageSource, /retainRejectedPhotoAsSupplement\(failedPhoto/,
    'the rejected-photo retention choice must use the tested durable workflow');
  assert.match(pageSource,
    /onPersisted: draft => \{[\s\S]{0,260}appendInspectionPhotoRetryDraft\([\s\S]{0,260}supplementLocalPhotos/);
  assert.match(pageSource, /setInspectionPhotoIssueMessage\(this, issue\)[\s\S]{0,160}wx\.showModal/);
  assert.match(pageSource, /if \(!uploadResult\.issues\.length\)[\s\S]{0,140}photoResultTitle': ''[\s\S]{0,80}photoResultMessage': ''/);
  assert.match(wxml, /class="ip-photo-result"[\s\S]{0,180}class="ip-photo-result-title"[\s\S]{0,120}class="ip-photo-result-message"/);
  assert.doesNotMatch(wxml, /class="ip-form-error"[^>]*wx:if="\{\{sheet\.photoResultMessage\}\}"/);
  const wxss = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxss'), 'utf8');
  assert.match(wxss, /\.ip-photo-result-title[^}]*font-size:\s*30rpx/);
  assert.match(wxss, /\.ip-photo-result-message[^}]*font-size:\s*24rpx/);
  assert.match(pageSource, /onDelPhoto\(e\)[\s\S]*?deletePendingPhotoOnce/);
});

test('inspection photo retry draft is isolated, durable and cleared only after ownership transfers', () => {
  const pageSource = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.js'), 'utf8');
  assert.match(pageSource,
    /inspection_photo_retry_\$\{user\.id\}_\$\{planId\}_\$\{itemId\}/,
    'retry storage must be isolated by user, plan and inspection item');
  assert.match(pageSource,
    /photoMetadata = Object\.assign\(\{\}, metadata, \{ _idempotency_key: idempotencyKey \}\)/);
  assert.match(pageSource,
    /appendInspectionPhotoRetryDraft\([\s\S]{0,180}photoMetadata/,
    'the persisted retry metadata must retain the original capture session and idempotency key');

  const retryBlock = pageSource.slice(
    pageSource.indexOf('_retryInspectionLocalPhoto(kind, index, forceSupplement)'),
    pageSource.indexOf('onRetryLocalPhoto(e)'));
  assert.match(retryBlock,
    /saveInspectionSheetPhotoRetryDraft\([\s\S]*?isCurrentInspectionPhotoContext/,
    'a successful late retry must update durable storage before touching page state');

  const requiredDeleteBlock = pageSource.slice(
    pageSource.indexOf('onDelLocalPhoto(e)'),
    pageSource.indexOf('onDelSupplementLocalPhoto(e)'));
  const supplementDeleteBlock = pageSource.slice(
    pageSource.indexOf('onDelSupplementLocalPhoto(e)'),
    pageSource.indexOf('onPreview(e)'));
  assert.match(requiredDeleteBlock, /saveInspectionSheetPhotoRetryDraft\(/);
  assert.match(supplementDeleteBlock, /saveInspectionSheetPhotoRetryDraft\(/);
  assert.match(pageSource,
    /if \(\(s\.supplementLocalPhotos \|\| \[\]\)\.length\)[\s\S]{0,160}补充材料尚未上传，请先重试或删除/);
  assert.match(pageSource,
    /_afterSubmit\(s, syncPending = false, response\)[\s\S]{0,180}saveInspectionPhotoRetryDraft\(s\.item, \{\}\)/,
    'once server submit or the local submit queue owns the work, the independent retry draft is cleared');

  const unloadBlock = pageSource.slice(
    pageSource.indexOf('onUnload()'), pageSource.indexOf('onHide()'));
  assert.doesNotMatch(unloadBlock, /saveInspectionPhotoRetryDraft|removeStorageSync/,
    'page unload must not discard retryable files');
  assert.match(pageSource,
    /if \(!isCurrentInspectionPhotoContext\(this, sheet\.item\)\) return;/,
    'late upload responses must not overwrite another inspection item');
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
  assert.ok((page.match(/rejectedInspectionEvidence\(/g) || []).length >= 2,
    'opening and rebuilding must share the same rejected evidence projection');
  assert.match(template,
    /wx:for="\{\{sheet\.rejectedMedia\}\}"[\s\S]{0,260}src="\{\{p\.localPath\}\}"/,
    'rejected evidence must render only from the downloaded protected local file');
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
