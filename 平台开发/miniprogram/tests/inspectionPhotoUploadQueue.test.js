const test = require('node:test');
const assert = require('node:assert/strict');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

async function waitFor(predicate, message) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (predicate()) return;
    await flush();
  }
  assert.fail(message || 'condition was not reached');
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

const storage = { user: { id: 2, real_name: '测试执行人' }, sites: [] };
const app = { globalData: { token: 'test', executionTarget: null, sites: [] } };
let definition;
let toasts = [];
let modals = [];
let removedFiles = [];

global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getStorageSync: key => storage[key] || [],
  setStorageSync: (key, value) => { storage[key] = value; },
  removeStorageSync: key => { delete storage[key]; },
  showToast: options => { toasts.push(options); },
  showModal: options => {
    modals.push(options);
    if (options.success) options.success({ confirm: false, cancel: true });
  },
  showLoading: () => {},
  hideLoading: () => {},
  authorize: options => options.success({}),
  getLocation: options => options.success({ latitude: 28.6, longitude: 115.7 }),
  getFileSystemManager: () => ({
    readFile: options => options.success({ data: Buffer.from(options.filePath).toString('base64') }),
  }),
  saveFile: options => options.success({
    savedFilePath: `wxfile://usr/${String(options.tempFilePath).split('/').pop()}`,
  }),
  removeSavedFile: options => {
    removedFiles.push(options.filePath);
    if (options.success) options.success({});
  },
};

const api = require('../services/api.js');
require('../pages/inspection/inspection.js');

function createPhotoPage() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = (patch, done) => {
    Object.keys(patch).forEach(key => setPath(page.data, key, patch[key]));
    if (done) done();
  };
  page._alive = true;
  page.data.selSiteId = 20;
  page.data.categories = [{ items: [
    { item_id: 501, plan_id: 10, item_name: 'A项', required_photos: 0,
      review_status: 0, pending_photo_urls: [] },
    { item_id: 502, plan_id: 10, item_name: 'B项', required_photos: 0,
      review_status: 0, pending_photo_urls: [] },
  ] }];
  page.data.sheet = Object.assign({}, page.data.sheet, {
    open: true,
    item: {
      item_id: 501,
      plan_id: 10,
      item_name: '站房现场照片',
      required_photos: 0,
      review_status: 0,
    },
    result: 'normal',
    remark: '现场照片确认',
    photos: [],
    pendingPhotos: [],
    localPhotos: [],
    localPhotoMeta: [],
    supplementPhotos: [],
    supplementLocalPhotos: [],
    supplementLocalMeta: [],
    supplementOnly: false,
    originalPhotoCount: 0,
  });
  page._prepareProtectedMediaForSheet = () => {};
  return page;
}

function switchToItemB(page) {
  const item = page.data.categories[0].items[1];
  page.data.sheet = Object.assign({}, page.data.sheet, {
    open: true,
    item,
    remark: 'B项现场说明',
    photos: [],
    pendingPhotos: [],
    localPhotos: [],
    localPhotoMeta: [],
    supplementPhotos: [],
    supplementLocalPhotos: [],
    supplementLocalMeta: [],
  });
}

function reopenItemA(page) {
  page.hasSiteCheckIn = () => true;
  page.onOpenItem({ currentTarget: { dataset: { id: 501 } } });
}

