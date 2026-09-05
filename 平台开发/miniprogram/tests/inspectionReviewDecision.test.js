const assert = require('assert');
const {
  approveItemIdsForPhotoSelection,
  getRiskyPhotoIds,
  getUnqualifiedPhotoIds,
  groupReviewPhotosByItem
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
  getUnqualifiedPhotoIds([
    { id: 1, evidence_qualification: 'qualified' },
    { id: 2, evidence_qualification: 'review' },
    { id: 3, evidence_qualification: 'ineligible' }
  ], [2]),
  [3],
  'Only rejected nonqualified photos may be excluded from the approval gate.'
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

const groupedPhotos = groupReviewPhotosByItem([
  { id: 1, item_id: 101, itemLabel: '第一个检查项' },
  { id: 2, item_id: '101', itemLabel: '重复名称不影响分组' },
  { id: 3, item_id: 102, itemLabel: '同名检查项' },
  { id: 4, item_id: '103', itemLabel: '同名检查项' },
  { id: 5, item_id: null, itemLabel: '不得使用' },
  { id: 6, itemLabel: '不得使用' },
]);
assert.deepStrictEqual(groupedPhotos.map(group => ({
  key: group.key, itemId: group.itemId, itemLabel: group.itemLabel,
  photoIds: group.photos.map(photo => photo.id),
})), [
  { key: 'item:101', itemId: 101, itemLabel: '第一个检查项', photoIds: [1, 2] },
  { key: 'item:102', itemId: 102, itemLabel: '同名检查项', photoIds: [3] },
  { key: 'item:103', itemId: '103', itemLabel: '同名检查项', photoIds: [4] },
  { key: 'unknown', itemId: null, itemLabel: '关联检查项暂不可用', photoIds: [5, 6] },
], 'groups use item id, preserve encounter order, and keep unbound photos separate');

console.log('inspectionReviewDecision tests passed');
