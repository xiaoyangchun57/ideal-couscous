import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {
  groupExecutionPackagesByDate,
  canCancelPlanSchedule,
  hasMeaningfulSitePriority,
  itineraryRowSiteIds,
  planDetailItineraryRows,
  planExecutionPresentation,
  normalizePlanCancelReason,
  normalizePlanPurgeReason,
  sitePriorityPresentation,
  shouldShowPreExecutionRisks,
  shouldShowSitePriority,
} from './planExecutionPackages.js';

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
  assert.deepEqual(planExecutionPresentation({ execution_status: 'rework' }),
    { key: 'rework', label: '需整改', color: 'warning' });
});

test('schedule modifying, submitted change, and cancellation override execution aggregates', () => {
  assert.deepEqual(planExecutionPresentation({ status: 'modifying', execution_status: 'completed' }),
    { key: 'modifying', label: '变更中，暂缓执行', color: 'warning' });
  assert.deepEqual(planExecutionPresentation({ status: 'change_submitted', execution_status: 'pending' }),
    { key: 'change_pending', label: '变更待审', color: 'warning' });
  assert.deepEqual(planExecutionPresentation({ status: 'cancelled', execution_status: 'partial' }),
    { key: 'cancelled', label: '已取消', color: 'default' });
});

test('plan cancellation gate and payload follow owner/admin, status, trim, and length contracts', () => {
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'approved' }, { id: 7, role: 'operator' }), true);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'modifying' }, { id: 9, roles: ['reviewer', 'admin'] }), true);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'approved' }, { id: 9, roles: ['reviewer'] }), false);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'submitted' }, { id: 7, roles: ['operator'] }), false);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'cancelled' }, { id: 9, roles: ['admin'] }), false);
  assert.deepEqual(normalizePlanCancelReason('  排错路线  '), { value: '排错路线', error: '' });
  assert.equal(normalizePlanCancelReason('  ').error, '请填写取消原因');
  assert.equal(normalizePlanCancelReason('x'.repeat(500)).value.length, 500);
  assert.equal(normalizePlanCancelReason('x'.repeat(501)).error, '取消原因不能超过500字');
});

test('page initial null detail and missing user are safe for the cancellation gate', () => {
  assert.equal(canCancelPlanSchedule(null, { id: 7, roles: ['admin'] }), false);
  assert.equal(canCancelPlanSchedule(undefined, { id: 7, roles: ['admin'] }), false);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'approved' }, null), false);
});

test('irreversible purge reason is trimmed, required, and capped at 500 characters', () => {
  assert.deepEqual(normalizePlanPurgeReason('  主链测试误建  '),
    { value: '主链测试误建', error: '' });
  assert.equal(normalizePlanPurgeReason('  ').error, '请填写彻底删除原因');
  assert.equal(normalizePlanPurgeReason('x'.repeat(500)).value.length, 500);
  assert.equal(normalizePlanPurgeReason('x'.repeat(501)).error, '彻底删除原因不能超过500字');
});