test('inspection capture stays visible through delayed upload and weak-network retry without duplicates', async () => {
  const originalUpload = api.uploadSitePhoto;
  const originalTrack = api.trackEvent;
  const originalSubmit = api.submitItem;
  try {
    toasts = [];
    modals = [];
    removedFiles = [];
    Object.keys(storage).filter(key => key.startsWith('inspection_photo_retry_'))
      .forEach(key => delete storage[key]);

    const page = createPhotoPage();
    const capturePaths = ['/tmp/first.jpg', '/tmp/second.jpg', '/tmp/third.jpg'];
    let cameraCalls = 0;
    page._openTrustedCamera = () => {
      const path = capturePaths[cameraCalls];
      cameraCalls += 1;
      return Promise.resolve({
        path,
        captureSession: `session-${cameraCalls}`,
        gpsLat: 28.6,
        gpsLng: 115.7,
        frameOrientation: 'portrait',
        frameRatio: '3:4',
        frameMode: 'photo',
      });
    };

    const uploadCalls = [];
    let uploadHandler;
    api.trackEvent = () => {};
    api.submitItem = () => { throw new Error('submit must remain blocked while a photo is active'); };
    api.uploadSitePhoto = (siteId, image, key, metadata) => {
      uploadCalls.push({ siteId, image, key, metadata: Object.assign({}, metadata) });
      return uploadHandler(siteId, image, key, metadata);
    };

    const firstGate = deferred();
    uploadHandler = () => firstGate.promise;
    const first = page.onAddPhoto({ currentTarget: { dataset: { source: 'camera' } } });
    await waitFor(() => uploadCalls.length === 1, 'the first upload did not start');
    assert.deepEqual(page.data.sheet.localPhotos, ['/tmp/first.jpg']);
    assert.equal(page.data.sheet.localPhotoMeta[0].uploadStatus, 'uploading');
    assert.equal(page.data.sheet.localPhotoMeta[0].uploadStatusLabel, '上传中');

    const duplicate = page.onAddPhoto({ currentTarget: { dataset: { source: 'camera' } } });
    assert.strictEqual(duplicate, first, 'a repeated tap must join the in-flight action');
    assert.equal(cameraCalls, 1, 'a repeated tap must not open a second camera');
    assert.equal(toasts.at(-1).title, '上一张正在处理');

    page.onSubmitItem();
    assert.equal(toasts.at(-1).title, '照片仍在处理或上传，请等待完成');

    firstGate.resolve({
      url: '/uploads/site_photos/first.jpg',
      display_url: '/uploads/site_photos/first.jpg',
      accepted_for_review: true,
    });
    await first;
    assert.deepEqual(page.data.sheet.localPhotos, []);
    assert.equal(page.data.sheet.photos.length, 1);
    assert.match(page.data.sheet.photos[0], /\/uploads\/site_photos\/first\.jpg$/);

    uploadHandler = () => Promise.reject({ code: -1, error: '弱网中断' });
    await page.onAddPhoto({ currentTarget: { dataset: { source: 'camera' } } });
    assert.equal(cameraCalls, 2);
    assert.deepEqual(page.data.sheet.localPhotos, ['wxfile://usr/second.jpg']);
    assert.equal(page.data.sheet.localPhotoMeta[0].uploadStatus, 'failed');
    assert.equal(page.data.sheet.localPhotoMeta[0].uploadStatusLabel, '上传失败，可重试/删除');
    const failedKey = uploadCalls[1].key;
    assert.equal(page.data.sheet.localPhotoMeta[0]._idempotency_key, failedKey);
    assert.equal(page.data.sheet.localPhotoMeta[0].capture_session, 'session-2');
    const retryDraft = storage.inspection_photo_retry_2_10_501;
    assert.deepEqual(retryDraft.localPhotos, ['wxfile://usr/second.jpg']);
    assert.equal(retryDraft.localPhotoMeta[0]._idempotency_key, failedKey);

    const retryGate = deferred();
    uploadHandler = () => retryGate.promise;
    const retry = page._retryInspectionLocalPhoto('required', 0, false);
    await waitFor(() => uploadCalls.length === 3, 'the retry upload did not start');
    assert.equal(page.data.sheet.localPhotoMeta[0].uploadStatus, 'uploading');
    const duplicateRetry = page._retryInspectionLocalPhoto('required', 0, false);
    assert.strictEqual(duplicateRetry, retry, 'repeated retry taps must share the in-flight upload');
    assert.equal(toasts.at(-1).title, '上一张正在处理');
    assert.equal(uploadCalls[2].key, failedKey, 'retry must preserve the original idempotency key');
    retryGate.resolve({
      url: '/uploads/site_photos/second.jpg',
      display_url: '/uploads/site_photos/second.jpg',
      accepted_for_review: true,
    });
    await retry;
    assert.deepEqual(page.data.sheet.localPhotos, []);
    assert.equal(page.data.sheet.photos.length, 2);
    assert.equal(new Set(page.data.sheet.photos).size, 2, 'retry must not duplicate the remote photo');
    assert.ok(removedFiles.includes('wxfile://usr/second.jpg'));

    uploadHandler = () => Promise.reject({ code: -1, error: '弱网中断' });
    await page.onAddPhoto({ currentTarget: { dataset: { source: 'camera' } } });
    assert.equal(cameraCalls, 3, 'three explicit taps produce three capture attempts');
    assert.deepEqual(page.data.sheet.localPhotos, ['wxfile://usr/third.jpg']);
    page.onSubmitItem();
    assert.equal(modals.at(-1).title, '现场照片尚未上传');
    assert.equal(modals.at(-1).confirmText, '保存并同步');
    assert.match(modals.at(-1).content, /返回重试\/删除/);

    page.onDelLocalPhoto({ currentTarget: { dataset: { idx: 0 } } });
    assert.deepEqual(page.data.sheet.localPhotos, []);
    assert.ok(removedFiles.includes('wxfile://usr/third.jpg'),
      'deleting the failed third capture must remove its persisted file');
    assert.equal(storage.inspection_photo_retry_2_10_501, undefined,
      'deleting the last failed photo must clear its durable retry draft');
    assert.equal(page.data.sheet.photos.length, 2,
      'after three captures and deleting one, visible accepted photos equal the final attachment count');
  } finally {
    api.uploadSitePhoto = originalUpload;
    api.trackEvent = originalTrack;
    api.submitItem = originalSubmit;
  }
});

