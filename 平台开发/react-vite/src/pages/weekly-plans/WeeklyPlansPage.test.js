import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./WeeklyPlansPage.jsx', import.meta.url), 'utf8');

test('legacy weekly-plan assignee never gets a stale asynchronous initial value', () => {
  assert.match(source, /api\.get\('\/users\?status=active'\)/);
  assert.match(source, /setInspectorCandidates\(filterAssignableUsers\(u\)\)/);
  assert.match(source, /<Form\.Item name="user_id" label="巡检人" rules=\{\[\{ required: true \}\]\}>/);
  assert.match(source, /onCancel=\{\(\) => \{ form\.resetFields\(\); setCreateOpen\(false\); \}\}/);
  assert.doesNotMatch(source, /initialValue=|DEFAULT_INSPECTOR_ID|setFieldsValue\(\{\s*user_id/);
});
