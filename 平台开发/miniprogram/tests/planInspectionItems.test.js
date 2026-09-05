const assert = require('node:assert/strict');
const test = require('node:test');

const requestPath = require.resolve('../utils/request.js');
const apiPath = require.resolve('../services/api.js');
const originalRequestModule = require.cache[requestPath];
const pagePath = require.resolve('../pages/plan-edit/plan-edit.js');
const fs = require('node:fs');
const path = require('node:path');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function flushAsync() {
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
}

function existingPlanDetail(overrides) {
  return Object.assign({
    id: 42,
    version: 3,
    status: 'rejected',
    reject_reason: '请补充历史站点安排',
    schedule_type: 'monthly',
    period_start: '2026-09-01',
    period_end: '2026-09-30',
    plan_data: {
      '2026-09-08': { sites: [91], notes: '保留现场输入', inspection_items: { '91': [11] } },
    },
    vehicle_days: {},
    site_map: { '91': { id: 91, name: '历史第一水厂' } },
    spare_parts: [],
    work_order_ids: [],
    template_context: [],
    remarks: '原计划备注',
    coverage_exception_reason: '',
    vehicle_exception_reason: '无需用车',
    change_reason: '',
  }, overrides || {});
}

test.after(() => {
  delete require.cache[apiPath];
  if (originalRequestModule) require.cache[requestPath] = originalRequestModule;
  else delete require.cache[requestPath];
});

test('inspection item matching sends site and explicit schedule frequency', async () => {
  const calls = [];
  require.cache[requestPath] = {
    id: requestPath, filename: requestPath, loaded: true,
    exports: { request(path, method, data) { calls.push({ path, method, data }); return Promise.resolve({ items: [] }); } },
  };
  delete require.cache[apiPath];
  const api = require(apiPath);
  await api.inspectionConfigMatches(42, 'monthly');
  assert.deepEqual(calls, [{
    path: '/api/inspection-v2/configs/match?site_id=42&schedule_type=monthly', method: 'GET', data: undefined,
  }]);
});

