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
const planReviewTarget = resolveNotificationTarget({
  source_type: 'plan_schedule', source_id: 44,
  payload_json: JSON.stringify({ notification_target: 'review', review_type: 'plan_schedule' })
});
assert.deepEqual(planReviewTarget, {
  kind: 'review', reviewType: 'plan_schedule', sourceId: '44', attachmentIds: [],
  page: '/pages/review/view?target_type=plan_schedule&target_id=44'
});
['inspection_due_suggestion', 'inspection_follow_up_suggestion'].forEach(sourceType => {
  assert.deepEqual(resolveNotificationTarget({ source_type: sourceType, source_id: 710 }), {
    kind: 'page', page: '/pages/plan/plan'
  });
});

const reviewCases = [
  ['inspection_review_batch', 'insp_batch_8_9', 'inspection_batch'],
  ['attachment_review_batch', '9', 'inspection_batch'],
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

const voidTarget = resolveNotificationTarget({
  source_type: 'attachment_void', source_id: 10,
  payload_json: JSON.stringify({ plan_id: 990201, item_id: 990301, site_id: 990101 })
});
assert.deepEqual(voidTarget, { kind: 'tab', page: '/pages/inspection/inspection', planId: 990201, itemId: 990301, siteId: 990101 });
assert.equal(resolveNotificationTarget({ source_type: 'attachment_void', source_id: 10,
  payload_json: JSON.stringify({ plan_id: 990201, item_id: 0, site_id: 990101 }) }).kind, 'invalid');

const groups = [{ items: [
  { source_type: 'inspection_batch', id: 'insp_batch_8_9', site_id: 9, item_ids: [88] },
  { source_type: 'inspection_batch', id: 'insp_batch_10_11', site_id: 11 },
  { source_type: 'inspection_batch', id: 'insp_batch_9', site_id: 9, attachment_ids: [101] },
  { source_type: 'parts_request', id: 'pr_12' },
  { source_type: 'vehicle_application', id: 'va_13' },
  { source_type: 'data_review', id: 'dr_14' },
  { source_type: 'workorder_review', order_no: 'WO-2026-001' }
  , { source_type: 'plan_schedule', id: 'ps_44', schedule_id: 44 }
] }];
assert.equal(findReviewItem(groups, { reviewType: 'inspection_batch', sourceId: 'insp_batch_8_9' }).site_id, 9);
assert.equal(findReviewItem(groups, { reviewType: 'inspection_batch', sourceId: '88' }).id, 'insp_batch_8_9');
assert.equal(findReviewItem(groups, imageTarget).id, 'insp_batch_9');
assert.equal(findReviewItem(groups, { reviewType: 'parts_request', sourceId: '12' }).id, 'pr_12');
assert.equal(findReviewItem(groups, { reviewType: 'vehicle_application', sourceId: '13' }).id, 'va_13');
assert.equal(findReviewItem(groups, { reviewType: 'data_review', sourceId: '14' }).id, 'dr_14');
assert.equal(findReviewItem(groups, { reviewType: 'workorder_review', sourceId: 'WO-2026-001' }).order_no, 'WO-2026-001');
assert.equal(findReviewItem(groups, planReviewTarget).schedule_id, 44);
assert.equal(findReviewItem(groups, { reviewType: 'parts_request', sourceId: '404' }), null);

console.log('notificationTarget tests passed');