test('plan detail purge contract is admin-only, versioned, retryable, and refreshes after success', () => {
  const page = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  assert.match(page, /const canPurgeDetail = Boolean\(detail && isAdmin\)/);
  assert.match(page, /彻底删除无效计划/);
  assert.match(page, /version: Number\(detail\.version \|\| 1\)/);
  assert.match(page, /purgeActionRef\.current/);
  assert.match(page, /setPurgeError\(error\?\.message/);
  assert.match(page, /setDrawerOpen\(false\)[\s\S]*setDetail\(null\)[\s\S]*refreshAll\(\)/);
});

test('cancel and irreversible purge reason counters reserve local footer space responsively', () => {
  const page = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  const styles = fs.readFileSync(new URL('./PlanSchedulesPage.css', import.meta.url), 'utf8');
  const wrappers = page.match(/<div className="plan-destructive-reason">[\s\S]*?<Input\.TextArea[\s\S]*?showCount[\s\S]*?<\/div>/g) || [];
  assert.equal(wrappers.length, 2);
  assert.ok(wrappers.some(value => value.includes('value={cancelReason}')));
  assert.ok(wrappers.some(value => value.includes('value={purgeReason}')));
  assert.ok(wrappers.every(value => value.includes('maxLength={500}')));
  assert.match(styles, /\.plan-destructive-reason\s*\{[^}]*padding-bottom:\s*40px/);
  assert.match(styles, /@media\s*\(max-width:\s*760px\)[\s\S]*\.plan-destructive-reason\s*\{[^}]*padding-bottom:\s*44px/);
  assert.doesNotMatch(styles, /(^|\n)\s*\.ant-input-textarea-show-count\s*\{/);
});

test('legacy and taskless plans never expose internal active as field work in progress', () => {
  assert.equal(planExecutionPresentation({ status: 'approved', field_status: 'active' }).key, 'pending');
  assert.equal(planExecutionPresentation({ status: 'change_submitted', field_status: 'active' }).key, 'change_pending');
  assert.equal(planExecutionPresentation({ status: 'approved', field_status: 'completed' }).key, 'completed');
  assert.equal(planExecutionPresentation({ status: 'approved', field_status: 'rework' }).key, 'rework');
  assert.equal(planExecutionPresentation({}).key, 'pending');
});

test('completed, archived and cancelled plans hide pre-execution risks while actionable plans keep them', () => {
  assert.equal(shouldShowPreExecutionRisks({ status: 'approved', execution_status: 'completed' }), false);
  assert.equal(shouldShowPreExecutionRisks({ status: 'archived', execution_status: 'pending' }), false);
  assert.equal(shouldShowPreExecutionRisks({ status: 'cancelled', execution_status: 'cancelled' }), false);
  assert.equal(shouldShowPreExecutionRisks({ status: 'approved', execution_status: 'pending' }), true);
  assert.equal(shouldShowPreExecutionRisks({ status: 'submitted' }), true);
});

test('generated site tasks replace planned chips for the same date and keep each site once', () => {
  const rows = planDetailItineraryRows({
    '2026-08-19': { sites: [1, 2], notes: '按路线执行' },
    '2026-08-20': { sites: [3] },
  }, [
    { date: '2026-08-19', site_id: 1, site_name: '青云站', status: 'partial' },
    { date: '2026-08-19', site_id: 1, site_name: '青云站', status: 'partial' },
    { date: '2026-08-19', site_id: 2, site_name: '扬子洲站', status: 'pending' },
  ]);
  assert.deepEqual(rows[0].planned_site_ids, []);
  assert.deepEqual(rows[0].tasks.map(task => task.site_id), [1, 2]);
  assert.deepEqual(itineraryRowSiteIds(rows[0]), [1, 2]);
  assert.deepEqual(rows[1].planned_site_ids, [3]);
  assert.deepEqual(itineraryRowSiteIds(rows[1]), [3]);
});

test('site priority is visible only for a non-zero score or a real reason', () => {
  assert.equal(hasMeaningfulSitePriority(0, []), false);
  assert.equal(hasMeaningfulSitePriority('0', ['']), false);
  assert.equal(hasMeaningfulSitePriority(12, []), true);
  assert.equal(hasMeaningfulSitePriority(0, ['近期水质异常']), true);
});

test('site priority also follows the pre-execution risk gate', () => {
  assert.equal(shouldShowSitePriority(true, 12, []), true);
  assert.equal(shouldShowSitePriority(true, 0, ['近期水质异常']), true);
  assert.equal(shouldShowSitePriority(false, 12, ['近期水质异常']), false);
});

test('site priority presentation hides empty facts and never exposes score zero', () => {
  assert.equal(sitePriorityPresentation(false, 30, ['近期水质异常']), null);
  assert.equal(sitePriorityPresentation(true, 0, []), null);
  assert.deepEqual(sitePriorityPresentation(true, 12, []), {
    label: '优先级低', tone: 'priority', score: 12, reasons: [], tooltip: '优先级评分 12',
  });
  assert.equal(sitePriorityPresentation(true, 16, []).label, '优先级中');
  assert.equal(sitePriorityPresentation(true, 30, []).label, '优先级高');
  assert.deepEqual(sitePriorityPresentation(true, 0, ['近期水质异常']), {
    label: '有关注项', tone: 'warning', score: 0,
    reasons: ['近期水质异常'], tooltip: '近期水质异常',
  });
});

test('plan list and detail share the user-facing execution presentation', () => {
  const source = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  assert.match(source, /cancelled:\s*\{ label: '已取消'/);
  assert.match(source, /dataIndex: 'execution_status'/);
  assert.match(source, /render: \(_, record\) => \{[\s\S]*planExecutionPresentation\(record\)/);
  assert.match(source, /label="执行进度"[\s\S]*planExecutionPresentation\(detail\)/);
  assert.doesNotMatch(source, /现场进行中/);
});

test('detail cancellation keeps reason and context on failure and refreshes after success', () => {
  const source = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  const handlerStart = source.indexOf('  const submitPlanCancellation = async () => {');
  const handlerEnd = source.indexOf('\n  };', handlerStart);
  const handler = source.slice(handlerStart, handlerEnd);
  const failureBranch = handler.match(/catch \(error\) \{([\s\S]*?)\n\s{4}\} finally/)?.[1] || '';
  assert.notEqual(handlerStart, -1);
  assert.notEqual(handlerEnd, -1);
  assert.match(handler, /canCancelPlanSchedule\(detail, user\)/);
  assert.match(handler, /canCancelPlanSchedule\(detail, user\) \|\| cancelActionRef\.current\) return/);
  assert.match(handler, /cancelActionRef\.current = true/);
  assert.match(handler, /postStrict\(`\/plan-schedules\/\$\{detail\.id\}\/cancel`,\s*\{ reason: normalized\.value, version \}/);
  assert.match(failureBranch, /setCancelError\(error\?\.message \|\| '取消计划失败/);
  assert.doesNotMatch(failureBranch, /setCancelReason\(''\)/);
  assert.match(handler, /finally \{[\s\S]*cancelActionRef\.current = false/);
  assert.match(handler, /await openDetail\(scheduleId\)/);
  assert.match(source, /maxLength=\{500\}/);
  assert.match(source, /confirmLoading=\{cancelLoading\}/);
});

test('plan detail removes duplicate technical guidance while keeping overdue actions', () => {
  const source = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  assert.match(source, />行程与执行进度</);
  assert.match(source, /Number\(detail\.version \|\| 1\) > 1/);
  assert.match(source, /detail\.submitted_at &&/);
  assert.match(source, /detail\.approver_name &&/);
  assert.match(source, /detail\.remarks\?\.trim\(\) &&/);
  assert.match(source, /task\.site_name}[\s\S]*scoreBadge\(task\.site_id\)/);
  assert.match(source, /itineraryRowSiteIds\(row\)\.forEach/);
  assert.doesNotMatch(source, /\{lv\.label\}·\{/);
  assert.doesNotMatch(source, /低·0/);
  assert.doesNotMatch(source, /已生成 .*日期×站点任务/);
  assert.doesNotMatch(source, /当日任务/);
  assert.doesNotMatch(source, /执行说明/);
  assert.doesNotMatch(source, /现场执行请在小程序完成/);
  assert.match(source, /handleOverdueAction\(task, 'remind'\)/);
  assert.match(source, /closeOverdueExecution\(task\)/);
});
