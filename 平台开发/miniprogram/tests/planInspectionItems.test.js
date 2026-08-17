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
      days: [{ date: '2026-09-02', sites: [10], notes: '', inspection_items: { '10': [11] } }],
      inspectionItemsState: 'ready', selectedParts: [], suggestions: [], planVehicleId: null,
    }),
  });
  page.setData = (patch, done) => { Object.assign(page.data, patch); if (done) done(); };
  const payload = page.buildPayload(false);
  assert.deepEqual(payload.plan_data['2026-09-02'].inspection_items, { '10': [11] });
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
});
