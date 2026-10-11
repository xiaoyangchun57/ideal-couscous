const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  normalizeStationHubTarget,
  localBusinessDate,
  localBusinessDateTime,
  projectReagentOverview,
  projectReagentScope,
  projectReagentSiteGroups,
  reviewExecutionProjection,
  teamOverviewText,
} = require('../utils/round1Wiring.js');

function flush() { return new Promise(resolve => setImmediate(resolve)); }

function applyPatch(target, patch) {
  Object.entries(patch).forEach(([key, value]) => {
    const parts = key.split('.');
    let cursor = target;
    while (parts.length > 1) {
      const part = parts.shift();
      if (!cursor[part] || typeof cursor[part] !== 'object') cursor[part] = {};
      cursor = cursor[part];
    }
    cursor[parts[0]] = value;
  });
}

(async () => {
  assert.deepEqual(normalizeStationHubTarget({ view: 'stations', filter: 'expired' }), { view: 'stations' });
  assert.deepEqual(normalizeStationHubTarget({ view: 'reagents', filter: 'low_volume' }), { view: 'reagents', filter: 'low_volume' });
  assert.equal(normalizeStationHubTarget({ view: 'reagents', filter: 'pending_qc' }), null);

  assert.deepEqual(reviewExecutionProjection({ source_type: 'inspection_batch', plan_id: 12, site_id: 34 }), {
    executionPlanId: 12, executionSiteId: 34, canViewInspection: true,
  });
  assert.equal(reviewExecutionProjection({ source_type: 'inspection_batch', plan_id: 12 }).canViewInspection, false);
  assert.equal(reviewExecutionProjection({ source_type: 'workorder_review', plan_id: 12, site_id: 34 }).canViewInspection, false);

  const projected = projectReagentOverview({ items: [{
    site_id: 1, reagent_id: 2, site_name: '万松站', reagent_name: '余氯试剂', current_qty: 0, unit: 'mL',
    attention_reasons: [{ type: 'expired' }, { type: 'low_volume' }], qc_status: 'pending',
    can_replace: true, can_calibrate: true,
  }] }, '');
  assert.equal(projected[0].volume, 0, 'quantity 0 must be retained');
  assert.deepEqual(projected[0].attention_reasons.map(item => item.type), ['expired', 'low_volume']);
  assert.equal(projected[0].qc_status, 'pending');
  assert.equal(projected[0].qc_status_tone, 'pending');
  assert.equal(projected[0].qc_status_label, '待标定');
  const notRequired = projectReagentOverview({ items: [{
    site_id: 1, reagent_id: 9, qc_status: 'not_required', attention_reasons: [],
    can_replace: true, can_calibrate: false,
  }] }, '')[0];
  assert.equal(notRequired.qc_status_label, '无需标定');
  assert.equal(notRequired.qc_status_tone, 'unknown');
  assert.equal(notRequired.attention_reasons.length, 0);
  assert.equal(notRequired.canCalibrate, false);
  assert.equal(projectReagentOverview({ items: projected }, 'expired').length, 1);
  const normal = projectReagentOverview({ items: [{ site_id: 1, site_name: '万松站', reagent_id: 3, qc_status: 'passed', attention_reasons: [] }] }, '')[0];
  const groups = projectReagentSiteGroups({ items: [projected[0], normal, {
    site_id: 2, reagent_id: 4, site_name: '无关注站', qc_status: 'mystery', attention_reasons: [],
  }] }, '', {});
  assert.equal(groups.length, 2);
  assert.equal(groups[0].expanded, true, 'a site with attention defaults open');
  assert.equal(groups[1].expanded, false, 'a normal site defaults closed');
  const allScopeGroups = projectReagentSiteGroups({ scope: 'all', items: [projected[0], {
    site_id: 2, reagent_id: 4, site_name: '第二关注站', qc_status: 'failed',
    attention_reasons: [{ type: 'failed_qc' }],
  }] }, '', {});
  assert.deepEqual(allScopeGroups.map(group => group.expanded), [false, false],
    'all-scope attention groups must initially remain collapsed');
  assert.equal(projectReagentSiteGroups({ items: [projected[0], normal] }, 'low_volume', {})[0].reagents.length, 1);
  assert.equal(projectReagentSiteGroups({ items: [projected[0], normal] }, 'expiring', {}).length, 0);
  assert.equal(projectReagentSiteGroups({ items: [projected[0], normal] }, '', { 1: false })[0].expanded, false);
  assert.deepEqual(projectReagentScope({ scope: 'all', available_scopes: ['mine', 'all'], scope_counts: { mine: 2, all: 5 } }), {
    scope: 'all', availableScopes: ['mine', 'all'], canViewAll: true, scopeCounts: { mine: 2, all: 5 },
  });
  assert.equal(teamOverviewText(null), '查看团队整体巡检进度');
  assert.match(teamOverviewText([{ status: 'approved' }, { status: 'completed' }]), /2 个计划，1 个待推进/);
  const beijingMidnight = new Date(2026, 8, 30, 0, 30, 45);
  assert.equal(localBusinessDate(beijingMidnight), '2026-09-30');
  assert.equal(localBusinessDateTime(beijingMidnight), '2026-09-30 00:30:45');

  const app = { globalData: { token: 'test', stationHubTarget: { view: 'reagents', filter: 'expired' }, executionTarget: null } };
  let cachedUser = { roles: ['admin', 'operator'], capabilities: { station_monitoring_public: true } };
  let definition;
  const tabBarActions = [];
  const tabBarOptions = [];
  global.getApp = () => app;
  global.Page = value => { definition = value; };
  global.wx = {
    getStorageSync: key => key === 'user' ? cachedUser : null,
    navigateTo: () => {}, showToast: () => {}, getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
    hideTabBar: options => { tabBarActions.push('hide'); tabBarOptions.push(options); },
    showTabBar: options => { tabBarActions.push('show'); tabBarOptions.push(options); },
  };
  const api = require('../services/api.js');
  const originals = {
    reagentOverview: api.reagentOverview,
    replaceReagentInventory: api.replaceReagentInventory,
    submitReagentQc: api.submitReagentQc,
    executionSiteTasks: api.executionSiteTasks,
    planSchedules: api.planSchedules,
    auditPending: api.auditPending,
    todayExecution: api.todayExecution,
    planScheduleFavorites: api.planScheduleFavorites,
  };

  try {
    const secondSiteReagent = projectReagentOverview({ items: [{
      site_id: 2, reagent_id: 2, site_name: '沿江站', reagent_name: '余氯试剂', current_qty: 6, unit: 'mL',
      attention_reasons: [{ type: 'expired' }], qc_status: 'pending', can_replace: true, can_calibrate: true,
    }] }, '')[0];
    const readOnlySiteReagent = projectReagentOverview({ items: [{
      site_id: 3, reagent_id: 2, site_name: '只读站', reagent_name: '余氯试剂', current_qty: 6, unit: 'mL',
      attention_reasons: [{ type: 'expired' }], qc_status: 'pending', can_replace: false, can_calibrate: false,
    }] }, '')[0];
    api.reagentOverview = options => Promise.resolve({
      scope: options && options.scope || 'mine', available_scopes: ['mine', 'all'], scope_counts: { mine: 2, all: 3 },
      items: [projected[0], secondSiteReagent, readOnlySiteReagent],
    });
    require('../pages/responsible-sites/responsible-sites.js');
    const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
    page.setData = (patch, done) => { applyPatch(page.data, patch); if (done) done(); };
    page.onLoad(); page.onShow(); await flush();
    assert.deepEqual(tabBarActions, [], 'entering the station tab must not re-show an already visible native TabBar');
    assert.equal(app.globalData.stationHubTarget, null, 'station target is one-shot');
    assert.equal(page.data.activeTab, 'reagents');
    assert.equal(page.data.activeFilter, 'expired');
    assert.equal(page.data.siteGroups[0].reagents[0].volume, 0);
    assert.equal(page.data.scopeCounts.all, 3);
    page.onToggleSiteGroup({ currentTarget: { dataset: { siteId: 1 } } });
    assert.equal(page.data.siteGroups.find(group => group.site_id === 1).expanded, false);
    page.onReagentFilterTap({ currentTarget: { dataset: { filter: 'low_volume' } } });
    assert.equal(page.data.siteGroups.length, 1);
    assert.equal(page.data.siteGroups[0].expanded, false, 'manual expansion survives filtering');
    page.onReagentFilterTap({ currentTarget: { dataset: { filter: 'expired' } } });

    let replaceCalls = [];
    let rejectReplace;
    api.replaceReagentInventory = payload => {
      replaceCalls.push(payload);
      return new Promise((resolve, reject) => { rejectReplace = reject; });
    };
    page.onOpenReplaceSheet({ currentTarget: { dataset: { siteId: 3, reagentId: 2 } } });
    assert.equal(page.data.replaceSheetVisible, false, 'a visible but unassigned site exposes no write action');
    page.onOpenReplaceSheet({ currentTarget: { dataset: { siteId: 2, reagentId: 2 } } });
    assert.equal(tabBarActions.at(-1), 'hide', 'replacement sheet owns the native tab bar while open');
    assert.equal(tabBarOptions.at(-1).animation, false, 'sheet TabBar transitions must not animate the whole bar');
    assert.equal(page.data.replaceSheet.siteId, 2, 'same reagent id must resolve by exact site and reagent identity');
    assert.equal(page.data.replaceSheet.replaceTime, localBusinessDate(), 'replacement defaults to local business date');
    assert.equal(page.onSubmitReplace(), false);
    assert.equal(page.data.replaceSheet.scrollIntoView, 'replace-field-newVolume');
    assert.equal(page.data.replaceSheet.focusField, 'newVolume');
    assert.match(page.data.replaceSheet.errors.newVolume, /新余量/);
    page.onReplaceVolumeInput({ detail: { value: '5' } });
    assert.equal(page.data.replaceSheet.errors.newVolume, '', 'editing a field clears only its own validation error');
    page.onReplaceEstDaysInput({ detail: { value: '30' } });
    const firstSubmit = page.onSubmitReplace();
    assert.equal(page.onSubmitReplace(), false, 'double submit is blocked while request is pending');
    assert.equal(replaceCalls.length, 1);
    rejectReplace({ error: '暂时失败' });
    await firstSubmit;
    const firstKey = page.data.replaceSheet.idempotencyKey;
    assert.equal(page.data.replaceSheet.newVolume, '5', 'failure retains input');
    api.replaceReagentInventory = payload => { replaceCalls.push(payload); return Promise.reject({ error: '仍失败' }); };
    await page.onSubmitReplace();
    assert.equal(replaceCalls[1]._idempotency_key, firstKey, 'same-form retry reuses idempotency key');
    page.onReplaceVolumeInput({ detail: { value: '6' } });
    assert.equal(page.data.replaceSheet.idempotencyKey, null, 'editing input invalidates retry key');
    page.onCloseReplaceSheet();
    assert.equal(tabBarActions.at(-1), 'show', 'closing replacement sheet restores the native tab bar');

    const qcCalls = [];
    api.submitReagentQc = payload => { qcCalls.push(payload); return Promise.reject({ error: '标定失败' }); };
    page.onOpenCalibrateSheet({ currentTarget: { dataset: { siteId: 1, reagentId: 2 } } });
    assert.equal(tabBarActions.at(-1), 'hide', 'calibration sheet owns the native tab bar while open');
    assert.equal(page.data.calibrateSheet.siteId, 1, 'calibration target uses exact site and reagent identity');
    page.onCalibrateStandardInput({ detail: { value: '   ' } });
    page.onCalibrateMeasuredInput({ detail: { value: '0' } });
    assert.equal(page.onSubmitCalibrate(), false, 'blank calibration input must not be coerced to zero');
    assert.equal(qcCalls.length, 0);
    assert.equal(page.data.calibrateSheet.scrollIntoView, 'calibrate-field-standardValue');
    assert.equal(page.data.calibrateSheet.focusField, 'standardValue');
    page.onCalibrateStandardInput({ detail: { value: '0' } });
    assert.equal(page.data.calibrateSheet.errors.standardValue, '');
    page.onCalibrateMeasuredInput({ detail: { value: '0' } });
    await page.onSubmitCalibrate();
    assert.equal(qcCalls[0].standard_value, 0, 'explicit zero remains valid');
    assert.equal(qcCalls[0].measured_value, 0, 'explicit zero remains valid');
    assert.match(qcCalls[0].qc_time, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    page.onCalibrateStandardInput({ detail: { value: '1.2' } });
    page.onCalibrateMeasuredInput({ detail: { value: '1.3' } });
    page.onCalibrateResultTap({ currentTarget: { dataset: { result: 'fail' } } });
    page.onCalibrateFollowUpTap({ currentTarget: { dataset: { follow: 'recalibrate' } } });
    await page.onSubmitCalibrate();
    const qcKey = page.data.calibrateSheet.idempotencyKey;
    assert.equal(qcCalls[1].fail_action, 'calibrate');
    await page.onSubmitCalibrate();
    assert.equal(qcCalls[2]._idempotency_key, qcKey);
    assert.equal(page.data.calibrateSheet.standardValue, '1.2');
    page.onCloseCalibrateSheet();
    assert.equal(tabBarActions.at(-1), 'show', 'closing calibration sheet restores the native tab bar');

    let resolveMine;
    let resolveAll;
    api.reagentOverview = options => new Promise(resolve => {
      if (options.scope === 'all') resolveAll = resolve;
      else resolveMine = resolve;
    });
    const staleMine = page.loadReagents('mine', { restoreScroll: false });
    const freshAll = page.loadReagents('all', { restoreScroll: false });
    resolveAll({ scope: 'all', available_scopes: ['mine', 'all'], scope_counts: { mine: 2, all: 3 }, items: [secondSiteReagent] });
    await freshAll;
    resolveMine({ scope: 'mine', available_scopes: ['mine', 'all'], scope_counts: { mine: 2, all: 3 }, items: [projected[0]] });
    await staleMine;
    assert.equal(page.data.scope, 'all', 'late old-scope response cannot replace the current scope');
    assert.equal(page.data.siteGroups[0].site_id, 2);
    assert.equal(page.data.siteGroups[0].expanded, false, 'switching to all scope starts attention groups collapsed');
    page.onToggleSiteGroup({ currentTarget: { dataset: { siteId: 2 } } });
    assert.equal(page.data.siteGroups[0].expanded, true, 'all-scope groups remain individually expandable');

    api.reagentOverview = options => Promise.resolve({
      scope: options.scope, available_scopes: ['mine', 'all'], scope_counts: { mine: 2, all: 3 },
      items: options.scope === 'all' ? [secondSiteReagent] : [projected[0]],
    });
    await page.loadReagents('mine', { restoreScroll: false });
    assert.equal(page.data.siteGroups[0].expanded, false, 'mine keeps its manually collapsed state');
    await page.loadReagents('all', { restoreScroll: false });
    assert.equal(page.data.siteGroups[0].expanded, true, 'all keeps its independently expanded state');

    let restoredScroll = null;
    global.wx.pageScrollTo = options => { restoredScroll = options.scrollTop; };
    page.onPageScroll({ scrollTop: 246 });
    page.onReagentFilterTap({ currentTarget: { dataset: { filter: '' } } });
    api.reagentOverview = () => Promise.resolve({
      scope: 'all', available_scopes: ['mine', 'all'], scope_counts: { mine: 2, all: 3 },
      items: [Object.assign({}, secondSiteReagent, { attention_reasons: [] })],
    });
    await page.loadReagents('all');
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(restoredScroll, 246, 'successful same-page reload restores the prior scroll context');
    assert.equal(page.data.siteGroups[0].expanded, true, 'successful reload preserves the currently expanded site even after attention clears');

    api.reagentOverview = () => Promise.reject({ status: 403 });
    await page.loadReagents();
    assert.equal(page.data.reagentNoViewPermission, true);
    assert.deepEqual(page.data.siteGroups, []);

    delete require.cache[require.resolve('../pages/inspection/inspection.js')];
    require('../pages/inspection/inspection.js');
    const inspection = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
    inspection.setData = (patch, done) => { applyPatch(inspection.data, patch); if (done) done(); };
    inspection._alive = true;
    inspection._entryTarget = { executionPlanId: 9, siteId: 8 };
    inspection.loadTasks = (siteId, done, response) => { inspection._loadedReview = { siteId, response }; if (done) done(); };
    let reviewReadArgs = null;
    api.executionSiteTasks = (planId, siteId, options) => {
      reviewReadArgs = { planId, siteId, options };
      return Promise.resolve({ site: { id: 8, name: '审核站点' }, categories: [] });
    };
    await inspection._loadReviewExecution();
    assert.equal(inspection.data.viewMode, 'review');
    assert.equal(inspection._loadedReview.siteId, 8);
    assert.deepEqual(reviewReadArgs, { planId: 9, siteId: 8, options: { reviewOnly: true } });
    api.executionSiteTasks = () => Promise.reject({ status: 403 });
    await inspection._loadReviewExecution();
    assert.equal(inspection.data.viewMode, 'no_permission');

    delete require.cache[require.resolve('../pages/review/view.js')];
    let navigatedUrl = '';
    global.wx.navigateTo = options => { navigatedUrl = options.url; if (options.complete) options.complete(); };
    require('../pages/review/view.js');
    const reviewPage = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
    reviewPage.setData = patch => applyPatch(reviewPage.data, patch);
    reviewPage.data.groups = [{ items: [{ id: 'insp_1', canViewInspection: true, executionPlanId: 9, executionSiteId: 8 }] }];
    reviewPage.onViewSiteInspection({ currentTarget: { dataset: { id: 'insp_1' } } });
    assert.equal(navigatedUrl, '/pages/inspection/inspection?view_mode=review');
    assert.deepEqual(app.globalData.executionTarget, {
      executionPlanId: 9, siteId: 8, source: 'review_execution_record', viewMode: 'review', readOnly: true,
    });
    reviewPage._alive = true;
    api.auditPending = () => Promise.reject({ status: 403 });
    reviewPage.load(); await flush(); await flush();
    assert.equal(reviewPage.data.loadState, 'no_permission');

    delete require.cache[require.resolve('../pages/plan/plan.js')];
    api.planSchedules = team => Promise.resolve(team
      ? [{ id: 1, status: 'approved' }, { id: 2, status: 'completed' }]
      : [{ id: 3, status: 'approved' }]);
    api.auditPending = () => Promise.resolve([{ id: 1 }]);
    api.todayExecution = () => Promise.resolve({ packages: [{ id: 9, sites: [{ site_id: 8, phase: 'unvisited' }] }] });
    api.planScheduleFavorites = () => Promise.resolve([]);
    require('../pages/plan/plan.js');
    const planPage = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
    planPage.setData = patch => applyPatch(planPage.data, patch);
    tabBarActions.length = 0;
    tabBarOptions.length = 0;
    planPage.onLoad(); planPage.onShow(); await flush(); await flush();
    assert.deepEqual(tabBarActions, [], 'entering the plan tab must not re-show an already visible native TabBar');
    assert.equal(planPage.data.homeLayout, 'operator');
    assert.equal(planPage.data.hasReviewRole, false, 'admin role alone is not presented as reviewer');
    await planPage.loadScope('team');
    assert.match(planPage.data.teamOverviewText, /2 个计划，1 个待推进/);
    planPage.onToggleScope();
    assert.equal(planPage.data.pendingScope, 'mine', 'header scope control remains the explicit way back to personal scope');
    let refreshedReview = 0;
    planPage.data.canUseFavorites = true;
    planPage.data.hasReviewRole = true;
    planPage.loadMain = () => Promise.resolve(true);
    planPage.loadFavorites = () => Promise.resolve(true);
    planPage.loadReviewEntry = () => { refreshedReview += 1; return Promise.resolve(true); };
    global.wx.stopPullDownRefresh = () => {};
    await planPage.onPullDownRefresh();
    assert.equal(refreshedReview, 1, 'pull refresh reloads the visible review count');

    let requestedReviewUrl = '';
    global.wx.request = options => {
      requestedReviewUrl = options.url;
      options.success({ statusCode: 200, data: {} });
    };
    await originals.reagentOverview({ scope: 'all' });
    assert.match(requestedReviewUrl, /\/api\/reagent-overview\?scope=all$/);
    await originals.executionSiteTasks(9, 8, { reviewOnly: true });
    assert.match(requestedReviewUrl, /\/api\/mobile\/execution-plans\/9\/sites\/8\?scope=review$/);

    const reviewSource = fs.readFileSync(path.join(__dirname, '../pages/review/view.js'), 'utf8');
    assert.match(reviewSource, /reviewExecutionProjection\(item\)/);
    assert.match(reviewSource, /viewMode: 'review'/);
    const planSource = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.js'), 'utf8');
    assert.match(planSource, /goReview\(\)/);
    assert.doesNotMatch(planSource, /goTodayInspection\(|loadTodayInspectionEntry\(/);
    const planWxml = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.wxml'), 'utf8');
    assert.doesNotMatch(planWxml, /bindtap="goTodayInspection"|今日巡检/);
    const homeWxml = fs.readFileSync(path.join(__dirname, '../pages/index/index.wxml'), 'utf8');
    assert.match(homeWxml, /bindtap="goInspection" wx:if="\{\{workPackage\.hasPlan\}\}"/);
    assert.doesNotMatch(homeWxml, /bindtap="goInspection"[^>]*homeLayout === 'operator'/);
    assert.match(planSource, /teamOverviewText\(rows\)/);
    const reagentWxml = fs.readFileSync(path.join(__dirname, '../pages/responsible-sites/responsible-sites.wxml'), 'utf8');
    assert.match(reagentWxml, /data-site-id="\{\{group\.site_id\}\}" data-reagent-id="\{\{reagent\.reagent_id\}\}"/);
    assert.match(reagentWxml, /ro-row-qc--\{\{reagent\.qc_status_tone\}\}/);
    assert.match(reagentWxml, /scroll-into-view="\{\{replaceSheet\.scrollIntoView\}\}"/);
    assert.match(reagentWxml, /focus="\{\{calibrateSheet\.focusField === 'standardValue'\}\}"/);
    const inspectionWxss = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxss'), 'utf8');
    assert.match(inspectionWxss, /\.parts-apply-sheet\s*\{[\s\S]*height:\s*75vh;[\s\S]*display:\s*flex;/);
    const appConfig = JSON.parse(fs.readFileSync(path.join(__dirname, '../app.json'), 'utf8'));
    const iconPaths = appConfig.tabBar.list.flatMap(item => [item.iconPath, item.selectedIconPath]);
    assert.equal(iconPaths.length, 10);
    iconPaths.forEach(iconPath => {
      const bytes = fs.readFileSync(path.join(__dirname, '..', iconPath));
      assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', `${iconPath} is a PNG`);
      assert.equal(bytes.readUInt32BE(16), 81, `${iconPath} width`);
      assert.equal(bytes.readUInt32BE(20), 81, `${iconPath} height`);
    });
    console.log('round1 development wiring tests passed');
  } finally {
    Object.assign(api, originals);
    delete global.getApp; delete global.Page; delete global.wx;
  }
})().catch(error => { console.error(error && error.stack || error); process.exitCode = 1; });
