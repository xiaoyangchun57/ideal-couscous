const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

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
let toasts = [];
let modals = [];
let modalResponse = { confirm: true, cancel: false };
global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getStorageSync: () => [], setStorageSync: () => {}, removeStorageSync: () => {},
  showToast: options => { toasts.push(options); },
  showModal: options => {
    modals.push(options);
    if (options.success) options.success(modalResponse);
  },
  navigateTo: options => { if (options.success) options.success(); },
  switchTab: () => {}, reLaunch: () => {}, stopPullDownRefresh: () => {},
  setNavigationBarTitle: () => {},
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
};

const api = require('../services/api.js');
const { buildDeparturePreparation } = require('../utils/departurePreparation.js');
require('../pages/inspection/inspection.js');

function createPage() {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setDataCalls: 0,
  });
  page.setData = (patchData, done) => {
    page.setDataCalls += 1;
    Object.keys(patchData).forEach(key => setPath(page.data, key, patchData[key]));
    if (done) done();
  };
  page.onLoad();
  page._departureEpoch = 1;
  page._departureActionIds = {};
  return page;
}

function normalPackage(overrides) {
  return Object.assign({
    plan_id: 10,
    schedule_id: 100,
    work_date: '2026-08-28',
    sites: [{ site_id: 20, name: '青云站', total: 1, completed: 0, abnormal: 0 }],
    resource_parts: [],
    departure_confirmation: null,
    is_rework: false,
    is_carryover: false,
    arrival_gate: { allowed: true, code: null, message: null },
  }, overrides || {});
}

function seedDeparture(page, packageOverrides, targetOverrides) {
  const pkg = normalPackage(packageOverrides);
  const target = Object.assign({
    scheduleId: pkg.schedule_id,
    executionPlanId: pkg.plan_id,
    siteId: pkg.sites[0].site_id,
    workDate: pkg.work_date,
    source: 'plan_detail',
  }, targetOverrides || {});
  page.data.viewPhase = 'departure';
  page.data.departureState = 'ready';
  page.data.departureTarget = target;
  page.data.currentPackage = pkg;
  page.data.packages = [pkg];
  page.data.departureVm = buildDeparturePreparation(pkg, target);
  page.data.departureRefreshError = '';
  return { pkg, target };
}

const originals = {};
for (const key of [
  'confirmDepartureResources', 'issueExecutionParts', 'todayExecution', 'executionSiteTasks',
  'vehicles', 'requestReworkResource', 'vehicleInspectionTemplate',
  'submitVehicleInspection', 'checkOutVehicle',
]) originals[key] = api[key];

test.afterEach(() => {
  Object.assign(api, originals);
  toasts = [];
  modals = [];
  modalResponse = { confirm: true, cancel: false };
});

function checkoutPrimaryBinding() {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
  const primaryTag = wxml.match(/<view\s+class="departure-primary-btn"[\s\S]*?>/)[0];
  assert.match(primaryTag, /data-action="\{\{departureVm\.vehicle\.primaryAction\}\}"/);
  return { name: '车辆卡出车登记', handler: primaryTag.match(/bindtap="([^"]+)"/)[1] };
}

function matchingBlockRange(wxml, marker) {
  const start = wxml.indexOf(marker);
  assert.ok(start >= 0, 'expected conditional block must exist');
  const tags = /<\/?block\b[^>]*>/g;
  tags.lastIndex = start;
  let depth = 0;
  let match;
  while ((match = tags.exec(wxml))) {
    if (match[0].startsWith('</')) depth -= 1;
    else if (!match[0].endsWith('/>')) depth += 1;
    if (depth === 0) return { start, end: tags.lastIndex };
  }
  assert.fail('conditional block must have a matching closing tag');
}

