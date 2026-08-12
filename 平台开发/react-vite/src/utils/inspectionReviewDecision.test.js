import assert from 'node:assert/strict';
import test from 'node:test';
import { autoPassNormalCount, canSubmitPhotoReview, failedApprovalPhotoIds, reviewPhotoGridStyle } from './inspectionReviewDecision.js';

test('auto pass is unavailable when every pending image has risk', () => {
  assert.equal(autoPassNormalCount([{ source_type: 'photo_review', attachment_details: [{ id: 1, review_status: 'pending', is_flagged: true }] }]), 0);
});

test('a failed image blocks approval but not a selected rejection', () => {
  const photos = [{ id: 1 }, { id: 2 }];
  assert.deepEqual(failedApprovalPhotoIds(photos, [], [1]), [1]);
  assert.deepEqual(failedApprovalPhotoIds(photos, [1], [1]), []);
  assert.equal(reviewPhotoGridStyle(1).gridTemplateColumns, 'minmax(0, 1fr)');
  assert.match(reviewPhotoGridStyle(2).gridTemplateColumns, /260px/);
});

test('selected photo rejections require a non-blank shared reason before submission', () => {
  assert.equal(canSubmitPhotoReview([1], [1], ''), false);
  assert.equal(canSubmitPhotoReview([1], [1], '   '), false);
  assert.equal(canSubmitPhotoReview([1], [1], 'image is obstructed'), true);
  assert.equal(canSubmitPhotoReview([1], [], ''), true);
});
