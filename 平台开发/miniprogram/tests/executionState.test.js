const assert = require('assert');

const {
  photoRequirement,
  inspectionPhotoProgress,
  inspectionFieldItemCompleted,
  inspectionFieldProgress,
  projectInspectionFieldProgress,
  inspectionItemPhotoState,
  inspectionItemPhotoRequirement,
  applyRejectedInspectionPhotoPurge,
  rejectedInspectionEvidence,
  applyInspectionSubmission,
  removePendingInspectionPhoto,
  addPendingInspectionPhotos,
} = require('../utils/executionState.js');

const fieldCategories = [{ items: [
  { item_id: 1, result: 'normal', review_status: 3, evidence_status: 'supplement_required' },
  { item_id: 2, result: 'normal', review_status: 1, evidence_status: 'replacement_submitted' },
] }];
assert.equal(inspectionFieldItemCompleted(fieldCategories[0].items[0]), false);
assert.equal(inspectionFieldItemCompleted(fieldCategories[0].items[1]), true);
assert.deepEqual(inspectionFieldProgress(fieldCategories), { total: 2, completed: 1, percent: 50 });
assert.deepEqual(
  projectInspectionFieldProgress(fieldCategories).categories.map(category => ({
    total: category.total, completed: category.completed,
  })),
  [{ total: 2, completed: 1 }],
);
const fieldAfterRejectedPurge = [{
  items: fieldCategories[0].items.map(item => item.item_id === 1
    ? applyRejectedInspectionPhotoPurge(item, 99, { photo_urls: [], actual_photos: 0 })
    : item),
}];
assert.deepEqual(
  projectInspectionFieldProgress(fieldAfterRejectedPurge).categories
    .map(category => ({ total: category.total, completed: category.completed })),
  [{ total: 2, completed: 1 }],
  'purging rejected evidence must immediately keep the category at one of two complete',
);
const fieldAfterSubmission = applyInspectionSubmission(
  fieldCategories, 1, 'normal', ['/uploads/replacement.jpg'], false, 1, 'replacement_submitted');
assert.deepEqual(inspectionFieldProgress(fieldAfterSubmission), { total: 2, completed: 2, percent: 100 });
assert.deepEqual(projectInspectionFieldProgress(fieldAfterSubmission).categories[0].completed, 2,
  'the category count must update immediately after the final retake submission');
assert.deepEqual(inspectionFieldProgress(fieldAfterSubmission), { total: 2, completed: 2, percent: 100 },
  'reloading the same authoritative categories must not revert progress');

assert.deepEqual(
  photoRequirement(2, 1, 0),
  { required: 2, captured: 1, missing: 1, ready: false },
);
assert.deepEqual(
  photoRequirement(2, 1, 1),
  { required: 2, captured: 2, missing: 0, ready: true },
);

assert.deepEqual(
  inspectionItemPhotoState({
    photo_urls: '["/uploads/formal.jpg","/uploads/shared.jpg"]',
    pending_photo_urls: ['/uploads/shared.jpg', '/uploads/pending.jpg'],
  }),
  {
    formalPhotos: ['/uploads/formal.jpg', '/uploads/shared.jpg'],
    pendingPhotos: ['/uploads/pending.jpg'],
    photos: ['/uploads/formal.jpg', '/uploads/shared.jpg', '/uploads/pending.jpg'],
  },
  'reopening an item must restore pending photos without duplicates',
);

const replacementItem = {
  required_photos: 4,
  evidence_status: 'supplement_required',
  replacement_required_photos: 1,
  current_photo_urls: ['/uploads/keep-a.jpg', '/uploads/keep-b.jpg', '/uploads/keep-c.jpg'],
  photo_urls: '["/uploads/keep-a.jpg","/uploads/rejected.jpg","/uploads/keep-b.jpg","/uploads/keep-c.jpg"]',
  pending_photo_urls: ['/uploads/replacement.jpg'],
};
assert.deepEqual(inspectionItemPhotoState(replacementItem), {
  formalPhotos: ['/uploads/keep-a.jpg', '/uploads/keep-b.jpg', '/uploads/keep-c.jpg'],
  pendingPhotos: ['/uploads/replacement.jpg'],
  photos: ['/uploads/keep-a.jpg', '/uploads/keep-b.jpg', '/uploads/keep-c.jpg', '/uploads/replacement.jpg'],
});
assert.deepEqual(inspectionItemPhotoState({
  evidence_status: '',
  current_photo_urls: ['/uploads/stale-server-projection.jpg'],
  photo_urls: '["/uploads/current-page-state.jpg"]',
}).photos, ['/uploads/current-page-state.jpg'],
'the replacement projection must not overwrite normal-item page state');
assert.deepEqual(inspectionItemPhotoRequirement(replacementItem, 0),
  { required: 1, captured: 1, missing: 0, ready: true });