test('departure WXML handlers are reachable and legacy aggregate copy is absent', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
  const style = fs.readFileSync(path.join(__dirname, '../pages/inspection/inspection.wxss'), 'utf8');
  for (const handler of [
    'onDepartureToggleAck', 'onDepartureVehicleAction',
    'onDepartureIssueParts', 'onDepartureContinueToArrival',
  ]) {
    assert.match(wxml, new RegExp(handler));
    assert.equal(typeof definition[handler], 'function', `${handler} must exist on the page`);
  }
  for (const removed of ['onConfirmDeparture', '一键确认', '已就绪', '待准备', '实现说明', '动作代次保护']) {
    assert.equal(wxml.includes(removed), false, `${removed} must not remain in the business page`);
  }
  const footerZ = Number(style.match(/\.departure-footer\s*\{[\s\S]*?z-index:\s*(\d+)/)[1]);
  const maskZ = Number(style.match(/\.vehicle-check-mask\s*\{[\s\S]*?z-index:\s*(\d+)/)[1]);
  const sheetZ = Number(style.match(/\.vehicle-check-sheet\s*\{[\s\S]*?z-index:\s*(\d+)/)[1]);
  assert.ok(maskZ > footerZ, 'vehicle check mask must cover the fixed departure footer');
  assert.ok(sheetZ > maskZ, 'vehicle check sheet must stay above its mask');
  const inspectionBlock = matchingBlockRange(
    wxml, '<block wx:if="{{viewPhase === \'inspection\'}}">'
  );
  const maskIndex = wxml.indexOf('class="sheet-mask vehicle-check-mask"');
  const sheetIndex = wxml.indexOf('class="sheet vehicle-check-sheet"');
  assert.equal(wxml.match(/class="sheet-mask vehicle-check-mask"/g).length, 1);
  assert.equal(wxml.match(/class="sheet vehicle-check-sheet"/g).length, 1);
  for (const index of [maskIndex, sheetIndex]) {
    assert.ok(index > inspectionBlock.end,
      'the shared vehicle sheet must not inherit the inspection-only ancestor condition');
  }
  assert.match(wxml, /vehicleSheet\.mode === 'return' \? '还车检查' : '出车前检查'/);
  assert.match(wxml, /vehicleSheet\.mode === 'return' \? '确认检查并还车' : '确认检查并出车'/);
  const scrollRule = style.match(/\.vehicle-check-scroll\s*\{([\s\S]*?)\}/)[1];
  assert.match(scrollRule, /padding:\s*0\s*;/);
  assert.doesNotMatch(scrollRule, /padding:\s*0\s+32rpx/);
});

test('the vehicle card is the single checkout entry and drives the reliable Page chain', async () => {
  let inspectionWrites = 0;
  let checkoutWrites = 0;
  api.submitVehicleInspection = () => { inspectionWrites += 1; return Promise.resolve({ id: 91 }); };
  api.checkOutVehicle = () => { checkoutWrites += 1; return Promise.resolve({}); };
  const pending = deferred();
  let templateCalls = 0;
  api.vehicleInspectionTemplate = () => { templateCalls += 1; return pending.promise; };
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 35, plate_no: '赣AFF3288', current_mileage: 115738 },
    vehicle_application_id: 21,
    arrival_gate: {
      allowed: false, code: 'VEHICLE_CHECKOUT_REQUIRED',
      message: '计划车辆尚未完成出车登记，出车后方可到站',
    },
  });
  const binding = checkoutPrimaryBinding();
  const action = page.data.departureVm.vehicle.primaryAction;
  assert.equal(action, 'checkout');
  assert.equal(page.data.departureVm.todoItems.some(item => item.action === 'checkout'), false);
  assert.equal(typeof page[binding.handler], 'function');

  page[binding.handler]({ currentTarget: { dataset: { action } } });
  assert.equal(page.data.departureVehicleTemplateLoading, true,
    binding.name + ' must show loading before the request settles');
  page[binding.handler]({ currentTarget: { dataset: { action } } });
  assert.equal(toasts.at(-1).title, '出车前检查正在加载，请稍候');
  await flush();
  assert.equal(templateCalls, 1, binding.name + ' must de-duplicate the template read');
  pending.resolve([{ key: 'tyre', label: '轮胎及胎压', status: 'normal', remark: '' }]);
  await flush();
  assert.equal(page.data.departureVehicleTemplateLoading, false);
  assert.equal(page.data.vehicleSheet.open, true, binding.name + ' must open the check sheet');
  assert.equal(page.data.vehicleSheet.items.length, 1);
  assert.equal(inspectionWrites, 0);
  assert.equal(checkoutWrites, 0);
});

