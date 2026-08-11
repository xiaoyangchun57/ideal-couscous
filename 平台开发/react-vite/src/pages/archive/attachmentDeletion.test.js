import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canSubmitAttachmentDelete,
  hasAdminRole,
  normalizeDeleteReason,
} from './attachmentDeletion.js';

test('only a real admin role exposes the deletion capability', () => {
  assert.equal(hasAdminRole({ roles: ['admin', 'operator'] }), true);
  assert.equal(hasAdminRole({ roles: ['reviewer'] }), false);
  assert.equal(hasAdminRole({ role: 'operator' }), false);
  assert.equal(hasAdminRole({ roles: [] }), false);
});

test('delete reasons reject empty and whitespace-only values', () => {
  assert.equal(normalizeDeleteReason('  '), '');
  assert.equal(normalizeDeleteReason('  测试误传  '), '测试误传');
  assert.equal(canSubmitAttachmentDelete({ can_delete: true }, '  '), false);
  assert.equal(canSubmitAttachmentDelete({ can_delete: true }, '测试误传'), true);
  assert.equal(canSubmitAttachmentDelete({ can_delete: false }, '测试误传'), false);
  assert.equal(canSubmitAttachmentDelete({ can_delete: true }, '测试误传', true), false);
});
