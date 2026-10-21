import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const audit = readFileSync(new URL('./AuditPage.jsx', import.meta.url), 'utf8');

test('remote workorder review consumes structured records and does not require check-in', () => {
  assert.match(audit, /const remoteWorkorder = item\.source_type === 'workorder_review' && item\.service_mode === 'remote'/);
  assert.match(audit, /!item\.remote_contact \|\| !item\.remote_process \|\| !item\.remote_result/);
  assert.match(audit, /<Descriptions\.Item label="处置方式">/);
  assert.match(audit, /<Descriptions\.Item label="协助对象">/);
  assert.match(audit, /<Descriptions\.Item label="协助经过">/);
  assert.match(audit, /<Descriptions\.Item label="处理结果">/);
  assert.match(audit, /remoteWorkorder \? '远程协助无需签到'/);
});

test('remote supplements are visible but explicitly excluded from onsite photo count', () => {
  assert.match(audit, /photo\.supplemental/);
  assert.match(audit, /远程补充材料，不计现场照片/);
  assert.match(audit, /现场合格 \{item\.actual_photos \|\| 0\} 张/);
});
