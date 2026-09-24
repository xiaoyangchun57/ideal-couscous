const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const {
  errorMessage,
  formatHomeDate,
  homeActions,
  homePackage,
  projectHome,
  projectIdentity,
  projectReagentSummary,
  projectReview,
  projectStationSummary,
  projectUnread
} = require('../utils/homeTaskState.js');
const { workorderCn, linkedWorkorderCn, metricCn } = require('../services/maps.js');

test('manual report workorders and alerts use the same Chinese business label', () => {
  const mapped = workorderCn({
    source: 'manual_report', title: '【人工上报】感官异常', display_title: '感官异常'
  });
  assert.equal(mapped.source_cn, '人工上报');
  assert.equal(mapped.title, '【人工上报】感官异常', 'the raw audit title stays intact');
  assert.equal(mapped.display_title, '感官异常');
  assert.equal(linkedWorkorderCn({ title: '[自动] 原始标题' }).display_title, '工单事项',
    'the client never derives a display title from an audit title');
  assert.equal(metricCn('manual_report'), '人工上报');
});

test('all miniprogram workorder surfaces consume the server display title', () => {
  const workorderView = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.wxml'), 'utf8');
  const inspectionView = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
  const planDetailView = fs.readFileSync(path.join(__dirname, '../pages/plan-detail/plan-detail.wxml'), 'utf8');
  assert.match(workorderView, /class="wo-title">\{\{item\.display_title\}\}/);
  assert.match(workorderView, /class="wos-title">\{\{sheet\.item\.display_title\}\}/);
  assert.match(inspectionView, /class="ip-linked-title">\{\{item\.display_title\}\}/);
  assert.match(planDetailView, /wx:for="\{\{linkedWorkorders\}\}"[^>]*>\{\{wo\.display_title\}\}/);
  assert.doesNotMatch(workorderView, /class="(?:wo-title|wos-title)">\{\{(?:item|sheet\.item)\.title\}\}/);
});

test('today actions expose at most one exact workorder and one independent alert', () => {
  const actions = homeActions(
    [{ order_no: 'WO-1', status: 'pending', title: '[自动] 泵站处置', display_title: '泵站处置' }, { order_no: 'WO-2' }],
    [{ id: 8, level: 'red', metric: 'ph' }, { id: 9 }]
  );
  assert.equal(actions.length, 2);
  assert.equal(actions[0].kind, 'workorder');
  assert.equal(actions[0].target.objectId, 'WO-1');
  assert.equal(actions[0].statusLabel, '待受理');
  assert.equal(actions[0].statusTone, 'info');
  assert.equal(actions[0].title, '泵站处置');
  assert.equal(actions[1].kind, 'alert');
  assert.equal(actions[1].target.objectId, 8);
  assert.equal(actions[1].statusCls, 'red');
  assert.equal(actions[1].statusLabel, 'I级');
  assert.equal(actions[1].statusTone, 'danger');
  assert.deepEqual(homeActions([], []), []);

  const levels = ['red', 'orange', 'yellow', 'blue'].map((level, index) => homeActions([], [{ id: index + 1, level }])[0].statusLabel);
  assert.deepEqual(levels, ['I级', 'II级', 'III级', 'IV级']);
  const alertTones = ['red', 'orange', 'yellow', 'blue'].map((level, index) => homeActions([], [{ id: index + 1, level }])[0].statusTone);
  assert.deepEqual(alertTones, ['danger', 'warning', 'warning', 'info']);
  const workorderTones = ['pending', 'in_progress', 'reviewing', 'closed', 'unknown'].map(status => homeActions([{ order_no: status, status }], [])[0].statusTone);
  assert.deepEqual(workorderTones, ['info', 'warning', 'warning', 'success', 'neutral']);
});

test('today package has an explicit empty state and no future-plan projection', () => {
  assert.deepEqual(homePackage(null), {
    hasPlan: false,
    siteNames: [],
    progress: null,
    resources: {
      vehicleAssigned: false, departureConfirmed: false, departurePendingCount: null,
      partsCount: null, linkedWorkorders: null,
      vehicleText: '车辆待安排', vehicleStatusCls: 'orange',
      departureText: '出发资源待确认', departureStatusCls: 'orange'
    }
  });
  const state = projectHome({ summary: {}, upcoming: [{ schedule_id: 99 }], work_package: { has_plan: false } });
  assert.equal(state.workPackage.hasPlan, false);
  assert.equal(Object.hasOwn(state, 'upcoming'), false);
});

