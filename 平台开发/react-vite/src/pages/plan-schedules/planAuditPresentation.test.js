import assert from 'node:assert/strict';
import test from 'node:test';
import {
  normalizePlanPurgeAudits,
  planCancellationPresentation,
  planPurgeAuditPath,
  planPurgeAuditPresentation,
} from './planAuditPresentation.js';

test('cancelled plans use only the server cancellation object and expose honest missing facts', () => {
  assert.deepEqual(planCancellationPresentation({
    status: 'cancelled', remarks: '不得用作取消原因',
    cancellation: { reason: '  路线调整  ', operator_name: '  管理员甲 ', occurred_at: '2026-09-16 08:30:00' },
  }), {
    reason: '路线调整', operatorName: '管理员甲', occurredAt: '2026-09-16 08:30:00',
  });
  assert.deepEqual(planCancellationPresentation({ status: 'cancelled', remarks: '错误兜底' }), {
    reason: '未记录', operatorName: '未记录', occurredAt: '未记录',
  });
  assert.equal(planCancellationPresentation({
    status: 'cancelled', cancellation: { operator_id: 18 },
  }).operatorName, '用户 #18');
  assert.equal(planCancellationPresentation({ status: 'approved' }), null);
});

test('purge audit requests follow the fixed paginated read-only contract', () => {
  assert.equal(planPurgeAuditPath(2), '/plan-schedules/purge-audits?page=2&page_size=20');
  assert.equal(planPurgeAuditPath('invalid', 0), '/plan-schedules/purge-audits?page=1&page_size=20');
  assert.deepEqual(normalizePlanPurgeAudits({ items: [], total: 0, page: 1, page_size: 20 }), {
    items: [], total: 0, page: 1, pageSize: 20,
  });
  assert.throws(() => normalizePlanPurgeAudits({ items: [], total: '0', page: 1, page_size: 20 }),
    /删除记录返回格式异常/);
});

test('purge audit presentation keeps every server fact and marks missing values', () => {
  assert.deepEqual(planPurgeAuditPresentation({
    plan_id: 42, plan_name: '九月周巡检', status_before_delete: 'draft', owner_name: '运维甲',
    period_start: '2026-09-14', period_end: '2026-09-20', site_ids: [7, 8], reason: '误建计划',
    operator_name: '管理员乙', purged_at: '2026-09-16 09:00:00',
  }), {
    key: '42-2026-09-16 09:00:00', planId: '42', planName: '九月周巡检',
    statusBeforeDelete: 'draft', ownerName: '运维甲', period: '2026-09-14 ~ 2026-09-20',
    siteCount: 2, reason: '误建计划', operatorName: '管理员乙', purgedAt: '2026-09-16 09:00:00',
  });
  const missing = planPurgeAuditPresentation({ plan_id: 43, site_ids: null });
  assert.equal(missing.reason, '未记录');
  assert.equal(missing.period, '未记录');
  assert.equal(missing.siteCount, 0);
});