assert.deepEqual(inspectionPhotoProgress([{ items: [replacementItem] }]),
  { req: 1, taken: 0, missing: 1 },
  'site progress must describe this replacement cycle, not the original total');
const oneRetainedReplacementItem = {
  required_photos: 4,
  evidence_status: 'supplement_required',
  review_comment: '1',
  supplement_reason: '1',
  replacement_required_photos: 3,
  current_photo_urls: ['/uploads/keep-a.jpg'],
};
assert.deepEqual(inspectionItemPhotoState(oneRetainedReplacementItem).formalPhotos,
  ['/uploads/keep-a.jpg']);
assert.deepEqual(inspectionItemPhotoRequirement(oneRetainedReplacementItem, 0),
  { required: 3, captured: 0, missing: 3, ready: false },
  'the item panel and submit gate use the server replacement requirement, never review text');
assert.deepEqual(inspectionPhotoProgress([{ items: [oneRetainedReplacementItem] }]),
  { req: 3, taken: 0, missing: 3 },
  'the station summary uses the same server replacement requirement as the card and item panel');
const pendingReviewItem = {
  required_photos: 4,
  evidence_status: 'supplement_required',
  replacement_photo_status: 'pending_review',
  replacement_required_photos: null,
  replacement_block_reason: '原照片待审核，审核完成后才能补拍',
  current_photo_urls: [],
};
assert.deepEqual(inspectionItemPhotoRequirement(pendingReviewItem, 0), {
  required: 0, captured: 0, missing: 0, ready: false, blocked: true,
  blockReason: '原照片待审核，审核完成后才能补拍',
});
assert.deepEqual(inspectionPhotoProgress([{ items: [pendingReviewItem] }]),
  { req: 0, taken: 0, missing: 0 },
  'the station summary must not invent a replacement count while review is pending');
assert.deepEqual(inspectionItemPhotoRequirement({
  required_photos: 4,
  evidence_status: 'supplement_required',
  replacement_required_photos: 4,
  current_photo_urls: [],
  pending_photo_urls: [],
}, 0), { required: 4, captured: 0, missing: 4, ready: false });

const rejectedHistoryItem = {
  item_id: 15,
  required_photos: 2,
  photo_urls: ['/uploads/approved.jpg', '/uploads/rejected-a.jpg', '/uploads/rejected-b.jpg'],
  pending_photo_urls: ['/uploads/pending.jpg', '/uploads/rejected-a.jpg'],
  actual_photos: 1,
  evidence_attachments: [
    { id: 151, stored_path: '/uploads/rejected-a.jpg', review_status: 'rejected', uploader_id: 2 },
    { id: 152, stored_path: '/uploads/rejected-b.jpg', review_status: 'rejected', uploader_id: 2 },
    { id: 153, stored_path: '/uploads/approved.jpg', review_status: 'approved', uploader_id: 2 },
  ],
};
assert.deepEqual(
  inspectionItemPhotoState(rejectedHistoryItem).photos,
  ['/uploads/approved.jpg', '/uploads/pending.jpg'],
  'rejected history must not also appear in the current submittable photo list',
);
const afterRejectedPurge = applyRejectedInspectionPhotoPurge(
  rejectedHistoryItem, 151,
  { photo_urls: ['/uploads/approved.jpg', '/uploads/rejected-b.jpg'], actual_photos: 1 },
);
assert.deepEqual(
  inspectionItemPhotoState(afterRejectedPurge),
  {
    formalPhotos: ['/uploads/approved.jpg'],
    pendingPhotos: ['/uploads/pending.jpg'],
    photos: ['/uploads/approved.jpg', '/uploads/pending.jpg'],
  },
  'purging one rejected photo must retain valid and pending inputs without restoring other history',
);
assert.deepEqual(afterRejectedPurge.evidence_attachments.map(item => item.id), [152, 153]);
assert.equal(afterRejectedPurge.actual_photos, 1,
  'the current item must use the server authoritative photo count');

