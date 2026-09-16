const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const pagePath = path.resolve(__dirname, '../pages/plan-detail/plan-detail.js');
const templatePath = path.resolve(__dirname, '../pages/plan-detail/plan-detail.wxml');
const stylePath = path.resolve(__dirname, '../pages/plan-detail/plan-detail.wxss');
const apiPath = path.resolve(__dirname, '../services/api.js');
const inspectionTemplatePath = path.resolve(__dirname, '../pages/inspection/inspection.wxml');
const maps = require('../services/maps.js');
const api = require('../services/api.js');
const {
  canCancelPlanSchedule,
  normalizePlanCancelReason,
  startPlanCancellation,
} = require('../utils/executionState.js');

let pageDefinition;
let currentUser = null;
let toasts = [];
let modals = [];
let navigations = [];
const testApp = { globalData: { token: 'token', executionTarget: null } };
global.getApp = () => testApp;
global.wx = {
  getStorageSync: key => key === 'user' ? currentUser : null,
  showToast: options => { toasts.push(options); },
  showModal: options => { modals.push(options); },
  reLaunch: () => {},
  navigateTo: options => { navigations.push(options); },
  stopPullDownRefresh: () => {},
};
global.Page = page => { pageDefinition = page; };

delete require.cache[pagePath];
const { normalizeGeneratedTasks, planHeaderPresentation, summarizeGeneratedTasks } = require(pagePath);

const originalApi = {
  planScheduleDetail: api.planScheduleDetail,
  planScheduleFavorites: api.planScheduleFavorites,
  cancelPlanSchedule: api.cancelPlanSchedule,
};

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function setPath(target, key, value) {
  const parts = key.split('.');
  let current = target;
  while (parts.length > 1) {
    const part = parts.shift();
    if (!current[part] || typeof current[part] !== 'object') current[part] = {};
    current = current[part];
  }
  current[parts[0]] = value;
}

function createPage() {
  const page = Object.assign({}, pageDefinition, {
    data: JSON.parse(JSON.stringify(pageDefinition.data)),
  });
  page.setData = patchData => {
    Object.keys(patchData).forEach(key => setPath(page.data, key, patchData[key]));
  };
  return page;
}

function detailResponse(overrides) {
  return Object.assign({
    id: 46,
    user_id: 7,
    version: 3,
    status: 'approved',
    can_cancel: true,
    cancel_block_reason: '',
    execution_completed: true,
    period_start: '2026-08-29',
    period_end: '2026-08-29',
    plan_data: { '2026-08-29': { sites: [] } },
    vehicle_days: {}, vehicle_map: {}, site_map: {},
    generated_site_tasks: [], resource_parts: [], spare_parts: [], linked_workorders: [],
  }, overrides || {});
}

async function loadPage(detail, user) {
  currentUser = user;
  api.planScheduleDetail = () => Promise.resolve(detail);
  api.planScheduleFavorites = () => Promise.resolve([]);
  const page = createPage();
  page.onLoad({ id: String(detail.id || 46) });
  page.onShow();
  await flush();
  return page;
}

test.afterEach(() => {
  Object.assign(api, originalApi);
  currentUser = null;
  toasts = [];
  modals = [];
  navigations = [];
  testApp.globalData.executionTarget = null;
});

test('detail header uses overall execution status while change review stays authoritative', () => {
  assert.deepEqual(planHeaderPresentation({
    status: 'approved', execution_status: 'partial', execution_status_cn: '部分完成',
  }), { label: '部分完成', cls: 'orange' });
  assert.deepEqual(planHeaderPresentation({
    status: 'change_submitted', execution_status: 'partial', execution_status_cn: '部分完成',
  }), { label: '变更待审', cls: 'orange' });
  assert.deepEqual(planHeaderPresentation({
    status: 'submitted', execution_status: 'pending', execution_status_cn: '待执行',
  }), { label: '待审批', cls: 'blue' });
  assert.deepEqual(planHeaderPresentation({ status: 'cancelled' }),
    { label: '已取消', cls: 'gray' });
});