test('template loading de-duplicates checkout without locking independent preparation actions', async () => {
  const pendingTemplate = deferred();
  let templateCalls = 0;
  const confirmations = [];
  const serverConfirmation = { vehicle_confirmed: 0, parts_confirmed: 0 };
  api.vehicleInspectionTemplate = () => {
    templateCalls += 1;
    return pendingTemplate.promise;
  };
  api.confirmDepartureResources = (planId, payload) => {
    confirmations.push({ planId, payload });
    if (Object.prototype.hasOwnProperty.call(payload, 'vehicle_confirmed')) {
      serverConfirmation.vehicle_confirmed = payload.vehicle_confirmed ? 1 : 0;
    }
    if (Object.prototype.hasOwnProperty.call(payload, 'parts_confirmed')) {
      serverConfirmation.parts_confirmed = payload.parts_confirmed ? 1 : 0;
    }
    return Promise.resolve({ confirmation: Object.assign({}, serverConfirmation) });
  };
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 35, plate_no: '赣AFF3288', current_mileage: 115738 },
    vehicle_application_id: 21,
    resource_parts: [
      { part_id: 3, part_name: '滤芯', planned_quantity: 2, issued_quantity: 0, remaining_quantity: 2 },
    ],
  });

  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  assert.equal(toasts.at(-1).title, '出车前检查正在加载，请稍候');
  await flush();
  assert.equal(templateCalls, 1);

  page.onDepartureConfirmVehicle();
  page.onDepartureConfirmParts();
  await flush();
  assert.deepEqual(confirmations, [
    { planId: 10, payload: { vehicle_confirmed: true } },
    { planId: 10, payload: { parts_confirmed: true } },
  ]);
  assert.equal(page.data.departureVm.vehicleAcknowledgement.status, 'confirmed');
  assert.equal(page.data.departureVm.partsAcknowledgement.status, 'confirmed');
  assert.notEqual(toasts.at(-1).title, '出车前检查正在加载，请稍候');

  page.onDepartureIssueParts();
  assert.equal(page.data.partsIssueSheet.open, true,
    'parts issue keeps its own gate while the vehicle template is pending');
  assert.equal(page.data.partsIssueSheet.items.length, 1);

  pendingTemplate.resolve([{ key: 'tyre', label: '轮胎及胎压', status: 'normal' }]);
  await flush();
  assert.equal(page.data.vehicleSheet.open, true);
});

test('template timeout stays on departure, explains retry and never writes before confirmation', async () => {
  const pending = deferred();
  let templateCalls = 0;
  let inspectionWrites = 0;
  let checkoutWrites = 0;
  api.vehicleInspectionTemplate = () => { templateCalls += 1; return pending.promise; };
  api.submitVehicleInspection = () => { inspectionWrites += 1; return Promise.resolve({ id: 91 }); };
  api.checkOutVehicle = () => { checkoutWrites += 1; return Promise.resolve({}); };
  modalResponse = { confirm: false, cancel: true };
  const page = createPage();
  page._vehicleTemplateTimeoutMs = 5;
  seedDeparture(page, {
    vehicle: { id: 35, plate_no: '赣AFF3288', current_mileage: 115738 },
    vehicle_application_id: 21,
  });

  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  assert.equal(page.data.departureVehicleTemplateLoading, true);
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(page.data.viewPhase, 'departure');
  assert.equal(page.data.vehicleSheet.open, false);
  assert.equal(page.data.departureVehicleTemplateLoading, false);
  assert.equal(modals.at(-1).title, '车辆检查加载失败');
  assert.equal(modals.at(-1).content, '车辆检查项加载超时，请重试');
  assert.equal(inspectionWrites, 0);
  assert.equal(checkoutWrites, 0);

  api.vehicleInspectionTemplate = () => {
    templateCalls += 1;
    return Promise.resolve([{ key: 'tyre', label: '轮胎及胎压', status: 'normal' }]);
  };
  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  await flush();
  await flush();
  assert.equal(templateCalls, 2);
  assert.equal(page.data.vehicleSheet.open, true);
  assert.equal(inspectionWrites, 0);
  assert.equal(checkoutWrites, 0);
});

test('late template results cannot open a sheet after hide or target exit', async () => {
  for (const transition of ['hide', 'back']) {
    const pending = deferred();
    api.vehicleInspectionTemplate = () => pending.promise;
    const page = createPage();
    seedDeparture(page, {
      vehicle: { id: 35, plate_no: '赣AFF3288', current_mileage: 115738 },
      vehicle_application_id: 21,
    });
    page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
    await flush();
    assert.equal(page.data.departureVehicleTemplateLoading, true);

    if (transition === 'hide') page.onHide();
    else page.onDepartureBack();
    const writesAfterTransition = page.setDataCalls;
    pending.resolve([{ key: 'tyre', label: '轮胎及胎压', status: 'normal' }]);
    await flush();
    assert.equal(page.data.vehicleSheet.open, false, transition + ' must keep the old sheet closed');
    assert.equal(page.setDataCalls, writesAfterTransition,
      transition + ' must reject the old template result without a late write');
    if (transition === 'back') {
      assert.equal(page.data.viewPhase, 'entry');
      assert.equal(page.data.departureVehicleTemplateLoading, false);
    }
  }
});

