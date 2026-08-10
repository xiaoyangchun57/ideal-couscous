const assert = require('assert');
const {
  approveItemIdsForPhotoSelection,
  getRiskyPhotoIds
} = require('../utils/inspectionReviewDecision.js');

const itemIds = [100, 101, 101, 102];
const photos = [
  { id: 200, item_id: 100 },
  { id: 201, item_id: 100 },
  { id: 202, item_id: 101 },
  { id: 203, item_id: null },
];

assert.deepStrictEqual(
  approveItemIdsForPhotoSelection(itemIds, photos, [200]),
  [101, 102],
  'A rejected photo must withhold only its linked check item.'
);

assert.deepStrictEqual(
  getRiskyPhotoIds([
    { id: 1, is_flagged: 1, flag_reason: 'GPS偏离' },
    { id: 2, duplicate_of_id: 1 },
    { id: 3, is_flagged: 0 }
  ]),
  [1, 2],
  'Flagged or duplicate photos must require explicit reviewer acknowledgement.'
);
assert.deepStrictEqual(
  approveItemIdsForPhotoSelection(itemIds, photos, []),
  [100, 101, 102],
  'No rejected photos should approve every pending check item.'
);
assert.deepStrictEqual(
  approveItemIdsForPhotoSelection(itemIds, photos, [203]),
  [100, 101, 102],
  'An unbound photo must not block an unrelated check item.'
);

console.log('inspectionReviewDecision tests passed');