test('candidate site tasks win over legacy packages and keep same-day sites distinct', () => {
  const result = normalizeGeneratedTasks({
    generated_site_tasks: [
      { plan_id: 91, execution_date: '2026-08-31', site_id: 10, site_name: '青云', status: 'pending', status_cn: '待执行' },
      { plan_id: 91, execution_date: '2026-08-31', site_id: 11, site_name: '扬子洲', status: 'partial', status_cn: '部分完成' },
      { plan_id: 92, execution_date: '2026-09-01', site_id: 12, site_name: '室内站', status: 'completed', status_cn: '已完成' },
      { plan_id: 93, execution_date: '2026-09-02', site_id: 10, site_name: '青云', status: 'change_pending', status_cn: '变更待审' },
    ],
    generated_plans: [{ id: 91, plan_name: '旧内部包', status: 'active' }],
  });

  assert.equal(result.mode, 'site');
  assert.equal(result.items.length, 4);
  assert.equal(new Set(result.items.map(item => item.key)).size, 4);
  assert.deepEqual(result.items.map(item => item.display_name), [
    '2026-08-31 · 青云', '2026-08-31 · 扬子洲',
    '2026-09-01 · 室内站', '2026-09-02 · 青云',
  ]);
  assert.deepEqual(result.items.map(item => item.status_cn),
    ['待执行', '部分完成', '已完成', '变更待审']);
  assert.equal(result.items.some(item => item.display_name === '旧内部包'), false);
});

test('site task item counts use authoritative fields and retain explicit zero values', () => {
  const result = normalizeGeneratedTasks({
    generated_site_tasks: [
      { plan_id: 91, site_id: 10, status: 'partial', completed_items: 2, total_items: 4 },
      { plan_id: 91, site_id: 11, status: 'pending', completed_items: 0, total_items: 0 },
      { plan_id: 91, site_id: 12, status: 'pending' },
    ],
  });

  assert.deepEqual(result.items.map(item => item.item_counts_text), [
    '2/4 项', '0/0 项', '检查项未记录'
  ]);
  assert.deepEqual(summarizeGeneratedTasks(result), {
    totalSites: 3,
    completedSites: 0,
    totalItems: 4,
    completedItems: 2,
  });
});

test('r9 response without site task field keeps an explicit legacy package fallback', () => {
  const result = normalizeGeneratedTasks({
    generated_plans: [
      { id: 91, plan_name: '万松·周检-20260831', generate_date: '2026-08-31', status: 'active' },
    ],
  });

  assert.equal(result.mode, 'legacy');
  assert.equal(result.items.length, 1);
  assert.equal(result.items[0].display_name, '万松·周检-20260831');
  assert.equal(result.items[0].status_cn, '执行中');
  assert.equal(result.items[0].legacy, true);
});

test('an authoritative empty candidate list does not fall back or crash', () => {
  const result = normalizeGeneratedTasks({
    generated_site_tasks: [],
    generated_plans: [{ id: 91, plan_name: '不应回退的旧包', status: 'active' }],
  });
  assert.deepEqual(result, { mode: 'site', items: [] });
});

test('task list uses the date-site key and labels the legacy fallback', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  assert.match(template, /wx:key="key"/);
  assert.match(template, /generatedTaskMode === 'legacy'/);
  assert.match(template, /\{\{item\.display_name\}\}/);
  assert.match(template, /\{\{item\.status_cn\}\}/);
  assert.match(template, /\{\{item\.item_counts_text\}\}/);
  assert.doesNotMatch(template, /photo_count|张照片/);
  const page = fs.readFileSync(pagePath, 'utf8');
  assert.doesNotMatch(page, /\bitem_count\b|\bcompleted_item_count\b|photo_count/);
});