const localBaseUrl = 'http://192.168.2.103:5000';
const resolveLocalDisplayUrl = path => /^https?:\/\//.test(path) ? path : localBaseUrl + path;
const rejectedDisplay = rejectedInspectionEvidence([
  { id: 201, stored_path: '/uploads/rejected-relative.jpg', review_status: 'rejected', uploader_id: 2 },
  { id: 202, stored_path: 'https://cdn.example/rejected.jpg', review_status: 'rejected', uploader_id: 2 },
  { id: 203, stored_path: '/uploads/approved.jpg', review_status: 'approved', uploader_id: 2 },
  { id: 204, stored_path: '/uploads/pending.jpg', review_status: 'pending', uploader_id: 2 },
  { id: 205, stored_path: '/uploads/other-user.jpg', review_status: 'rejected', uploader_id: 3 },
], 2, resolveLocalDisplayUrl);
assert.deepEqual(rejectedDisplay, [
  {
    id: 201, stored_path: '/uploads/rejected-relative.jpg', review_status: 'rejected', uploader_id: 2,
    display_url: localBaseUrl + '/uploads/rejected-relative.jpg',
  },
  {
    id: 202, stored_path: 'https://cdn.example/rejected.jpg', review_status: 'rejected', uploader_id: 2,
    display_url: 'https://cdn.example/rejected.jpg',
  },
], 'rejected evidence display URLs must preserve identity and storage ownership fields');
assert.deepEqual(
  rejectedInspectionEvidence(afterRejectedPurge.evidence_attachments, 2, resolveLocalDisplayUrl)
    .map(item => ({ id: item.id, stored_path: item.stored_path, display_url: item.display_url })),
  [{
    id: 152,
    stored_path: '/uploads/rejected-b.jpg',
    display_url: localBaseUrl + '/uploads/rejected-b.jpg',
  }],
  'the remaining rejected evidence must retain the same display URL projection after purge',
);

const submittedCategories = applyInspectionSubmission([{ items: [{
  item_id: 9, required_photos: 1, result: null, photo_urls: '[]',
  actual_photos: 0, effective_evidence_count: 0, pending_photo_urls: ['/uploads/final.jpg'],
}] }], 9, 'normal', ['/uploads/final.jpg'], false, 1, '');
assert.deepEqual(inspectionPhotoProgress(submittedCategories), { req: 1, taken: 1, missing: 0 });
assert.equal(submittedCategories[0].items[0].actual_photos, 1);
assert.equal(submittedCategories[0].items[0].effective_evidence_count, 1);
assert.deepEqual(submittedCategories[0].items[0].pending_photo_urls, []);

const offlineCategories = applyInspectionSubmission([{ items: [{
  item_id: 10, required_photos: 1, result: null, photo_urls: '[]',
  actual_photos: 0, effective_evidence_count: 0,
}] }], 10, 'normal', ['wxfile://usr/pending.jpg'], true, undefined, undefined);
assert.deepEqual(inspectionPhotoProgress(offlineCategories), { req: 1, taken: 0, missing: 1 },
  'local pending photos must not count as formal evidence');

const afterPendingDelete = removePendingInspectionPhoto([{ items: [{
  item_id: 9, photo_urls: '[]', pending_photo_urls: ['/uploads/a.jpg', '/uploads/b.jpg'],
}] }], 9, '/uploads/a.jpg');
assert.deepEqual(inspectionItemPhotoState(afterPendingDelete[0].items[0]).photos, ['/uploads/b.jpg'],
  'a confirmed delete must stay removed when the item is reopened from current page state');

const initialPageCategories = [{ items: [{
  item_id: 12, photo_urls: '[]', pending_photo_urls: [],
}] }];
const afterUpload = addPendingInspectionPhotos(
  initialPageCategories, 12,
  ['http://192.168.2.103:5000/uploads/site_photos/new.jpg', '/uploads/site_photos/new.jpg'],
);
assert.deepEqual(
  inspectionItemPhotoState(afterUpload[0].items[0]).photos,
  ['/uploads/site_photos/new.jpg'],
  'closing and reopening from categories must retain a newly uploaded pending photo once',
);
const afterDeleteAndReopen = removePendingInspectionPhoto(
  afterUpload, 12, '/uploads/site_photos/new.jpg');
assert.deepEqual(inspectionItemPhotoState(afterDeleteAndReopen[0].items[0]).photos, [],
  'a confirmed delete must not reappear on the next same-page reopen');

assert.deepEqual(
  inspectionPhotoProgress([{ items: [
    { required_photos: 1, review_status: 2, evidence_status: 'effective', effective_evidence_count: 0, photo_urls: '["historical-approved.jpg"]' },
    { required_photos: 1, review_status: 3, evidence_status: 'supplement_required', effective_evidence_count: 2, photo_urls: '["rejected-a.jpg","rejected-b.jpg"]' },
  ] }]),
  { req: 2, taken: 1, missing: 1 },
  'rejected history must not satisfy the current photo requirement',
);

assert.deepEqual(
  inspectionPhotoProgress([{ items: [{ required_photos: 1, photo_urls: '["legacy.jpg"]' }] }]),
  { req: 1, taken: 1, missing: 0 },
  'legacy payloads without evidence counts remain readable',
);

console.log('executionState tests passed');
