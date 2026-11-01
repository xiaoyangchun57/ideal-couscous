import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { nextQueueItem } from './auditQueueNavigation.js';
import {
  createAuditReviewSession,
  createAuditReviewSubmission,
  isAuditReviewSubmissionCurrent,
} from './auditReviewSession.js';

const auditSource = readFileSync(new URL('./AuditPage.jsx', import.meta.url), 'utf8');

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test('hidden tabs retain independent queues and workorder submission advances within its filter', () => {
  const workorders = [
    { source_type: 'workorder_review', id: 'wo_1' },
    { source_type: 'workorder_review', id: 'wo_2' },
  ];
  const vehicles = [{ source_type: 'vehicle_application', id: 'va_1' }];
  const queues = new Map([['workorder', workorders], ['vehicle', vehicles]]);
  const session = createAuditReviewSession(workorders[0], 'session-workorder');
  const submission = createAuditReviewSubmission(session, queues);

  assert.deepEqual(submission.queue, workorders);
  assert.equal(nextQueueItem(submission.queue, workorders[0], [workorders[1], vehicles[0]]), workorders[1]);
});

test('a delayed response cannot commit after the review window switches to another item', async () => {
  const itemA = { source_type: 'workorder_review', id: 'wo_1' };
  const itemB = { source_type: 'vehicle_application', id: 'va_1' };
  let currentSession = createAuditReviewSession(itemA, 'session-a');
  const submission = createAuditReviewSubmission(currentSession, new Map([['workorder', [itemA]]]));
  const request = deferred();
  let committed = false;
  const completion = request.promise.then(() => {
    if (isAuditReviewSubmissionCurrent(submission, currentSession)) committed = true;
  });

  currentSession = createAuditReviewSession(itemB, 'session-b');
  request.resolve();
  await completion;

  assert.equal(committed, false);
});

test('a delayed response cannot commit after its review window closes', async () => {
  const item = { source_type: 'workorder_review', id: 'wo_1' };
  let currentSession = createAuditReviewSession(item, 'session-a');
  const submission = createAuditReviewSubmission(currentSession, new Map([['workorder', [item]]]));
  const request = deferred();
  let committed = false;
  const completion = request.promise.then(() => {
    if (isAuditReviewSubmissionCurrent(submission, currentSession)) committed = true;
  });

  currentSession = null;
  request.resolve();
  await completion;

  assert.equal(committed, false);
});

test('refresh retry is read-only and reuses the committed session', () => {
  const retryStart = auditSource.indexOf('const retryCommittedRefresh');
  const reviewStart = auditSource.indexOf('const handleReview', retryStart);
  const retrySource = auditSource.slice(retryStart, reviewStart);

  assert.match(retrySource, /committedSubmissionRef\.current/);
  assert.match(retrySource, /advanceAfterCommittedReview/);
  assert.doesNotMatch(retrySource, /handleReview\(|postStrict|putStrict/);
  assert.match(auditSource, /提交正在处理中，请等待完成后再切换审核事项/);
  assert.match(auditSource, /提交正在处理中，请等待完成后再关闭审核窗口/);
});