test('approval card uses only real plan detail fields and preserves empty-value guards', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  const cardStart = template.indexOf('<!-- ===== 卡片2：审批信息 ===== -->');
  const cardEnd = template.indexOf('<!-- ===== 卡片3：出发准备 ===== -->');
  const approvalCard = template.slice(cardStart, cardEnd);

  assert.match(approvalCard, /wx:if="\{\{loaded && detail && \(detail\.user_name \|\| detail\.submitted_at \|\| detail\.approver_name \|\| detail\.approved_at \|\| detail\.remarks\)\}\}"/);
  assert.match(approvalCard, /wx:if="\{\{detail\.user_name\}\}"[\s\S]*负责人[\s\S]*\{\{detail\.user_name\}\}/);
  assert.match(approvalCard, /wx:if="\{\{detail\.submitted_at\}\}"[\s\S]*提交时间[\s\S]*\{\{detail\.submitted_at\}\}/);
  assert.match(approvalCard, /wx:if="\{\{detail\.approver_name\}\}"[\s\S]*审批人[\s\S]*\{\{detail\.approver_name\}\}/);
  assert.match(approvalCard, /wx:if="\{\{detail\.approved_at\}\}"[\s\S]*审批时间[\s\S]*\{\{detail\.approved_at\}\}/);
  assert.match(approvalCard, /wx:if="\{\{detail\.remarks\}\}"[\s\S]*计划备注[\s\S]*\{\{detail\.remarks\}\}/);
  assert.doesNotMatch(approvalCard, /submitter_name|approval_comment|审批意见/);
});

test('plan cancellation gate uses owner or admin across all roles and only actionable states', () => {
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'approved' }, { id: 7, role: 'operator' }), true);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'modifying' }, { id: 9, roles: ['reviewer', 'admin'] }), true);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'approved' }, { id: 9, roles: ['reviewer'] }), false);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'submitted' }, { id: 7, roles: ['operator'] }), false);
  assert.equal(canCancelPlanSchedule({ user_id: 7, status: 'cancelled' }, { id: 9, roles: ['admin'] }), false);
});

test('cancelled detail retains server audit and actual inspection frequencies without inference', async () => {
  const cancellation = { reason: '原路线无效', operator_name: '真实操作人', occurred_at: '2026-09-15 10:00:00' };
  const template_context = [{ date: '2026-09-15', site_name: 'A', frequency: 'monthly', frequency_cn: '月检', item_count: 2 }];
  const page = await loadPage(detailResponse({ status: 'cancelled', cancellation, template_context }), { id: 7, role: 'operator' });
  assert.deepEqual(page.data.detail.cancellation, cancellation);
  assert.deepEqual(page.data.detail.template_context, template_context);
  const template = fs.readFileSync(templatePath, 'utf8');
  for (const field of ['reason', 'operator_name', 'occurred_at']) assert.ok(template.includes('{{detail.cancellation.' + field + '}}'));
  assert.match(template, /item\.frequency_cn/);
});

test('owner and admin cancellation actions load the real page and open without writing', async () => {
  let writes = 0;
  api.cancelPlanSchedule = () => {
    writes += 1;
    return Promise.resolve({});
  };
  const cases = [
    {
      name: 'owner approved',
      detail: detailResponse({ user_id: 7, status: 'approved', execution_completed: true }),
      user: { id: 7, role: 'operator' },
    },
    {
      name: 'admin modifying',
      detail: detailResponse({ user_id: 7, status: 'modifying', execution_completed: false }),
      user: { id: 9, roles: ['reviewer', 'admin'] },
    },
  ];
  for (const item of cases) {
    const page = await loadPage(item.detail, item.user);
    assert.equal(page.data.canCancel, true, item.name + ' exposes the cancellation action');
    page.onOpenCancel();
    assert.equal(page.data.cancelSheet.open, true, item.name + ' opens the reason sheet');
    assert.equal(page.data.cancelSheet.submitting, false);
  }
  assert.equal(writes, 0, 'opening the sheet never calls the cancellation API');
  const template = fs.readFileSync(templatePath, 'utf8');
  assert.match(template, /bindtap="onOpenCancel"\s+wx:if="\{\{canCancel\}\}"/);
});

