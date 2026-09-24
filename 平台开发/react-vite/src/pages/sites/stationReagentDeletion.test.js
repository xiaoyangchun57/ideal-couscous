import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync(new URL('./SitesPage.jsx', import.meta.url), 'utf8');

test('reagent inventory deletion requires explicit confirmation and a bounded reason', () => {
  assert.match(page, /title=\{`确认删除试剂库存/);
  assert.match(page, /okText="确认删除"/);
  assert.match(page, /disabled: !reagentDelete\?\.reason\.trim\(\)/);
  assert.match(page, /maxLength=\{200\}/);
  assert.match(page, /const reason = reagentDelete\?\.reason\.trim\(\)/);
});

test('strict deletion sends the same idempotency key on retry and blocks duplicate clicks', () => {
  assert.match(page, /if \(!row \|\| reagentDeletingRef\.current\) return/);
  assert.match(page, /const key = reagentDelete\.key \|\|/);
  assert.match(page, /reagentDeletingRef\.current = true/);
  assert.match(page, /api\.deleteStrict\(`\/reagent-inventory\/\$\{row\.site_id\}\/\$\{row\.reagent_id\}`, \{\s*reason, _idempotency_key: key/);
  assert.match(page, /setReagentDelete\(\(current\) => current && \{ \.\.\.current, error: error\?\.message/);
  assert.match(page, /onChange=\{\(event\) => setReagentDelete\(\(current\) => \(\{ \.\.\.current, reason: event\.target\.value, key: '', error: '' \}\)\)\}/);
});

test('the UI does not close or refresh before deletion succeeds, and reports refresh failure', () => {
  const deletion = page.slice(page.indexOf('const deleteReagent = async'), page.indexOf('// 试剂标定：'));
  assert.ok(deletion.indexOf('await api.deleteStrict') < deletion.indexOf('setReagentDelete(null)'));
  assert.ok(deletion.indexOf('await api.deleteStrict') < deletion.indexOf('await api.getStrict'));
  assert.match(deletion, /setReagentInventoryError\(error\?\.message \|\| '库存刷新失败，请重新加载'\)/);
  assert.match(page, /confirmLoading=\{reagentDeleting\}/);
  assert.match(page, /if \(!reagentDeletingRef\.current\) setReagentDelete\(null\)/);
});
