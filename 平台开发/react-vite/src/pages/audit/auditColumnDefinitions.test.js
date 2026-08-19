import assert from 'node:assert/strict';
import test from 'node:test';
import {
  countUniqueAuditSites,
  getAuditAllowedTabs,
  getAuditColumnKeys,
  getAuditColumnProfile,
} from './auditColumnDefinitions.js';

test('administrator navigation keeps every rendered approval tab reachable', () => {
  assert.deepEqual(getAuditAllowedTabs(['admin', 'operator']), [
    'data', 'inspection', 'plan', 'workorder', 'parts', 'vehicle',
  ]);
  assert.deepEqual(getAuditAllowedTabs(['reviewer']), ['data', 'inspection', 'workorder']);
  assert.deepEqual(getAuditAllowedTabs(['operator']), []);
});

test('plan approval uses route, vehicle, and spare-part columns instead of photos', () => {
  assert.deepEqual(getAuditColumnKeys(['plan_schedule']), [
    'plan', 'executor', 'route_sites', 'vehicle', 'spare_parts', 'submit_time', 'action',
  ]);
  assert.equal(getAuditColumnKeys(['plan_schedule']).includes('photos'), false);
});

test('vehicle approval uses request-focused columns instead of photos', () => {
  assert.deepEqual(getAuditColumnKeys(['vehicle_application']), [
    'applicant_vehicle', 'use_time', 'destination_reason', 'related_work', 'submit_time', 'action',
  ]);
  assert.equal(getAuditColumnKeys(['vehicle_application']).includes('photos'), false);
});

test('workorder and inspection item review retain the thumbnail-oriented photo column', () => {
  assert.equal(getAuditColumnKeys(['inspection_batch']).includes('photos'), true);
  assert.equal(getAuditColumnKeys(['workorder_review']).includes('photos'), true);
});

test('parts requests retain their dedicated list component', () => {
  assert.equal(getAuditColumnProfile(['parts_request']), 'parts');
  assert.equal(getAuditColumnProfile(['spare_part_request']), 'parts');
  assert.deepEqual(getAuditColumnKeys(['spare_part_request']), [
    'part', 'request_type', 'request', 'submit_time', 'action',
  ]);
});

test('plan approval metrics expand route site ids and deduplicate overlaps', () => {
  assert.equal(countUniqueAuditSites([
    { source_type: 'plan_schedule', site_ids: [1, 2, 3], site_name: '青云、扬子洲、室内站' },
    { source_type: 'plan_schedule', site_ids: ['2', 3, 4], site_name: '扬子洲、室内站、梅港' },
  ]), 4);
});

test('single-site audit records keep id and name fallbacks without splitting labels', () => {
  assert.equal(countUniqueAuditSites([
    { source_type: 'inspection_batch', site_id: 7, site_name: '青云' },
    { source_type: 'workorder_review', site_id: '7', site_name: '青云' },
    { source_type: 'workorder_review', site_name: '扬子洲' },
    { source_type: 'plan_schedule', site_name: '青云、扬子洲、室内站' },
  ]), 3);
});

test('route arrays ignore empty and invalid ids instead of falling back to display text', () => {
  assert.equal(countUniqueAuditSites([
    { site_ids: [null, '', 0, -1, 2.5, 'invalid', '  ', '8'], site_name: '不得拆分的路线名称' },
    { site_ids: [], site_id: 9, site_name: '数组存在时不回退' },
  ]), 1);
});