test('vehicle and parts acknowledgements submit only their own field', async () => {
  const calls = [];
  const serverConfirmation = { vehicle_confirmed: 0, parts_confirmed: 0 };
  api.confirmDepartureResources = (planId, payload) => {
    calls.push({ planId, payload });
    if (Object.prototype.hasOwnProperty.call(payload, 'vehicle_confirmed')) {
      serverConfirmation.vehicle_confirmed = payload.vehicle_confirmed ? 1 : 0;
    }
    if (Object.prototype.hasOwnProperty.call(payload, 'parts_confirmed')) {
      serverConfirmation.parts_confirmed = payload.parts_confirmed ? 1 : 0;
    }
    return Promise.resolve({ confirmation: Object.assign({}, serverConfirmation, {
      confirmed_at: '2026-08-28 08:00:00',
    }) });
  };
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 1, plate_no: '赣A00001', current_mileage: 1000 },
    vehicle_application_id: 9,
    resource_parts: [
      { part_id: 3, part_name: '滤芯', planned_quantity: 2, issued_quantity: 0, remaining_quantity: 2 },
    ],
  });

  page.onDepartureConfirmVehicle();
  await flush();
  assert.deepEqual(calls[0], {
    planId: 10, payload: { vehicle_confirmed: true },
  });
  assert.equal(page.data.departureVm.vehicleAcknowledgement.status, 'confirmed');
  assert.equal(page.data.departureVm.partsAcknowledgement.status, 'unconfirmed');

  page.onDepartureConfirmParts();
  await flush();
  assert.deepEqual(calls[1], {
    planId: 10, payload: { parts_confirmed: true },
  });
  assert.equal(page.data.departureVm.partsAcknowledgement.status, 'confirmed');
});

test('vehicle inspection template failure stays on departure and retries without writes', async () => {
  const retry = deferred();
  let templateCalls = 0;
  let inspectionWrites = 0;
  let checkoutWrites = 0;
  api.vehicleInspectionTemplate = () => {
    templateCalls += 1;
    return templateCalls === 1
      ? Promise.reject({ error: '检查模板暂不可用，请稍后重试' })
      : retry.promise;
  };
  api.submitVehicleInspection = () => {
    inspectionWrites += 1;
    return Promise.resolve({ id: 91 });
  };
  api.checkOutVehicle = () => {
    checkoutWrites += 1;
    return Promise.resolve({});
  };
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 1, plate_no: '赣A00001', current_mileage: 1000 },
    vehicle_application_id: 9,
  });

  page.onOpenVehicleCheckout();
  await flush();
  assert.equal(templateCalls, 2, 'the explicit retry starts a fresh template read');
  assert.equal(page.data.viewPhase, 'departure');
  assert.equal(page.data.vehicleSheet.open, false);
  assert.deepEqual(page.data.vehicleSheet.items, []);
  page.onSubmitVehicleCheckout();
  assert.equal(inspectionWrites, 0);
  assert.equal(checkoutWrites, 0);
  assert.equal(modals[0].title, '车辆检查加载失败');
  assert.equal(modals[0].content, '检查模板暂不可用，请稍后重试');
  assert.equal(modals[0].confirmText, '重试');

  retry.resolve([{ key: 'tyre', label: '轮胎及胎压', status: 'normal', remark: '' }]);
  await flush();
  assert.equal(page.data.vehicleSheet.open, true);
  assert.equal(page.data.vehicleSheet.items[0].key, 'tyre');
  assert.equal(inspectionWrites, 0);
  assert.equal(checkoutWrites, 0);
});

test('due-soon checkout primary action reads the real template array and opens the sheet', async () => {
  let templateCalls = 0;
  let acknowledgementWrites = 0;
  api.vehicleInspectionTemplate = () => {
    templateCalls += 1;
    return Promise.resolve([
      { key: 'licence', label: '驾驶证随车', status: 'normal', remark: '' },
      { key: 'insurance', label: '保险与年检', status: 'normal', remark: '' },
    ]);
  };
  api.confirmDepartureResources = () => {
    acknowledgementWrites += 1;
    return Promise.resolve({});
  };
  const page = createPage();
  seedDeparture(page, {
    vehicle: {
      id: 1, plate_no: '赣A00001', current_mileage: 1000,
      document_state: { expired: [], due_soon: ['insurance'] },
    },
    vehicle_application_id: 9,
    arrival_gate: {
      allowed: false, code: 'VEHICLE_CHECKOUT_REQUIRED',
      message: '计划车辆尚未完成出车登记，出车后方可到站',
    },
  });

  assert.equal(page.data.departureVm.vehicle.statusLabel, '证照即将到期，车辆待出车');
  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  await flush();
  assert.equal(templateCalls, 1);
  assert.equal(page.data.vehicleSheet.open, true);
  assert.deepEqual(page.data.vehicleSheet.items.map(item => item.key), ['licence', 'insurance']);
  assert.equal(acknowledgementWrites, 0, '车辆核对不能替代出车主动作');
  assert.equal(page.data.departureVm.canContinueToArrival, false);
  page.onCloseVehicleCheckout();
  assert.equal(page.data.vehicleSheet.open, false);
  assert.equal(page.data.departureVm.canContinueToArrival, false,
    '返回出发准备不得本地放行到站');
});

