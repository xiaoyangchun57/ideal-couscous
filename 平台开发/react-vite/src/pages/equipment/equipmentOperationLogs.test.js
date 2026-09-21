import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('./EquipmentPage.jsx', import.meta.url), 'utf8');

test('equipment log tab requests only the device operation-log module', () => {
  assert.match(source, /getStrict\('\/operation-logs\?module=device&limit=50'\)/);
  assert.doesNotMatch(source, /getStrict\('\/operation-logs\?limit=50'\)/);
});

test('inventory log merge uses persisted ids and stable source-aware deduplication', () => {
  assert.match(source, /id: `inv-\$\{o\.id\}`/);
  assert.match(source, /const mergedByKey = new Map\(\)/);
  assert.match(source, /const key = `\$\{row\._type\}:\$\{row\.id\}`/);
});

test('batch device imports are presented in Chinese in both log views', () => {
  assert.equal((source.match(/batch_import: '批量导入'/g) || []).length, 2);
});
