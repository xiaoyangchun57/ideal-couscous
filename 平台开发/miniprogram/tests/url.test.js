const assert = require('node:assert/strict');
const test = require('node:test');

global.wx = {
  getSystemInfoSync: () => ({ platform: 'ios' }),
  getStorageSync: () => '',
};

const {
  resolveUploadUrl,
  uploadStoragePath,
  prepareReportPhotoStoragePaths,
} = require('../utils/url.js');

test('upload URLs are displayed absolutely but mutations use trusted storage paths', () => {
  const storedPath = '/uploads/workorder_photos/evidence.jpg';
  const displayUrl = resolveUploadUrl(storedPath);
  assert.equal(displayUrl, 'https://ops.hhyc-tec.cn' + storedPath);
  assert.equal(uploadStoragePath(displayUrl), storedPath);
  assert.equal(uploadStoragePath(storedPath), storedPath);
});

test('storage path conversion rejects external and unrelated URLs', () => {
  assert.equal(uploadStoragePath('https://example.com/uploads/evidence.jpg'), '');
  assert.equal(uploadStoragePath('https://ops.hhyc-tec.cn/not-uploads/evidence.jpg'), '');
  assert.equal(uploadStoragePath('/uploads/evidence.jpg?token=secret'), '');
  assert.equal(uploadStoragePath('/uploads/evidence.jpg#preview'), '');
  assert.equal(uploadStoragePath('https://ops.hhyc-tec.cn/uploads/evidence.jpg?token=secret'), '');
  assert.equal(uploadStoragePath('https://ops.hhyc-tec.cn/uploads/evidence.jpg#preview'), '');
  assert.equal(uploadStoragePath('/uploads/'), '');
  assert.equal(uploadStoragePath(''), '');
  assert.equal(uploadStoragePath(null), '');
});

test('report photo paths reject the whole batch when any item is invalid', () => {
  const prepared = prepareReportPhotoStoragePaths([
    '/uploads/site_photos/valid.jpg',
    'https://example.com/uploads/site_photos/external.jpg',
  ]);

  assert.deepEqual(prepared, { ok: false, paths: [], reason: 'invalid_path' });
});

test('report photo paths deduplicate valid one-to-six photo payloads', () => {
  const one = prepareReportPhotoStoragePaths([
    'https://ops.hhyc-tec.cn/uploads/site_photos/one.jpg',
    '/uploads/site_photos/one.jpg',
  ]);
  assert.deepEqual(one, { ok: true, paths: ['/uploads/site_photos/one.jpg'] });

  const sixPaths = Array.from({ length: 6 }, (_, index) => `/uploads/site_photos/${index + 1}.jpg`);
  assert.deepEqual(prepareReportPhotoStoragePaths(sixPaths), { ok: true, paths: sixPaths });
});

test('report photo paths enforce the one-to-six count after deduplication', () => {
  assert.deepEqual(prepareReportPhotoStoragePaths([]), { ok: false, paths: [], reason: 'invalid_count' });
  assert.deepEqual(
    prepareReportPhotoStoragePaths(Array.from({ length: 7 }, (_, index) => `/uploads/site_photos/${index + 1}.jpg`)),
    { ok: false, paths: [], reason: 'invalid_count' }
  );
});

test('an invalid delete address cannot reach the mutation boundary', () => {
  let requests = 0;
  const storagePath = uploadStoragePath('https://example.com/uploads/site_photos/external.jpg');
  if (storagePath) requests += 1;

  assert.equal(storagePath, '');
  assert.equal(requests, 0);
});
