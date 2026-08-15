import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canSubmitAttachmentDelete,
  canVoidAttachment,
  attachmentDeletionDialogMode,
  attachmentResourceState,
  archiveStatusLayout,
  archivePrimaryTitle,
  archiveSecondaryMeta,
  archiveHistoryStatus,
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

test('archive metadata keeps capture and upload timestamps semantically separate', () => {
  assert.deepEqual(archiveSecondaryMeta({
    site_name: 'Site A', category: 'photo', taken_at: null, created_at: '2026-08-12 10:10:00',
  }), ['Site A', 'photo', '拍摄时间待确认', '上传：2026-08-12 10:10:00']);
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

test('history status keeps soft deletion and null review status visible', () => {
  assert.deepEqual(archiveHistoryStatus({
    is_deleted: 1, review_status: 'approved', delete_reason: '测试资料清理',
  }), { label: '已移出', color: 'default', reason: '测试资料清理' });
  assert.deepEqual(archiveHistoryStatus({ review_status: null }),
    { label: '待所属业务审核', color: 'processing', reason: '' });
  assert.equal(archiveHistoryStatus({
    review_status: 'voided', void_reason: '错误证据', reject_reason: '旧驳回原因',
  }, { voided: { label: '已作废', color: 'default' } }).reason, '错误证据');
  assert.equal(archiveHistoryStatus({
    review_status: 'rejected', reject_reason: '内容不清晰', evidence_reason: '来源原因',
  }, { rejected: { label: '已驳回', color: 'error' } }).reason, '内容不清晰');
  assert.equal(archiveHistoryStatus({
    review_status: 'approved', evidence_qualification: 'ineligible', evidence_reason: '无法确认拍摄时间',
  }).reason, '无法确认拍摄时间');
  assert.match(archiveHistoryStatus({
    review_status: 'approved', evidence_qualification: 'qualified',
    extra_json: JSON.stringify({ material_role: 'supplement' }),
  }).reason, /补充材料/);
  for (const reviewStatus of ['pending', null]) {
    const supplement = archiveHistoryStatus({
      review_status: reviewStatus,
      extra_json: JSON.stringify({ material_role: 'supplement' }),
    });
    assert.equal(supplement.label, '补充材料');
    assert.match(supplement.reason, /不进入业务审核/);
  }
});

test('delete dialog never offers a reason field for a server-blocked formal record', () => {
  assert.equal(attachmentDeletionDialogMode(undefined), 'checking');
  assert.equal(attachmentDeletionDialogMode({ can_delete: true }), 'ordinary');
  assert.equal(attachmentDeletionDialogMode({ can_delete: false, source_type: 'inspection', association_status: 'linked' }), 'void');
  assert.equal(attachmentDeletionDialogMode({ can_delete: false, source_type: 'test', association_status: 'unlinked' }), 'blocked');
});