test('weekly dates reconcile automatically while longer periods keep explicit dates only', () => {
  let definition;
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showToast() {} };
  delete require.cache[pagePath];
  const { periodDates, reconcilePeriodDays } = require(pagePath);

  const existing = [{
    date: '2026-09-03', sites: [10], notes: '保留安排', vehicle_id: 7,
    inspection_items: { '10': [11] },
  }];
  const weekly = reconcilePeriodDays('2026-09-01', '2026-09-07', existing, true);
  assert.equal(weekly.length, 7);
  assert.deepEqual(weekly.map(day => day.date), [
    '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04',
    '2026-09-05', '2026-09-06', '2026-09-07',
  ]);
  assert.deepEqual(weekly[2].sites, [10]);
  assert.equal(weekly[2].notes, '保留安排');
  assert.deepEqual(weekly[2].inspection_items, { '10': [11] });

  const expanded = reconcilePeriodDays('2026-09-01', '2026-09-09', weekly, true);
  assert.equal(expanded.length, 9);
  assert.equal(expanded[2].notes, '保留安排');
  assert.equal(periodDates('2028-01-01', '2028-12-31').length, 366);
  assert.throws(() => periodDates('2028-01-01', '2029-01-01'), /366/);
  assert.deepEqual(reconcilePeriodDays('2026-09-01', '2026-09-30', [], false), []);
  assert.deepEqual(
    reconcilePeriodDays('2026-09-01', '2026-09-30', existing, false).map(day => day.date),
    ['2026-09-03']);

  const page = Object.assign({}, definition, { data: Object.assign({}, definition.data) });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.loadInspectionItems = () => {};
  page.refreshValidation = () => {};
  page.initPeriod('weekly');
  assert.equal(page.data.days.length, 7);
  page.onAddDay({ detail: { value: page.data.periodStart } });
  assert.equal(page.data.days.length, 7, '周检不得通过选择器重复添加日期');
  for (const type of ['monthly', 'quarterly', 'yearly']) {
    page.initPeriod(type);
    assert.deepEqual(page.data.days, [], `${type} 不得铺开空日期`);
  }
  page.initPeriod('monthly');
  const selectedDate = page.data.periodStart;
  page.onAddDay({ detail: { value: selectedDate } });
  assert.deepEqual(page.data.days.map(day => day.date), [selectedDate]);

  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('plan payload keeps per-site inspection selections and blocks save after matching failure', () => {
  let definition;
  const toasts = [];
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showToast: value => toasts.push(value) };
  delete require.cache[pagePath];
  const pageHelpers = require(pagePath);
  const page = Object.assign({}, definition, {
    data: Object.assign({}, definition.data, {
      scheduleType: 'monthly', periodStart: '2026-09-01', periodEnd: '2026-09-30',
      days: [
        { date: '2026-09-02', sites: [10], notes: '现场安排', inspection_items: { '10': [11] } },
        { date: '2026-09-03', sites: [], notes: '无站点不提交', inspection_items: {} },
      ],
      detailState: 'ready', inspectionItemsState: 'ready', selectedParts: [], suggestions: [], planVehicleId: 7,
    }),
  });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  const payload = page.buildPayload(false);
  assert.deepEqual(payload.plan_data['2026-09-02'].inspection_items, { '10': [11] });
  assert.deepEqual(Object.keys(payload.plan_data), ['2026-09-02']);
  assert.deepEqual(payload.vehicle_days, { '2026-09-02': 7 });
  page.data.inspectionItemsState = 'unavailable';
  page.data.inspectionItemsError = '检查项加载失败，请重试后再保存';
  page.onSaveDraft();
  assert.equal(toasts.length, 1);
  assert.match(toasts[0].title, /加载失败/);
  const initialized = pageHelpers.initializeInspectionItemSelections([
    { date: '2026-09-02', sites: [10, 20], inspection_items: { '10': [99] } },
  ], {
    10: [{ id: 11 }],
    20: [],
  });
  assert.deepEqual(initialized[0].inspection_items, { '10': [99], '20': [] });
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('plan reference presentation keeps operational references and excludes reagent display only', () => {
  let definition;
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showToast() {} };
  delete require.cache[pagePath];
  const { summarizeTemplateContext, schedulingReferencePresentation } = require(pagePath);
  const templates = Array.from({ length: 10 }, (_, index) => ({
    site_id: index + 1, template_name: '水质站点每周巡检模板', item_count: 12,
  }));
  assert.deepEqual(summarizeTemplateContext(templates, 'weekly'), [{
    key: '["水质站点每周巡检模板",12]', template_name: '水质站点每周巡检模板',
    item_count: 12, site_count: 10, label: '周检可选检查项：12项 · 适用10个负责站点',
  }]);
  assert.equal(summarizeTemplateContext(templates.concat({
    site_id: 11, template_name: '设备专项模板', item_count: 4,
  }), 'weekly').length, 2);

  const raw = [
    { type: 'work_order', site_id: 1, ref_id: 7, text: '未关工单' },
    { type: 'work_order', site_id: 1, ref_id: 7, text: '未关工单' },
    { type: 'alert', site_id: 2, ref_id: 18, text: '独立告警' },
    { type: 'manual_report', site_id: 2, ref_id: 19, text: '独立人工上报' },
    { type: 'reagent', site_id: 2, ref_id: 10, text: '试剂临期' },
    { type: 'priority', site_id: 1, text: '重复聚合建议' },
  ];
  const reference = schedulingReferencePresentation(raw);
  assert.deepEqual(reference.items.map(item => item.type),
    ['work_order', 'alert', 'manual_report']);
  assert.equal(reference.summary, '工单1 · 告警1 · 人工上报1');
  assert.equal(reference.total, 3);
  assert.equal(raw.find(item => item.type === 'reagent').text, '试剂临期',
    '展示投影不得改写原始试剂建议事实');
  assert.deepEqual(schedulingReferencePresentation(null), { items: [], total: 0, summary: '' });

  const page = Object.assign({}, definition, { data: Object.assign({}, definition.data, {
    scheduleType: 'weekly', periodStart: '2026-09-01', periodEnd: '2026-09-07',
    days: [{ date: '2026-09-01', sites: [1], notes: '', inspection_items: { '1': [11] } }],
    suggestions: raw, selectedParts: [], noVehicleRequired: true, vehicleExceptionReason: '步行',
  }) });
  assert.deepEqual(page.buildPayload(false).work_order_ids, [7],
    '展示层排除 priority 和去重不能改变原始工单关联');
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('vehicle mode and scheduling reference toggles change only their intended transient state', () => {
  let definition;
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showToast() {} };
  delete require.cache[pagePath];
  require(pagePath);
  const page = Object.assign({}, definition, {
    data: Object.assign({}, definition.data, {
      noVehicleRequired: false, planVehicleId: 7, vehicleExceptionReason: '步行',
      days: [{ date: '2026-09-01', sites: [1], vehicle_id: 7, inspection_items: { '1': [11] } }],
      selectedParts: [], suggestions: [], inspectionItemsState: 'ready', referenceExpanded: false,
    }),
  });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.refreshValidation = () => {};

  page.onVehicleModeSelect({ currentTarget: { dataset: { mode: 'none' } } });
  assert.equal(page.data.noVehicleRequired, true);
  assert.equal(page.data.planVehicleId, null);
  assert.equal(page.data.days[0].vehicle_id, null);
  page.onVehicleModeSelect({ currentTarget: { dataset: { mode: 'vehicle' } } });
  assert.equal(page.data.noVehicleRequired, false);
  assert.equal(page.data.vehicleExceptionReason, '');
  assert.equal(page.buildPayload(false).vehicle_exception_reason, '');

  const before = JSON.parse(JSON.stringify(page.data.days));
  page.onToggleSchedulingReference();
  assert.equal(page.data.referenceExpanded, true);
  page.onToggleSchedulingReference();
  assert.equal(page.data.referenceExpanded, false);
  assert.deepEqual(page.data.days, before);
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('plan editor puts vehicle and daily scheduling before compact reference without a switch', () => {
  const source = fs.readFileSync(path.join(__dirname, '../pages/plan-edit/plan-edit.wxml'), 'utf8');
  const vehicle = source.indexOf('计划用车');
  const daily = source.indexOf('作业日与站点');
  const reference = source.indexOf('排程参考');
  assert.ok(vehicle > -1 && daily > vehicle && reference > daily);
  assert.doesNotMatch(source, /<switch\b/);
  assert.match(source, /data-mode="vehicle"[^>]*bindtap="onVehicleModeSelect"/);
  assert.match(source, /data-mode="none"[^>]*bindtap="onVehicleModeSelect"/);
  assert.match(source, /wx:if="{{!noVehicleRequired}}"[\s\S]*选择车辆/);
  assert.match(source, /wx:if="{{noVehicleRequired}}"[\s\S]*vehicleExceptionReason/);
  assert.match(source, /referenceExpanded[\s\S]*展开|展开[\s\S]*referenceExpanded/);
  assert.doesNotMatch(source, /审批后自动生成检查项/);
  assert.doesNotMatch(source, /按实际设备选择/);
  assert.match(source, /按站点实际情况选择/);
});

test('unavailable inspection item state exposes a direct retry binding', () => {
  const source = fs.readFileSync(path.join(__dirname, '../pages/plan-edit/plan-edit.wxml'), 'utf8');
  assert.match(source, /inspectionItemsState === 'unavailable'[\s\S]*bindtap="loadInspectionItems"/);
  assert.match(source, /点击重试/);
  assert.match(source, /wx:if="{{scheduleType !== 'weekly'}}"[\s\S]*添加作业日/);
  assert.match(source, /wx:if="{{scheduleType !== 'weekly'}}"[^>]*data-date="{{day\.date}}"[^>]*bindtap="onRemoveDay"/);
  assert.match(source, /bindinput="onDayNotes"/);
  assert.doesNotMatch(source, /添加巡检日期/);
});

test('legacy empty weekly skeleton dates can shrink but real arrangements remain protected', () => {
  let definition;
  const modals = [];
  const toasts = [];
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showModal: value => modals.push(value), showToast: value => toasts.push(value) };
  delete require.cache[pagePath];
  const { dayHasBusinessContent } = require(pagePath);
  assert.equal(dayHasBusinessContent({ date: '2026-08-18', sites: [], notes: '', inspection_items: {} }), false);
  assert.equal(dayHasBusinessContent({ date: '2026-08-19', sites: [10], notes: '', inspection_items: {} }), true);
  assert.equal(dayHasBusinessContent({ date: '2026-08-20', sites: [], notes: '现场安排', inspection_items: {} }), true);
  assert.equal(dayHasBusinessContent({ date: '2026-08-21', sites: [], notes: '', inspection_items: {}, vehicle_id: 3 }), true);
  const page = Object.assign({}, definition, {
    data: Object.assign({}, definition.data, {
      periodStart: '2026-08-18', periodEnd: '2026-08-24',
      days: [
        { date: '2026-08-18', sites: [], notes: '', inspection_items: {} },
        { date: '2026-08-20', sites: [10], notes: '', inspection_items: {} },
        { date: '2026-08-24', sites: [], notes: '', inspection_items: {} },
      ],
    }),
  });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.refreshValidation = () => {};
  page.updatePeriod('periodEnd', '2026-08-20');
  assert.equal(page.data.periodEnd, '2026-08-20');
  assert.deepEqual(page.data.days.map(day => day.date), ['2026-08-18', '2026-08-19', '2026-08-20']);
  page.updatePeriod('periodEnd', '2026-08-19');
  assert.equal(page.data.periodEnd, '2026-08-20');
  assert.equal(modals.length, 1);
  assert.match(modals[0].content, /2026-08-20/);

  page.updatePeriod('periodEnd', '2027-08-20');
  assert.equal(page.data.periodEnd, '2026-08-20');
  assert.match(toasts.at(-1).title, /366/);

  page.data.scheduleType = 'monthly';
  page.data.periodStart = '2026-09-01';
  page.data.periodEnd = '2026-09-30';
  page.data.days = [
    { date: '2026-09-05', sites: [], notes: '', inspection_items: {} },
    { date: '2026-09-20', sites: [10], notes: '保留长周期安排', inspection_items: { '10': [11] } },
  ];
  page.updatePeriod('periodEnd', '2026-09-10');
  assert.equal(page.data.periodEnd, '2026-09-30');
  assert.equal(modals.length, 2);
  assert.match(modals[1].content, /2026-09-20/);
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('change submit retries the same saved payload without another PUT after response loss', async () => {
  let definition;
  const modals = [];
  const toasts = [];
  let navigations = 0;
  global.getApp = () => ({ globalData: { token: 'token' } });
  global.Page = value => { definition = value; };
  global.wx = {
    getStorageSync: key => key === 'user' ? { id: 2, role: 'operator' } : '',
    showModal: value => modals.push(value),
    showToast: value => toasts.push(value),
    navigateBack: () => { navigations += 1; },
  };
  const api = require(apiPath);
  const originals = {
    validate: api.validatePlanSchedule,
    update: api.updatePlanSchedule,
    submit: api.submitPlanSchedule,
  };
  const updateVersions = [];
  let submitCount = 0;
  let submitShouldFail = true;
  api.validatePlanSchedule = () => Promise.resolve({ errors: [], warnings: [] });
  api.updatePlanSchedule = (id, payload, options) => {
    updateVersions.push({ version: payload.version, options });
    return Promise.resolve({ id, status: 'modifying', version: payload.version + 1 });
  };
  api.submitPlanSchedule = () => {
    submitCount += 1;
    return submitShouldFail
      ? Promise.reject({ error: '提交响应丢失，请重试' })
      : Promise.resolve({ status: 'change_submitted', already_submitted: true });
  };
  delete require.cache[pagePath];
  require(pagePath);
  const originalDays = [{
    date: '2026-08-25', sites: [10], notes: '保留输入', inspection_items: { '10': [11] },
  }];
  const page = Object.assign({}, definition, {
    data: Object.assign({}, definition.data, {
      editId: 42, version: 7, isChange: true,
      detailState: 'ready',
      scheduleType: 'weekly', periodStart: '2026-08-24', periodEnd: '2026-08-30',
      days: originalDays, inspectionItemsState: 'ready', selectedParts: [], suggestions: [],
      noVehicleRequired: true, vehicleExceptionReason: '无需用车',
    }),
  });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.applyValidation = () => {};
  page.onSubmit();
  page.onSubmit();
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(submitCount, 1, '重复点击不能发出第二次正式提交');
  assert.deepEqual(updateVersions, [{ version: 7, options: { queue: false } }]);
  assert.equal(page.data.version, 8, 'PUT 成功后的最新版本必须保留供直接重试');
  assert.deepEqual(page.data.days, originalDays, '失败不得清空用户输入');
  assert.match(page.data.submitError, /提交响应丢失/);
  assert.equal(navigations, 0);
  assert.equal(toasts.some(item => item.title === '已提交审批'), false);
  assert.equal(modals.length, 1);
  assert.match(modals[0].content, /重试/);

  submitShouldFail = false;
  const originalSetTimeout = global.setTimeout;
  global.setTimeout = callback => { callback(); return 1; };
  page.onSubmit();
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  global.setTimeout = originalSetTimeout;
  assert.deepEqual(updateVersions, [{ version: 7, options: { queue: false } }],
    '相同输入的人工重试不得再次更新计划');
  assert.equal(page.data.version, 8);
  assert.equal(submitCount, 2);
  assert.equal(navigations, 1);
  assert.equal(toasts.some(item => item.title === '已提交审批'), true);

  api.validatePlanSchedule = originals.validate;
  api.updatePlanSchedule = originals.update;
  api.submitPlanSchedule = originals.submit;
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('change submit runs PUT again when the user edits payload after a failed POST', async () => {
  let definition;
  global.getApp = () => ({ globalData: { token: 'token' } });
  global.Page = value => { definition = value; };
  global.wx = {
    getStorageSync: key => key === 'user' ? { id: 2, role: 'operator' } : '',
    showModal() {}, showToast() {}, navigateBack() {},
  };
  const api = require(apiPath);
  const originals = {
    validate: api.validatePlanSchedule,
    update: api.updatePlanSchedule,
    submit: api.submitPlanSchedule,
  };
  const updatePayloads = [];
  let submitCount = 0;
  api.validatePlanSchedule = () => Promise.resolve({ errors: [], warnings: [] });
  api.updatePlanSchedule = (id, payload, options) => {
    updatePayloads.push({ remarks: payload.remarks, version: payload.version, options });
    return Promise.resolve({ id, status: 'modifying', version: payload.version + 1 });
  };
  api.submitPlanSchedule = () => {
    submitCount += 1;
    return submitCount === 1
      ? Promise.reject({ error: '提交响应丢失' })
      : Promise.resolve({ status: 'change_submitted' });
  };
  delete require.cache[pagePath];
  require(pagePath);
  const page = Object.assign({}, definition, {
    data: Object.assign({}, definition.data, {
      editId: 42, version: 7, isChange: true, remarks: '原内容',
      detailState: 'ready',
      scheduleType: 'weekly', periodStart: '2026-08-24', periodEnd: '2026-08-30',
      days: [{ date: '2026-08-25', sites: [10], inspection_items: { '10': [11] } }],
      inspectionItemsState: 'ready', selectedParts: [], suggestions: [],
      noVehicleRequired: true, vehicleExceptionReason: '无需用车',
    }),
  });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.applyValidation = () => {};

  page.onSubmit();
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  page.data.remarks = '用户修改后的内容';
  const originalSetTimeout = global.setTimeout;
  global.setTimeout = callback => { callback(); return 1; };
  page.onSubmit();
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  global.setTimeout = originalSetTimeout;

  assert.deepEqual(updatePayloads, [
    { remarks: '原内容', version: 7, options: { queue: false } },
    { remarks: '用户修改后的内容', version: 8, options: { queue: false } },
  ]);
  assert.equal(submitCount, 2);

  api.validatePlanSchedule = originals.validate;
  api.updatePlanSchedule = originals.update;
  api.submitPlanSchedule = originals.submit;
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('daily site selection projects all-state safely and keeps inspection choices on partial selection', () => {
  let definition;
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showToast() {} };
  delete require.cache[pagePath];
  const { hasAllAvailableSites, withDaySiteSelectionState } = require(pagePath);
  const mySites = [{ id: 1, name: '一号站' }, { id: 2, name: '二号站' }];
  assert.equal(hasAllAvailableSites([], []), false, '零可选站点不能伪装为已全选');
  assert.equal(hasAllAvailableSites([1], mySites), false);
  assert.equal(hasAllAvailableSites([1, 2], mySites), true);
  assert.equal(withDaySiteSelectionState([{ sites: [] }, { sites: [1, 2] }], mySites)[0].allSitesSelected, false);
  assert.equal(withDaySiteSelectionState([{ sites: [] }, { sites: [1, 2] }], mySites)[1].allSitesSelected, true);

  const page = Object.assign({}, definition, { data: Object.assign({}, definition.data, {
    mySites,
    days: [{ sites: [1], inspection_items: { '1': [101] }, allSitesSelected: false }],
  }) });
  page.setData = (patch, done) => {
    Object.keys(patch).forEach(key => {
      const match = /^days\[(\d+)\]\.(.+)$/.exec(key);
      if (match) page.data.days[Number(match[1])][match[2]] = patch[key];
      else page.data[key] = patch[key];
    });
    if (done) done();
  };
  page.loadInspectionItems = () => {};
  page.refreshValidation = () => {};

  page.onToggleAll({ currentTarget: { dataset: { dayIdx: 0 } } });
  assert.deepEqual(page.data.days[0].sites, [1, 2], '部分选择应补足全部真实可选站点');
  assert.deepEqual(page.data.days[0].inspection_items, { '1': [101] },
    '部分切换为全选不能清掉既有检查项选择');
  assert.equal(page.data.days[0].allSitesSelected, true);

  page.onToggleAll({ currentTarget: { dataset: { dayIdx: 0 } } });
  assert.deepEqual(page.data.days[0].sites, []);
  assert.deepEqual(page.data.days[0].inspection_items, {}, '全不选继续使用既有检查项清理规则');
  assert.equal(page.data.days[0].allSitesSelected, false);
  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('all three plan editor back affordances share one guarded navigation handler', t => {
  let definition;
  const toasts = [];
  let navigateCalls = 0;
  let navigation;
  let writes = 0;
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = {
    showToast: value => toasts.push(value),
    navigateBack: value => { navigateCalls += 1; navigation = value; },
  };
  const api = require(apiPath);
  const originals = { create: api.createPlanSchedule, update: api.updatePlanSchedule };
  api.createPlanSchedule = () => { writes += 1; return Promise.resolve({}); };
  api.updatePlanSchedule = () => { writes += 1; return Promise.resolve({}); };
  t.after(() => {
    api.createPlanSchedule = originals.create;
    api.updatePlanSchedule = originals.update;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  });
  delete require.cache[pagePath];
  require(pagePath);

  const source = fs.readFileSync(path.join(__dirname, '../pages/plan-edit/plan-edit.wxml'), 'utf8');
  assert.equal((source.match(/<view class="pe-nav-back" data-action="back" bindtap="onNavBack">/g) || []).length, 3,
    'ready/loading/error 三个返回热区必须绑定同一处理器');
  const page = Object.assign({}, definition, { data: Object.assign({}, definition.data) });
  page.onNavBack();
  page.onNavBack();
  assert.equal(navigateCalls, 1, '导航处理中重复点击不能再次发起返回');
  assert.equal(navigation.delta, 1);
  assert.equal(writes, 0, '返回失败或重试不得触发任何业务写入');
  assert.match(toasts.at(-1).title, /正在返回/);

  navigation.fail({ errMsg: 'navigateBack:fail' });
  assert.match(toasts.at(-1).title, /无法返回.*重试/);
  page.onNavBack();
  assert.equal(navigateCalls, 2, '失败后应释放门禁供用户重试');
  navigation.success();
});

test('plan editor projects rejection and real site names from authoritative detail and local sites', () => {
  let definition;
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  global.wx = { showToast() {} };
  delete require.cache[pagePath];
  const { projectSiteNameMap } = require(pagePath);

  assert.deepEqual(projectSiteNameMap({
    '91': { id: 91, name: '历史第一水厂' },
    '7': { id: 7, name: '服务端现名' },
  }, [
    { id: 7, name: '本地旧名' },
    { id: 8, name: '新建可选站点' },
  ]), {
    '7': '服务端现名',
    '8': '新建可选站点',
    '91': '历史第一水厂',
  });

  const source = fs.readFileSync(path.join(__dirname, '../pages/plan-edit/plan-edit.wxml'), 'utf8');
  assert.match(source, /detailState === 'ready' && loaded/);
  assert.match(source, /detailState === 'loading'[\s\S]*editId \? '编辑计划' : '新建计划'/,
    '编辑态加载骨架必须显示编辑计划，不能误称新建计划');
  assert.match(source, /siteNameById\[selectedSiteId\]/);
  assert.doesNotMatch(source, /pe-sub-title-group">{{selectedSiteId}}/);
  assert.match(source, /draftSaveError/);
  assert.match(source, /detailState === 'error'[\s\S]*bindtap="onRetryLoadExisting"/);
  assert.equal(definition.data.rejectReason, '');

  delete global.getApp;
  delete global.Page;
  delete global.wx;
});

test('edit detail load rejects duplicates and stale responses while keeping the original target', async t => {
  let definition;
  const toasts = [];
  global.getApp = () => ({ globalData: { token: 'token' } });
  global.Page = value => { definition = value; };
  global.wx = { showToast: value => toasts.push(value) };
  const api = require(apiPath);
  const originalDetail = api.planScheduleDetail;
  const requests = [];
  api.planScheduleDetail = id => {
    const request = deferred();
    requests.push({ id, request });
    return request.promise;
  };
  t.after(() => {
    api.planScheduleDetail = originalDetail;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  });
  delete require.cache[pagePath];
  require(pagePath);

  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.loadVehicles = () => {};
  page.loadInspectionItems = () => {};
  page._pageAlive = true;
  page._pageActive = true;
  page.data.mySites = [{ id: 8, name: '新建可选站点' }];
  page.data.siteNameById = { '8': '新建可选站点' };

  page.loadExisting(42);
  page.loadExisting(42);
  assert.equal(requests.length, 1, '重复加载只能发出一次详情请求');
  assert.match(toasts.at(-1).title, /正在加载/);
  assert.equal(page.data.detailState, 'loading');
  assert.equal(page.data.loaded, false);

  page.onHide();
  page.onShow();
  assert.equal(requests.length, 2, '返回页面后应重取被隐藏生命周期作废的详情请求');
  requests[0].request.resolve(existingPlanDetail({ reject_reason: '过期响应' }));
  await flushAsync();
  assert.equal(page.data.detailState, 'loading');
  assert.equal(page.data.rejectReason, '', '旧响应不得写回页面');

  requests[1].request.resolve(existingPlanDetail());
  await flushAsync();
  assert.equal(page.data.detailState, 'ready');
  assert.equal(page.data.loaded, true);
  assert.equal(page.data.rejectReason, '请补充历史站点安排');
  assert.equal(page.data.siteNameById['91'], '历史第一水厂');
  assert.equal(page.data.siteNameById['8'], '新建可选站点');

  page.loadExisting(42);
  assert.equal(requests.length, 3);
  page.onUnload();
  requests[2].request.resolve(existingPlanDetail({ reject_reason: '卸载后响应' }));
  await flushAsync();
  assert.equal(page.data.rejectReason, '请补充历史站点安排', '卸载后响应不得 setData');
  assert.equal(page.data.detailState, 'loading');
});

test('edit detail failure blocks all writes and retry restores the same edit target', async t => {
  let definition;
  const toasts = [];
  global.getApp = () => ({ globalData: { token: 'token' } });
  global.Page = value => { definition = value; };
  global.wx = { showToast: value => toasts.push(value) };
  const api = require(apiPath);
  const originals = {
    detail: api.planScheduleDetail,
    update: api.updatePlanSchedule,
    create: api.createPlanSchedule,
  };
  const requests = [];
  let writes = 0;
  api.planScheduleDetail = id => {
    const request = deferred();
    requests.push({ id, request });
    return request.promise;
  };
  api.updatePlanSchedule = () => { writes += 1; return Promise.resolve({}); };
  api.createPlanSchedule = () => { writes += 1; return Promise.resolve({}); };
  t.after(() => {
    api.planScheduleDetail = originals.detail;
    api.updatePlanSchedule = originals.update;
    api.createPlanSchedule = originals.create;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  });
  delete require.cache[pagePath];
  require(pagePath);

  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  page.loadVehicles = () => {};
  page.loadInspectionItems = () => {};
  page._pageAlive = true;
  page._pageActive = true;
  page.data.mySites = [];

  page.loadExisting(42);
  requests[0].request.reject({ error: '详情暂不可用' });
  await flushAsync();
  assert.equal(page.data.detailState, 'error');
  assert.equal(page.data.loaded, false, '加载失败不得显示可编辑默认表单');
  assert.equal(page.data.detailLoadError, '详情暂不可用');
  page.onSaveDraft();
  page.onSubmit();
  assert.equal(writes, 0, '详情失败时保存和提交都必须零写入');
  assert.equal(toasts.filter(item => /重试加载/.test(item.title)).length, 2);

  page.onRetryLoadExisting();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].id, 42, '重试必须使用原始 editId');
  assert.equal(page.data.detailState, 'loading');
  requests[1].request.resolve(existingPlanDetail());
  await flushAsync();
  assert.equal(page.data.detailState, 'ready');
  assert.equal(page.data.editId, 42);
  assert.equal(page.data.remarks, '原计划备注');
});

test('draft save failure keeps inputs, reports independently, and retries with one write per attempt', async t => {
  let definition;
  const toasts = [];
  let navigations = 0;
  global.getApp = () => ({ globalData: { token: 'token' } });
  global.Page = value => { definition = value; };
  global.wx = {
    showToast: value => toasts.push(value),
    navigateBack: () => { navigations += 1; },
  };
  const api = require(apiPath);
  const originalUpdate = api.updatePlanSchedule;
  const requests = [];
  api.updatePlanSchedule = (id, payload) => {
    const request = deferred();
    requests.push({ id, payload, request });
    return request.promise;
  };
  t.after(() => {
    api.updatePlanSchedule = originalUpdate;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  });
  delete require.cache[pagePath];
  require(pagePath);

  const originalDays = [{
    date: '2026-09-08', sites: [91], notes: '用户现场安排', inspection_items: { '91': [11] },
  }];
  const page = Object.assign({}, definition, { data: Object.assign({}, definition.data, {
    editId: 42,
    version: 3,
    detailState: 'ready',
    loaded: true,
    inspectionItemsState: 'ready',
    scheduleType: 'monthly',
    periodStart: '2026-09-01',
    periodEnd: '2026-09-30',
    days: originalDays,
    remarks: '用户输入的备注',
    coverageExceptionReason: '覆盖说明',
    noVehicleRequired: true,
    vehicleExceptionReason: '步行巡检',
    selectedParts: [{ part_id: 5, part_name: '滤芯', quantity: 2 }],
    suggestions: [],
    submitError: '正式提交的旧提示',
  }) });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  const inputSnapshot = JSON.parse(JSON.stringify({
    days: page.data.days,
    remarks: page.data.remarks,
    coverageExceptionReason: page.data.coverageExceptionReason,
    vehicleExceptionReason: page.data.vehicleExceptionReason,
    selectedParts: page.data.selectedParts,
  }));

  page.onSaveDraft();
  page.onSaveDraft();
  assert.equal(requests.length, 1, '重复点击只能产生一次草稿写入');
  requests[0].request.reject({ message: '网络暂不可用' });
  await flushAsync();
  assert.equal(page.data.submitting, false);
  assert.match(page.data.draftSaveError, /网络暂不可用[\s\S]*内容已保留，可重试/);
  assert.equal(page.data.submitError, '正式提交的旧提示', '草稿错误不得复用或覆盖 submitError');
  assert.deepEqual({
    days: page.data.days,
    remarks: page.data.remarks,
    coverageExceptionReason: page.data.coverageExceptionReason,
    vehicleExceptionReason: page.data.vehicleExceptionReason,
    selectedParts: page.data.selectedParts,
  }, inputSnapshot);
  assert.equal(navigations, 0);

  const originalSetTimeout = global.setTimeout;
  global.setTimeout = callback => { callback(); return 1; };
  page.onSaveDraft();
  assert.equal(page.data.draftSaveError, '', '人工重试开始时应清除旧草稿错误');
  assert.equal(requests.length, 2);
  requests[1].request.resolve({ version: 4, draft_issue_count: 0 });
  await flushAsync();
  global.setTimeout = originalSetTimeout;
  assert.equal(page.data.version, 4);
  assert.equal(page.data.draftSaveError, '');
  assert.equal(page.data.submitting, false);
  assert.equal(navigations, 1);
  assert.equal(toasts.some(item => item.title === '已保存草稿'), true);
});
