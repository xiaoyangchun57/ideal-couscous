import test from 'node:test';
import assert from 'node:assert/strict';
import {
  cleanupCandidateFactRows, cleanupCandidateIdentityRows, reconcileCleanupSelection,
} from './cleanupCandidateFacts.js';

test('cleanup preview exposes every required business fact in a stable order', () => {
  const rows = cleanupCandidateFactRows({
    locations: 1,
    checkins: 2,
    photos: 3,
    reviews: 4,
    attachments: 5,
    notifications: 6,
    execution_records: 7,
    resource_records: 8,
  });
  assert.deepEqual(rows, [
    { key: 'locations', label: '现场定位', value: 1 },
    { key: 'checkins', label: '签到', value: 2 },
    { key: 'photos', label: '照片', value: 3 },
    { key: 'reviews', label: '审核', value: 4 },
    { key: 'attachments', label: '附件', value: 5 },
    { key: 'notifications', label: '通知', value: 6 },
    { key: 'execution_records', label: '关联执行记录', value: 7 },
    { key: 'resource_records', label: '资源记录', value: 8 },
  ]);
});

test('cleanup preview treats absent and invalid counts as zero', () => {
  assert.deepEqual(
    cleanupCandidateFactRows({ photos: '2', reviews: -1, attachments: 'bad' })
      .map((row) => row.value),
    [0, 0, 2, 0, 0, 0, 0, 0],
  );
});

test('cleanup preview identifies the exact plan or workorder with explicit placeholders', () => {
  assert.deepEqual(cleanupCandidateIdentityRows({
    kind: 'plan_schedule', owner_name: '运维甲', period_start: '2026-08-01',
    period_end: '2026-08-07', status: 'rejected',
  }), ['执行人：运维甲', '周期：2026-08-01 ~ 2026-08-07', '状态：已退回']);
  assert.deepEqual(cleanupCandidateIdentityRows({
    kind: 'workorder', order_no: 'WO-1', title: '', site_name: '', status: 'pending',
  }), ['工单编号：WO-1', '标题：未填写', '站点：未关联', '状态：待处理']);
});

test('cleanup selection drops records that disappeared from a refreshed preview', () => {
  assert.deepEqual(reconcileCleanupSelection([
    { kind: 'plan_schedule', id: 1 },
    { kind: 'workorder', id: 2 },
  ], [
    { kind: 'workorder', id: 2 },
    { kind: 'workorder', id: 3 },
  ]), [{ kind: 'workorder', id: 2 }]);
  assert.deepEqual(reconcileCleanupSelection(null, null), []);
});
