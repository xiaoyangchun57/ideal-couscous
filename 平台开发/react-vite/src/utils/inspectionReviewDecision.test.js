import assert from 'node:assert/strict';
import test from 'node:test';
import { canSubmitPhotoReview, failedApprovalPhotoIds, getUnqualifiedPhotoIds, reviewPhotoGridStyle } from './inspectionReviewDecision.js';

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

test('nonqualified evidence blocks approval unless that photo is rejected', () => {
  const photos = [
    { id: 1, evidence_qualification: 'qualified' },
    { id: 2, evidence_qualification: 'review' },
    { id: 3, evidence_qualification: 'ineligible' },
  ];
  assert.deepEqual(getUnqualifiedPhotoIds(photos), [2, 3]);
  assert.deepEqual(getUnqualifiedPhotoIds(photos, [2]), [3]);
});
