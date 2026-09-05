const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const api = require('../services/api.js');
const pagePath = require.resolve('../pages/vehicle/vehicle.js');

const app = {
  globalData: {
    token: 'token',
    user: { id: 7, role: 'operator', roles: ['operator'] },
    vehicleTarget: null,
  },
};
let definition;
let toasts = [];
let modals = [];

global.getApp = () => app;
global.Page = value => { definition = value; };
global.wx = {
  showToast(options) { toasts.push(options); },
  showModal(options) { modals.push(options); },
  navigateTo() {},
  reLaunch() {},
  stopPullDownRefresh() {},
};

delete require.cache[pagePath];
require(pagePath);

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

function pageInstance() {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setDataCalls: 0,
  });
  page.setData = patch => {
    page.setDataCalls += 1;
    Object.keys(patch).forEach(key => setPath(page.data, key, patch[key]));
  };
  page.onLoad();
  return page;
}

function paged(items) {
  return { items: items || [], total: (items || []).length, page: 1, limit: 100, has_more: false };
}

function application(id, overrides) {
  return Object.assign({
    id, applicant_id: 7, vehicle_id: 1, plate_no: '赣A00001', status: 'approved',
    can_checkout: false, checkout_block_reason: '', needs_extension: false,
    reserves_vehicle: false, has_active_use: false,
  }, overrides || {});
}

function use(id, applicationId, overrides) {
  return Object.assign({
    id, application_id: applicationId, vehicle_id: 1, plate_no: '赣A00001',
    status: 'checked_out', returned_at: null, can_return: true, needs_extension: false,
  }, overrides || {});
}

const originals = {};
for (const key of [
  'vehicleApplications', 'vehicleUseRecords', 'vehicles', 'extendVehicleApplication',
  'refuelVehicleUse', 'reportVehicleFault', 'submitVehicleInspection', 'returnVehicle',
  'checkOutVehicle', 'applyVehicle', 'vehicleInspectionTemplate', 'sites',
]) originals[key] = api[key];

function stubSuccessfulLoad(options) {
  const opts = options || {};
  api.vehicleApplications = query => Promise.resolve(query && query.application_id
    ? (opts.exact || [])
    : paged(opts.applications || []));
  api.vehicleUseRecords = query => Promise.resolve(query && query.scope === 'history'
    ? paged(opts.history || [])
    : paged(opts.uses || []));
  api.vehicles = () => Promise.resolve(opts.vehicles || []);
  api.sites = () => Promise.resolve(opts.sites || []);
}

test.afterEach(() => {
  Object.assign(api, originals);
  app.globalData.vehicleTarget = null;
  toasts = [];
  modals = [];
});

test('vehicle page exposes explicit load states, exact item actions and the product title', () => {
  const js = fs.readFileSync(path.join(__dirname, '../pages/vehicle/vehicle.js'), 'utf8');
  const view = fs.readFileSync(path.join(__dirname, '../pages/vehicle/vehicle.wxml'), 'utf8');
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '../pages/vehicle/vehicle.json'), 'utf8'));
  const service = fs.readFileSync(path.join(__dirname, '../services/api.js'), 'utf8');

  assert.equal(config.navigationBarTitleText, '我的用车');
  assert.match(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8'), /vehicleTarget:\s*null/);
  assert.match(view, /loadState === 'initial_loading'/);
  assert.match(view, /loadState === 'initial_error'[\s\S]*bindtap="onRetryLoad"/);
  assert.match(view, /loadState === 'refresh_error'[\s\S]*刷新成功前不可操作/);
  assert.match(view, /activeUseConflict[\s\S]*多条未归还行程/);
  assert.match(view, /bindtap="onOpenExtension" data-id="\{\{item\.id\}\}"/);
  assert.match(js, /_writeBlockReason\(\)[\s\S]*authorityFresh[\s\S]*activeUseConflict/);
  assert.match(service, /'application_id'/);
});

test('regular entry loads current data without opening a sheet', async () => {
  stubSuccessfulLoad({ applications: [application(1)] });
  const page = pageInstance();
  page.load();
  await flush();
  assert.equal(page.data.loadState, 'ready');
  assert.equal(page.data.authorityFresh, true);
  assert.equal(page.data.extensionSheet.open, false);
  assert.equal(app.globalData.vehicleTarget, null);
});