test('detail page consumes the server cancellation capability and blocking reason', async () => {
  const blocked = await loadPage(detailResponse({
    user_id: 7,
    status: 'approved',
    can_cancel: false,
    cancel_block_reason: '计划已有现场或实际资源事实，不能取消',
  }), { id: 7, role: 'operator' });
  assert.equal(blocked.data.canCancel, false);
  assert.equal(blocked.data.cancelBlockReason, '计划已有现场或实际资源事实，不能取消');
  blocked.onOpenCancel();
  assert.equal(blocked.data.cancelSheet.open, false);
  assert.equal(toasts.at(-1).title, '计划已有现场或实际资源事实，不能取消');

  const source = fs.readFileSync(pagePath, 'utf8');
  assert.match(source, /canCancel:\s*res\.can_cancel === true/);
  assert.doesNotMatch(source, /canCancelPlanSchedule/);
});

test('historical rework exposes one authoritative continuation and navigates to its exact item', async () => {
  const reworkDetail = detailResponse({
    id: 43,
    user_id: 7,
    execution_completed: false,
    execution_status: 'rework',
    execution_status_cn: '需整改',
    period_start: '2026-08-21',
    period_end: '2026-08-21',
    plan_data: { '2026-08-21': { sites: [362] } },
    site_map: { 362: { id: 362, name: '历史站点' } },
    can_continue_rework: true,
    rework_block_reason: '',
    rework_execution_target: {
      schedule_id: 43,
      execution_plan_id: 136,
      work_date: '2026-08-21',
      site_id: 362,
      item_id: 7089,
    },
    generated_site_tasks: [
      { plan_id: 136, execution_date: '2026-08-21', site_id: 362, status: 'rework' },
    ],
  });
  const page = await loadPage(reworkDetail, { id: 7, role: 'operator' });
  assert.equal(page.data.canExecute, true);
  assert.equal(page.data.canContinueRework, true);
  assert.equal(page.data.executionActionText, '继续整改');
  assert.match(fs.readFileSync(templatePath, 'utf8'),
    /bindtap="onGoExecution">\{\{executionActionText\}\}<\/button>/);

  let preflightReads = 0;
  api.planScheduleDetail = () => {
    preflightReads += 1;
    return Promise.resolve(reworkDetail);
  };
  page.onGoExecution();
  page.onGoExecution();
  await flush();

  assert.equal(preflightReads, 1, 'duplicate taps share the active read preflight');
  assert.equal(navigations.length, 1);
  assert.equal(navigations[0].url, '/pages/inspection/inspection');
  assert.deepEqual(testApp.globalData.executionTarget, {
    executionPlanId: 136,
    scheduleId: 43,
    workDate: '2026-08-21',
    siteId: 362,
    itemId: 7089,
    source: 'plan_detail_rework',
    reworkOnly: true,
  });
  assert.ok(toasts.some(item => item.title === '正在进入现场，请稍候'));
  navigations[0].fail({ errMsg: 'navigateTo:fail' });
  assert.equal(testApp.globalData.executionTarget, null,
    'a navigation failure clears only this one-shot remediation target');
  assert.equal(page._executionNavigating, false);
});