test('an exact target in a multi-package response opens checkout from the visible primary action', async () => {
  let templateCalls = 0;
  let inspectionWrites = 0;
  let checkoutWrites = 0;
  api.todayExecution = () => Promise.resolve({
    packages: [
      normalPackage({
        plan_id: 136,
        schedule_id: 43,
        work_date: '2026-08-27',
        sites: [{
          site_id: 351, name: '历史站点', total: 3, completed: 3, abnormal: 0,
          checked_in: true, checked_out: true,
        }],
      }),
      normalPackage({
        plan_id: 139,
        schedule_id: 46,
        work_date: '2026-08-29',
        sites: [{ site_id: 362, name: '万松站', total: 5, completed: 0, abnormal: 0 }],
        vehicle_application_id: 21,
        vehicle: {
          id: 35, plate_no: '赣AFF3288', current_mileage: 115738,
          document_state: { expired: [], due_soon: ['maintenance'] },
        },
        vehicle_use: null,
        arrival_gate: {
          allowed: false,
          code: 'VEHICLE_CHECKOUT_REQUIRED',
          message: '计划车辆尚未完成出车登记，出车后方可到站',
        },
      }),
    ],
  });
  api.executionSiteTasks = (planId, siteId) => {
    assert.equal(planId, 139);
    assert.equal(siteId, 362);
    return Promise.resolve({
      categories: [],
      arrival_gate: {
        allowed: false,
        code: 'VEHICLE_CHECKOUT_REQUIRED',
        message: '计划车辆尚未完成出车登记，出车后方可到站',
      },
    });
  };
  api.vehicleInspectionTemplate = () => {
    templateCalls += 1;
    return Promise.resolve([
      { key: 'tyre', label: '轮胎及胎压', status: 'normal', remark: '' },
    ]);
  };
  api.submitVehicleInspection = () => {
    inspectionWrites += 1;
    return Promise.resolve({ id: 91 });
  };
  api.checkOutVehicle = () => {
    checkoutWrites += 1;
    return Promise.resolve({});
  };
  app.globalData.executionTarget = {
    executionPlanId: 139,
    scheduleId: 46,
    workDate: '2026-08-29',
    siteId: 362,
    source: 'home',
  };
  const page = createPage();

  page.loadExecution();
  await flush();
  await flush();
  assert.equal(page.data.viewPhase, 'departure');
  assert.equal(page.data.currentPackage.plan_id, 139);
  assert.equal(page.data.departureTarget.executionPlanId, 139);
  assert.equal(page.data.departureTarget.siteId, 362);
  assert.equal(page.data.departureVm.vehicle.primaryAction, 'checkout');
  assert.equal(page._departureCanWrite('vehicleCheckout'), true);

  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  await flush();
  assert.equal(templateCalls, 1);
  assert.equal(page.data.vehicleSheet.open, true);
  assert.equal(inspectionWrites, 0);
  assert.equal(checkoutWrites, 0);
});

test('a visible vehicle primary action explains state, target and concurrent write blocks', () => {
  let templateCalls = 0;
  api.vehicleInspectionTemplate = () => {
    templateCalls += 1;
    return Promise.resolve([]);
  };
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 35, plate_no: '赣AFF3288', current_mileage: 115738 },
    vehicle_application_id: 21,
  });
  page.data.currentPackage = normalPackage({
    plan_id: 136,
    schedule_id: 43,
    sites: [{ site_id: 351, name: '历史站点', total: 3, completed: 3, abnormal: 0 }],
  });

  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  assert.equal(templateCalls, 0);
  assert.equal(toasts.at(-1).title, '当前执行目标已变化，请刷新后重试');

  seedDeparture(page, {
    vehicle: { id: 35, plate_no: '赣AFF3288', current_mileage: 115738 },
    vehicle_application_id: 21,
  });
  page.data.departureSubmitting.vehicleCheckout = true;
  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  assert.equal(templateCalls, 0);
  assert.equal(toasts.at(-1).title, '当前操作正在处理中，请稍候');

  page.data.departureSubmitting.vehicleCheckout = false;
  page.data.departureState = 'refresh_error';
  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  assert.equal(templateCalls, 0);
  assert.equal(toasts.at(-1).title, '出发准备状态正在更新，请刷新后重试');
});

