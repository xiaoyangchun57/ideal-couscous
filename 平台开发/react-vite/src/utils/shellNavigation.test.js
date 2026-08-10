import test from 'node:test';
import assert from 'node:assert/strict';
import { buildGlobalSearchPath, getNotificationTarget } from './shellNavigation.js';

test('global search paths use the destination page query contract', () => {
  assert.equal(buildGlobalSearchPath({ type: 'site', identifier: 35 }), '/sites?archive=35');
  assert.equal(buildGlobalSearchPath({ type: 'workorder', identifier: 'WO-1' }), '/workorders?search=WO-1');
  assert.equal(buildGlobalSearchPath({ type: 'device', identifier: 'WB-003-01' }), '/equipment?q=WB-003-01');
});

test('notification targets respect role-visible pages', () => {
  assert.equal(getNotificationTarget({ source_type: 'photo_review', source_id: 8 }, ['operator']), null);
  assert.equal(getNotificationTarget({ source_type: 'photo_review', source_id: 8 }, ['reviewer']), '/audit?tab=photo');
  assert.equal(getNotificationTarget({ source_type: 'inspection_review_batch', source_id: 'insp_batch_10_1' }, ['reviewer']), '/audit?tab=inspection');
  assert.equal(getNotificationTarget({ source_type: 'inspection_review', source_id: 12 }, ['operator']), '/plan-schedules');
  assert.equal(getNotificationTarget({ source_type: 'inspection_rework', source_id: 18 }, ['operator']), '/plan-schedules?rework_plan=18');
  assert.equal(getNotificationTarget({ source_type: 'workorder_review', source_id: 'WO-1' }, ['admin']), '/audit?tab=workorder&order=WO-1');
  assert.equal(getNotificationTarget({ source_type: 'spare_part_request', source_id: 9 }, ['admin']), '/audit?tab=parts');
  assert.equal(getNotificationTarget({ source_type: 'data_review', source_id: 7 }, ['reviewer']), '/audit?tab=data');
  assert.equal(getNotificationTarget({ source_type: 'data_review', source_id: 7 }, ['operator']), null);
  assert.equal(getNotificationTarget({ source_type: 'reagent_qc', source_id: 2 }, ['reviewer']), null);
});
