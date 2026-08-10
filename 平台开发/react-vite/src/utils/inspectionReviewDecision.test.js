import assert from 'node:assert/strict';
import test from 'node:test';
import {
  approveItemIdsForPhotoSelection,
  canApprovePhotoReview,
  getRiskAcknowledgementLabel,
  getRiskyPhotoIds,
  photoRejectionNeedsReason,
} from './inspectionReviewDecision.js';

test('photo rejection holds only linked inspection items', () => {
  const itemIds = [100, 101, 102];
  const photos = [
    { id: 200, item_id: 100 },
    { id: 201, item_id: 100 },
    { id: 202, item_id: 101 },
  ];
  assert.deepEqual(approveItemIdsForPhotoSelection(itemIds, photos, [200]), [101, 102]);
  assert.deepEqual(approveItemIdsForPhotoSelection(itemIds, photos, []), [100, 101, 102]);
});

test('only a selected attached photo requires a rejection reason', () => {
  assert.equal(photoRejectionNeedsReason([200], [200]), true);
  assert.equal(photoRejectionNeedsReason([200], []), false);
  assert.equal(photoRejectionNeedsReason([], [200]), false);
});

test('risk photos block implicit all-pass until explicitly acknowledged', () => {
  const photos = [
    { id: 10, is_flagged: 1, flag_reason: 'GPS偏离' },
    { id: 11, is_flagged: 0, taken_at: '2026-08-10 10:00:00' },
    { id: 12, duplicate_of_id: 10, taken_at: '2026-08-10 10:01:00' },
  ];

  assert.deepEqual(getRiskyPhotoIds(photos), [10, 12]);
  assert.equal(canApprovePhotoReview(photos, false), false);
  assert.equal(canApprovePhotoReview(photos, true), true);
  assert.equal(canApprovePhotoReview(photos, false, { rejectedPhotoIds: [10, 12] }), true);
  assert.equal(getRiskAcknowledgementLabel(2), '已核对 2 张风险影像');
  assert.equal(canApprovePhotoReview([photos[1]], false), true);
});
