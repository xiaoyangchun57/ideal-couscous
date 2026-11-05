const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const api = require('../services/api.js');
const app = { globalData: { token: 'test-token', selWorkorderNo: null } };
const toasts = [];

global.getApp = () => app;
global.wx = {
  showToast(options) { toasts.push(options); },
  showLoading() {}, hideLoading() {}, reLaunch() {}, getNetworkType() {},
  getStorageSync() { return ''; },
};

function loadPage() {
  const pagePath = require.resolve('../pages/workorder/workorder.js');
  let definition;
  global.Page = value => { definition = value; };
  delete require.cache[pagePath];
  require(pagePath);
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = updates => Object.entries(updates).forEach(([key, value]) => {
    const parts = key.split('.');
    let target = page.data;
    while (parts.length > 1) {
      const part = parts.shift();
      target[part] = target[part] || {};
      target = target[part];
    }
    target[parts[0]] = value;
  });
  page.onLoad({});
  page.data.sheet = { open: true, item: {
    order_no: 'WO-NO-ACTION', status: 'in_progress', status_cn: '处置中',
    actions: { primary: 'submit_review', can_end_without_processing: true },
  } };
  return page;
}

test('no-action sheet keeps the reason after a failed write and refreshes after success', async () => {
  const originals = {
    end: api.endWorkorderWithoutProcessing,
    detail: api.workorderDetail,
    related: api.workorderRelated,
    list: api.workorders,
  };
  try {
    const page = loadPage();
    page.onOpenNoAction();
    assert.equal(page.data.noActionSheet.open, true);
    page.onNoActionReason({ detail: { value: '误建工单，无需现场处理' } });
    api.endWorkorderWithoutProcessing = () => Promise.reject({ error: '状态已变化' });
    await page.submitNoAction();
    assert.equal(page.data.noActionSheet.open, true);
    assert.equal(page.data.noActionSheet.reason, '误建工单，无需现场处理');
    assert.equal(toasts.at(-1).title, '状态已变化');

    api.endWorkorderWithoutProcessing = () => Promise.resolve({ success: true });
    api.workorderDetail = () => Promise.resolve({
      order_no: 'WO-NO-ACTION', status: 'closed', closure_type: 'no_action_required',
      closure_reason: '误建工单，无需现场处理', actions: {}, flow_events: [],
    });
    api.workorderRelated = () => Promise.resolve({ parts: [], recycles: [], vehicles: [] });
    api.workorders = () => Promise.resolve([]);
    await page.submitNoAction();
    assert.equal(page.data.noActionSheet.open, false);
    assert.equal(page.data.noActionSheet.reason, '');
    assert.equal(page.data.sheet.item.status_cn, '已提前结束');
    assert.equal(page.data.sheet.item.closure_reason, '误建工单，无需现场处理');
  } finally {
    api.endWorkorderWithoutProcessing = originals.end;
    api.workorderDetail = originals.detail;
    api.workorderRelated = originals.related;
    api.workorders = originals.list;
  }
});

test('template exposes the server capability and a required reason sheet', () => {
  const view = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.wxml'), 'utf8');
  assert.match(view, /actions\.can_end_without_processing/);
  assert.match(view, /bindtap="onOpenNoAction"/);
  assert.match(view, /bindinput="onNoActionReason"/);
  assert.match(view, /结束后工单、签到、照片、处置和审核记录都会保留/);
});
