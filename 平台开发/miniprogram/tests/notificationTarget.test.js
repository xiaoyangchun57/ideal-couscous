const assert = require('assert');
const {
  planScheduleDetailUrl,
  resolveNotificationTarget,
  findReviewItem
} = require('../utils/notificationTarget.js');

assert.equal(planScheduleDetailUrl(42), '/pages/plan-detail/plan-detail?id=42');
assert.equal(planScheduleDetailUrl('42'), '/pages/plan-detail/plan-detail?id=42');
assert.equal(planScheduleDetailUrl(''), '/pages/plan/plan');

assert.deepEqual(resolveNotificationTarget({ source_type: 'plan_schedule', source_id: 42 }), {
  kind: 'page',
  page: '/pages/plan-detail/plan-detail?id=42'
});
assert.deepEqual(resolveNotificationTarget({ source_type: 'plan_schedule', source_id: 43 }), {
  kind: 'page',
  page: '/pages/plan-detail/plan-detail?id=43'
});

const reviewCases = [
  ['inspection_review_batch', 'insp_batch_8_9', 'inspection_batch'],
  ['attachment_review_batch', '9', 'photo_review'],
  ['workorder_review', 'WO-2026-001', 'workorder_review'],
  ['parts_request', 12, 'parts_request'],
  ['vehicle_application', 13, 'vehicle_application']
];
reviewCases.forEach(([sourceType, sourceId, reviewType]) => {
  const target = resolveNotificationTarget({ source_type: sourceType, source_id: sourceId });
  assert.equal(target.kind, 'review');
  assert.equal(target.reviewType, reviewType);
  assert.equal(target.sourceId, String(sourceId));
  assert.match(target.page, /^\/pages\/review\/view\?/);
});
const imageTarget = resolveNotificationTarget({
  source_type: 'attachment_review_batch',
  source_id: 9,
  payload_json: JSON.stringify({ pending_attachment_ids: [101, 102] })
});
assert.deepEqual(imageTarget.attachmentIds, ['101', '102']);
assert.match(imageTarget.page, /target_attachment_ids=101%2C102/);

assert.equal(resolveNotificationTarget({ source_type: 'parts_request' }).kind, 'invalid');
assert.equal(resolveNotificationTarget({ source_type: 'unknown', source_id: 1 }).kind, 'invalid');

const groups = [{ items: [
  { source_type: 'inspection_batch', id: 'insp_batch_8_9', site_id: 9, item_ids: [88] },
  { source_type: 'inspection_batch', id: 'insp_batch_10_11', site_id: 11 },
  { source_type: 'photo_review', id: 'photo_batch_9', site_id: 9, attachment_ids: [101] },
  { source_type: 'parts_request', id: 'pr_12' },
  { source_type: 'vehicle_application', id: 'va_13' },
  { source_type: 'data_review', id: 'dr_14' },
  { source_type: 'workorder_review', order_no: 'WO-2026-001' }
] }];
assert.equal(findReviewItem(groups, { reviewType: 'inspection_batch', sourceId: 'insp_batch_8_9' }).site_id, 9);
assert.equal(findReviewItem(groups, { reviewType: 'inspection_batch', sourceId: '88' }).id, 'insp_batch_8_9');
assert.equal(findReviewItem(groups, imageTarget).id, 'photo_batch_9');
assert.equal(findReviewItem(groups, { reviewType: 'parts_request', sourceId: '12' }).id, 'pr_12');
assert.equal(findReviewItem(groups, { reviewType: 'vehicle_application', sourceId: '13' }).id, 'va_13');
assert.equal(findReviewItem(groups, { reviewType: 'data_review', sourceId: '14' }).id, 'dr_14');
assert.equal(findReviewItem(groups, { reviewType: 'workorder_review', sourceId: 'WO-2026-001' }).order_no, 'WO-2026-001');
assert.equal(findReviewItem(groups, { reviewType: 'parts_request', sourceId: '404' }), null);

console.log('notificationTarget tests passed');
