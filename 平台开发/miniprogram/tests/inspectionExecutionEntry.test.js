const assert = require('assert');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function flush() {
  return new Promise(resolve => setImmediate(resolve));
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

const app = { globalData: { token: 'test', executionTarget: null, sites: [] } };
let definition;
global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getStorageSync: () => [], setStorageSync: () => {}, removeStorageSync: () => {},
  showToast: () => {}, switchTab: () => {}, reLaunch: () => {}, stopPullDownRefresh: () => {},
  setNavigationBarTitle: () => {},
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
};

const api = require('../services/api.js');
require('../pages/inspection/inspection.js');

function createPage() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)), setDataCalls: 0 });
  page.setData = (patch, done) => {
    page.setDataCalls += 1;
    Object.keys(patch).forEach(key => setPath(page.data, key, patch[key]));
    if (done) done();
  };
  page.onLoad();
  return page;
}

function response(planId, siteId, total) {
  return {
    packages: [{
      plan_id: planId, schedule_id: planId, plan_name: '任务' + planId, schedule_type: 'weekly',
      work_date: '2026-08-28', site_order: [siteId],
      arrival_gate: { allowed: true, code: null, message: null },
      sites: [{ site_id: siteId, name: '站点' + siteId, total, completed: 0, abnormal: 0 }],
    }],
  };
}

function ambiguousResponse(planId = 2, siteId = 20) {
  return {
    packages: [
      response(planId, siteId, 3).packages[0],
      response(planId + 100, siteId + 100, 2).packages[0],
    ],
  };
}

function mixedClosedResponse() {
  return {
    packages: [
      {
        plan_id: 11, schedule_id: 101, plan_name: '目标闭环包', schedule_type: 'weekly',
        work_date: '2026-08-28', site_order: [110],
        sites: [{ site_id: 110, name: '目标闭环站', total: 2, completed: 2, abnormal: 0, checked_out: true }],
      },
      {
        plan_id: 12, schedule_id: 102, plan_name: '无关活动包', schedule_type: 'weekly',
        work_date: '2026-08-28', site_order: [120],
        sites: [{ site_id: 120, name: '无关活动站', total: 2, completed: 0, abnormal: 0 }],
      },
    ],
  };
}

function preparedResponse() {
  const result = response(46, 362, 2);
  Object.assign(result.packages[0], {
    vehicle: { id: 35, plate_no: '赣AFF3288' },
    vehicle_application_id: 21,
    vehicle_use: { id: 5, status: 'checked_out', returned_at: null },
    departure_confirmation: {
      vehicle_confirmed: 1,
      parts_confirmed: 1,
      confirmed_at: '2026-08-29 08:00:00',
    },
    resource_parts: [
      { part_id: 1, part_name: '滤芯', planned_quantity: 2, issued_quantity: 2, remaining_quantity: 0 },
    ],
  });
  return result;
}