test('refresh_error blocks every departure write entry while preserving old cards', () => {
  let writes = 0;
  api.confirmDepartureResources = () => { writes += 1; return Promise.resolve({}); };
  api.issueExecutionParts = () => { writes += 1; return Promise.resolve({}); };
  api.vehicles = () => { writes += 1; return Promise.resolve([]); };
  api.vehicleInspectionTemplate = () => { writes += 1; return Promise.resolve([]); };
  api.requestReworkResource = () => { writes += 1; return Promise.resolve({}); };
  api.submitVehicleInspection = () => { writes += 1; return Promise.resolve({ id: 1 }); };

  const page = createPage();
  const { target } = seedDeparture(page, {
    is_rework: true,
    resource_state: 'arrangement_required',
    rework_resource: { state: 'arrangement_required', arrival_allowed: false, canRequest: true },
    vehicle: { id: 1, plate_no: '赣A00001' },
    vehicle_application_id: 9,
    resource_parts: [
      { part_id: 3, part_name: '滤芯', planned_quantity: 2, issued_quantity: 0, remaining_quantity: 2 },
    ],
    departure_confirmation: { vehicle_confirmed: 0, parts_confirmed: 1 },
  });
  page.data.departureState = 'refresh_error';
  page.data.departureRefreshError = '旧数据可能过期';
  page._partsIssueDepartureTarget = target;
  page._reworkSheetDepartureTarget = target;
  page._vehicleSheetDepartureTarget = target;
  page.data.partsIssueSheet = { open: true, items: [], submitting: false };
  page.data.reworkResourceSheet.open = true;
  page.data.vehicleSheet.open = true;

  page.onDepartureConfirmVehicle();
  page.onDepartureIssueParts();
  page.onDepartureVehicleAction({ currentTarget: { dataset: { action: 'checkout' } } });
  page.onSubmitPartsIssue();
  page.onSubmitReworkResource();
  page.onSubmitVehicleCheckout();
  assert.equal(writes, 0);
  assert.ok(page.data.departureVm, 'the previous cards remain visible');
});

test('refresh failure preserves cards and retry replaces them only after success', async () => {
  const page = createPage();
  seedDeparture(page, { plan_name: '原资源卡' });
  const originalVm = page.data.departureVm;
  api.todayExecution = () => Promise.reject({ error: '网络暂不可用' });
  page.onDepartureRefresh();
  await flush();
  assert.equal(page.data.departureState, 'refresh_error');
  assert.equal(page.data.departureVm.mode, originalVm.mode);
  assert.equal(page.data.currentPackage.plan_name, '原资源卡');

  const retry = deferred();
  api.todayExecution = () => retry.promise;
  page.onDepartureRefresh();
  assert.equal(page.data.currentPackage.plan_name, '原资源卡');
  assert.equal(page.data.departureState, 'action_pending');
  retry.resolve({ packages: [normalPackage({ plan_name: '最新资源卡' })] });
  await flush();
  assert.equal(page.data.currentPackage.plan_name, '最新资源卡');
  assert.equal(page.data.departureState, 'ready');
  assert.equal(page.data.departureRefreshError, '');
});

test('partial parts issue failure keeps quantities, sheet and retry context', async () => {
  let calls = 0;
  const pending = deferred();
  api.issueExecutionParts = () => { calls += 1; return pending.promise; };
  const page = createPage();
  seedDeparture(page, {
    departure_confirmation: { vehicle_confirmed: 0, parts_confirmed: 1 },
    resource_parts: [
      { part_id: 3, part_name: '滤芯', planned_quantity: 2, issued_quantity: 0, remaining_quantity: 2 },
    ],
  });
  page.onDepartureIssueParts();
  page.data.partsIssueSheet.items[0].issue_quantity = '1';
  page.onSubmitPartsIssue();
  page.onSubmitPartsIssue();
  assert.equal(calls, 1, 'the irreversible issue request is de-duplicated');
  pending.reject({ error: '库存状态已变化' });
  await flush();
  assert.equal(page.data.partsIssueSheet.open, true);
  assert.equal(page.data.partsIssueSheet.items[0].issue_quantity, '1');
  assert.equal(page.data.partsIssueSheet.submitting, false);
  assert.equal(page.data.departureSubmitting.partsIssue, false);
  assert.equal(toasts.at(-1).title, '库存状态已变化');
});