test('home overview projects mine-scope monitoring states and authoritative reagent concerns', () => {
  assert.deepEqual(projectStationSummary({ summary: {
    total: 8, normal: 3, attention: 2, not_connected: 1,
    awaiting_first_frame: 1, raw_received_config_pending: 1,
  } }), { total: 8, normal: 3, attention: 2, unavailable: 3 });

  assert.deepEqual(projectReagentSummary({ concern_count: 0, items: [] }), {
    concernCount: 0, display: '暂无需处理', items: [], remainingCount: 0,
  });
  const projected = projectReagentSummary({ concern_count: 3, items: [
    { id: 1, site_id: 10, site_name: '南昌青云水厂', reagent_name: '余氯试剂', status: '临期' },
    { id: 2, site_id: 10, site_name: '南昌青云水厂', reagent_name: 'pH 标液', status: '低余量' },
    { id: 3, site_id: 11, site_name: '昌南站', reagent_name: '氨氮试剂', status: '已过期' },
  ] });
  assert.equal(projected.display, '3项需关注');
  assert.deepEqual(projected.items.map(item => item.text), [
    '南昌青云水厂 · 余氯试剂 · 临期',
    '南昌青云水厂 · pH 标液 · 低余量',
  ]);
  assert.equal(projected.remainingCount, 1);
});

test('rework sites remain discoverable as independent today actions without a work package', () => {
  const state = projectHome({
    summary: { rework_items: 1 },
    sites: [{ site_id: 362, site_name: '万松站', rework_items: 1, target_plan_id: 136, target_item_id: 7090 }],
    work_package: { has_plan: false }
  });
  assert.equal(state.workPackage.hasPlan, false);
  assert.equal(state.actions.length, 1);
  assert.deepEqual(state.actions[0], {
    key: 'inspection-rework:362', kind: 'inspection_rework', typeLabel: '巡检整改',
    statusLabel: '需整改', statusTone: 'warning', statusCls: 'orange', title: '万松站',
    meta: ['1项待整改'],
    target: { kind: 'inspection_rework', executionPlanId: 136, siteId: 362, itemId: 7090, reworkOnly: true, source: 'home_rework' }
  });
  const multiple = projectHome({
    sites: [
      { site_id: 1, site_name: '甲站', rework_items: 1, target_plan_id: 10, target_item_id: 100 },
      { site_id: 2, site_name: '乙站', rework_items: 2, target_plan_id: null, target_item_id: null }
    ], work_package: { has_plan: false }
  });
  assert.equal(multiple.actions.length, 2);
  assert.equal(multiple.actions[1].target.executionPlanId, null);
});

test('today package uses only authoritative item progress and site names', () => {
  const workPackage = {
    has_plan: true,
    sites: [{ name: '青云' }, { name: ' 扬子洲 ' }, { site_id: 9 }],
  };
  const projected = homePackage(workPackage, { completed_items: 3, total_items: 5 });
  assert.equal(projected.progress.label, '3/5 项');
  assert.equal(projected.progress.percent, 60);
  assert.deepEqual(projected.siteNames, ['青云', '扬子洲']);

  const zero = homePackage(workPackage, { completed_items: 0, total_items: 0 });
  assert.equal(zero.progress.label, '0/0 项');
  assert.equal(zero.progress.percent, 0);
  assert.equal(homePackage(workPackage, { completed_items: 0 }).progress, null);
  assert.equal(homePackage(workPackage, { total_items: 5 }).progress, null);
  assert.equal(homePackage(workPackage, { completed_items: null, total_items: null }).progress, null);
  assert.equal(homePackage(workPackage, { completed_items: 8, total_items: 5 }).progress.percent, 100);

  const resources = homePackage({ has_plan: true, readiness: {
    vehicle_assigned: true, departure_confirmed: false, departure_pending_count: 2,
    parts_count: null, linked_workorders: 0
  } }, {}).resources;
  assert.equal(resources.vehicleText, '车辆已安排');
  assert.equal(resources.departureText, '2项出发资源待确认');
  assert.equal(resources.vehicleAssigned, true);
  assert.equal(resources.departureConfirmed, false);
  assert.equal(resources.departurePendingCount, 2);
  assert.equal(resources.partsCount, null);
  assert.equal(resources.linkedWorkorders, 0);
  const missingCounts = homePackage({ has_plan: true, readiness: { departure_confirmed: false } }, {}).resources;
  assert.equal(missingCounts.departurePendingCount, null);
  assert.equal(missingCounts.departureText, '出发资源待确认');
  assert.doesNotMatch(missingCounts.departureText, /0项/);

  for (const field of ['currentStationName', 'nextStationName', 'doneStations', 'pendingStations', 'planTypeText']) {
    assert.equal(Object.hasOwn(projected, field), false);
  }
});

