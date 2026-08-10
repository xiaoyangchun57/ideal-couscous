import assert from 'node:assert/strict';
import test from 'node:test';
import { approveItemIdsForPhotoSelection, photoRejectionNeedsReason } from './inspectionReviewDecision.js';

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