test('stale, unauthorized and late rework preflights never navigate to an old target', async () => {
  const initial = detailResponse({
    id: 43,
    execution_completed: false,
    execution_status: 'rework',
    can_continue_rework: true,
    rework_execution_target: {
      schedule_id: 43, execution_plan_id: 136, work_date: '2026-08-21',
      site_id: 362, item_id: 7089,
    },
  });
  const page = await loadPage(initial, { id: 7, role: 'operator' });
  let refreshes = 0;
  page.load = () => { refreshes += 1; };
  api.planScheduleDetail = () => Promise.resolve(Object.assign({}, initial, {
    can_continue_rework: false,
    rework_execution_target: null,
    rework_block_reason: '整改任务已闭环或执行权限已变化，请刷新计划详情',
  }));
  page.onGoExecution();
  await flush();
  assert.equal(navigations.length, 0);
  assert.equal(testApp.globalData.executionTarget, null);
  assert.equal(refreshes, 1);
  assert.equal(toasts.at(-1).title, '整改任务已闭环或执行权限已变化，请刷新计划详情');

  const late = deferred();
  page._alive = true;
  page._executionNavigating = false;
  page.setData({ canExecute: true, detail: initial });
  api.planScheduleDetail = () => late.promise;
  page.onGoExecution();
  page.onHide();
  late.resolve(initial);
  await flush();
  assert.equal(navigations.length, 0, 'a response after hide cannot restore the old target');
  assert.equal(testApp.globalData.executionTarget, null);
});

test('carryover execution uses the authoritative exact package and site after a fresh read', async () => {
  const carryoverDetail = detailResponse({
    id: 46,
    execution_completed: false,
    execution_status: 'pending',
    period_start: '2026-08-28',
    period_end: '2026-08-28',
    plan_data: { '2026-08-28': { sites: [362] } },
    can_continue_execution: true,
    execution_block_reason: '',
    execution_target: {
      schedule_id: 46,
      execution_plan_id: 139,
      work_date: '2026-08-28',
      site_id: 362,
    },
    generated_site_tasks: [
      { plan_id: 139, execution_date: '2026-08-28', site_id: 362, status: 'pending' },
    ],
  });
  const page = await loadPage(carryoverDetail, { id: 7, role: 'operator' });
  assert.equal(page.data.canExecute, true);
  assert.equal(page.data.canContinueExecution, true);
  assert.equal(page.data.canContinueRework, false);
  assert.equal(page.data.executionActionText, '继续执行');

  let reads = 0;
  api.planScheduleDetail = () => {
    reads += 1;
    return Promise.resolve(carryoverDetail);
  };
  page.onGoExecution();
  page.onGoExecution();
  await flush();
  assert.equal(reads, 1);
  assert.equal(navigations.length, 1);
  assert.deepEqual(testApp.globalData.executionTarget, {
    executionPlanId: 139,
    scheduleId: 46,
    workDate: '2026-08-28',
    siteId: 362,
    itemId: null,
    source: 'plan_detail_execution',
  });
});

test('ordinary current execution and ambiguous schedule-scoped selection remain available', async () => {
  const normalDetail = detailResponse({
    execution_completed: false,
    execution_status: 'pending',
    period_start: '2026-08-29',
    period_end: '2026-08-29',
    plan_data: { '2026-08-29': { sites: [10] } },
    can_continue_execution: true,
    execution_target: {
      schedule_id: 46, execution_plan_id: 91, work_date: '2026-08-29', site_id: 10,
    },
    generated_site_tasks: [
      { plan_id: 91, execution_date: '2026-08-29', site_id: 10, status: 'pending' },
    ],
  });
  const normal = await loadPage(normalDetail, { id: 7, role: 'operator' });
  assert.equal(normal.data.canExecute, true);
  assert.equal(normal.data.executionActionText, '继续执行');
  normal.onGoExecution();
  await flush();
  assert.equal(navigations.length, 1);
  assert.equal(testApp.globalData.executionTarget.source, 'plan_detail_execution');

  navigations = [];
  testApp.globalData.executionTarget = null;
  const ambiguousDetail = detailResponse({
    execution_completed: false,
    execution_status: 'partial',
    can_continue_execution: true,
    execution_target_requires_selection: true,
    execution_target: { schedule_id: 46, source: 'plan_detail_execution' },
  });
  const ambiguous = await loadPage(ambiguousDetail, { id: 7, role: 'operator' });
  assert.equal(ambiguous.data.executionActionText, '继续执行');
  ambiguous.onGoExecution();
  await flush();
  assert.equal(navigations.length, 1);
  assert.deepEqual(testApp.globalData.executionTarget, {
    executionPlanId: null,
    scheduleId: 46,
    workDate: null,
    siteId: null,
    itemId: null,
    source: 'plan_detail_execution',
  }, 'multiple targets retain schedule scope and never guess the first candidate');
});