test('identity, auxiliary counts and errors use centralized non-fabricating projections', () => {
  assert.deepEqual(projectUnread({ count: 120 }), { notificationsState: 'ready', unreadCount: 120, unreadDisplay: '99+' });
  assert.deepEqual(projectUnread({ count: 0 }), { notificationsState: 'ready', unreadCount: 0, unreadDisplay: '' });
  assert.deepEqual(projectUnread({ count: null }), { notificationsState: 'unavailable', unreadCount: null, unreadDisplay: '' });
  assert.deepEqual(projectReview(null), { reviewState: 'unavailable', reviewCount: null, reviewDisplay: '数量暂不可用' });
  assert.equal(projectReview([]).reviewDisplay, '暂无待审核');
  assert.equal(errorMessage({ error: '服务拒绝', message: '网络错误' }, '重试'), '服务拒绝');
  assert.equal(errorMessage({ message: '网络错误' }, '重试'), '网络错误');
  assert.equal(formatHomeDate('2026-08-26'), '2026年8月26日 周三');
  const identity = projectIdentity({ real_name: '万松', roles: [], role: 'reviewer' }, '2026-08-26', 9);
  assert.equal(identity.displayName, '万松');
  assert.equal(identity.reviewDisplay, '加载中');
  assert.equal(identity.reviewVisible, true);
});

test('homepage keeps the approved structure while binding only projected status fields', () => {
  const page = fs.readFileSync(path.join(__dirname, '../pages/index/index.js'), 'utf8');
  const view = fs.readFileSync(path.join(__dirname, '../pages/index/index.wxml'), 'utf8');
  assert.match(view, /今日任务[\s\S]*今日行动[\s\S]*今日作业包/);
  assert.match(view, /workPackage\.resources\.vehicleText/);
  assert.match(view, /workPackage\.resources\.departureText/);
  assert.doesNotMatch(view, /vehicleAssigned|departurePendingCount/);
  assert.doesNotMatch(page, /res\.workorders|res\.alerts|res\.work_package|res\.summary|buildMainViewModel/);
  assert.match(page, /projectHome\(res\)/);
  assert.doesNotMatch(view, /wx:if="\{\{false\}\}"|我的计划|即将执行|待办站点|今日进度/);
});

const apiPath = require.resolve('../services/api.js');
const pagePath = require.resolve('../pages/index/index.js');
const api = require(apiPath);
const app = { globalData: { token: 'token' } };
const navigationCalls = [];
const toastCalls = [];

global.getApp = () => app;
global.Page = definition => { global.homePageDefinition = definition; };
global.wx = {
  getStorageSync(key) {
    return key === 'user' ? { real_name: '万松', roles: [], role: 'reviewer' } : '';
  },
  navigateTo(options) { navigationCalls.push({ method: 'navigateTo', options }); },
  switchTab(options) { navigationCalls.push({ method: 'switchTab', options }); },
  showToast(options) { toastCalls.push(options); },
  reLaunch() {},
  stopPullDownRefresh() {}
};
delete require.cache[pagePath];
const { formatHomeDate: pageFormatHomeDate } = require(pagePath);
const pageDefinition = global.homePageDefinition;

