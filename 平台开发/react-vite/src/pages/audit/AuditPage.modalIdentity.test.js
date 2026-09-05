import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import process from 'node:process';
import test from 'node:test';
import React from 'react';

const currentSource = readFileSync(new URL('./AuditPage.jsx', import.meta.url), 'utf8');
let source = process.env.AUDIT_MODAL_LEGACY_RENDER === '1'
  ? currentSource.replace('{renderReviewModal()}', '<ReviewModal />')
  : currentSource;
if (process.env.AUDIT_MODAL_LEGACY_LAYOUT === '1') {
  source = source.replace(
    /\s*<div data-review-reason-slot[\s\S]*?<\/div>/,
    '\n        {rejectionReasonMissing && <Alert message="已选择驳回照片，请填写统一驳回原因后提交" />}',
  );
}
const StableModalHost = Symbol('stable-modal-host');

function renderParent() {
  if (source.includes('<ReviewModal />')) {
    function ReviewModal() {
      return React.createElement('img');
    }
    return React.createElement(ReviewModal);
  }
  if (source.includes('{renderReviewModal()}')) {
    return React.createElement(StableModalHost);
  }
  throw new Error('无法识别审核弹窗渲染方式');
}

function reconcile(previous, next, childState) {
  if (!previous || previous.type !== next.type) {
    return {
      mounts: childState.mounts + 1,
      selectedPhotos: [],
      comment: '',
      scrollTop: 0,
    };
  }
  return childState;
}

test('review modal child survives photo selection and comment parent updates', () => {
  const first = renderParent();
  let state = reconcile(null, first, {
    mounts: 0, selectedPhotos: [], comment: '', scrollTop: 0,
  });
  state = {
    ...state,
    selectedPhotos: [111],
    comment: '需要补拍清晰影像',
    scrollTop: 240,
  };

  const afterSelection = renderParent();
  state = reconcile(first, afterSelection, state);
  const afterComment = renderParent();
  state = reconcile(afterSelection, afterComment, state);

  assert.equal(state.mounts, 1, 'parent state updates must not remount images');
  assert.deepEqual(state.selectedPhotos, [111]);
  assert.equal(state.comment, '需要补拍清晰影像');
  assert.equal(state.scrollTop, 240);
});

function photoSectionOffset(reasonMissing) {
  const alertIndex = source.indexOf('{rejectionReasonMissing && <Alert');
  const dynamicAlertBeforePhotos = alertIndex >= 0 && alertIndex < source.indexOf('<Descriptions');
  return dynamicAlertBeforePhotos && reasonMissing ? 52 : 0;
}

test('review reason feedback keeps a stable slot below the photo section', () => {
  const slotIndex = source.indexOf('<div data-review-reason-slot');
  const photoIndex = source.indexOf('<Image.PreviewGroup');
  const commentIndex = source.indexOf('<Text strong>审核意见</Text>');
  assert.ok(slotIndex > photoIndex, 'reason feedback must not be inserted before photos');
  assert.ok(slotIndex > commentIndex, 'reason feedback belongs with the review comment');
  assert.match(source.slice(slotIndex, slotIndex + 180), /height: 22/);
  assert.equal(photoSectionOffset(false), photoSectionOffset(true),
    'selecting a rejected photo must not shift the photo section');
});

test('inspection photo cards prioritize item and trusted capture facts', () => {
  const start = currentSource.indexOf("{item.source_type === 'inspection_batch' && item.attachment_details");
  const end = currentSource.indexOf("['inspection', 'inspection_batch'].includes", start);
  const cardSource = currentSource.slice(start, end);

  assert.match(cardSource, /<Text strong[^>]*>\{itemLabel\}<\/Text>/);
  assert.match(cardSource, /拍摄时间：\{photo\.taken_at \|\| '未记录'\}/);
  assert.match(cardSource, /拍摄者：\{photo\.uploader_name \|\| '未记录'\}/);
  assert.doesNotMatch(cardSource, /检查项：|档案名称：|原始文件名：/);
  assert.match(cardSource, /onError=\{\(\) => setFailedReviewPhotoIds/);
  assert.match(cardSource, /<Checkbox checked=\{selected\}/);
});