(async () => {
  const originalToday = api.todayExecution;
  const originalTasks = api.executionSiteTasks;
  const originalReagents = api.executionSiteReagents;
  const writeMethods = [
    'confirmDepartureResources', 'issueExecutionParts', 'submitVehicleInspection',
    'checkOutVehicle', 'checkIn',
  ];
  const originalWrites = Object.fromEntries(writeMethods.map(name => [name, api[name]]));
  try {
    app.globalData.executionTarget = { executionPlanId: 1, siteId: 10, source: 'message' };
    const firstFailure = createPage();
    api.todayExecution = () => Promise.reject({ error: '网络不可用' });
    firstFailure.loadExecution();
    await flush();
    assert.equal(firstFailure.data.entryState, 'blocking_error');
    assert.equal(firstFailure._entryTarget.executionPlanId, 1, 'page retains a target snapshot after global cleanup');
    assert.equal(app.globalData.executionTarget, null);

    const refresh = createPage();
    api.todayExecution = () => Promise.resolve(ambiguousResponse());
    refresh.loadExecution();
    await flush();
    assert.equal(refresh.data.entryState, 'selecting', 'ambiguous packages stay on the selection page');
    const before = refresh.data.entryPackages;
    api.todayExecution = () => Promise.reject({ error: '刷新失败' });
    refresh.loadExecution();
    await flush();
    assert.equal(refresh.data.entryPackages, before, 'refresh failure preserves the existing selection/list');
    assert.notEqual(refresh.data.entryState, 'blocking_error');
    assert.equal(refresh.data.entryRefreshError, '刷新失败');
    const retry = deferred();
    api.todayExecution = () => retry.promise;
    const stateBeforeRetry = refresh.data.entryState;
    refresh.onEntryRetry();
    assert.equal(refresh.data.entryPackages, before, 'retry keeps the previous packages while the request is pending');
    assert.equal(refresh.data.entryState, stateBeforeRetry, 'refresh retry does not switch back to initial loading');
    assert.equal(refresh.data.entryRefreshError, '');
    retry.resolve(ambiguousResponse());
    await flush();
    assert.equal(refresh.data.entryRefreshError, '', 'successful retry clears the refresh error');

    const late = deferred();
    const newest = deferred();
    const race = createPage();
    api.todayExecution = () => late.promise;
    race.loadExecution();
    api.todayExecution = () => newest.promise;
    race.loadExecution();
    newest.resolve(ambiguousResponse(4, 40));
    await flush();
    late.resolve(ambiguousResponse(3, 30));
    await flush();
    assert.equal(race.data.entryPackages[0].executionPlanId, 4, 'late loadExecution responses are ignored');

    const afterUnload = deferred();
    const unloaded = createPage();
    api.todayExecution = () => afterUnload.promise;
    unloaded.loadExecution();
    const writes = unloaded.setDataCalls;
    unloaded.onHide();
    afterUnload.resolve(response(5, 50, 1));
    await flush();
    assert.equal(unloaded.setDataCalls, writes, 'unloaded pages never receive late writes');

    const entry = createPage();
    entry.data.entryCanEnter = true;
    entry.data.selectedEntryPackageId = 6;
    entry.data.selectedEntrySiteId = 60;
    entry.data.entryPackages = [{ executionPlanId: 6, scheduleId: 6, sites: [{ siteId: 60, phase: 'await_arrival' }] }];
    api.executionSiteTasks = () => Promise.reject({ error: '站点暂不可达' });
    entry.onEnterSite();
    await flush();
    assert.equal(entry.data.entryCanEnter, true, 'station detail failure keeps the selected target retryable');
    assert.equal(entry.data.selectedEntryPackageId, 6);

    entry._entryTarget = { itemId: 99 };
    api.executionSiteTasks = () => Promise.resolve({ categories: [{ items: [{ item_id: 98 }] }] });
    entry.onEnterSite();
    await flush();
    assert.equal(entry.data.viewPhase, 'entry', 'an unavailable item does not open a different inspection item');

    const entered = createPage();
    let todayCalls = 0;
    api.todayExecution = () => { todayCalls += 1; return Promise.resolve(response(7, 70, 1)); };
    api.executionSiteTasks = () => Promise.resolve({
      site: { site_id: 70 }, categories: [{ items: [{ item_id: 71 }] }],
      arrival_gate: { allowed: true, code: null, message: null },
    });
    entered._entryTarget = { executionPlanId: 7, siteId: 70 };
    entered.loadExecution();
    await flush();
    await flush();
    assert.equal(entered.data.viewPhase, 'departure');
    assert.equal(entered.data.currentPackage.plan_id, 7);
    assert.equal(todayCalls, 1, 'entry validation opens departure preparation without refetching the package');

    const homeRework = createPage();
    app.globalData.executionTarget = {
      scheduleId: 136, executionPlanId: 136, siteId: 362, itemId: 7090, reworkOnly: true,
      workDate: '2026-08-28', source: 'home_rework',
    };
    homeRework._entryTarget = undefined;
    const closedRework = response(136, 362, 1);
    Object.assign(closedRework.packages[0], { is_rework: false });
    Object.assign(closedRework.packages[0].sites[0], { checked_out: true, rework_items: 1 });
    api.todayExecution = () => Promise.resolve(closedRework);
    const homeReworkTasks = deferred();
    api.executionSiteTasks = (planId, siteId, options) => {
      assert.deepEqual([planId, siteId, options], [136, 362, { reworkOnly: true }]);
      return homeReworkTasks.promise;
    };
    homeRework.loadExecution();
    await flush();
    assert.equal(homeRework.data.entryState, 'initial_loading',
      'a precise auto-entry keeps the chooser hidden while its station target is revalidated');
    homeReworkTasks.resolve({
      site: { site_id: 362, checked_out: true },
      categories: [{ items: [{ item_id: 7090 }] }],
      arrival_gate: { allowed: true, code: null, message: null },
    });
    await flush();
    assert.equal(homeRework.data.viewPhase, 'departure',
      'a unique home remediation target bypasses the package chooser even after the original station departure');
    assert.deepEqual(homeRework.data.entryCompletedPackages, [],
      'the active remediation package is not duplicated into the completed list');
    assert.equal(homeRework.data.entryTargetMessage, null);

    const staleItemHomeRework = createPage();
    app.globalData.executionTarget = {
      scheduleId: 136, executionPlanId: 136, siteId: 362, itemId: 7999, reworkOnly: true,
      workDate: '2026-08-28', source: 'home_rework',
    };
    staleItemHomeRework._entryTarget = undefined;
    api.todayExecution = () => Promise.resolve(closedRework);
    api.executionSiteTasks = () => Promise.resolve({
      site: { site_id: 362, checked_out: true },
      categories: [{ items: [{ item_id: 7090 }, { item_id: 7091 }] }],
      arrival_gate: { allowed: true, code: null, message: null },
    });
    staleItemHomeRework.loadExecution();
    await flush();
    await flush();
    assert.equal(staleItemHomeRework.data.viewPhase, 'departure',
      'a stale item locator continues with the same station remediation collection');
    assert.equal(staleItemHomeRework._entryTarget.itemId, 7090,
      'the first authoritative remediation item replaces the stale homepage locator');

    const staleHomeRework = createPage();
    app.globalData.executionTarget = {
      scheduleId: 137, executionPlanId: 137, siteId: 363, itemId: 7091, reworkOnly: true,
      workDate: '2026-08-28', source: 'home_rework',
    };
    staleHomeRework._entryTarget = undefined;
    const closedNonRework = response(137, 363, 1);
    Object.assign(closedNonRework.packages[0].sites[0], { checked_out: true, completed: 1 });
    let staleDetailCalls = 0;
    api.todayExecution = () => Promise.resolve(closedNonRework);
    api.executionSiteTasks = () => { staleDetailCalls += 1; return Promise.resolve({ categories: [] }); };
    staleHomeRework.loadExecution();
    await flush();
    assert.deepEqual(
      [staleHomeRework.data.entryState, staleHomeRework.data.entryCanEnter, staleHomeRework.data.entryTargetMessage],
      ['target_unavailable', false, '整改任务状态已更新，请刷新首页后重试'],
      'a closed non-rework response rejects a stale home remediation target instead of showing historical results',
    );
    assert.equal(staleDetailCalls, 0,
      'a stale remediation target is rejected before any station action can begin');

    const prepared = createPage();
    app.globalData.executionTarget = {
      scheduleId: 46, executionPlanId: 46, siteId: 362,
      workDate: '2026-08-28', source: 'home',
    };
    prepared._entryTarget = undefined;
    let businessWrites = 0;
    writeMethods.forEach(name => {
      api[name] = () => { businessWrites += 1; return Promise.resolve({}); };
    });
    api.todayExecution = () => Promise.resolve(preparedResponse());
    api.executionSiteTasks = () => Promise.resolve({
      site: { site_id: 362 },
      categories: [{ items: [{ item_id: 7001 }] }],
      arrival_gate: { allowed: true, code: null, message: null },
    });
    api.executionSiteReagents = () => Promise.resolve({ items: [] });
    prepared.loadExecution();
    await flush();
    await flush();
    assert.equal(prepared.data.departureVm.preparationCompleted, true);
    assert.equal(prepared.data.viewPhase, 'inspection',
      'an exact verified target skips a preparation page whose facts are already complete');
    assert.equal(prepared.data.selectedPlanId, 46);
    assert.equal(prepared.data.selSiteId, 362);
    assert.equal(businessWrites, 0,
      'skipping the completed preparation view never confirms resources, checks out, or checks in');

    const unavailablePreparation = createPage();
    app.globalData.executionTarget = {
      scheduleId: 47, executionPlanId: 47, siteId: 363,
      workDate: '2026-08-28', source: 'home',
    };
    unavailablePreparation._entryTarget = undefined;
    const unavailable = response(47, 363, 1);
    unavailable.packages[0].vehicle_exception_reason = '站点步行可达';
    unavailable.packages[0].arrival_gate = null;
    api.todayExecution = () => Promise.resolve(unavailable);
    api.executionSiteTasks = () => Promise.resolve({
      site: { site_id: 363 }, categories: [{ items: [] }], arrival_gate: null,
    });
    unavailablePreparation.loadExecution();
    await flush();
    await flush();
    assert.equal(unavailablePreparation.data.departureVm.preparationCompleted, false);
    assert.equal(unavailablePreparation.data.viewPhase, 'departure',
      'unknown authoritative preparation facts remain on the preparation page');
    assert.equal(businessWrites, 0);

    const enteringRace = createPage();
    enteringRace._entryTarget = {};
    enteringRace._allEntryPackages = [
      { executionPlanId: 8, scheduleId: 8, sites: [{ siteId: 80, phase: 'await_arrival' }] },
      { executionPlanId: 9, scheduleId: 9, sites: [{ siteId: 90, phase: 'await_arrival' }] },
    ];
    enteringRace.data.entryPackages = enteringRace._allEntryPackages;
    enteringRace.data.selectedEntryPackageId = 8;
    enteringRace.data.selectedEntrySiteId = 80;
    enteringRace.data.entryCanEnter = true;
    const delayedEntry = deferred();
    api.executionSiteTasks = () => delayedEntry.promise;
    enteringRace.onEnterSite();
    enteringRace.onEntrySelectPackage({ currentTarget: { dataset: { id: 9 } } });
    delayedEntry.resolve({ categories: [{ items: [] }] });
    await flush();
    assert.equal(enteringRace.data.viewPhase, 'entry', 'changing selection invalidates an older station-detail response');

    const locked = createPage();
    locked._entryTarget = { executionPlanId: 7, siteId: 70 };
    locked.data.entryPackages = [
      { executionPlanId: 7, sites: [{ siteId: 70, phase: 'await_arrival' }] },
      { executionPlanId: 8, sites: [{ siteId: 80, phase: 'await_arrival' }] },
    ];
    locked.data.selectedEntryPackageId = 7;
    locked.data.selectedEntrySiteId = 70;
    locked.onEntrySelectPackage({ currentTarget: { dataset: { id: 8 } } });
    locked.onEntrySelectSite({ currentTarget: { dataset: { id: 80 } } });
    assert.deepEqual([locked.data.selectedEntryPackageId, locked.data.selectedEntrySiteId], [7, 70],
      'a precise package-site target remains locked until the user explicitly reselects');

    const exactClosedTarget = {
      executionPlanId: 11, scheduleId: 101, source: 'plan_detail',
    };
    app.globalData.executionTarget = exactClosedTarget;
    const closedBrowse = createPage();
    api.todayExecution = () => Promise.resolve(mixedClosedResponse());
    closedBrowse.loadExecution();
    await flush();
    assert.equal(closedBrowse.data.entryState, 'closed_only');
    const targetSnapshot = Object.assign({}, closedBrowse._entryTarget);
    closedBrowse.onViewClosedResults();
    assert.deepEqual(closedBrowse._entryTarget, targetSnapshot,
      'viewing closed results preserves the original execution target');
    assert.deepEqual(
      closedBrowse.data.entryPackages.map(pkg => [pkg.executionPlanId, pkg.sites.map(site => site.siteId)]),
      [[11, [110]]],
      'the exact closed target excludes unrelated active packages from the result browser',
    );
    assert.deepEqual(
      [closedBrowse.data.selectedEntryPackageId, closedBrowse.data.selectedEntrySiteId, closedBrowse.data.entryCanEnter],
      [11, 110, true],
    );
    closedBrowse.onEntrySelectPackage({ currentTarget: { dataset: { id: 12 } } });
    closedBrowse.onEntrySelectSite({ currentTarget: { dataset: { id: 120 } } });
    assert.deepEqual(
      [closedBrowse.data.selectedEntryPackageId, closedBrowse.data.selectedEntrySiteId],
      [11, 110],
      'unrelated active packages and sites cannot be selected in closed-result mode',
    );

    const closedBeforeRefresh = closedBrowse.data.entryPackages;
    api.todayExecution = () => Promise.reject({ error: '闭环结果刷新失败' });
    closedBrowse.loadExecution();
    await flush();
    assert.equal(closedBrowse.data.entryPackages, closedBeforeRefresh,
      'a closed-result refresh failure preserves the scoped result list');
    assert.equal(closedBrowse.data.entryBrowseClosed, true);
    const closedRetry = deferred();
    api.todayExecution = () => closedRetry.promise;
    closedBrowse.onEntryRetry();
    assert.equal(closedBrowse.data.entryPackages, closedBeforeRefresh,
      'closed-result retry preserves the scoped list while pending');
    assert.equal(closedBrowse.data.entryBrowseClosed, true,
      'closed-result retry does not exit read-only browse mode');
    closedRetry.resolve(mixedClosedResponse());
    await flush();
    assert.deepEqual(
      closedBrowse.data.entryPackages.map(pkg => [pkg.executionPlanId, pkg.sites.map(site => site.siteId)]),
      [[11, [110]]],
      'closed-result retry reapplies the original target after refresh',
    );
    assert.deepEqual(closedBrowse._entryTarget, targetSnapshot);
    let unrelatedEntryCalls = 0;
    api.executionSiteTasks = () => {
      unrelatedEntryCalls += 1;
      return Promise.resolve({ categories: [] });
    };
    closedBrowse.data.selectedEntryPackageId = 12;
    closedBrowse.data.selectedEntrySiteId = 120;
    closedBrowse.data.entryCanEnter = true;
    closedBrowse.onEnterSite();
    assert.equal(unrelatedEntryCalls, 0,
      'closed-result mode rejects an unrelated active package before requesting station details');
    assert.equal(closedBrowse.data.entryCanEnter, false);

    app.globalData.executionTarget = exactClosedTarget;
    const closedReadOnly = createPage();
    api.todayExecution = () => Promise.resolve(mixedClosedResponse());
    api.executionSiteTasks = () => Promise.resolve({
      site: { id: 110, checked_out: true }, categories: [{ items: [] }],
      arrival_gate: {
        allowed: false, code: 'VEHICLE_APPLICATION_INVALID', message: '行程已结束',
      },
    });
    closedReadOnly.loadExecution();
    await flush();
    closedReadOnly.onViewClosedResults();
    closedReadOnly.loadTasks = function (siteId, done, verified) {
      this._closedResult = { siteId, verified };
    };
    closedReadOnly.onEnterSite();
    await flush();
    assert.equal(closedReadOnly.data.viewPhase, 'inspection',
      'closed result browsing bypasses the arrival gate without enabling a new check-in');
    assert.equal(closedReadOnly._closedResult.siteId, 110);
    assert.equal(closedReadOnly._closedResult.verified.site.checked_out, true);
  } finally {
    api.todayExecution = originalToday;
    api.executionSiteTasks = originalTasks;
    api.executionSiteReagents = originalReagents;
    Object.assign(api, originalWrites);
  }
  console.log('inspectionExecutionEntry tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
