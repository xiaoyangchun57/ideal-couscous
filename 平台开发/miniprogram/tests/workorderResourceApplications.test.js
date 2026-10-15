const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const api = require('../services/api.js');
const app = { globalData: { token: 'test-token', selWorkorderNo: null } };
const toastCalls = [];

global.getApp = () => app;
global.wx = {
  showToast(options) { toastCalls.push(options); },
  showLoading() {},
  hideLoading() {},
  reLaunch() {},
  getNetworkType() {},
  getStorageSync() { return ''; },
};

function loadPage() {
  const pagePath = require.resolve('../pages/workorder/workorder.js');
  let definition;
  global.Page = value => { definition = value; };
  delete require.cache[pagePath];
  require(pagePath);
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
  });
  page.setData = (updates, done) => {
    Object.entries(updates).forEach(([key, value]) => {
      const parts = key.split('.');
      let target = page.data;
      while (parts.length > 1) {
        const part = parts.shift();
        target[part] = target[part] || {};
        target = target[part];
      }
      target[parts[0]] = value;
    });
    if (done) done();
  };
  page.onLoad({});
  page.data.sheet = {
    open: true,
    item: {
      order_no: 'WO-RESOURCE-29',
      site_id: 29,
      site_name: '测试站点',
      display_title: '现场处置',
      actions: { primary: 'submit_review', can_apply_resources: true },
      related: { vehicles: [], parts: [], recycles: [] },
      images_arr: ['/uploads/existing.jpg'],
      has_images: true,
    },
  };
  page.data.resolutionNote = '保留的处置说明';
  const draft = {
    orderNo: 'WO-RESOURCE-29', siteId: '29', status: 'failed',
    path: 'wxfile://draft.jpg', idempotencyKey: 'photo-draft-29',
  };
  page._setWorkorderPhotoDraft(draft);
  return page;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setImmediate(resolve));
}

test('resource sheets are the only foreground while the workorder context stays mounted', () => {
  const view = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.wxml'), 'utf8');
  assert.match(view,
    /class="om-sheet-mask" wx:if="\{\{sheet\.open\}\}" hidden="\{\{vehicleApply\.open \|\| partsApply\.open\}\}"/);
  assert.match(view,
    /class="om-sheet workorder-detail-sheet" wx:if="\{\{sheet\.open\}\}" hidden="\{\{vehicleApply\.open \|\| partsApply\.open\}\}"/);
  assert.match(view, /checked="\{\{vehicleApply\.requiresVehicle\}\}" bindchange="onVehicleRequirementChange"/);
  assert.match(view, /wx:if="\{\{vehicleApply\.requiresVehicle\}\}" mode="selector"/);
  assert.match(view, /class="wo-detail-field" wx:if="\{\{vehicleApply\.requiresVehicle\}\}"/);

  const page = loadPage();
  const item = page.data.sheet.item;
  const draft = page.data.workorderPhotoDraft;
  page.onApplyParts();
  assert.equal(page.data.partsApply.open, true);
  assert.equal(page.data.sheet.open, true);
  assert.equal(page.data.sheet.item, item);
  assert.equal(page.data.resolutionNote, '保留的处置说明');
  assert.equal(page.data.workorderPhotoDraft, draft);
  page.onCloseParts();
  assert.equal(page.data.partsApply.open, false);
  assert.equal(page.data.sheet.item, item);
});

test('vehicle sheet defaults to no request and returns without validation or writes', async () => {
  const originalApplyVehicle = api.applyVehicle;
  let writes = 0;
  api.applyVehicle = () => { writes += 1; return Promise.resolve({}); };
  try {
    const page = loadPage();
    const item = page.data.sheet.item;
    const draft = page.data.workorderPhotoDraft;
    page.onApplyVehicle();
    assert.equal(page.data.vehicleApply.requiresVehicle, false);
    assert.match(page.data.vehicleApply.requestKey, /^vehicle_workorder_\d+$/);
    const result = await page.submitVehicle();
    assert.equal(result, false);
    assert.equal(writes, 0);
    assert.equal(page.data.vehicleApply.open, false);
    assert.equal(page.data.sheet.open, true);
    assert.equal(page.data.sheet.item, item);
    assert.equal(page.data.resolutionNote, '保留的处置说明');
    assert.equal(page.data.workorderPhotoDraft, draft);
  } finally {
    api.applyVehicle = originalApplyVehicle;
  }
});

