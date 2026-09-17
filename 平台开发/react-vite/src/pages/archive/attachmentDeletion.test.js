import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
  archivePurgeEligibility,
  hasAdminRole,
  hasReviewerRole,
  isFormalAttachment,
  normalizeDeleteReason,
  rejectedPurgeEligibility,
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

test('archive title uses one business name without rebuilding site and time metadata', () => {
  assert.equal(archivePrimaryTitle({ id: 8, item_name: '进水口', description: '进水口', category: '现场影像' }), '进水口');
  assert.equal(archivePrimaryTitle({ id: 9, description: '校准前读数', category: '校准' }), '校准前读数');
  assert.equal(archivePrimaryTitle({ id: 10, category: '水样留存' }), '水样留存');
  assert.equal(archivePrimaryTitle({ id: 11, archive_name: '站点 · 时间 · 类型' }), '影像 #11');
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

test('rejected hard purge is admin-only, inspection-only, and linked without client reason', () => {
  const target = { source_type: 'inspection', review_status: 'rejected', association_status: 'linked' };
  assert.equal(rejectedPurgeEligibility(target, { roles: ['admin'] }).allowed, true);
  assert.equal(rejectedPurgeEligibility(target, { roles: ['reviewer'] }).allowed, false);
  assert.equal(rejectedPurgeEligibility({ ...target, review_status: 'approved' }, { role: 'admin' }).allowed, false);
  assert.equal(rejectedPurgeEligibility({ ...target, source_type: 'workorder' }, { role: 'admin' }).allowed, false);
  assert.equal(rejectedPurgeEligibility({ ...target, association_status: 'migration_issue' }, { role: 'admin' }).allowed, false);
});

test('archive purge action consumes the server eligibility decision', () => {
  assert.equal(archivePurgeEligibility({ can_purge: true }, { roles: ['admin'] }).allowed, true);
  assert.equal(archivePurgeEligibility({ can_purge: true }, { roles: ['reviewer'] }).allowed, false);
  assert.deepEqual(archivePurgeEligibility({ can_purge: false, block_reason: '仍是当前有效证据' }, { role: 'admin' }), {
    allowed: false, reason: '仍是当前有效证据',
  });
});

test('archive purge confirmation uses authoritative preview, reason and retry context', () => {
  const source = readFileSync(new URL('./ArchivePage.jsx', import.meta.url), 'utf8');
  const modalStart = source.indexOf('<Modal open={purgeOpen}');
  const modalEnd = source.indexOf('</Modal>', modalStart);
  const modalSource = source.slice(modalStart, modalEnd);
  assert.match(modalSource, /purgeReason/);
  assert.match(modalSource, /不可恢复/);
  assert.match(modalSource, /请填写清理原因/);
  assert.match(source, /getStrict\(`\/attachments\/\$\{detail\.id\}\/purge`/);
  assert.match(source, /postStrict\(`\/attachments\/\$\{detail\.id\}\/purge`, \{ reason/);
  assert.match(source, /catch \(requestError\) \{\s*setPurgeError/);
  assert.doesNotMatch(source.match(/catch \(requestError\)[\s\S]*?finally/)[0], /setPurgeOpen\(false\)|setDetail\(null\)/);
});

test('archive batch purge uses one server preview and one atomic submit for table and grid selection', () => {
  const source = readFileSync(new URL('./ArchivePage.jsx', import.meta.url), 'utf8');
  assert.match(source, /postStrict\('\/attachments\/purge-batch\/preview'/);
  assert.match(source, /postStrict\('\/attachments\/purge-batch'/);
  assert.match(source, /idempotency_key: batchKeyRef\.current/);
  assert.match(source, /rowSelection=.*archiveMode === 'history'/s);
  assert.match(source, /aria-label=\{`选择历史影像/);
  assert.match(source, /有 \$\{batchPreview\.blocked_count\} 条不可清理/);
  assert.match(source, /temporary_cleanup_pending > 0/);
  assert.doesNotMatch(source, /selectedIds\.map[\s\S]*?\/attachments\/\$\{/);
});

test('uncertain batch purge keeps the original request available for idempotent confirmation', () => {
  const source = readFileSync(new URL('./ArchivePage.jsx', import.meta.url), 'utf8');
  const submitStart = source.indexOf('const submitBatchPurge = async () =>');
  const submitEnd = source.indexOf('\n  useEffect(() => {', submitStart);
  const submitSource = source.slice(submitStart, submitEnd);
  const catchStart = submitSource.indexOf('} catch (requestError) {');
  const catchEnd = submitSource.indexOf('} finally {', catchStart);
  const catchSource = submitSource.slice(catchStart, catchEnd);
  const uncertainStart = catchSource.indexOf('if (uncertainResult) {');
  const definiteStart = catchSource.indexOf('} else {', uncertainStart);
  const uncertainSource = catchSource.slice(uncertainStart, definiteStart);
  const definiteSource = catchSource.slice(definiteStart);

  assert.match(submitSource, /batchResultUnknown \? batchRequestRef\.current/);
  assert.match(submitSource, /attachment_ids: \[\.\.\.selectedIds\]/);
  assert.match(submitSource, /reason,\s*idempotency_key: batchKeyRef\.current/);
  assert.match(submitSource, /postStrict\('\/attachments\/purge-batch', request\)/);
  assert.match(catchSource, /\['NETWORK_ERROR', 'REQUEST_TIMEOUT', 'INVALID_JSON_RESPONSE'\]/);
  const uncertainCodes = catchSource.match(/\[('(?:NETWORK_ERROR|REQUEST_TIMEOUT|INVALID_JSON_RESPONSE)'(?:, )?)+\]/)[0]
    .match(/[A-Z_]+/g);
  assert.deepEqual(uncertainCodes, ['NETWORK_ERROR', 'REQUEST_TIMEOUT', 'INVALID_JSON_RESPONSE']);
  assert.equal(uncertainCodes.includes('IDEMPOTENCY_KEY_CONFLICT'), false, 'an explicit 409 remains a definite failure');
  assert.match(uncertainSource, /setBatchResultUnknown\(true\)/);
  assert.match(uncertainSource, /未能确认服务端处理结果。请使用原请求确认结果或重试/);
  assert.doesNotMatch(uncertainSource, /loadBatchPreview/);
  assert.match(definiteSource, /setBatchResultUnknown\(false\)/);
  assert.match(definiteSource, /batchRequestRef\.current = null/);
  assert.match(definiteSource, /批量清理未执行/);
  assert.match(definiteSource, /loadBatchPreview\(selectedIds\)/);
  assert.doesNotMatch(definiteSource, /setSelectedIds|setBatchReason|message\.(?:success|warning)|未能确认服务端处理结果/);
});

test('batch purge retry stays enabled and successful replay closes and refreshes normally', () => {
  const source = readFileSync(new URL('./ArchivePage.jsx', import.meta.url), 'utf8');
  const modalStart = source.indexOf('<Modal open={batchPurgeOpen}');
  const modalEnd = source.indexOf('</Modal>', modalStart);
  const modalSource = source.slice(modalStart, modalEnd);
  const submitStart = source.indexOf('const submitBatchPurge = async () =>');
  const submitEnd = source.indexOf('\n  useEffect(() => {', submitStart);
  const submitSource = source.slice(submitStart, submitEnd);

  assert.match(modalSource, /batchResultUnknown \? '确认结果 \/ 重试'/);
  assert.match(modalSource, /disabled: batchResultUnknown\s*\? !batchRequestRef\.current/);
  assert.match(modalSource, /batchPreview\?\.can_purge && <div style=\{\{ marginBottom: 24 \}\}>/);
  assert.match(modalSource, /disabled=\{batchResultUnknown\}/);
  assert.match(modalSource, /cancelButtonProps=\{\{ disabled: batchResultUnknown \}\}/);
  assert.match(modalSource, /closable=\{!batchLoading && !batchResultUnknown\}/);
  assert.match(modalSource, /maskClosable=\{!batchLoading && !batchResultUnknown\}/);
  assert.match(modalSource, /keyboard=\{!batchLoading && !batchResultUnknown\}/);
  assert.match(submitSource, /setBatchResultUnknown\(false\);\s*batchRequestRef\.current = null;\s*setBatchPurgeOpen\(false\);\s*setSelectedIds\(\[\]\);\s*await load\(\)/);
  assert.doesNotMatch(submitSource, /idempotent_replay.*(?:error|失败)/);
});

test('batch purge modal separates blocked and purgeable presentation', () => {
  const source = readFileSync(new URL('./ArchivePage.jsx', import.meta.url), 'utf8');
  const modalStart = source.indexOf('<Modal open={batchPurgeOpen}');
  const modalEnd = source.indexOf('</Modal>', modalStart);
  const modalSource = source.slice(modalStart, modalEnd);

  assert.match(source, /const batchPreviewBlocked = Boolean\(batchPreview && !batchPreview\.can_purge\)/);
  assert.match(source, /const batchHasSubmitAction = batchResultUnknown \|\| Boolean\(batchPreview\?\.can_purge\)/);
  assert.match(modalSource, /\{batchHasSubmitAction && <OkBtn \/>\}/);
  assert.match(modalSource, /\{batchPreviewBlocked && <Alert/);
  assert.match(modalSource, /\(batchPreview\.items \|\| \[\]\)[\s\S]*?\.filter\(item => !item\.can_purge\)/);
  assert.match(source, /const currentAttachmentById = new Map\(items\.map\(item => \[item\.id, item\]\)\)/);
  assert.match(modalSource, /attachment\s*\? displayTitle\(attachment\)\s*: `未找到对应照片（记录 #\$\{item\.attachment_id\}）`/);
  assert.match(modalSource, /attachment\.site_name \|\| '未关联站点'/);
  assert.match(modalSource, /attachment\.taken_at \|\| '-'/);
  assert.match(modalSource, /阻断原因：\{item\.block_reason \|\| '服务端未提供具体原因'\}/);
  assert.match(modalSource, /\{batchPreview\?\.can_purge && <Typography\.Paragraph>/);
  assert.match(modalSource, /提交前服务端会再次核验全部记录，任一项状态变化时整批不执行/);
  assert.match(modalSource, /batchError && !batchPreviewBlocked/);
});
