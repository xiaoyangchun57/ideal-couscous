import assert from 'node:assert/strict';
import test from 'node:test';
import { getAuditAllowedTabs, getAuditColumnKeys, getAuditColumnProfile } from './auditColumnDefinitions.js';

test('administrator navigation keeps every rendered approval tab reachable', () => {
  assert.deepEqual(getAuditAllowedTabs(['admin', 'operator']), [
    'data', 'inspection', 'plan', 'workorder', 'parts', 'vehicle', 'photo',
  ]);
  assert.deepEqual(getAuditAllowedTabs(['reviewer']), ['data', 'inspection', 'workorder', 'photo']);
  assert.deepEqual(getAuditAllowedTabs(['operator']), []);
});

test('plan approval uses route, vehicle, and spare-part columns instead of photos', () => {
  assert.deepEqual(getAuditColumnKeys(['plan_schedule']), [
    'plan', 'route_sites', 'vehicle', 'spare_parts', 'submit_time', 'action',
  ]);
  assert.equal(getAuditColumnKeys(['plan_schedule']).includes('photos'), false);
});

test('vehicle approval uses request-focused columns instead of photos', () => {
  assert.deepEqual(getAuditColumnKeys(['vehicle_application']), [
    'applicant_vehicle', 'use_time', 'destination_reason', 'related_work', 'submit_time', 'action',
  ]);
  assert.equal(getAuditColumnKeys(['vehicle_application']).includes('photos'), false);
});

test('workorder and image review retain the thumbnail-oriented photo column', () => {
  assert.equal(getAuditColumnKeys(['photo_review']).includes('photos'), true);
  assert.equal(getAuditColumnKeys(['workorder_review']).includes('photos'), true);
});

test('parts requests retain their dedicated list component', () => {
  assert.equal(getAuditColumnProfile(['parts_request']), 'parts');
});
