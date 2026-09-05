const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  inventoryOptions, inventoryErrorMessage, buildPartsPayload,
} = require('../utils/partsApplication.js');

test('inventory options only contain real server inventory records', () => {
  assert.deepEqual(inventoryOptions([
    { id: 0, part_name: '手动输入（自定义名称）' },
    { id: null, part_name: '缺少 ID' },
    { id: 19, part_name: 'pH 电极', part_code: 'PH-19', quantity: 3 },
  ]), [{ id: 19, part_name: 'pH 电极', label: 'pH 电极（PH-19） 余3' }]);
});

test('fulfillment payloads preserve the server inventory boundary', () => {
  const options = [{ id: 19, part_name: 'pH 电极', label: 'pH 电极 余3' }];
  const stock = buildPartsPayload({ fulfillment_type: 'stock', index: 0, quantity: '2', reason: '更换故障电极' }, options);
  assert.deepEqual(stock.payload, {
    part_name: 'pH 电极', specification: '', quantity: 2, reason: '更换故障电极',
    spare_part_id: 19, fulfillment_type: 'stock', estimated_amount: null,
  });
  const purchase = buildPartsPayload({
    fulfillment_type: 'local_purchase', part_name: '临时接头', specification: '6mm',
    estimated_amount: '30', quantity: '2', reason: '现场漏水', index: 0,
  }, options);
  assert.equal(purchase.payload.spare_part_id, null);
  assert.equal(purchase.payload.part_name, '临时接头');
  assert.equal(purchase.payload.specification, '6mm');
  assert.equal(purchase.payload.estimated_amount, 30);
  assert.equal(buildPartsPayload({ fulfillment_type: 'stock', quantity: 1, reason: '更换' }, []).error, '请选择库存备件');
  assert.equal(buildPartsPayload({ fulfillment_type: 'stock', quantity: 1, reason: '更换' }, options, 'loading').error,
    '库存加载中，请稍候');
  assert.equal(buildPartsPayload({ fulfillment_type: 'stock', quantity: 1, reason: '更换' }, options, 'empty').error,
    '暂无可领用库存');
  assert.equal(buildPartsPayload({ fulfillment_type: 'stock', quantity: 1, reason: '更换' }, options, 'error').error,
    '库存加载失败，请重试');
  assert.equal(buildPartsPayload({ fulfillment_type: 'vendor_order', part_name: '电极', quantity: 0, reason: '更换' }, []).error, '请填写有效数量');
  assert.equal(inventoryErrorMessage({ error: '服务超时' }), '服务超时');
});

test('all three parts sheets carry explicit inventory state and retry connections', () => {
  const pages = [
    ['pages/inspection/inspection.js', 'pages/inspection/inspection.wxml'],
    ['pages/site/site.js', 'pages/site/site.wxml'],
    ['pages/workorder/workorder.js', 'pages/workorder/workorder.wxml'],
  ];
  for (const [jsFile, wxmlFile] of pages) {
    const js = fs.readFileSync(path.join(__dirname, '..', jsFile), 'utf8');
    const wxml = fs.readFileSync(path.join(__dirname, '..', wxmlFile), 'utf8');
    assert.match(js, /partsOptions:\s*\[\]/, jsFile + ' has no synthetic inventory option');
    assert.match(js, /partsInventoryStatus/, jsFile + ' exposes inventory state');
    assert.match(js, /onRetryPartsInventory/, jsFile + ' has retry handler');
    assert.match(js, /buildPartsPayload/, jsFile + ' uses shared request validation');
    assert.doesNotMatch(js, /手动输入（自定义名称）/, jsFile + ' does not retain a manual stock choice');
    assert.match(wxml, /partsInventoryStatus === 'ready'/, wxmlFile + ' only renders the picker for real inventory');
    assert.match(wxml, /partsInventoryStatus === 'empty'/, wxmlFile + ' distinguishes empty inventory');
    assert.match(wxml, /bindtap="onRetryPartsInventory"/, wxmlFile + ' keeps a retry action in the sheet');
  }
});

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

function setPath(target, key, value) {
  const keys = key.split('.');
  let current = target;
  while (keys.length > 1) {
    const next = keys.shift();
    if (!current[next] || typeof current[next] !== 'object') current[next] = {};
    current = current[next];
  }
  current[keys[0]] = value;
}