test('late first-upload success returns to item A without overwriting item B and submits from A', async () => {
  const originalUpload = api.uploadSitePhoto;
  const originalTrack = api.trackEvent;
  const originalSubmit = api.submitItem;
  try {
    Object.keys(storage).filter(key => key !== 'user' && key !== 'sites')
      .forEach(key => delete storage[key]);
    const page = createPhotoPage();
    page._openTrustedCamera = () => Promise.resolve({
      path: '/tmp/late-a.jpg', captureSession: 'late-a', gpsLat: 28.6, gpsLng: 115.7,
      frameOrientation: 'portrait', frameRatio: '3:4', frameMode: 'photo',
    });
    const gate = deferred();
    let uploadStarted = false;
    let submittedPayload = null;
    api.trackEvent = () => {};
    api.uploadSitePhoto = () => {
      uploadStarted = true;
      return gate.promise;
    };
    api.submitItem = payload => {
      submittedPayload = payload;
      return Promise.resolve({
        photo_urls: JSON.parse(payload.photo_urls), review_status: 1, evidence_status: 'pending',
      });
    };

    const capture = page.onAddPhoto({ currentTarget: { dataset: { source: 'camera' } } });
    await waitFor(() => uploadStarted, 'item A upload did not start');
    switchToItemB(page);
    gate.resolve({
      url: '/uploads/site_photos/late-a.jpg',
      display_url: '/uploads/site_photos/late-a.jpg',
      accepted_for_review: true,
    });
    await capture;

    assert.equal(page.data.sheet.item.item_id, 502);
    assert.deepEqual(page.data.sheet.photos, [], 'late item A response must not overwrite item B');
    const itemA = page.data.categories[0].items[0];
    assert.deepEqual(itemA.pending_photo_urls, ['/uploads/site_photos/late-a.jpg']);

    reopenItemA(page);
    assert.equal(page.data.sheet.item.item_id, 501);
    assert.ok(page.data.sheet.photos.some(url => /late-a\.jpg$/.test(url)));
    assert.ok(page.data.sheet.pendingPhotos.some(url => /late-a\.jpg$/.test(url)));
    page.onSubmitItem();
    await waitFor(() => submittedPayload, 'item A was not submitted');
    assert.ok(JSON.parse(submittedPayload.photo_urls).some(url => /late-a\.jpg$/.test(url)));
  } finally {
    api.uploadSitePhoto = originalUpload;
    api.trackEvent = originalTrack;
    api.submitItem = originalSubmit;
  }
});

test('late retry success clears item A draft and remains visible after returning from item B', async () => {
  const originalUpload = api.uploadSitePhoto;
  try {
    Object.keys(storage).filter(key => key !== 'user' && key !== 'sites')
      .forEach(key => delete storage[key]);
    removedFiles = [];
    const page = createPhotoPage();
    const failedPath = 'wxfile://usr/late-retry-a.jpg';
    const failedMeta = {
      _idempotency_key: 'retry-a-key', uploadStatus: 'failed',
      uploadStatusLabel: '上传失败，可重试/删除',
    };
    page.data.sheet.localPhotos = [failedPath];
    page.data.sheet.localPhotoMeta = [failedMeta];
    storage.inspection_photo_retry_2_10_501 = {
      localPhotos: [failedPath], localPhotoMeta: [failedMeta],
      supplementLocalPhotos: [], supplementLocalMeta: [],
    };
    const gate = deferred();
    let uploadStarted = false;
    api.uploadSitePhoto = () => {
      uploadStarted = true;
      return gate.promise;
    };

    const retry = page._retryInspectionLocalPhoto('required', 0, false);
    await waitFor(() => uploadStarted, 'item A retry did not start');
    switchToItemB(page);
    gate.resolve({
      url: '/uploads/site_photos/late-retry-a.jpg',
      display_url: '/uploads/site_photos/late-retry-a.jpg',
      accepted_for_review: true,
    });
    await retry;

    assert.equal(page.data.sheet.item.item_id, 502);
    assert.deepEqual(page.data.sheet.photos, [], 'late retry response must not overwrite item B');
    assert.equal(storage.inspection_photo_retry_2_10_501, undefined);
    assert.ok(removedFiles.includes(failedPath));
    assert.deepEqual(
      page.data.categories[0].items[0].pending_photo_urls,
      ['/uploads/site_photos/late-retry-a.jpg'],
    );

    reopenItemA(page);
    assert.ok(page.data.sheet.photos.some(url => /late-retry-a\.jpg$/.test(url)));
    assert.ok(page.data.sheet.pendingPhotos.some(url => /late-retry-a\.jpg$/.test(url)));
  } finally {
    api.uploadSitePhoto = originalUpload;
  }
});