function pageInstance() {
  const page = Object.assign({}, pageDefinition, {
    data: JSON.parse(JSON.stringify(pageDefinition.data)),
    setDataCalls: 0
  });
  page.setData = updates => {
    page.setDataCalls += 1;
    Object.assign(page.data, updates);
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

function homeResponse(title, completed = 1, total = 2) {
  return {
    workorders: [{ order_no: `WO-${title}`, status: 'pending', title: `[自动] ${title}`, display_title: title }],
    alerts: [{ id: 9, level: 'orange', site_name: '青云', metric: 'ph' }],
    summary: { completed_items: completed, total_items: total },
    work_package: { has_plan: true, sites: [{ name: '青云' }], readiness: {
      vehicle_assigned: false, departure_confirmed: true, departure_pending_count: 0,
      parts_count: 0, linked_workorders: 0
    } }
  };
}

test('main request distinguishes first failure and preserves old data across refresh retry', async () => {
  const original = api.myToday;
  try {
    const page = pageInstance();
    let request = deferred();
    api.myToday = () => request.promise;
    page.loadMain();
    request.reject({ error: '服务暂不可用' });
    await flush();
    assert.equal(page.data.mainState, 'blocking_error');
    assert.equal(page.data.mainError, '服务暂不可用');

    request = deferred();
    api.myToday = () => request.promise;
    page.loadMain(true);
    request.reject({ message: '首次刷新也失败' });
    await flush();
    assert.equal(page.data.mainState, 'blocking_error');

    request = deferred();
    api.myToday = () => request.promise;
    page.onRetryMain();
    request.resolve(homeResponse('旧任务'));
    await flush();
    const oldActions = page.data.actions;
    assert.equal(page.data.mainState, 'ready');

    request = deferred();
    api.myToday = () => request.promise;
    page.loadMain(true);
    assert.equal(page.data.mainState, 'refreshing');
    request.reject({ message: '弱网，请重试' });
    await flush();
    assert.equal(page.data.mainState, 'refresh_error');
    assert.equal(page.data.actions, oldActions);

    request = deferred();
    api.myToday = () => request.promise;
    page.onRetryMain();
    assert.equal(page.data.mainState, 'refreshing');
    assert.equal(page.data.actions, oldActions);
    request.resolve(homeResponse('新任务', 2, 2));
    await flush();
    assert.equal(page.data.mainState, 'ready');
    assert.equal(page.data.actions[0].title, '新任务');
  } finally {
    api.myToday = original;
  }
});

test('station and reagent overview load retry and stale responses are independent', async () => {
  const originals = {
    stationMonitoringSites: api.stationMonitoringSites,
    reagentOverview: api.reagentOverview,
  };
  try {
    const page = pageInstance();
    const oldStations = deferred();
    const newStations = deferred();
    let stationCall = 0;
    api.stationMonitoringSites = options => {
      assert.equal(options.scope, 'mine');
      return (++stationCall === 1 ? oldStations.promise : newStations.promise);
    };
    page.loadStations();
    page.loadStations();
    newStations.resolve({ summary: { total: 2, normal: 1, attention: 1 } });
    await flush();
    oldStations.resolve({ summary: { total: 99, normal: 99, attention: 0 } });
    await flush();
    assert.deepEqual(page.data.stationSummary, { total: 2, normal: 1, attention: 1, unavailable: 0 });

    const existing = page.data.stationSummary;
    api.stationMonitoringSites = () => Promise.reject({ error: '站点服务失败' });
    await page.loadStations(true);
    assert.equal(page.data.stationsState, 'refresh_error');
    assert.equal(page.data.stationSummary, existing);

    api.reagentOverview = () => Promise.resolve({ concern_count: 1, items: [
      { id: 3, site_id: 1, site_name: '青云站', reagent_name: '余氯试剂', status: '临期' },
    ] });
    await page.loadReagents();
    assert.equal(page.data.reagentsState, 'ready');
    assert.equal(page.data.reagentSummary.concernCount, 1);
    assert.equal(page.data.stationSummary, existing, 'reagent success does not replace station state');

    const reagentExisting = page.data.reagentSummary;
    api.reagentOverview = () => Promise.reject({ message: '试剂服务失败' });
    await page.loadReagents(true);
    assert.equal(page.data.reagentsState, 'refresh_error');
    assert.equal(page.data.reagentSummary, reagentExisting);

    const callsBeforeUnload = page.setDataCalls;
    const late = deferred();
    api.reagentOverview = () => late.promise;
    page.loadReagents();
    page.onUnload();
    late.resolve({ concern_count: 0, items: [] });
    await flush();
    assert.equal(page.setDataCalls, callsBeforeUnload + 1,
      'only the loading transition occurs before unload; late data is ignored');
  } finally {
    Object.assign(api, originals);
  }
});

test('each request category ignores stale responses and all requests ignore unload', async () => {
  const originals = { myToday: api.myToday, unreadCount: api.unreadCount, auditPending: api.auditPending };
  try {
    const page = pageInstance();
    const oldMain = deferred();
    const newMain = deferred();
    let mainCall = 0;
    api.myToday = () => (++mainCall === 1 ? oldMain.promise : newMain.promise);
    page.loadMain();
    page.loadMain();
    newMain.resolve(homeResponse('最新'));
    await flush();
    oldMain.resolve(homeResponse('过期'));
    await flush();
    assert.equal(page.data.actions[0].title, '最新');

    const oldUnread = deferred();
    const newUnread = deferred();
    let unreadCall = 0;
    api.unreadCount = () => (++unreadCall === 1 ? oldUnread.promise : newUnread.promise);
    page.loadNotifications();
    page.loadNotifications();
    newUnread.resolve({ count: 7 });
    await flush();
    oldUnread.resolve({ count: 99 });
    await flush();
    assert.equal(page.data.unreadCount, 7);

    const oldReview = deferred();
    const newReview = deferred();
    let reviewCall = 0;
    api.auditPending = () => (++reviewCall === 1 ? oldReview.promise : newReview.promise);
    page.loadReview();
    page.loadReview();
    newReview.resolve([{}]);
    await flush();
    oldReview.resolve([{}, {}, {}]);
    await flush();
    assert.equal(page.data.reviewCount, 1);

    const review = deferred();
    api.auditPending = () => review.promise;
    page.loadReview();
    const callsBeforeUnload = page.setDataCalls;
    page.onUnload();
    review.resolve([{}, {}]);
    await flush();
    assert.equal(page.setDataCalls, callsBeforeUnload);
  } finally {
    Object.assign(api, originals);
  }
});

test('auxiliary failures stay independent and never fabricate zero', async () => {
  const originals = { unreadCount: api.unreadCount, auditPending: api.auditPending };
  try {
    const page = pageInstance();
    assert.equal(page.data.reviewDisplay, '加载中');
    api.unreadCount = () => Promise.reject(new Error('offline'));
    api.auditPending = () => Promise.reject(new Error('offline'));
    await Promise.all([page.loadNotifications(), page.loadReview()]);
    assert.equal(page.data.notificationsState, 'unavailable');
    assert.equal(page.data.unreadCount, null);
    assert.equal(page.data.unreadDisplay, '');
    assert.equal(page.data.reviewState, 'unavailable');
    assert.equal(page.data.reviewCount, null);
    assert.equal(page.data.reviewDisplay, '数量暂不可用');
  } finally {
    Object.assign(api, originals);
  }
});

test('unread refresh failure clears a previously displayed badge', async () => {
  const original = api.unreadCount;
  try {
    const page = pageInstance();
    page.setData({ notificationsState: 'ready', unreadCount: 6, unreadDisplay: '6' });
    api.unreadCount = () => Promise.reject(new Error('offline'));
    await page.loadNotifications(true);
    assert.deepEqual({
      state: page.data.notificationsState,
      count: page.data.unreadCount,
      display: page.data.unreadDisplay
    }, { state: 'unavailable', count: null, display: '' });
  } finally {
    api.unreadCount = original;
  }
});

test('all home navigation is single-flight and failed exact targets are cleared', () => {
  const page = pageInstance();
  navigationCalls.length = 0;
  page.goMessages();
  page.goMessages();
  assert.equal(navigationCalls.length, 1);
  navigationCalls[0].options.complete();

  page.data.actions = [{ target: { kind: 'workorder', objectId: 'WO-7' } }];
  page.onActionTap({ currentTarget: { dataset: { index: 0 } } });
  page.onActionTap({ currentTarget: { dataset: { index: 0 } } });
  assert.equal(navigationCalls.length, 2);
  assert.equal(app.globalData.selWorkorderNo, 'WO-7');
  navigationCalls[1].options.fail();
  assert.equal(app.globalData.selWorkorderNo, null);

  page.data.actions = [{ target: { kind: 'alert', objectId: 12 } }];
  page.onActionTap({ currentTarget: { dataset: { index: 0 } } });
  assert.equal(app.globalData.selAlertId, 12);
  navigationCalls[2].options.fail();
  assert.equal(app.globalData.selAlertId, null);

  const beforeInvalid = navigationCalls.length;
  page.data.actions = [{ target: { kind: 'alert', objectId: null } }];
  page.onActionTap({ currentTarget: { dataset: { index: 0 } } });
  assert.equal(navigationCalls.length, beforeInvalid);

  page.onUnload();
  page.data.actions = [{ target: { kind: 'workorder', objectId: 'WO-AFTER-UNLOAD' } }];
  page.onActionTap({ currentTarget: { dataset: { index: 0 } } });
  assert.notEqual(app.globalData.selWorkorderNo, 'WO-AFTER-UNLOAD');

  const workorderPage = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.js'), 'utf8');
  const alertPage = fs.readFileSync(path.join(__dirname, '../pages/alert/alert.js'), 'utf8');
  assert.match(workorderPage, /selWorkorderNo = null/);
  assert.match(alertPage, /selAlertId = null/);
});

test('all-workorders entry clears stale targets, reports failure, and stays independent from exact cards', () => {
  const page = pageInstance();
  navigationCalls.length = 0;
  toastCalls.length = 0;

  app.globalData.selWorkorderNo = 'WO-STALE';
  assert.equal(page.goWorkorder(), true);
  assert.equal(app.globalData.selWorkorderNo, null);
  assert.equal(navigationCalls.length, 1);
  assert.equal(navigationCalls[0].options.url, '/pages/workorder/workorder');
  assert.equal(page.goWorkorder(), false);
  assert.equal(navigationCalls.length, 1, 'duplicate list taps share one navigation');
  navigationCalls[0].options.fail({ errMsg: 'navigateTo:fail' });
  assert.equal(app.globalData.selWorkorderNo, null, 'failed list navigation never restores a stale exact target');
  assert.deepEqual(toastCalls.at(-1), { title: '打开工单列表失败，请重试', icon: 'none' });

  app.globalData.selWorkorderNo = 'WO-OLDER';
  assert.equal(page.goWorkorder(), true);
  assert.equal(app.globalData.selWorkorderNo, null);
  navigationCalls[1].options.complete();
  assert.equal(app.globalData.selWorkorderNo, null, 'successful list navigation remains in list state');

  page.data.actions = [{ target: { kind: 'workorder', objectId: 'WO-EXACT' } }];
  assert.equal(page.onActionTap({ currentTarget: { dataset: { index: 0 } } }), true);
  assert.equal(app.globalData.selWorkorderNo, 'WO-EXACT');
  assert.equal(navigationCalls[2].options.url, '/pages/workorder/workorder');
  app.globalData.selWorkorderNo = 'WO-NEWER';
  navigationCalls[2].options.fail({ errMsg: 'navigateTo:fail' });
  assert.equal(app.globalData.selWorkorderNo, 'WO-NEWER',
    'an exact navigation failure clears only the target written by that invocation');
});

test('home station summaries use one-time targets and switch to the station tab', () => {
  const page = pageInstance();
  navigationCalls.length = 0;
  toastCalls.length = 0;
  app.globalData.stationHubTarget = null;

  assert.equal(page.goResponsibleSites(), true);
  const stationTarget = app.globalData.stationHubTarget;
  assert.deepEqual(stationTarget, { view: 'stations' });
  assert.equal(navigationCalls[0].method, 'switchTab');
  assert.equal(navigationCalls[0].options.url, '/pages/responsible-sites/responsible-sites');
  assert.equal(page.goResponsibleSites(), false, 'duplicate taps share one navigation');
  navigationCalls[0].options.fail({ errMsg: 'switchTab:fail' });
  assert.equal(app.globalData.stationHubTarget, null);
  assert.deepEqual(toastCalls.at(-1), { title: '打开站点列表失败，请重试', icon: 'none' });

  assert.equal(page.goResponsibleSitesReagent(), true);
  const reagentTarget = app.globalData.stationHubTarget;
  assert.deepEqual(reagentTarget, { view: 'reagents', filter: '' });
  assert.equal(navigationCalls[1].method, 'switchTab');
  navigationCalls[1].options.complete();
  assert.equal(app.globalData.stationHubTarget, reagentTarget,
    'a successful switch leaves the target for the station tab to consume');

  assert.equal(page.goResponsibleSites(), true);
  const failedTarget = app.globalData.stationHubTarget;
  app.globalData.stationHubTarget = { view: 'reagents', filter: 'expired' };
  navigationCalls[2].options.fail({ errMsg: 'switchTab:fail' });
  assert.notEqual(app.globalData.stationHubTarget, failedTarget,
    'an older failure cannot clear a newer station target');
  assert.deepEqual(app.globalData.stationHubTarget, { view: 'reagents', filter: 'expired' });
});

test('home rework action navigates with exact target and clears it on failure', () => {
  const page = pageInstance();
  navigationCalls.length = 0;
  page.data.actions = [{
    target: { kind: 'inspection_rework', executionPlanId: 136, siteId: 362, itemId: 7090, reworkOnly: true, source: 'home_rework' }
  }];
  assert.equal(page.onActionTap({ currentTarget: { dataset: { index: 0 } } }), true);
  assert.deepEqual(app.globalData.executionTarget, {
      executionPlanId: 136, scheduleId: null, workDate: null,
      siteId: 362, itemId: 7090, reworkOnly: true, source: 'home_rework'
  });
  assert.equal(navigationCalls.at(-1).options.url, '/pages/inspection/inspection');
  navigationCalls.at(-1).options.fail();
  assert.equal(app.globalData.executionTarget, null);
});

test('home inspection navigation carries a unique executable package and station only', async () => {
  const original = api.todayExecution;
  try {
    const page = pageInstance();
    page.data.workPackage = { hasPlan: true };
    navigationCalls.length = 0;
    api.todayExecution = () => Promise.resolve({ packages: [{
      plan_id: 70, schedule_id: 7, work_date: '2026-08-28', site_order: [8],
      sites: [{ site_id: 8, total: 1, completed: 0, abnormal: 0 }],
    }] });
    await page.goInspection();
    assert.deepEqual(app.globalData.executionTarget, {
      executionPlanId: 70, scheduleId: 7, workDate: '2026-08-28',
      siteId: 8, itemId: null, source: 'home',
    });
    assert.equal(navigationCalls.at(-1).options.url, '/pages/inspection/inspection');
    navigationCalls.at(-1).options.complete();

    api.todayExecution = () => Promise.resolve({ packages: [
      {
        plan_id: 71, schedule_id: 7, work_date: '2026-08-28', site_order: [8],
        sites: [{ site_id: 8, total: 1, completed: 0, abnormal: 0 }],
      },
      {
        plan_id: 72, schedule_id: 8, work_date: '2026-08-28', site_order: [9],
        sites: [{ site_id: 9, total: 1, completed: 0, abnormal: 0 }],
      },
    ] });
    await page.goInspection();
    assert.equal(app.globalData.executionTarget, null,
      'ambiguous packages enter the existing selection page without inventing a target');
    navigationCalls.at(-1).options.complete();

    api.todayExecution = () => Promise.resolve({ packages: [{
      plan_id: 73, schedule_id: 9, work_date: '2026-08-28', site_order: [10],
      sites: [{ site_id: 10, total: 1, completed: 0, abnormal: 0 }],
    }] });
    await page.goInspection();
    assert.equal(app.globalData.executionTarget.executionPlanId, 73);
    navigationCalls.at(-1).options.fail();
    assert.equal(app.globalData.executionTarget, null,
      'failed navigation clears only the target written by that attempt');
  } finally {
    api.todayExecution = original;
  }
});

test('notification connection remains independent and page re-exports the date formatter', () => {
  const home = fs.readFileSync(path.join(__dirname, '../pages/index/index.js'), 'utf8');
  const mine = fs.readFileSync(path.join(__dirname, '../pages/mine/mine.js'), 'utf8');
  assert.match(home, /api\.unreadCount\(\)/);
  assert.match(mine, /api\.unreadCount\(\)/);
  assert.equal(pageFormatHomeDate('2026-08-26'), '2026年8月26日 周三');
});