test('stale and late ordinary execution preflights do not navigate', async () => {
  const initial = detailResponse({
    id: 46,
    execution_completed: false,
    execution_status: 'pending',
    can_continue_execution: true,
    execution_target: {
      schedule_id: 46, execution_plan_id: 139, work_date: '2026-08-28', site_id: 362,
    },
  });
  const page = await loadPage(initial, { id: 7, role: 'operator' });
  let refreshes = 0;
  page.load = () => { refreshes += 1; };
  api.planScheduleDetail = () => Promise.resolve(Object.assign({}, initial, {
    can_continue_execution: false,
    execution_target: null,
    execution_block_reason: '执行任务已闭环或执行权限已变化，请刷新计划详情',
  }));
  page.onGoExecution();
  await flush();
  assert.equal(navigations.length, 0);
  assert.equal(testApp.globalData.executionTarget, null);
  assert.equal(refreshes, 1);
  assert.equal(toasts.at(-1).title, '执行任务已闭环或执行权限已变化，请刷新计划详情');

  const late = deferred();
  page._alive = true;
  page._executionNavigating = false;
  page.setData({ canExecute: true, detail: initial });
  api.planScheduleDetail = () => late.promise;
  page.onGoExecution();
  page.onHide();
  late.resolve(initial);
  await flush();
  assert.equal(navigations.length, 0);
  assert.equal(testApp.globalData.executionTarget, null);
});

test('completed plans stay non-executable', async () => {
  const completed = await loadPage(detailResponse({
    execution_completed: true,
    execution_status: 'completed',
    can_continue_execution: false,
    execution_target: null,
  }), { id: 7, role: 'operator' });
  assert.equal(completed.data.canExecute, false);

});

