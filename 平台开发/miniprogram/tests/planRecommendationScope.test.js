const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const {
  filterPlans,
  planDisplayName,
  planStatusInfo,
  statusNoteInfo,
  projectFavoritePlans,
  projectPlanCards
} = require('../utils/planViewModel.js');
const api = require('../services/api.js');

const app = { globalData: { token: 'token' } };
let definition;
global.getApp = () => app;
global.Page = page => { definition = page; };
const pagePath = require.resolve('../pages/plan/plan.js');
delete require.cache[pagePath];
require(pagePath);
delete global.getApp;
delete global.Page;

function setPath(target, key, value) {
  const parts = key.split('.');
  let parent = target;
  parts.slice(0, -1).forEach(part => { parent = parent[part]; });
  parent[parts[parts.length - 1]] = value;
}

function pageInstance(overrides) {
  const page = Object.assign({}, definition, {
    data: Object.assign({}, JSON.parse(JSON.stringify(definition.data)), overrides || {}),
    setDataCalls: 0
  });
  page.setData = updates => {
    page.setDataCalls += 1;
    Object.entries(updates).forEach(([key, value]) => setPath(page.data, key, value));
  };
  page.onLoad();
  return page;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

function installWx(user) {
  const routes = [];
  const toasts = [];
  global.wx = {
    getStorageSync(key) { return key === 'user' ? (user || {}) : ''; },
    navigateTo(options) { routes.push(options); },
    showModal(options) { options.success({ confirm: true }); },
    showToast(options) { toasts.push(options); },
    reLaunch() {},
    stopPullDownRefresh() {}
  };
  return { routes, toasts };
}

function releaseRoute(routes) {
  routes[routes.length - 1].complete();
}

function planRows() {
  return [
    { id: 1, user_id: 7, user_name: '万松', status: 'draft', schedule_type: 'weekly', period_start: '2026-08-25', period_end: '2026-08-31', site_count: 2 },
    { id: 2, user_id: 7, user_name: '万松', status: 'approved', execution_status: 'partial', schedule_type: 'monthly', period_start: '2026-08-01', period_end: '2026-08-31', site_count: 3 },
    { id: 3, user_id: 8, user_name: '林青', status: 'archived', execution_status: 'pending', schedule_type: 'unknown_type', period_start: '2026-07-01', period_end: '2026-07-31' }
  ];
}

test('PlanCard and FavoritePlan centralize safe business projections', () => {
  assert.deepEqual(planStatusInfo({ status: 'archived', execution_status: 'completed' }), { label: '已归档', tone: 'neutral', group: 'done' });
  assert.deepEqual(planStatusInfo({ status: 'approved', execution_status: 'completed' }), { label: '已完成', tone: 'success', group: 'done' });
  assert.deepEqual(planStatusInfo({ status: 'approved', execution_status: 'partial', execution_completed: true }), { label: '执行中', tone: 'info', group: 'active' });
  assert.equal(planDisplayName({ user_name: '万松', period_start: '2026-08-25', period_end: '2026-08-31' }, '周检'), '万松·周检-20260825~20260831');

  const cards = projectPlanCards(planRows());
  assert.equal(cards[0].primaryTarget.kind, 'edit');
  assert.equal(cards[0].secondaryAction, 'delete_draft');
  assert.equal(cards[1].statusTone, 'info');
  assert.equal(cards[2].typeLabel, '巡检');
  assert.equal(cards[2].siteCount, null);
  assert.equal(cards[2].title.includes('unknown_type'), false);
  assert.deepEqual(filterPlans(cards, 'done').map(item => item.id), [3]);

  assert.deepEqual(projectFavoritePlans([{ id: 4, name: '', schedule_type: 'legacy' }]), [{
    id: 4, name: '常用计划', typeLabel: '巡检', suggestedPeriodStart: null
  }]);
});

test('PlanCard status notes cover the complete safe status matrix', () => {
  const cases = [
    [{ status: 'draft' }, { text: '尚未提交审批', tone: 'neutral' }],
    [{ status: 'submitted' }, { text: '等待管理员审批', tone: 'info' }],
    [{ status: 'rejected', reject_reason: '站点范围需调整' }, { text: '退回原因：站点范围需调整', tone: 'warning' }],
    [{ status: 'rejected' }, { text: '退回原因待补充，请联系管理员', tone: 'warning' }],
    [{ status: 'modifying' }, { text: '计划变更编辑中', tone: 'info' }],
    [{ status: 'change_submitted', execution_status: 'change_pending' }, { text: '等待变更审批', tone: 'info' }],
    [{ status: 'approved', execution_status: 'change_pending' }, { text: '等待变更审批', tone: 'info' }],
    [{ status: 'approved', execution_status: 'pending' }, { text: '等待现场执行', tone: 'info' }],
    [{ status: 'approved', execution_status: 'partial', execution_summary: {
      available: true, total_tasks: 3, completed_tasks: 1
    } }, { text: '1/3个站点任务已完成', tone: 'info' }],
    [{ status: 'approved', execution_status: 'partial', execution_summary: {
      available: true, total_tasks: null, completed_tasks: 0
    } }, { text: '现场执行中', tone: 'info' }],
    [{ status: 'approved', execution_status: 'rework', execution_summary: {
      available: true, supplement_required_items: 2
    } }, { text: '2项需补拍', tone: 'warning' }],
    [{ status: 'approved', execution_status: 'rework', execution_summary: {
      available: true, supplement_required_items: 0
    } }, { text: '请按审核意见完成整改', tone: 'warning' }],
    [{ status: 'approved', execution_status: 'rework', execution_summary: {} }, { text: '请按审核意见完成整改', tone: 'warning' }],
    [{ status: 'approved', execution_status: 'completed' }, { text: '全部站点任务已完成', tone: 'success' }],
    [{ status: 'approved' }, { text: '等待现场执行', tone: 'info' }],
    [{ status: 'cancelled', cancel_reason: '任务窗口已失效' }, { text: '取消原因：任务窗口已失效', tone: 'neutral' }],
    [{ status: 'cancelled' }, { text: '计划已取消，不再安排执行', tone: 'neutral' }],
    [{ status: 'archived' }, { text: '已归档，可在历史记录中查看', tone: 'neutral' }],
    [{ status: 'future_state', execution_status: 'wire_value' }, { text: '状态信息暂不可用', tone: 'neutral' }],
    [{}, { text: '状态信息暂不可用', tone: 'neutral' }]
  ];
  cases.forEach(([input, expected]) => assert.deepEqual(statusNoteInfo(input), expected, JSON.stringify(input)));

  const card = projectPlanCards([{ id: 9, status: 'approved', execution_status: 'partial', execution_summary: {
    available: true, total_tasks: 2, completed_tasks: 1
  } }])[0];
  assert.equal(card.statusNote, '1/2个站点任务已完成');
  assert.equal(card.statusNoteTone, 'info');
});

test('main list keeps old data through refresh failure, retry and out-of-order scope responses', async () => {
  const original = api.planSchedules;
  installWx();
  try {
    const page = pageInstance({ canSwitchTeam: true });
    const initial = deferred();
    api.planSchedules = () => initial.promise;
    page.loadMain();
    initial.reject({ error: '网络不可用' });
    await flush();
    assert.equal(page.data.mainState, 'blocking_error');
    assert.equal(page.data.mainError, '网络不可用');

    const loaded = deferred();
    api.planSchedules = () => loaded.promise;
    page.onRetryMain();
    loaded.resolve(planRows());
    await flush();
    const existingPlans = page.data.allPlans;
    assert.equal(page.data.mainState, 'ready');

    const refresh = deferred();
    api.planSchedules = () => refresh.promise;
    page.loadMain(true);
    refresh.reject({ message: '弱网' });
    await flush();
    assert.equal(page.data.mainState, 'refresh_error');
    assert.equal(page.data.allPlans, existingPlans);

    const team = deferred();
    const mineRefresh = deferred();
    let call = 0;
    api.planSchedules = () => (++call === 1 ? team.promise : mineRefresh.promise);
    page.onToggleScope();
    page.loadMain(true);
    team.resolve([{ id: 12, status: 'approved', schedule_type: 'weekly' }]);
    await flush();
    mineRefresh.resolve([{ id: 11, status: 'approved', schedule_type: 'weekly' }]);
    await flush();
    assert.equal(page.data.scope, 'team');
    assert.deepEqual(page.data.allPlans.map(item => item.id), [12]);
  } finally {
    api.planSchedules = original;
    delete global.wx;
  }
});

test('favorites preserve prior results and ignore stale or unloaded responses', async () => {
  const original = api.planScheduleFavorites;
  try {
    const page = pageInstance({ canUseFavorites: true });
    page.data.favorites = [{ id: 3, name: '旧收藏' }];
    api.planScheduleFavorites = () => Promise.reject(new Error('offline'));
    await page.loadFavorites(true);
    assert.equal(page.data.favoritesState, 'unavailable');
    assert.deepEqual(page.data.favorites, [{ id: 3, name: '旧收藏' }]);

    const oldFavorites = deferred();
    const newFavorites = deferred();
    let call = 0;
    api.planScheduleFavorites = () => (++call === 1 ? oldFavorites.promise : newFavorites.promise);
    page.loadFavorites();
    page.loadFavorites();
    newFavorites.resolve([{ id: 8, name: '新收藏', schedule_type: 'weekly' }]);
    await flush();
    oldFavorites.resolve([{ id: 9, name: '旧收藏', schedule_type: 'weekly' }]);
    await flush();
    assert.equal(page.data.favorites[0].id, 8);

    const late = deferred();
    api.planScheduleFavorites = () => late.promise;
    page.loadFavorites();
    const writes = page.setDataCalls;
    page.onUnload();
    late.resolve([{ id: 10, name: '晚到收藏', schedule_type: 'weekly' }]);
    await flush();
    assert.equal(page.setDataCalls, writes);
  } finally {
    api.planScheduleFavorites = original;
  }
});

test('first favorite load reports loading rather than a false empty state', () => {
  const { toasts } = installWx();
  try {
    const page = pageInstance({ canUseFavorites: true, favoritesState: 'loading', favorites: [] });
    assert.equal(page.onOpenFavorites(), false);
    assert.equal(page.data.favoriteSheet.open, false);
    assert.equal(toasts.at(-1).title, '常用计划加载中，请稍候');

    page.data.favoritesState = 'ready';
    assert.equal(page.onOpenFavorites(), false);
    assert.equal(toasts.at(-1).title, '暂无常用计划');
  } finally {
    delete global.wx;
  }
});

test('reviewer can switch plan scopes without loading operator-only favorites', async () => {
  const originals = {
    schedules: api.planSchedules,
    favorites: api.planScheduleFavorites
  };
  const { routes } = installWx({ roles: [], role: 'reviewer' });
  try {
    let scheduleCalls = 0;
    api.planSchedules = () => { scheduleCalls += 1; return Promise.resolve([]); };
    api.planScheduleFavorites = () => Promise.resolve([]);
    const page = pageInstance();
    page.onShow();
    await flush();
    assert.equal(page.data.canSwitchTeam, true);
    assert.equal(page.data.favoritesVisible, false);
    page.onToggleScope();
    await flush();
    assert.equal(scheduleCalls, 2);
    assert.equal(page.data.scope, 'team');
    assert.equal(routes.length, 0);
  } finally {
    api.planSchedules = originals.schedules;
    api.planScheduleFavorites = originals.favorites;
    delete global.wx;
  }
});

test('all page actions single-flight, release navigation failures, and retain failed write context', async () => {
  const originals = {
    deletePlan: api.deletePlanSchedule,
    createFavorite: api.createDraftFromPlanScheduleFavorite,
    deleteFavorite: api.deletePlanScheduleFavorite,
    schedules: api.planSchedules
  };
  const { routes } = installWx();
  try {
    const page = pageInstance({
      canUseFavorites: true,
      allPlans: projectPlanCards(planRows()),
      favorites: [{ id: 4, name: '周检模板', suggestedPeriodStart: '2026-08-25' }],
      favoriteSheet: { open: true, selectedId: 4, selectedIndex: 0, periodStart: '2026-08-25', sheetState: 'idle', errorMessage: '' }
    });
    api.planSchedules = () => Promise.resolve(planRows());

    page.onNewPlan();
    page.onNewPlan();
    assert.equal(routes.length, 1);
    routes[0].fail();
    page.onNewPlan();
    assert.equal(routes.length, 2, 'a failed navigation releases the gate');
    releaseRoute(routes);

    page.onItemTap({ currentTarget: { dataset: { id: 1 } } });
    assert.equal(routes.at(-1).url, '/pages/plan-edit/plan-edit?id=1');
    releaseRoute(routes);
    page.onItemTap({ currentTarget: { dataset: { id: 2 } } });
    assert.equal(routes.at(-1).url, '/pages/plan-detail/plan-detail?id=2');
    releaseRoute(routes);

    const deleteDraft = deferred();
    let deleteDraftCalls = 0;
    api.deletePlanSchedule = () => { deleteDraftCalls += 1; return deleteDraft.promise; };
    page.onDeleteDraft(1);
    page.onDeleteDraft(1);
    assert.equal(deleteDraftCalls, 1);
    deleteDraft.reject({ error: '仍在使用' });
    await flush();
    assert.equal(page.data.allPlans[0].id, 1, 'failed draft deletion retains the list context');

    const createFavorite = deferred();
    let favoriteCalls = 0;
    api.createDraftFromPlanScheduleFavorite = () => { favoriteCalls += 1; return createFavorite.promise; };
    page.onCreateFavoriteDraft();
    page.onCreateFavoriteDraft();
    assert.equal(favoriteCalls, 1);
    createFavorite.reject({ message: '模板已失效' });
    await flush();
    assert.equal(page.data.favoriteSheet.open, true);
    assert.equal(page.data.favoriteSheet.selectedId, 4);
    assert.equal(page.data.favoriteSheet.sheetState, 'error');

    page.data.favoriteSheet.sheetState = 'idle';
    const deleteFavorite = deferred();
    let deleteFavoriteCalls = 0;
    api.deletePlanScheduleFavorite = () => { deleteFavoriteCalls += 1; return deleteFavorite.promise; };
    page.onDeleteFavorite();
    page.onDeleteFavorite();
    assert.equal(deleteFavoriteCalls, 1);
    deleteFavorite.reject({ error: '删除失败' });
    await flush();
    assert.equal(page.data.favoriteSheet.open, true);
    assert.equal(page.data.favoriteSheet.sheetState, 'error');

  } finally {
    api.deletePlanSchedule = originals.deletePlan;
    api.createDraftFromPlanScheduleFavorite = originals.createFavorite;
    api.deletePlanScheduleFavorite = originals.deleteFavorite;
    api.planSchedules = originals.schedules;
    delete global.wx;
  }
});

test('plan page keeps the approved new-plan, filter, card and favorite bindings', () => {
  const view = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.wxml'), 'utf8');
  const page = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.js'), 'utf8');
  assert.match(view, /visiblePlans[\s\S]*item\.typeLabel[\s\S]*item\.statusLabel/);
  assert.match(view, /bindtap="onFilterTap"[\s\S]*bindtap="onOpenFavorites"[\s\S]*bindtap="onNewPlan"[\s\S]*bindtap="onItemTap"/);
  assert.match(page, /projectPlanCards\(rows\)/);
  assert.doesNotMatch(page, /item\.schedule_type|item\.anomaly_type/);
});
