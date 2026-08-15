const assert = require('node:assert/strict');
const test = require('node:test');

global.wx = {
  getSystemInfoSync: () => ({ platform: 'ios' }),
  getStorageSync: () => '',
};

const { resolveUploadUrl, uploadStoragePath } = require('../utils/url.js');

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
  assert.equal(uploadStoragePath(null), '');
});