test('an exact target outside the first page opens only its extension sheet with zero writes', async () => {
  let extensionWrites = 0;
  api.extendVehicleApplication = () => { extensionWrites += 1; return Promise.resolve({}); };
  app.globalData.vehicleTarget = {
    applicationId: 120, expectedAction: 'extend', source: 'vehicle_use_expiry'
  };
  stubSuccessfulLoad({
    applications: [application(1)],
    exact: [application(120, { needs_extension: true, reserves_vehicle: true })],
  });
  const page = pageInstance();
  page.load();
  await flush();

  assert.equal(page.data.extensionSheet.open, true);
  assert.equal(page.data.extensionSheet.applicationId, 120);
  assert.equal(extensionWrites, 0);
  assert.equal(app.globalData.vehicleTarget, null);
});

test('failed target load remains retryable and a changed target never opens another application', async () => {
  app.globalData.vehicleTarget = {
    applicationId: 44, expectedAction: 'extend', source: 'inspection_departure'
  };
  api.vehicleApplications = query => query && query.application_id
    ? Promise.reject({ error: '网络不可用' })
    : Promise.resolve(paged([]));
  api.vehicleUseRecords = () => Promise.resolve(paged([]));
  api.vehicles = () => Promise.resolve([]);
  api.sites = () => Promise.resolve([]);
  const page = pageInstance();
  page.load();
  await flush();
  assert.equal(page.data.loadState, 'initial_error');
  assert.equal(page.data.extensionSheet.open, false);
  assert.equal(app.globalData.vehicleTarget.applicationId, 44);

  stubSuccessfulLoad({ exact: [application(44, { needs_extension: false })] });
  page.onRetryLoad();
  await flush();
  assert.equal(page.data.loadState, 'ready');
  assert.equal(page.data.extensionSheet.open, false);
  assert.equal(app.globalData.vehicleTarget, null);
  assert.match(toasts.at(-1).title, /无需延续/);
});

test('missing or mismatched targets never fall back to another application', async () => {
  app.globalData.vehicleTarget = {
    applicationId: 88, expectedAction: 'inspect', source: 'vehicle_use_expiry'
  };
  stubSuccessfulLoad({
    applications: [application(1, { needs_extension: true })],
    exact: [application(88, { needs_extension: true })],
  });
  const mismatched = pageInstance();
  mismatched.load();
  await flush();
  assert.equal(mismatched.data.extensionSheet.open, false);
  assert.equal(app.globalData.vehicleTarget, null);
  assert.match(toasts.at(-1).title, /动作无效/);

  app.globalData.vehicleTarget = {
    applicationId: 89, expectedAction: 'extend', source: 'vehicle_use_expiry'
  };
  stubSuccessfulLoad({ applications: [application(1, { needs_extension: true })], exact: [] });
  const missing = pageInstance();
  missing.load();
  await flush();
  assert.equal(missing.data.extensionSheet.open, false);
  assert.equal(app.globalData.vehicleTarget, null);
  assert.match(toasts.at(-1).title, /不存在、已结束或无权查看/);
});

test('only the newest load response applies and an unloaded page ignores late responses', async () => {
  const first = deferred();
  const second = deferred();
  let applicationCalls = 0;
  api.vehicleApplications = () => (++applicationCalls === 1 ? first.promise : second.promise);
  api.vehicleUseRecords = () => Promise.resolve(paged([]));
  api.vehicles = () => Promise.resolve([]);
  api.sites = () => Promise.resolve([]);
  const page = pageInstance();
  page.load();
  page.load();
  second.resolve(paged([application(2)]));
  await flush();
  first.resolve(paged([application(1)]));
  await flush();
  assert.deepEqual(page.data.applications.map(item => item.id), [2]);

  const late = deferred();
  api.vehicleApplications = () => late.promise;
  const unloaded = pageInstance();
  unloaded.load();
  const writesBeforeUnload = unloaded.setDataCalls;
  unloaded.onUnload();
  late.resolve(paged([application(9)]));
  await flush();
  assert.equal(unloaded.setDataCalls, writesBeforeUnload);
  assert.deepEqual(unloaded.data.applications, []);

  app.globalData.vehicleTarget = {
    applicationId: 99, expectedAction: 'extend', source: 'vehicle_use_expiry'
  };
  const targetPage = pageInstance();
  targetPage.onUnload();
  assert.equal(app.globalData.vehicleTarget, null,
    'unloading finishes a pending one-shot target');
});

