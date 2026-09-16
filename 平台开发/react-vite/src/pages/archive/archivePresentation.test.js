import test from 'node:test';
import assert from 'node:assert/strict';
import { archiveCaptureLabel, archiveSourceLabel, hasArchiveFilters } from './archivePresentation.js';

test('archive enums never expose internal values', () => {
  assert.equal(archiveSourceLabel('manual_report'), '人工上报');
  assert.equal(archiveSourceLabel('unknown'), '待确认');
  assert.equal(archiveSourceLabel('server_new_value'), '待确认');
  assert.equal(archiveSourceLabel(), '未记录');
  assert.equal(archiveCaptureLabel('web_upload'), '网页补充');
  assert.equal(archiveCaptureLabel('unknown'), '待确认');
});

test('archive empty state distinguishes filtered results from an empty scope', () => {
  assert.equal(hasArchiveFilters({}), false);
  assert.equal(hasArchiveFilters({ keyword: '不存在' }), true);
  assert.equal(hasArchiveFilters({ date_range: [{}, {}] }), true);
});