test('arrival ignores cached details and rereads package gate plus exact site', async () => {
  let packageReads = 0;
  let siteReads = 0;
  api.todayExecution = () => {
    packageReads += 1;
    return Promise.resolve({ packages: [normalPackage()] });
  };
  api.executionSiteTasks = (planId, siteId) => {
    siteReads += 1;
    assert.deepEqual([planId, siteId], [10, 20]);
    return Promise.resolve({
      site: { id: 20 }, categories: [], total: 1, completed: 0,
      arrival_gate: { allowed: true, code: null, message: null },
    });
  };
  const page = createPage();
  seedDeparture(page);
  page._departureCachedSiteTasks = { stale: true };
  page.loadTasks = function (siteId, done, verified) { this._verifiedArrival = { siteId, verified }; };
  page.onDepartureContinueToArrival();
  await flush();
  await flush();
  assert.equal(packageReads, 1);
  assert.equal(siteReads, 1);
  assert.equal(page.data.viewPhase, 'inspection');
  assert.equal(page._verifiedArrival.verified.stale, undefined);
});

test('a rework gate change blocks arrival before the exact site request', async () => {
  let siteReads = 0;
  const page = createPage();
  seedDeparture(page, {
    is_rework: true,
    rework_resource: {
      state: 'vehicle_checked_out', arrival_allowed: true, application_status: 'approved',
    },
    arrival_gate: { allowed: true },
    vehicle: { id: 2, plate_no: '赣A00002' },
    vehicle_application_id: 20,
    vehicle_use: { id: 30, status: 'checked_out' },
  });
  api.todayExecution = () => Promise.resolve({ packages: [normalPackage({
    is_rework: true,
    rework_resource: {
      state: 'pending_approval', arrival_allowed: false, application_status: 'pending',
      gate_message: '整改补检资源尚未获批',
    },
    arrival_gate: {
      allowed: false, code: 'REWORK_RESOURCE_APPROVAL_REQUIRED',
      message: '整改补检资源尚未获批',
    },
  })] });
  api.executionSiteTasks = () => { siteReads += 1; return Promise.resolve({}); };
  page.onDepartureContinueToArrival();
  await flush();
  assert.equal(page.data.viewPhase, 'departure');
  assert.equal(page.data.departureVm.canContinueToArrival, false);
  assert.equal(siteReads, 0);
});

test('exact station preflight blocks arrival when the vehicle gate changes after package refresh', async () => {
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 1, plate_no: '赣A00001' },
    vehicle_application_id: 9,
    vehicle_use: { id: 90, status: 'checked_out', returned_at: null },
    arrival_gate: { allowed: true },
  });
  api.todayExecution = () => Promise.resolve({ packages: [normalPackage({
    vehicle: { id: 1, plate_no: '赣A00001' },
    vehicle_application_id: 9,
    vehicle_use: { id: 90, status: 'checked_out', returned_at: null },
    arrival_gate: { allowed: true },
  })] });
  api.executionSiteTasks = () => Promise.resolve({
    categories: [],
    arrival_gate: {
      allowed: false, code: 'VEHICLE_APPLICATION_INVALID',
      message: '当前用车申请已失效，请重新安排后再到站',
    },
  });
  page.onDepartureContinueToArrival();
  await flush();
  await flush();
  assert.equal(page.data.viewPhase, 'departure');
  assert.equal(page.data.departureEnteringSite, false);
  assert.equal(page.data.departureVm.canContinueToArrival, false);
  assert.equal(page.data.departureVm.gateCode, 'VEHICLE_APPLICATION_INVALID');
});

test('late refresh and arrival responses do not write after hide', async () => {
  const refresh = deferred();
  api.todayExecution = () => refresh.promise;
  const page = createPage();
  seedDeparture(page);
  page.onDepartureRefresh();
  page.onHide();
  const writesAfterHide = page.setDataCalls;
  refresh.resolve({ packages: [normalPackage({ plan_name: '过期响应' })] });
  await flush();
  assert.equal(page.setDataCalls, writesAfterHide);

  const arrival = deferred();
  const page2 = createPage();
  seedDeparture(page2);
  api.todayExecution = () => arrival.promise;
  page2.onDepartureContinueToArrival();
  page2.onHide();
  const arrivalWrites = page2.setDataCalls;
  arrival.resolve({ packages: [normalPackage()] });
  await flush();
  assert.equal(page2.setDataCalls, arrivalWrites);
});

