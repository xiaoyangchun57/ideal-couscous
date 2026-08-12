import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canSubmitAttachmentDelete,
  canVoidAttachment,
  attachmentDeletionDialogMode,
  attachmentResourceState,
  archiveStatusLayout,
  archivePrimaryTitle,
  hasAdminRole,
  hasReviewerRole,
  isFormalAttachment,
  normalizeDeleteReason,
  voidEligibility,
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

test('formal evidence uses the void workflow and keeps primary status exclusive', () => {
  const target = { source_type: 'inspection', association_status: 'linked', review_status: 'approved' };
  assert.equal(isFormalAttachment(target), true);
  assert.equal(hasReviewerRole({ role: 'reviewer' }), true);
  assert.equal(canVoidAttachment(target, { role: 'reviewer' }), true);
  assert.equal(canVoidAttachment({ ...target, review_status: 'pending' }, { role: 'reviewer' }), false);
  assert.equal(canVoidAttachment({ ...target, archived: 1 }, { role: 'admin' }), false);
  assert.equal(isFormalAttachment({ source_type: 'test', association_status: 'unlinked' }), false);
  assert.equal(isFormalAttachment({ source_type: 'test', source_id: 0, association_status: 'unlinked' }), false);
  assert.equal(voidEligibility(target, { role: 'reviewer' }).allowed, true);
  assert.equal(voidEligibility(target, { role: 'reviewer' }).allowed && normalizeDeleteReason('x'.repeat(240)).length, 240);
});

test('archive presentation keeps failed image fallback bounded and item identity primary', () => {
  const target = { id: 1, filename: 'very-long-original-file-name.jpg', item_name: 'water intake' };
  assert.equal(archivePrimaryTitle(target), 'water intake');
  assert.deepEqual(attachmentResourceState(target, true), {
    failed: true, imageAlt: '', downloadDisabled: true, label: '文件不可用',
  });
});

test('archive status layout contains risk history without consuming the action column', () => {
  const layout = archiveStatusLayout();
  assert.equal(layout.statusColumnWidth, 176);
  assert.equal(layout.actionColumnWidth, 176);
  assert.equal(layout.tableMinWidth, 1180);
  assert.equal(layout.tagStyle.whiteSpace, 'normal');
  assert.equal(layout.tagStyle.overflowWrap, 'anywhere');
});

test('delete dialog never offers a reason field for a server-blocked formal record', () => {
  assert.equal(attachmentDeletionDialogMode(undefined), 'checking');
  assert.equal(attachmentDeletionDialogMode({ can_delete: true }), 'ordinary');
  assert.equal(attachmentDeletionDialogMode({ can_delete: false, source_type: 'inspection', association_status: 'linked' }), 'void');
  assert.equal(attachmentDeletionDialogMode({ can_delete: false, source_type: 'test', association_status: 'unlinked' }), 'blocked');
});