function loadPage(pageFile) {
  let definition;
  global.Page = value => { definition = value; };
  const modulePath = require.resolve('../' + pageFile);
  delete require.cache[modulePath];
  require(modulePath);
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = (patch, done) => {
    Object.entries(patch).forEach(([key, value]) => setPath(page.data, key, value));
    if (done) done();
  };
  return page;
}

test('three page handlers retain a failed parts sheet and submit non-stock requests without an inventory id', async () => {
  const api = require('../services/api.js');
  const originals = { partsInventory: api.partsInventory, applyParts: api.applyParts };
  const toasts = [];
  global.getApp = () => ({ globalData: { token: 'test' } });
  global.wx = {
    showToast: options => toasts.push(options), showLoading: () => {}, hideLoading: () => {},
    getStorageSync: () => null, setStorageSync: () => {}, removeStorageSync: () => {},
  };
  const pages = [
    {
      file: 'pages/inspection/inspection.js', open: 'onOpenPartsApply', submit: 'onSubmitPartsApply',
      seed: page => { page.data.selSite = { id: 11 }; },
    },
    {
      file: 'pages/site/site.js', open: 'onOpenPartsApply', submit: 'onSubmitPartsApply',
      seed: page => { page.data.site = { id: 12 }; },
    },
    {
      file: 'pages/workorder/workorder.js', open: 'onApplyParts', submit: 'submitParts',
      seed: page => { page.data.sheet.item = { site_id: 13, order_no: 'WO-PARTS-13' }; },
    },
  ];
  try {
    for (const config of pages) {
      const page = loadPage(config.file);
      config.seed(page);
      api.partsInventory = () => Promise.reject({ error: '库存服务超时' });
      page[config.open]();
      await flush();
      await flush();
      assert.equal(page.data.partsApply.open, true, config.file + ' keeps the sheet open after inventory failure');
      assert.equal(page.data.partsInventoryStatus, 'error');
      assert.equal(page.data.partsInventoryError, '库存服务超时');
      let blockedCalls = 0;
      api.applyParts = () => { blockedCalls += 1; return Promise.resolve({}); };
      page[config.submit]();
      assert.equal(blockedCalls, 0, config.file + ' blocks stock submission while inventory is unavailable');

      api.partsInventory = () => Promise.resolve([{ id: 27, part_name: 'pH 电极', part_code: 'PH-27', quantity: 3 }]);
      page.onRetryPartsInventory();
      await flush();
      await flush();
      assert.equal(page.data.partsInventoryStatus, 'ready', config.file + ' reloads inventory in the existing sheet');
      assert.deepEqual(page.data.partsOptions.map(option => option.id), [27]);

      page.onPartsFulfillmentSelect({ currentTarget: { dataset: { index: 1 } } });
      page.onPartsName({ detail: { value: '临时接头' } });
      page.onPartsSpecification({ detail: { value: '6mm' } });
      page.onPartsQty({ detail: { value: '2' } });
      page.onPartsReason({ detail: { value: '现场漏水' } });
      const key = page.data.partsApply.requestKey;
      const calls = [];
      api.applyParts = payload => {
        calls.push(payload);
        return Promise.reject({ error: '网络异常' });
      };
      page[config.submit]();
      page[config.submit]();
      await flush();
      await flush();
      assert.equal(calls.length, 1, config.file + ' blocks duplicate submits');
      assert.equal(calls[0].spare_part_id, null);
      assert.equal(calls[0].fulfillment_type, 'local_purchase');
      assert.equal(page.data.partsApply.open, true, config.file + ' keeps a failed request open');
      assert.equal(page.data.partsApply.part_name, '临时接头');
      assert.equal(page.data.partsApply.specification, '6mm');
      assert.equal(page.data.partsApply.requestKey, key);
      assert.equal(page.data.partsApply.submitting, false);
    }
    assert.equal(toasts.filter(item => item.title === '网络异常').length, 3);
  } finally {
    Object.assign(api, originals);
    delete global.Page;
    delete global.getApp;
    delete global.wx;
  }
});