test('refresh failure preserves cards but every write entry remains blocked until success', async () => {
  let writes = 0;
  api.applyVehicle = () => { writes += 1; return Promise.resolve({}); };
  stubSuccessfulLoad({
    applications: [application(5)],
    vehicles: [{ id: 1, plate_no: '赣A00001', dispatchable: true }],
  });
  const page = pageInstance();
  page.load();
  await flush();
  api.vehicleApplications = () => Promise.reject({ error: '刷新失败' });
  page.load();
  await flush();
  assert.equal(page.data.loadState, 'refresh_error');
  assert.deepEqual(page.data.applications.map(item => item.id), [5]);
  page.onOpenApply();
  assert.equal(page.data.applySheet.open, false);
  assert.equal(writes, 0);
  assert.match(toasts.at(-1).title, /刷新/);
});

test('multiple active trips block writes and extension rows use their own application id', async () => {
  let refuelWrites = 0;
  api.refuelVehicleUse = () => { refuelWrites += 1; return Promise.resolve({}); };
  stubSuccessfulLoad({
    applications: [
      application(11, { needs_extension: true, reserves_vehicle: true }),
      application(12, { needs_extension: true, reserves_vehicle: true }),
    ],
    uses: [use(1, 11), use(2, 12)],
  });
  const conflictPage = pageInstance();
  conflictPage.load();
  await flush();
  assert.equal(conflictPage.data.activeUseConflict, true);
  assert.equal(conflictPage.data.activeUse, null);
  conflictPage.onOpenRefuel();
  assert.equal(refuelWrites, 0);
  assert.match(toasts.at(-1).title, /多条未归还行程/);

  stubSuccessfulLoad({
    applications: [
      application(21, { needs_extension: true, reserves_vehicle: true }),
      application(22, { needs_extension: true, reserves_vehicle: true }),
    ],
  });
  const arrangements = pageInstance();
  arrangements.load();
  await flush();
  assert.deepEqual(arrangements.data.applications.map(item => item.id), [21, 22]);
  arrangements.onOpenExtension({ currentTarget: { dataset: { id: 22 } } });
  assert.equal(arrangements.data.extensionSheet.applicationId, 22);
});

test('application submits a cross-day server-scoped site with one stable request key', async () => {
  let payload;
  let writes = 0;
  const pending = deferred();
  api.applyVehicle = value => { writes += 1; payload = value; return pending.promise; };
  const page = pageInstance();
  page.setData({
    authorityFresh: true,
    sitesAuthorityFresh: true,
    vehicles: [{ id: 1, plate_no: '赣A00001' }],
    sites: [{ id: 9, name: '授权水站' }],
  });
  page.onOpenApply();
  page.setData({
    'applySheet.startDate': '2026-09-04',
    'applySheet.startTime': '18:00',
    'applySheet.endDate': '2026-09-05',
    'applySheet.endTime': '08:00',
    'applySheet.reason': '跨日巡检',
  });
  const requestKey = page.data.applySheet.requestKey;
  page.onSubmitApply();
  page.onSubmitApply();
  assert.equal(writes, 1);
  assert.deepEqual(payload, {
    vehicle_id: 1,
    start_at: '2026-09-04 18:00:00',
    end_at: '2026-09-05 08:00:00',
    destination_mode: 'site',
    site_id: 9,
    destination: '',
    reason: '跨日巡检',
    _idempotency_key: requestKey,
  });
  pending.reject({ error: '网络失败' });
  await flush();
  assert.equal(page.data.applySheet.submitting, false);
  assert.equal(page.data.applySheet.reason, '跨日巡检');
  assert.equal(page.data.applySheet.requestKey, requestKey);
  page.onSubmitApply();
  await flush();
  assert.equal(writes, 2);
  assert.equal(payload._idempotency_key, requestKey);
});

test('application exposes other destination only by explicit mode and validates date range', () => {
  let writes = 0;
  api.applyVehicle = () => { writes += 1; return Promise.resolve({}); };
  const page = pageInstance();
  page.setData({ authorityFresh: true, sitesAuthorityFresh: true, vehicles: [{ id: 1 }], sites: [] });
  page.onOpenApply();
  assert.equal(page.data.applySheet.destinationMode, 'other');
  page.setData({
    'applySheet.startDate': '2026-09-05',
    'applySheet.endDate': '2026-09-04',
    'applySheet.otherDestination': '维修点',
    'applySheet.reason': '送修',
  });
  page.onSubmitApply();
  assert.equal(writes, 0);
  assert.match(toasts.at(-1).title, /结束时间/);

  page.setData({ 'applySheet.endDate': '2026-09-05', 'applySheet.otherDestination': ' '.repeat(3) });
  page.onSubmitApply();
  assert.equal(writes, 0);
  assert.match(toasts.at(-1).title, /其他地点/);
});

