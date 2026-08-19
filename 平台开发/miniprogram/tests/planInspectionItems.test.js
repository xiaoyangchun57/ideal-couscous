const assert = require('node:assert/strict');
const test = require('node:test');

const requestPath = require.resolve('../utils/request.js');
const apiPath = require.resolve('../services/api.js');
const originalRequestModule = require.cache[requestPath];
const pagePath = require.resolve('../pages/plan-edit/plan-edit.js');
const fs = require('node:fs');
const path = require('node:path');

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
      inspectionItemsState: 'ready', selectedParts: [], suggestions: [], planVehicleId: 7,
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

test('unavailable inspection item state exposes a direct retry binding', () => {
  const source = fs.readFileSync(path.join(__dirname, '../pages/plan-edit/plan-edit.wxml'), 'utf8');
  assert.match(source, /inspectionItemsState === 'unavailable'[\s\S]*bindtap="loadInspectionItems"/);
  assert.match(source, /点击重试/);
  assert.match(source, /wx:if="{{scheduleType !== 'weekly'}}"[\s\S]*选择执行日期/);
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