test('required vehicle submission keeps one stable request and restores the exact workorder', async () => {
  const originals = { applyVehicle: api.applyVehicle, workorderRelated: api.workorderRelated };
  const pending = deferred();
  const calls = [];
  api.applyVehicle = payload => { calls.push(payload); return pending.promise; };
  api.workorderRelated = () => Promise.resolve({
    vehicles: [{ id: 88, status: 'pending' }], parts: [], recycles: [],
  });
  try {
    const page = loadPage();
    const item = page.data.sheet.item;
    page.data.vehicleOptions = [{ id: 88, label: '赣A00088' }];
    page.onApplyVehicle();
    page.onVehicleRequirementChange({ detail: { value: true } });
    page.onVehicleReason({ detail: { value: '前往站点处置' } });
    const requestKey = page.data.vehicleApply.requestKey;
    const first = page.submitVehicle();
    const duplicate = page.submitVehicle();
    assert.equal(duplicate, undefined);
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0], {
      site_id: 29,
      work_order_no: 'WO-RESOURCE-29',
      reason: '前往站点处置',
      vehicle_id: 88,
      _idempotency_key: requestKey,
    });
    assert.equal(page.data.vehicleApply.open, true);
    pending.resolve({ id: 101 });
    await first;
    assert.equal(page.data.vehicleApply.open, false);
    assert.equal(page.data.vehicleApply.submitting, false);
    assert.equal(page.data.sheet.item, item);
    assert.equal(page.data.sheet.item.related.vehicles.length, 1);
    assert.equal(page.data.resolutionNote, '保留的处置说明');
    assert.equal(page.data.workorderPhotoDraft.status, 'failed');
  } finally {
    Object.assign(api, originals);
  }
});

test('failed vehicle submission retains input and its idempotency key for retry', async () => {
  const originalApplyVehicle = api.applyVehicle;
  const calls = [];
  api.applyVehicle = payload => {
    calls.push(payload);
    return Promise.reject({ error: '车辆申请网络异常' });
  };
  try {
    const page = loadPage();
    page.data.vehicleOptions = [{ id: 66, label: '赣A00066' }];
    page.onApplyVehicle();
    page.onVehicleRequirementChange({ detail: { value: true } });
    page.onVehicleReason({ detail: { value: '保留失败输入' } });
    const requestKey = page.data.vehicleApply.requestKey;
    await page.submitVehicle();
    await settle();
    assert.equal(calls.length, 1);
    assert.equal(page.data.vehicleApply.open, true);
    assert.equal(page.data.vehicleApply.requiresVehicle, true);
    assert.equal(page.data.vehicleApply.reason, '保留失败输入');
    assert.equal(page.data.vehicleApply.requestKey, requestKey);
    assert.equal(page.data.vehicleApply.submitting, false);
    assert.equal(toastCalls.at(-1).title, '车辆申请网络异常');
  } finally {
    api.applyVehicle = originalApplyVehicle;
  }
});

