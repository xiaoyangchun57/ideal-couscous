import test from 'node:test';
import assert from 'node:assert/strict';
import { auditItemKey, nextQueueItem } from './auditQueueNavigation.js';

test('next audit item follows the visible queue and skips rows no longer pending', () => {
  const queue = [
    { source_type: 'workorder_review', id: 'wo_1' },
    { source_type: 'workorder_review', id: 'wo_2' },
    { source_type: 'workorder_review', id: 'wo_3' },
  ];
  const refreshed = [queue[2], { source_type: 'plan_schedule', id: 'ps_9' }];

  assert.equal(auditItemKey(queue[0]), 'workorder_review:wo_1');
  assert.equal(nextQueueItem(queue, queue[0], refreshed), queue[2]);
});

test('isolated deep links and exhausted filters do not invent a next item', () => {
  const queue = [{ source_type: 'inspection_batch', id: 'batch_1' }];
  const isolated = { source_type: 'inspection_batch', id: 'batch_9' };

  assert.equal(nextQueueItem(queue, isolated, queue), null);
  assert.equal(nextQueueItem(queue, queue[0], []), null);
});

test('queue keys prevent same numeric id from crossing audit types', () => {
  const queue = [
    { source_type: 'parts_request', id: 7 },
    { source_type: 'vehicle_application', id: 7 },
  ];

  assert.equal(nextQueueItem(queue, queue[0], [queue[1]]), queue[1]);
});
