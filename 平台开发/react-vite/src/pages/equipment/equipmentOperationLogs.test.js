import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('./EquipmentPage.jsx', import.meta.url), 'utf8');

test('equipment log tab requests the complete server-paginated device log stream', () => {
  assert.match(source, /operation-logs\?module=device&include_inventory=1&paginated=1&page=\$\{page\}&page_size=\$\{pageSize\}/);
  assert.doesNotMatch(source, /getStrict\('\/operation-logs\?limit=50'\)/);
});

test('operation-log pagination no longer merges truncated dashboard samples in the browser', () => {
  assert.doesNotMatch(source, /latest_operations/);
  assert.doesNotMatch(source, /merged\.slice\(0, 100\)/);
  assert.match(source, /pageSizeOptions: \[20, 50, 100\]/);
});

test('batch device imports are presented in Chinese in both log views', () => {
  assert.equal((source.match(/batch_import: '批量导入'/g) || []).length, 2);
});
