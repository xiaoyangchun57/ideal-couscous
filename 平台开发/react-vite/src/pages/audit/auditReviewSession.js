import { auditItemKey } from './auditQueueNavigation.js';

export function auditQueueKeyForItem(item) {
  switch (item?.source_type) {
    case 'inspection':
    case 'inspection_batch':
      return 'inspection';
    case 'plan_schedule':
      return 'plan';
    case 'workorder_review':
      return 'workorder';
    case 'parts_request':
    case 'spare_part_request':
      return 'parts';
    case 'vehicle_application':
      return 'vehicle';
    default:
      return '';
  }
}

export function createAuditReviewSession(item, sessionId) {
  return {
    sessionId,
    itemKey: auditItemKey(item),
    queueKey: auditQueueKeyForItem(item),
  };
}

export function createAuditReviewSubmission(session, visibleItemsByQueue) {
  const visibleItems = visibleItemsByQueue instanceof Map
    ? visibleItemsByQueue.get(session?.queueKey)
    : visibleItemsByQueue?.[session?.queueKey];
  return {
    ...session,
    queue: Array.isArray(visibleItems) ? [...visibleItems] : [],
  };
}

export function isAuditReviewSubmissionCurrent(submission, currentSession) {
  return Boolean(
    submission?.sessionId
    && submission.sessionId === currentSession?.sessionId
    && submission.itemKey === currentSession?.itemKey,
  );
}