test('destination and safety handlers accept picker or explicit data-index without losing false', () => {
  const page = pageInstance();
  page.onApplyDestinationMode({ currentTarget: { dataset: { index: 1 } }, detail: {} });
  assert.equal(page.data.applySheet.destinationModeIndex, 1);
  assert.equal(page.data.applySheet.destinationMode, 'other');
  page.onApplyDestinationMode({ currentTarget: { dataset: { index: 0 } }, detail: {} });
  assert.equal(page.data.applySheet.destinationModeIndex, 0);
  assert.equal(page.data.applySheet.destinationMode, 'site');
  page.onApplyDestinationMode({ detail: { value: '1' } });
  assert.equal(page.data.applySheet.destinationMode, 'other');

  page.onFaultSafetyPick({ currentTarget: { dataset: { index: 0 } }, detail: {} });
  assert.equal(page.data.faultSheet.affectsSafeOperation, false);
  assert.equal(page.data.faultSheet.safetyLabel, '不影响安全行驶');
  page.onFaultSafetyPick({ currentTarget: { dataset: { index: 1 } }, detail: {} });
  assert.equal(page.data.faultSheet.affectsSafeOperation, true);
  assert.equal(page.data.faultSheet.safetyLabel, '影响安全行驶');
  page.onFaultSafetyPick({ detail: { value: '0' } });
  assert.equal(page.data.faultSheet.affectsSafeOperation, false);
  assert.equal(page.data.faultSheet.safetyLabel, '不影响安全行驶');
});

test('invalid explicit or picker option indexes preserve state and perform zero writes', () => {
  let writes = 0;
  api.applyVehicle = () => { writes += 1; return Promise.resolve({}); };
  api.reportVehicleFault = () => { writes += 1; return Promise.resolve({}); };
  const page = pageInstance();
  page.setData({
    'applySheet.destinationModeIndex': 1,
    'applySheet.destinationMode': 'other',
    'faultSheet.affectsSafeOperation': false,
    'faultSheet.safetyLabel': '不影响安全行驶',
  });
  const invalidEvents = [
    { currentTarget: { dataset: { index: '' } }, detail: { value: '0' } },
    { currentTarget: { dataset: { index: -1 } }, detail: { value: '0' } },
    { currentTarget: { dataset: { index: 2 } }, detail: { value: '0' } },
    { currentTarget: { dataset: { index: 'abc' } }, detail: { value: '0' } },
    { detail: { value: '' } },
    { detail: { value: '9' } },
    {},
  ];
  invalidEvents.forEach(event => {
    page.onApplyDestinationMode(event);
    assert.equal(page.data.applySheet.destinationModeIndex, 1);
    assert.equal(page.data.applySheet.destinationMode, 'other');
    assert.match(toasts.at(-1).title, /目的地选项无效/);
    page.onFaultSafetyPick(event);
    assert.equal(page.data.faultSheet.affectsSafeOperation, false);
    assert.equal(page.data.faultSheet.safetyLabel, '不影响安全行驶');
    assert.match(toasts.at(-1).title, /安全影响选项无效/);
  });
  assert.equal(writes, 0);
});

test('refueling requires amount and retries with the same idempotency key while preserving input', async () => {
  let payload;
  api.refuelVehicleUse = (id, value) => { payload = value; return Promise.reject({ error: '暂时不可用' }); };
  const page = pageInstance();
  page.setData({ authorityFresh: true, activeUse: use(1, 1, { start_mileage: 1000, fuel_type: 'electric' }) });
  page.onOpenRefuel();
  page.setData({ 'refuelSheet.quantity': '12.5', 'refuelSheet.mileage': '1010' });
  page.onSubmitRefuel();
  assert.match(toasts.at(-1).title, /金额/);
  page.setData({ 'refuelSheet.amount': '30' });
  const requestKey = page.data.refuelSheet.requestKey;
  page.onSubmitRefuel();
  await flush();
  assert.equal(payload._idempotency_key, requestKey);
  assert.equal(page.data.refuelSheet.submitting, false);
  assert.equal(page.data.refuelSheet.amount, '30');
  assert.equal(page.data.refuelSheet.quantity, '12.5');
  page.onSubmitRefuel();
  await flush();
  assert.equal(payload._idempotency_key, requestKey);
});