test('late detail refresh preserves edits and clearing while another workorder still initializes its saved note', async () => {
  const originals = { workorderDetail: api.workorderDetail, workorderRelated: api.workorderRelated };
  api.workorderRelated = () => Promise.resolve({ vehicles: [], parts: [], recycles: [] });
  try {
    const editedPage = loadPage();
    const editedPending = deferred();
    api.workorderDetail = () => editedPending.promise;
    editedPage.onApplyVehicle();
    const editedRefresh = editedPage._refreshOpenWorkorderDetail('WO-RESOURCE-29');
    editedPage.onResolutionNote({ detail: { value: '请求发出后继续编辑的说明' } });
    editedPending.resolve({
      order_no: 'WO-RESOURCE-29', site_id: 29, status: 'in_progress',
      display_title: '刷新后的现场处置', remark: '服务器已保存的旧说明', flow_events: [], images: '[]',
    });
    await editedRefresh;
    assert.equal(editedPage.data.vehicleApply.open, true);
    assert.equal(editedPage.data.sheet.open, true);
    assert.equal(editedPage.data.sheet.item.order_no, 'WO-RESOURCE-29');
    assert.equal(editedPage.data.resolutionNote, '请求发出后继续编辑的说明');
    assert.equal(editedPage.data.workorderPhotoDraft.idempotencyKey, 'photo-draft-29');

    const clearedPage = loadPage();
    const clearedPending = deferred();
    api.workorderDetail = () => clearedPending.promise;
    const clearedRefresh = clearedPage._refreshOpenWorkorderDetail('WO-RESOURCE-29');
    clearedPage.onResolutionNote({ detail: { value: '' } });
    clearedPending.resolve({
      order_no: 'WO-RESOURCE-29', site_id: 29, status: 'in_progress',
      display_title: '刷新后的现场处置', remark: '服务器已保存的旧说明', flow_events: [], images: '[]',
    });
    await clearedRefresh;
    assert.equal(clearedPage.data.resolutionNote, '', 'an intentional local clear remains empty');

    const otherPage = loadPage();
    api.workorderDetail = () => Promise.resolve({
      order_no: 'WO-OTHER-30', site_id: 30, status: 'in_progress',
      display_title: '另一工单', remark: '另一工单已保存的说明', flow_events: [], images: '[]',
    });
    otherPage.openWorkorderSheet({ order_no: 'WO-OTHER-30' });
    await settle();
    assert.equal(otherPage.data.sheet.item.order_no, 'WO-OTHER-30');
    assert.equal(otherPage.data.resolutionNote, '另一工单已保存的说明');
  } finally {
    Object.assign(api, originals);
  }
});

test('parts success returns to the same workorder with the original purchase payload', async () => {
  const originals = { applyParts: api.applyParts, workorderRelated: api.workorderRelated };
  let payload;
  api.applyParts = value => { payload = value; return Promise.resolve({ id: 301 }); };
  api.workorderRelated = () => Promise.resolve({
    vehicles: [], parts: [{ id: 301, fulfillment_type: 'local_purchase' }], recycles: [],
  });
  try {
    const page = loadPage();
    const item = page.data.sheet.item;
    page.data.partsInventoryStatus = 'ready';
    page.onApplyParts();
    page.onPartsFulfillmentSelect({ currentTarget: { dataset: { index: 1 } } });
    page.onPartsName({ detail: { value: '临时接头' } });
    page.onPartsSpecification({ detail: { value: '6mm' } });
    page.onPartsEstimatedAmount({ detail: { value: '30' } });
    page.onPartsQty({ detail: { value: '2' } });
    page.onPartsReason({ detail: { value: '现场漏水' } });
    const requestKey = page.data.partsApply.requestKey;
    await page.submitParts();
    assert.deepEqual(payload, {
      site_id: 29,
      work_order_no: 'WO-RESOURCE-29',
      _idempotency_key: requestKey,
      part_name: '临时接头',
      specification: '6mm',
      quantity: 2,
      reason: '现场漏水',
      spare_part_id: null,
      fulfillment_type: 'local_purchase',
      estimated_amount: 30,
    });
    assert.equal(page.data.partsApply.open, false);
    assert.equal(page.data.sheet.item, item);
    assert.equal(page.data.sheet.item.related.parts.length, 1);
    assert.equal(page.data.workorderPhotoDraft.idempotencyKey, 'photo-draft-29');
  } finally {
    Object.assign(api, originals);
  }
});
