import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { groupExecutionPackagesByDate, planExecutionPresentation } from './planExecutionPackages.js';

test('two sites in one internal day package expose one day-level action entry', () => {
  const grouped = groupExecutionPackagesByDate([
    { date: '2026-08-17', plan_id: 91, site_id: 1, status: 'pending', overdue: true },
    { date: '2026-08-17', plan_id: 91, site_id: 2, status: 'partial', overdue: true },
  ]);
  assert.equal(grouped['2026-08-17'].length, 1);
  assert.equal(grouped['2026-08-17'][0].site_count, 2);
  assert.equal(grouped['2026-08-17'][0].can_handle_overdue, true);
});

test('overall execution presentation uses the server user state', () => {
  assert.deepEqual(planExecutionPresentation({ execution_status: 'pending' }),
    { key: 'pending', label: '待执行', color: 'default' });
  assert.deepEqual(planExecutionPresentation({ execution_status: 'partial' }),
    { key: 'partial', label: '部分完成', color: 'processing' });
  assert.deepEqual(planExecutionPresentation({ execution_status: 'completed' }),
    { key: 'completed', label: '已完成', color: 'success' });
  assert.deepEqual(planExecutionPresentation({ execution_status: 'change_pending' }),
    { key: 'change_pending', label: '变更待审', color: 'warning' });
});

test('legacy and taskless plans never expose internal active as field work in progress', () => {
  assert.equal(planExecutionPresentation({ status: 'approved', field_status: 'active' }).key, 'pending');
  assert.equal(planExecutionPresentation({ status: 'change_submitted', field_status: 'active' }).key, 'change_pending');
  assert.equal(planExecutionPresentation({ status: 'approved', field_status: 'completed' }).key, 'completed');
  assert.equal(planExecutionPresentation({ status: 'approved', field_status: 'rework' }).key, 'rework');
  assert.equal(planExecutionPresentation({}).key, 'pending');
});

test('plan list and detail share the user-facing execution presentation', () => {
  const source = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  assert.match(source, /dataIndex: 'execution_status'/);
  assert.match(source, /render: \(_, record\) => \{[\s\S]*planExecutionPresentation\(record\)/);
  assert.match(source, /label="现场状态"[\s\S]*planExecutionPresentation\(detail\)/);
  assert.doesNotMatch(source, /现场进行中/);
});
