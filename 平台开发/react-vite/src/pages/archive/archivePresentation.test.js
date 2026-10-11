import test from 'node:test';
import assert from 'node:assert/strict';
import { archiveCaptureLabel, archiveSourceLabel, hasArchiveFilters } from './archivePresentation.js';
import { readFileSync } from 'node:fs';

test('archive enums never expose internal values', () => {
  assert.equal(archiveSourceLabel('manual_report'), '人工上报');
  assert.equal(archiveSourceLabel('unknown'), '待确认');
  assert.equal(archiveSourceLabel('server_new_value'), '待确认');
  assert.equal(archiveSourceLabel(), '未记录');
  assert.equal(archiveCaptureLabel('web_upload'), '网页补充');
  assert.equal(archiveCaptureLabel('camera'), '系统现场取证');
  assert.equal(archiveCaptureLabel('watermark_album'), '历史水印材料（非当前取证方式）');
  assert.equal(archiveCaptureLabel('unknown'), '待确认');
});

test('archive empty state distinguishes filtered results from an empty scope', () => {
  assert.equal(hasArchiveFilters({}), false);
  assert.equal(hasArchiveFilters({ keyword: '不存在' }), true);
  assert.equal(hasArchiveFilters({ date_range: [{}, {}] }), true);
});

test('archive filters query immediately except for the debounced keyword', () => {
  const source = readFileSync(new URL('./ArchivePage.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, />查询<\/Button>/);
  assert.match(source, /setTimeout\(\(\) => \{[\s\S]*?\}, 400\)/);
  assert.match(source, /onChange=\{value => updateImmediateFilter\(\{ site_id: listFilterValue\(value\) \}\)\}/);
  assert.match(source, /onChange=\{value => updateImmediateFilter\(\{ business_type: listFilterValue\(value\) \}\)\}/);
  assert.match(source, /onChange=\{value => updateImmediateFilter\(\{ date_range: value \}\)\}/);
  assert.match(source, /dataIndex: 'display_url'/);
  assert.match(source, /window\.open\(item\.display_url, '_blank'\)/);
  assert.doesNotMatch(source, /window\.open\(item\.stored_path/);
  assert.match(source, /label="展示副本文件"/);
  assert.match(source, /alt=\{displayTitle\(detail\)\} width="100%"/);
  assert.match(source, /objectFit: 'contain'/);
});
