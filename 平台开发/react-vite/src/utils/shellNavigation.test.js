import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildGlobalSearchPath,
  getAuditTargetFromSearchParams,
  getNotificationTarget,
  resolveAuditTarget,
} from './shellNavigation.js';

test('global search paths use the destination page query contract', () => {
  assert.equal(buildGlobalSearchPath({ type: 'site', identifier: 35 }), '/sites?archive=35');
  assert.equal(buildGlobalSearchPath({ type: 'workorder', identifier: 'WO-1' }), '/workorders?search=WO-1');
  assert.equal(buildGlobalSearchPath({ type: 'device', identifier: 'WB-003-01' }), '/equipment?q=WB-003-01');
});

test('notification targets respect role-visible pages', () => {
  assert.equal(getNotificationTarget({ source_type: 'photo_review', source_id: 8 }, ['operator']), null);
  assert.equal(getNotificationTarget({ source_type: 'photo_review', source_id: 8 }, ['reviewer']), '/audit?tab=photo&photo=8');
  assert.equal(getNotificationTarget({ source_type: 'inspection_review_batch', source_id: 'insp_batch_10_1' }, ['reviewer']), '/audit?tab=inspection&inspection_batch=insp_batch_10_1');
  assert.equal(getNotificationTarget({ source_type: 'inspection_review', source_id: 12 }, ['operator']), '/plan-schedules');
  assert.equal(getNotificationTarget({ source_type: 'inspection_rework', source_id: 18 }, ['operator']), '/plan-schedules?rework_plan=18');
  assert.equal(getNotificationTarget({ source_type: 'workorder_review', source_id: 'WO-1' }, ['admin']), '/audit?tab=workorder&order=WO-1');
  assert.equal(getNotificationTarget({ source_type: 'spare_part_request', source_id: 9 }, ['admin']), '/audit?tab=parts&request=9&request_type=spare_part_request');
  assert.equal(getNotificationTarget({ source_type: 'data_review', source_id: 7 }, ['reviewer']), '/audit?tab=data&review=7');
  assert.equal(getNotificationTarget({ source_type: 'data_review', source_id: 7 }, ['operator']), null);
  assert.equal(getNotificationTarget({ source_type: 'reagent_qc', source_id: 2 }, ['reviewer']), null);
});

test('audit notifications locate every supported review object', () => {
  assert.equal(getNotificationTarget({ source_type: 'plan_schedule', source_id: 42 }, ['admin']), '/audit?tab=plan&plan=42');
  assert.equal(getNotificationTarget({ source_type: 'plan_schedule_change', source_id: 42 }, ['admin']), '/audit?tab=plan&plan=42&change=1');
  assert.equal(getNotificationTarget({ source_type: 'attachment_review_batch', source_id: 7 }, ['reviewer']), '/audit?tab=photo&site=7');
  assert.equal(getNotificationTarget({ source_type: 'inspection_review', source_id: 31 }, ['reviewer']), '/audit?tab=inspection&inspection=31');
  assert.equal(getNotificationTarget({ source_type: 'vehicle_application', source_id: 14 }, ['admin']), '/audit?tab=vehicle&request=14');
  assert.equal(getNotificationTarget({ source_type: 'parts_request', source_id: 15 }, ['admin']), '/audit?tab=parts&request=15&request_type=parts_request');
  assert.equal(getNotificationTarget({
    source_type: 'attachment_review_batch',
    source_id: 7,
    payload_json: JSON.stringify({ pending_attachment_ids: [101, 102] }),
  }, ['reviewer']), '/audit?tab=photo&photos=101%2C102');
});

test('invalid audit targets stay explicit instead of opening a generic row', () => {
  const target = getAuditTargetFromSearchParams(new URLSearchParams('tab=photo&photo=999'));
  const result = resolveAuditTarget([
    { id: 'photo_batch_site_7', source_type: 'photo_review', site_id: 7, attachment_ids: [12] },
  ], target);

  assert.equal(target.tab, 'photo');
  assert.equal(target.kind, 'photo');
  assert.equal(result.status, 'missing');
  assert.equal(result.item, null);
});

test('data review and attachment cursors resolve the exact audit object', () => {
  const dataTarget = getAuditTargetFromSearchParams('tab=data&review=7');
  assert.equal(resolveAuditTarget([{ id: 7 }, { id: 8 }], dataTarget).item.id, 7);

  const photoTarget = getAuditTargetFromSearchParams('tab=photo&photos=101%2C102');
  const photoResult = resolveAuditTarget([
    { id: 'photo_batch_site_7', source_type: 'photo_review', attachment_ids: [88] },
    { id: 'insp_batch_10_7', source_type: 'inspection_batch', attachment_ids: [102] },
  ], photoTarget);
  assert.equal(photoResult.item.id, 'insp_batch_10_7');
});

test('parts notification URL parses its type and resolves the exact prefixed row', () => {
  const rows = [
    { id: 'pr_9', source_type: 'parts_request', title: '巡检预申报' },
    { id: 'spr_9', source_type: 'spare_part_request', title: '历史备件申请' },
  ];
  const notificationUrl = getNotificationTarget(
    { source_type: 'spare_part_request', source_id: 9 },
    ['admin'],
  );
  const target = getAuditTargetFromSearchParams(new URL(notificationUrl, 'http://localhost').searchParams);
  const result = resolveAuditTarget(rows, target);

  assert.equal(target.requestType, 'spare_part_request');
  assert.equal(target.value, '9');
  assert.equal(result.status, 'found');
  assert.equal(result.item.id, 'spr_9');
  assert.equal(result.item.title, '历史备件申请');
});

test('typed parts URLs distinguish the same numeric id in both request tables', () => {
  const rows = [
    { id: 'pr_12', source_type: 'parts_request' },
    { id: 'spr_12', source_type: 'spare_part_request' },
  ];

  for (const requestType of ['parts_request', 'spare_part_request']) {
    const target = getAuditTargetFromSearchParams(
      `tab=parts&request=12&request_type=${requestType}`,
    );
    const result = resolveAuditTarget(rows, target);
    assert.equal(result.status, 'found');
    assert.equal(result.item.id, requestType === 'parts_request' ? 'pr_12' : 'spr_12');
  }
});

test('legacy untyped numeric request targets refuse an ambiguous match', () => {
  const target = getAuditTargetFromSearchParams('tab=parts&request=12');
  const result = resolveAuditTarget([
    { id: 'pr_12', source_type: 'parts_request' },
    { id: 'spr_12', source_type: 'spare_part_request' },
  ], target);

  assert.equal(target.requestType, null);
  assert.equal(result.status, 'ambiguous');
  assert.equal(result.item, null);
});

test('request resolution reports processed, forbidden, and missing targets explicitly', () => {
  const target = getAuditTargetFromSearchParams('tab=parts&request=12&request_type=parts_request');
  assert.equal(resolveAuditTarget([
    { id: 'pr_12', source_type: 'parts_request', status: 'approved' },
  ], target).status, 'processed');
  assert.equal(resolveAuditTarget([
    { id: 'pr_12', source_type: 'parts_request', accessible: false },
  ], target).status, 'forbidden');
  assert.equal(resolveAuditTarget([], target).status, 'missing');
  assert.equal(resolveAuditTarget([
    { id: 'pr_12', source_type: 'parts_request' },
  ], getAuditTargetFromSearchParams('tab=parts&request=12&request_type=unknown')).status, 'invalid');
});
