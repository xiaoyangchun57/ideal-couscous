const assert = require('assert');
const fs = require('node:fs');
const path = require('node:path');
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
assert.deepEqual(resolveNotificationTarget({ source_type: 'vehicle_extension_conflict', source_id: 43 }), {
  kind: 'page',
  page: '/pages/plan-detail/plan-detail?id=43'
});
assert.equal(resolveNotificationTarget({
  source_type: 'vehicle_extension_conflict', source_id: 'bad'
}).kind, 'invalid');
const planReviewTarget = resolveNotificationTarget({
  source_type: 'plan_schedule', source_id: 44,
  payload_json: JSON.stringify({ notification_target: 'review', review_type: 'plan_schedule' })
});
assert.deepEqual(planReviewTarget, {
  kind: 'review', reviewType: 'plan_schedule', sourceId: '44', attachmentIds: [],
  page: '/pages/review/view?target_type=plan_schedule&target_id=44'
});
['inspection_due_suggestion'].forEach(sourceType => {
  assert.deepEqual(resolveNotificationTarget({ source_type: sourceType, source_id: 710 }), {
    kind: 'tab', page: '/pages/plan/plan'
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

assert.deepEqual(resolveNotificationTarget({ source_type: 'alert', source_id: 77 }), {
  kind: 'tab', page: '/pages/alert/alert', alertId: '77'
});
assert.equal(resolveNotificationTarget({ source_type: 'alert', source_id: '' }).kind, 'invalid');
assert.deepEqual(resolveNotificationTarget({ source_type: 'vehicle_use_expiry', source_id: 91 }), {
  kind: 'page', page: '/pages/vehicle/vehicle',
  vehicleTarget: { applicationId: 91, expectedAction: 'extend', source: 'vehicle_use_expiry' }
});
assert.deepEqual(resolveNotificationTarget({ source_type: 'vehicle_application_result', source_id: 92 }), {
  kind: 'page',
  page: '/pages/vehicle/vehicle?application_id=92&source=approval_result',
  vehicleTarget: { applicationId: 92, expectedAction: 'view_result', source: 'approval_result' }
});
assert.deepEqual(resolveNotificationTarget({ id: 301, source_type: 'parts_request_result', source_id: 12 }), {
  kind: 'page', page: '/pages/message/message?notification_id=301'
});
['', 0, -1, 'abc', '1.5'].forEach(sourceId => {
  const target = resolveNotificationTarget({ source_type: 'vehicle_use_expiry', source_id: sourceId });
  assert.equal(target.kind, 'invalid');
  assert.match(target.message, /申请编号/);
});
assert.equal(resolveNotificationTarget({ source_type: 'vehicle_application_result', source_id: 'bad' }).kind, 'invalid');
assert.equal(resolveNotificationTarget({ source_type: 'parts_request_result', source_id: 12 }).kind, 'invalid');
const alertPage = fs.readFileSync(path.join(__dirname, '../pages/alert/alert.js'), 'utf8');
const messagePage = fs.readFileSync(path.join(__dirname, '../pages/message/message.js'), 'utf8');
const planDetailPage = fs.readFileSync(path.join(__dirname, '../pages/plan-detail/plan-detail.js'), 'utf8');
const reportsPage = fs.readFileSync(path.join(__dirname, '../pages/reports/reports.js'), 'utf8');
assert.match(alertPage, /selAlertId != null \? ''/);
assert.match(alertPage, /selAlertId = null[\s\S]*openAlertDetail\(\{ id: focusedId \}\)/);
assert.match(messagePage, /target\.executionTarget[\s\S]*globalData\.executionTarget[\s\S]*target\.kind === 'tab'/);
assert.match(messagePage, /target\.vehicleTarget[\s\S]*writeTarget\('vehicleTarget', target\.vehicleTarget\)/);
assert.match(planDetailPage, /buildScheduleExecutionTarget\([\s\S]*_navigateToExecution\(target\)/);
assert.match(planDetailPage, /_navigateToExecution\(target\)[\s\S]*globalData\.executionTarget = target[\s\S]*navigateTo\(\{/);
assert.match(planDetailPage, /fail:[\s\S]*globalData\.executionTarget === target[\s\S]*globalData\.executionTarget = null/);
assert.match(reportsPage, /onGoReport\(\)[\s\S]*loadAuthorizedReportSites/);
assert.doesNotMatch(reportsPage, /onGoReport\(\)[\s\S]{0,500}(?:inspection|executionTarget|selSiteId)/);
assert.doesNotMatch([messagePage, planDetailPage, reportsPage].join('\n'), /switchTab\(\{ url: '\/pages\/inspection\/inspection'/);

const voidTarget = resolveNotificationTarget({
  source_type: 'attachment_void', source_id: 10,
  payload_json: JSON.stringify({ plan_id: 990201, item_id: 990301, site_id: 990101 })
});
assert.deepEqual(voidTarget, {
  kind: 'page', page: '/pages/inspection/inspection',
  executionTarget: { executionPlanId: 990201, itemId: 990301, siteId: 990101, source: 'attachment_void' }
});
assert.deepEqual(resolveNotificationTarget({ source_type: 'inspection_rework', source_id: 990201 }), {
  kind: 'page', page: '/pages/inspection/inspection',
  executionTarget: { executionPlanId: 990201, source: 'inspection_rework' }
});
assert.deepEqual(resolveNotificationTarget({ source_type: 'reagent_qc', source_id: 990101 }), {
  kind: 'page', page: '/pages/inspection/inspection',
  executionTarget: { siteId: 990101, source: 'reagent_qc' }
});
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