test('fault requires an explicit safety choice and uses the server returned vehicle status', async () => {
  let payload;
  stubSuccessfulLoad();
  api.reportVehicleFault = (id, value) => {
    payload = value;
    return Promise.resolve({ vehicle_status: 'restricted' });
  };
  const page = pageInstance();
  page.setData({ authorityFresh: true, activeUse: use(1, 1, { start_mileage: 1000 }) });
  page.onOpenFault();
  page.setData({ 'faultSheet.mileage': '1010', 'faultSheet.description': '低速异响' });
  page.onSubmitFault();
  assert.match(toasts.at(-1).title, /是否影响安全/);
  page.onFaultSafetyPick({ detail: { value: '0' } });
  const requestKey = page.data.faultSheet.requestKey;
  page.onSubmitFault();
  await flush();
  assert.equal(payload.affects_safe_operation, false);
  assert.equal(payload._idempotency_key, requestKey);
  assert.match(modals.at(-1).content, /此前已受限/);

  const view = fs.readFileSync(path.join(__dirname, '../pages/vehicle/vehicle.wxml'), 'utf8');
  assert.match(view, /途中补给/);
  assert.match(view, /金额（元）/);
  assert.match(view, /faultSheet\.affectsSafeOperation[\s\S]*上报并限制使用[\s\S]*提交故障上报/);
  assert.match(view, /applySheet\.startDate[\s\S]*applySheet\.endDate/);
  assert.match(view, /applySheet\.destinationMode === 'site'/);
});

test('empty mileage never becomes zero when the trip starts at zero, while explicit zero is valid', async () => {
  let refuelWrites = 0;
  let faultWrites = 0;
  let refuelPayload;
  let faultPayload;
  api.refuelVehicleUse = (id, payload) => {
    refuelWrites += 1;
    refuelPayload = payload;
    return Promise.reject({ error: '测试失败' });
  };
  api.reportVehicleFault = (id, payload) => {
    faultWrites += 1;
    faultPayload = payload;
    return Promise.reject({ error: '测试失败' });
  };
  const page = pageInstance();
  page.setData({ authorityFresh: true, activeUse: use(1, 1, { start_mileage: 0 }) });

  page.onOpenRefuel();
  page.setData({ 'refuelSheet.quantity': '1', 'refuelSheet.amount': '1', 'refuelSheet.mileage': '' });
  page.onSubmitRefuel();
  page.setData({ 'refuelSheet.mileage': '   ' });
  page.onSubmitRefuel();
  assert.equal(refuelWrites, 0);
  assert.match(toasts.at(-1).title, /当前里程/);
  page.setData({ 'refuelSheet.mileage': '0' });
  page.onSubmitRefuel();
  await flush();
  assert.equal(refuelWrites, 1);
  assert.equal(refuelPayload.mileage_at, 0);

  page.onOpenFault();
  page.setData({
    'faultSheet.description': '测试故障',
    'faultSheet.affectsSafeOperation': false,
    'faultSheet.mileage': '',
  });
  page.onSubmitFault();
  page.setData({ 'faultSheet.mileage': '  ' });
  page.onSubmitFault();
  assert.equal(faultWrites, 0);
  assert.match(toasts.at(-1).title, /故障里程/);
  page.setData({ 'faultSheet.mileage': '0' });
  page.onSubmitFault();
  await flush();
  assert.equal(faultWrites, 1);
  assert.equal(faultPayload.mileage_at, 0);
});

test('site loading failure keeps core trip actions usable and only gates application until refresh', async () => {
  let applicationWrites = 0;
  stubSuccessfulLoad({
    applications: [application(1)],
    uses: [use(1, 1, { start_mileage: 1000 })],
    vehicles: [{ id: 1, plate_no: '赣A00001', dispatchable: true }],
  });
  api.sites = () => Promise.reject({ error: '站点网络失败' });
  api.applyVehicle = () => { applicationWrites += 1; return Promise.resolve({}); };
  const page = pageInstance();
  page.load();
  await flush();
  assert.equal(page.data.loadState, 'ready');
  assert.equal(page.data.authorityFresh, true);
  assert.equal(page.data.sitesAuthorityFresh, false);
  assert.match(page.data.sitesLoadError, /站点网络失败/);

  page.onOpenRefuel();
  assert.equal(page.data.refuelSheet.open, true);
  page.onOpenApply();
  assert.equal(page.data.applySheet.open, false);
  assert.equal(applicationWrites, 0);
  assert.match(toasts.at(-1).title, /站点信息加载失败.*刷新/);

  api.sites = () => Promise.resolve([{ id: 9, name: '恢复站点' }]);
  page.load();
  await flush();
  assert.equal(page.data.loadState, 'ready');
  assert.equal(page.data.sitesAuthorityFresh, true);
  assert.deepEqual(page.data.sites.map(item => item.id), [9]);
  page.onOpenApply();
  assert.equal(page.data.applySheet.open, true);
});