test('cancel sheet stays above the fixed footer', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  const style = fs.readFileSync(stylePath, 'utf8');
  assert.match(template, /class="sheet-mask"\s+wx:if="\{\{cancelSheet\.open\}\}"/);
  assert.match(template, /class="sheet"\s+wx:if="\{\{cancelSheet\.open\}\}"/);
  const footerZ = Number(style.match(/\.bottom-bar\s*\{[\s\S]*?z-index:\s*(\d+)/)[1]);
  const maskZ = Number(style.match(/\.sheet-mask\s*\{[\s\S]*?z-index:\s*(\d+)/)[1]);
  const sheetZ = Number(style.match(/\.sheet\s*\{[\s\S]*?z-index:\s*(\d+)/)[1]);
  assert.ok(maskZ > footerZ, 'the cancellation mask must cover the fixed footer');
  assert.ok(sheetZ > maskZ, 'the cancellation sheet must stay above its mask');
});

test('fixed footer keeps one, two and three actions horizontal within 360 and 375px', () => {
  const style = fs.readFileSync(stylePath, 'utf8');
  const footer = style.match(/\.bottom-bar\s*\{([\s\S]*?)\}/)[1];
  const primary = style.match(/\.btn-primary-main\s*\{([\s\S]*?)\}/)[1];
  const secondary = style.match(/\.btn-secondary-outline\s*\{([\s\S]*?)\}/)[1];

  assert.match(footer, /display:\s*flex/);
  assert.doesNotMatch(footer, /flex-wrap:\s*(?:wrap|wrap-reverse)/);
  assert.match(primary, /flex:\s*1\s+1\s+0/);
  assert.match(primary, /min-width:\s*0/);
  assert.match(primary, /margin:\s*0/);
  assert.match(primary, /padding:\s*0/);
  assert.match(primary, /white-space:\s*nowrap/);
  assert.match(secondary, /width:\s*180rpx/);
  assert.match(secondary, /flex:\s*0\s+1\s+180rpx/);
  assert.match(secondary, /min-width:\s*0/);
  assert.match(secondary, /margin:\s*0/);
  assert.match(secondary, /white-space:\s*nowrap/);

  for (const viewportRpx of [720, 750]) {
    const innerWidth = viewportRpx - 64;
    for (const actionCount of [1, 2, 3]) {
      const secondaryCount = Math.max(0, actionCount - 1);
      const gaps = Math.max(0, actionCount - 1) * 20;
      const preferredSecondaryWidth = secondaryCount * 180;
      const primaryWidth = innerWidth - gaps - preferredSecondaryWidth;
      assert.ok(primaryWidth >= 192,
        `${viewportRpx}rpx with ${actionCount} actions keeps the longest primary label on one line`);
      assert.ok(gaps + preferredSecondaryWidth + primaryWidth <= innerWidth,
        `${viewportRpx}rpx with ${actionCount} actions stays inside the footer`);
    }
  }
});

test('mask, close icon and back action share submitting close feedback without side effects', async () => {
  let writes = 0;
  api.cancelPlanSchedule = () => {
    writes += 1;
    return Promise.resolve({});
  };
  const page = await loadPage(
    detailResponse({ user_id: 7, status: 'approved', execution_completed: true }),
    { id: 7, role: 'operator' }
  );
  page.onOpenCancel();
  page.onCancelReasonInput({ detail: { value: '现场条件变化，取消本次计划' } });
  page.data.cancelSheet.submitting = true;
  const template = fs.readFileSync(templatePath, 'utf8');
  assert.equal((template.match(/bindtap="onCloseCancel"/g) || []).length, 3);

  for (const source of ['mask', 'close icon', 'back action']) {
    page.onCloseCancel();
    assert.equal(page.data.cancelSheet.open, true, source + ' must not close an active request');
    assert.equal(page.data.cancelSheet.reason, '现场条件变化，取消本次计划');
    assert.equal(toasts.at(-1).title, '取消请求正在处理中，请稍候');
  }
  assert.equal(writes, 0);
});

test('cancel page behavior reports stale state and preserves retry input with one write', async () => {
  const page = await loadPage(
    detailResponse({ user_id: 7, status: 'approved', execution_completed: true }),
    { id: 7, role: 'operator' }
  );
  page.data.canCancel = false;
  page.onOpenCancel();
  assert.equal(page.data.cancelSheet.open, false);
  assert.equal(toasts.at(-1).title, '计划状态或权限已变化，请刷新后重试');

  page.data.canCancel = true;
  page.data.cancelSheet.submitting = true;
  page.onOpenCancel();
  assert.equal(page.data.cancelSheet.open, false);
  assert.equal(toasts.at(-1).title, '取消请求正在处理中，请稍候');

  page.data.cancelSheet.submitting = false;
  page.onOpenCancel();
  page.data.canCancel = false;
  page.onCancelReasonInput({ detail: { value: '状态变化前填写的原因' } });
  page.onConfirmCancel();
  assert.equal(page.data.cancelSheet.error, '计划状态或权限已变化，请刷新后重试');

  page.data.canCancel = true;
  page.onCancelReasonInput({ detail: { value: '   ' } });
  page.onConfirmCancel();
  assert.equal(page.data.cancelSheet.error, '请填写取消原因');

  let writes = 0;
  api.cancelPlanSchedule = () => {
    writes += 1;
    return Promise.reject({ error: '计划版本已变化，请刷新后重试' });
  };
  page.onCancelReasonInput({ detail: { value: '现场条件变化，取消本次计划' } });
  page.onConfirmCancel();
  page.onConfirmCancel();
  await flush();
  await flush();
  assert.equal(writes, 1);
  assert.equal(page.data.cancelSheet.open, true);
  assert.equal(page.data.cancelSheet.reason, '现场条件变化，取消本次计划');
  assert.equal(page.data.cancelSheet.error, '计划版本已变化，请刷新后重试');
  assert.equal(page.data.cancelSheet.submitting, false);
  assert.ok(toasts.some(item => item.title === '取消请求正在处理中，请稍候'));

  api.cancelPlanSchedule = () => {
    writes += 1;
    return Promise.resolve({});
  };
  page.onConfirmCancel();
  await flush();
  await flush();
  assert.equal(writes, 2, 'the preserved input can be retried once after failure');
  assert.equal(page.data.cancelSheet.open, false);
  assert.equal(page.data.cancelSheet.reason, '');
});

test('plan cancellation reason trims, enforces 500 chars, and locks concurrent actions', async () => {
  assert.deepEqual(normalizePlanCancelReason('  路线错误  '), { value: '路线错误', error: '' });
  assert.equal(normalizePlanCancelReason('   ').error, '请填写取消原因');
  assert.equal(normalizePlanCancelReason('x'.repeat(501)).error, '取消原因不能超过500字');
  assert.equal(normalizePlanCancelReason('x'.repeat(500)).value.length, 500);

  let calls = 0;
  let release;
  const owner = {};
  const first = startPlanCancellation(owner, () => {
    calls += 1;
    return new Promise(resolve => { release = resolve; });
  });
  const duplicate = startPlanCancellation(owner, () => { calls += 1; });
  assert.equal(first.started, true);
  assert.equal(duplicate.started, false);
  assert.equal(first.promise, duplicate.promise);
  assert.equal(calls, 0);
  await Promise.resolve();
  assert.equal(calls, 1);
  release({ success: true });
  await first.promise;
  const retried = startPlanCancellation(owner, () => { calls += 1; return 'retried'; });
  assert.equal(retried.started, true);
  assert.equal(await retried.promise, 'retried');
  assert.equal(calls, 2);
});

test('detail cancellation source preserves retry context and package labels keep legacy fallback', () => {
  const page = fs.readFileSync(pagePath, 'utf8');
  const template = fs.readFileSync(templatePath, 'utf8');
  const apiSource = fs.readFileSync(apiPath, 'utf8');
  const inspectionTemplate = fs.readFileSync(inspectionTemplatePath, 'utf8');
  assert.match(apiSource, /cancelPlanSchedule:\s*\(id, reason, version\)[\s\S]*\/cancel'[\s\S]*reason:\s*String\(reason[\s\S]*version[\s\S]*queue:\s*false/);
  assert.match(page, /canCancel:\s*res\.can_cancel === true/);
  assert.match(page, /startPlanCancellation\(this,[\s\S]*api\.cancelPlanSchedule/);
  assert.match(page, /'cancelSheet\.error':\s*\(err && \(err\.error \|\| err\.message\)\)/);
  assert.doesNotMatch(page, /catch[\s\S]{0,240}cancelSheet\.reason'\s*:\s*''/);
  assert.match(template, /maxlength="500"/);
  assert.match(template, /placeholder="请填写取消原因（必填，最多500字）"/);
  assert.match(template, /loading="\{\{cancelSheet\.submitting\}\}"/);
  assert.match(template, /提交审批/);
  assert.match(page, /开始修改计划/);
  assert.match(inspectionTemplate, /currentPackage\.plan_display_name/);
  assert.equal(maps.PLAN_SCHEDULE_STATUS.cancelled, '已取消');
  assert.equal(maps.PLAN_SCHEDULE_STATUS_CLS.cancelled, 'gray');
});

test.after(() => {
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});