test('a write response for an old departure target cannot update the new package', async () => {
  const pending = deferred();
  api.confirmDepartureResources = () => pending.promise;
  const page = createPage();
  seedDeparture(page, {
    vehicle: { id: 1, plate_no: '赣A00001' }, vehicle_application_id: 9,
  });
  page.onDepartureConfirmVehicle();
  const newPackage = normalPackage({
    plan_id: 11, schedule_id: 101,
    sites: [{ site_id: 21, name: '新站点', total: 1, completed: 0, abnormal: 0 }],
  });
  const newTarget = {
    scheduleId: 101, executionPlanId: 11, siteId: 21,
    workDate: '2026-08-28', source: 'plan_detail',
  };
  page.data.departureTarget = newTarget;
  page.data.currentPackage = newPackage;
  page.data.departureVm = buildDeparturePreparation(newPackage, newTarget);
  const writesBeforeResponse = page.setDataCalls;
  pending.resolve({ confirmation: { vehicle_confirmed: 1, parts_confirmed: 0 } });
  await flush();
  assert.equal(page.setDataCalls, writesBeforeResponse);
  assert.equal(page.data.currentPackage.plan_id, 11);
  assert.equal(page.data.departureVm.vehicleAcknowledgement.status, 'unconfirmed');
});

test('rework request failure preserves the chosen vehicle or exception input', async () => {
  api.requestReworkResource = () => Promise.reject({ error: '申请状态已变化' });
  const page = createPage();
  const { target } = seedDeparture(page, {
    is_rework: true,
    resource_state: 'arrangement_required',
    rework_resource: { state: 'arrangement_required', arrival_allowed: false, canRequest: true },
  });
  page._reworkSheetDepartureTarget = target;
  page.data.reworkResourceSheet = {
    open: true, mode: 'no_vehicle', vehicles: [], vehicleIndex: 0,
    exceptionReason: '站点步行可达', submitting: false,
  };
  page.onSubmitReworkResource();
  await flush();
  assert.equal(page.data.reworkResourceSheet.open, true);
  assert.equal(page.data.reworkResourceSheet.exceptionReason, '站点步行可达');
  assert.equal(page.data.reworkResourceSheet.submitting, false);
  assert.equal(page.data.departureSubmitting.reworkRequest, false);
});

test('vehicle checkout failure keeps inspection input and releases only its own action', async () => {
  api.submitVehicleInspection = () => Promise.resolve({ id: 91 });
  api.checkOutVehicle = () => Promise.reject({ error: '车辆已被其他安排占用' });
  const page = createPage();
  const { target } = seedDeparture(page, {
    vehicle: { id: 1, plate_no: '赣A00001', current_mileage: 1000 },
    vehicle_application_id: 9,
  });
  page._vehicleSheetDepartureTarget = target;
  page.data.vehicleSheet = {
    open: true, mode: 'dispatch', mileage: '1001', remarks: '已检查',
    items: [{ key: '轮胎', status: 'normal' }], submitting: false,
  };
  page.onSubmitVehicleCheckout();
  await flush();
  await flush();
  assert.equal(page.data.vehicleSheet.open, true);
  assert.equal(page.data.vehicleSheet.mileage, '1001');
  assert.equal(page.data.vehicleSheet.submitting, false);
  assert.equal(page.data.departureSubmitting.vehicleCheckout, false);
  assert.equal(toasts.at(-1).title, '车辆已被其他安排占用');
});

test('overdue vehicle continuation writes one exact application target before navigation', () => {
  const originalNavigateTo = wx.navigateTo;
  try {
    let navigation;
    wx.navigateTo = options => { navigation = options; };
    app.globalData.vehicleTarget = null;
    const page = createPage();
    seedDeparture(page, {
      vehicle: { id: 1, plate_no: '赣A00001' },
      vehicle_application_id: 91,
      vehicle_needs_extension: true,
    });

    page.onOpenVehicleCheckout();

    assert.equal(navigation.url, '/pages/vehicle/vehicle');
    assert.deepEqual(app.globalData.vehicleTarget, {
      applicationId: 91,
      expectedAction: 'extend',
      source: 'inspection_departure',
      executionPlanId: 10,
      siteId: 20,
    });

    const written = app.globalData.vehicleTarget;
    app.globalData.vehicleTarget = {
      applicationId: 92, expectedAction: 'extend', source: 'newer'
    };
    navigation.fail();
    assert.equal(app.globalData.vehicleTarget.applicationId, 92,
      'a late failure cannot clear a newer vehicle target');
    assert.notEqual(app.globalData.vehicleTarget, written);

    const retryPage = createPage();
    seedDeparture(retryPage, {
      vehicle: { id: 1, plate_no: '赣A00001' },
      vehicle_application_id: 93,
      vehicle_needs_extension: true,
    });
    retryPage.onOpenVehicleCheckout();
    assert.equal(app.globalData.vehicleTarget.applicationId, 93);
    navigation.fail();
    assert.equal(app.globalData.vehicleTarget, null,
      'navigation failure clears the target written by this attempt');
    assert.equal(toasts.at(-1).title, '车辆页面打开失败，请重试');
  } finally {
    wx.navigateTo = originalNavigateTo;
    app.globalData.vehicleTarget = null;
  }
});