test('stale and hidden site responses cannot overwrite the newest site authority state', async () => {
  const first = deferred();
  const second = deferred();
  let calls = 0;
  stubSuccessfulLoad();
  api.sites = () => (++calls === 1 ? first.promise : second.promise);
  const page = pageInstance();
  page.load();
  page.load();
  second.resolve([{ id: 2, name: '新站点' }]);
  await flush();
  first.resolve([{ id: 1, name: '旧站点' }]);
  await flush();
  assert.deepEqual(page.data.sites.map(item => item.id), [2]);
  assert.equal(page.data.sitesAuthorityFresh, true);

  const hidden = deferred();
  api.sites = () => hidden.promise;
  page._loadSites();
  page.onHide();
  hidden.resolve([{ id: 3, name: '隐藏后返回' }]);
  await flush();
  assert.deepEqual(page.data.sites.map(item => item.id), [2]);
  assert.equal(page.data.sitesAuthorityFresh, false);
});

test('all four submitting sheets refuse close and preserve their retry context', () => {
  let writes = 0;
  api.applyVehicle = api.refuelVehicleUse = api.reportVehicleFault = api.extendVehicleApplication = () => {
    writes += 1;
    return Promise.resolve({});
  };
  const page = pageInstance();
  page.setData({
    applySheet: { open: true, submitting: true, reason: '申请输入', requestKey: 'apply-key' },
    refuelSheet: { open: true, submitting: true, amount: '20', requestKey: 'refuel-key' },
    faultSheet: { open: true, submitting: true, description: '故障输入', requestKey: 'fault-key' },
    extensionSheet: { open: true, submitting: true, endDate: '2026-09-10', applicationId: 8 },
  });
  const before = JSON.parse(JSON.stringify({
    applySheet: page.data.applySheet,
    refuelSheet: page.data.refuelSheet,
    faultSheet: page.data.faultSheet,
    extensionSheet: page.data.extensionSheet,
  }));
  page.onCloseApply();
  page.onCloseRefuel();
  page.onCloseFault();
  page.onCloseExtension();
  assert.deepEqual({
    applySheet: page.data.applySheet,
    refuelSheet: page.data.refuelSheet,
    faultSheet: page.data.faultSheet,
    extensionSheet: page.data.extensionSheet,
  }, before);
  assert.equal(writes, 0);
  assert.equal(toasts.slice(-4).every(item => /正在处理中，请稍候/.test(item.title)), true);
});

test('fault and extension failures release submitting state and retry the same context', async () => {
  const faultKeys = [];
  const extensionCalls = [];
  api.reportVehicleFault = (id, payload) => {
    faultKeys.push(payload._idempotency_key);
    return Promise.reject({ error: '故障网络失败' });
  };
  api.extendVehicleApplication = (id, endDate) => {
    extensionCalls.push([id, endDate]);
    return Promise.reject({ error: '延续网络失败' });
  };
  const page = pageInstance();
  page.setData({ authorityFresh: true, activeUse: use(1, 8, { start_mileage: 0 }) });
  page.onOpenFault();
  page.setData({
    'faultSheet.mileage': '0',
    'faultSheet.description': '故障输入',
    'faultSheet.affectsSafeOperation': false,
  });
  const faultKey = page.data.faultSheet.requestKey;
  page.onSubmitFault();
  await flush();
  page.onSubmitFault();
  await flush();
  assert.deepEqual(faultKeys, [faultKey, faultKey]);
  assert.equal(page.data.faultSheet.submitting, false);
  assert.equal(page.data.faultSheet.description, '故障输入');

  page.setData({ extensionSheet: { open: true, submitting: false, applicationId: 8, endDate: '2026-09-10' } });
  page.onSubmitExtension();
  await flush();
  page.onSubmitExtension();
  await flush();
  assert.deepEqual(extensionCalls, [[8, '2026-09-10'], [8, '2026-09-10']]);
  assert.equal(page.data.extensionSheet.submitting, false);
  assert.equal(page.data.extensionSheet.endDate, '2026-09-10');
});
