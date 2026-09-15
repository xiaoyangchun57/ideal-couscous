import test from 'node:test';
import assert from 'node:assert/strict';
import process from 'node:process';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { MONITORING_STATUS_META } from './stationMonitoring.js';

// Run against Vite with an externally supplied Playwright runtime; no repo dependency is added.
const baseURL = process.env.STATION_WEB_TEST_URL;
const playwrightModule = process.env.STATION_WEB_PLAYWRIGHT_MODULE;
const evidenceDir = process.env.STATION_WEB_EVIDENCE_DIR;
const require = createRequire(import.meta.url);
const row = { id: 7, name: '隔离测试站甲', code: 'WEB-TEST-7', type: 'water_quality', status: 'maintenance', district: '测试区', manager: '测试负责人' };

function overview(status = 'interval_unconfigured', id = 7) {
  return {
    site: {
      ...row, id, name: id === 7 ? row.name : '隔离测试站乙',
      monitoring_status: status, monitoring_status_label: MONITORING_STATUS_META[status].label,
      monitoring_reason: `服务端主原因:${status}`,
      last_communication_at: '2026-09-15T08:00:00+08:00',
      last_valid_observation_at: '2026-09-10T07:00:00+08:00',
    },
    monitoring: {
      latest_values: ['normal', 'attention', 'interval_unconfigured'].includes(status)
        ? [{ business_metric: '酸碱度', standard_value: 7.25, standard_unit: 'pH', observed_at: '2026-09-10T07:00:00+08:00' }] : [],
      instruments: [{ business_metric: '酸碱度', instrument_asset_code: '仪器甲', status: 'has_valid_observation' }],
      recent_items: [], axes: { data: { state: status }, rtu: { state: 'unknown' } },
      capabilities: { latest: true, trend: false },
    },
  };
}

const summary = {
  runtime: { enabled_endpoints: 11 }, identity: { bound_sites: 7, received_raw_sites: 5 },
  configuration: { configured_sites: 3 }, observation: { valid_sites: 2 },
  quality: { open_items: 1 }, storage: { raw_frames: 19, observation_batches: 13 },
  updated_at: '2026-09-15T08:00:00+08:00',
};

test('station monitoring real Web behavior with isolated API fixtures', {
  skip: !baseURL || !playwrightModule ? 'Set STATION_WEB_TEST_URL and STATION_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = require(playwrightModule);
  const browser = await chromium.launch({ channel: process.env.STATION_WEB_BROWSER_CHANNEL || 'msedge', headless: true });
  t.after(() => browser.close());
  if (evidenceDir) await mkdir(evidenceDir, { recursive: true });

  async function session(roles = ['admin'], viewport = { width: 1440, height: 1000 }, handler) {
    const context = await browser.newContext({ viewport });
    await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-web-test-token'));
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    const writes = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (request.method() !== 'GET') {
        writes.push(`${request.method()} ${url.pathname}`);
        await route.fulfill({ status: 405, json: { error: '隔离验证禁止业务写入' } });
        return;
      }
      if (handler && await handler(route, url)) return;
      let body = {};
      if (url.pathname === '/api/auth/me') body = { user: { id: 991020, username: 'web-test', role: roles[0], roles }, site_ids: [7, 8] };
      else if (url.pathname === '/api/sites') body = [row, { ...row, id: 8, name: '隔离测试站乙', code: 'WEB-TEST-8' }];
      else if (url.pathname === '/api/station-monitoring/sites') body = { items: [overview().site, overview('normal', 8).site], summary: { interval_unconfigured: 1, normal: 1 } };
      else if (/\/station-monitoring\/sites\/\d+\/overview$/.test(url.pathname)) body = overview('interval_unconfigured', Number(url.pathname.split('/').at(-2)));
      else if (url.pathname === '/api/station-monitoring/access-summary') body = summary;
      else if (url.pathname === '/api/sites/7/archive') body = { ...row, has_sensor_data: false, equipment: [] };
      else if (url.pathname === '/api/reagent-inventory/7') body = [];
      else if (url.pathname === '/api/sites/data-sources' || url.pathname === '/api/users') body = [];
      else if (url.pathname === '/api/global-search') body = { results: [{ id: 7, type: 'site', title: row.name, path: '/sites?archive=7' }] };
      await route.fulfill({ json: body });
    });
    async function close() {
      if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, 'latest-session.png'), fullPage: true });
      if (errors.length) t.diagnostic(JSON.stringify(errors));
      await context.close();
      assert.deepEqual(errors, [], 'no React runtime errors');
      assert.deepEqual(writes, [], 'no business write requests');
    }
    return { page, close };
  }

  async function visible(locator) { await locator.first().waitFor({ state: 'visible' }); }
  async function absent(page, text) { assert.equal(await page.getByText(text, { exact: true }).count(), 0); }
  async function snapshot(page, name) {
    if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage: true });
  }

  await t.test('eight independent statuses and communication/observation times render', async () => {
    let state = 'not_connected';
    const { page, close } = await session(['admin'], undefined, async (route, url) => {
      if (!url.pathname.endsWith('/7/overview')) return false;
      await route.fulfill({ json: overview(state) });
      return true;
    });
    try {
      for (const [key, meta] of Object.entries(MONITORING_STATUS_META)) {
        state = key;
        await page.goto(`${baseURL}/sites/7`);
        await visible(page.getByRole('heading', { name: row.name }));
        await visible(page.getByText(meta.label, { exact: true }));
        await visible(page.getByText(`服务端主原因:${key}`, { exact: true }));
      }
      state = 'interval_unconfigured';
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      await visible(page.getByText('周期未配置', { exact: true }));
      await visible(page.getByText('7.25 pH', { exact: true }));
      await visible(page.getByText('最后通信', { exact: true }));
      await visible(page.getByText('最后有效观测', { exact: true }));
      await visible(page.getByText('暂无服务端聚合事实，趋势暂不可用', { exact: true }));
      await absent(page, '设备健康');
      await snapshot(page, 'detail-desktop');
    } finally { await close(); }
  });

  await t.test('first failure retries; refresh failure preserves values; mismatched site is rejected', async () => {
    let mode = 'failure';
    const { page, close } = await session(['reviewer'], undefined, async (route, url) => {
      if (!url.pathname.endsWith('/7/overview')) return false;
      await route.fulfill(mode === 'failure'
        ? { status: 503, json: { error: '隔离服务暂不可用' } }
        : { json: overview('interval_unconfigured', mode === 'mismatch' ? 8 : 7) });
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.getByText('站点监测加载失败', { exact: true }));
      mode = 'success';
      await page.getByRole('button', { name: '重试', exact: true }).click();
      await visible(page.getByText('7.25 pH', { exact: true }));
      mode = 'failure';
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      await visible(page.getByText('刷新失败，当前保留上次成功结果', { exact: true }));
      await visible(page.getByText('7.25 pH', { exact: true }));
      mode = 'mismatch';
      await page.getByRole('button', { name: '重新加载', exact: true }).click();
      await visible(page.getByText('服务端返回的站点监测资料与当前站点不匹配，请重试', { exact: true }));
      await absent(page, '隔离测试站乙');
    } finally { await close(); }
  });

  await t.test('403 and 404 stay on monitoring failure without archive fallback', async () => {
    let status = 403;
    const requested = [];
    const { page, close } = await session(['operator'], undefined, async (route, url) => {
      requested.push(url.pathname);
      if (!url.pathname.endsWith('/7/overview')) return false;
      await route.fulfill({ status, json: { error: status === 403 ? '站点范围拒绝' : '站点不存在' } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.getByText('站点范围拒绝', { exact: true }));
      status = 404;
      await page.getByRole('button', { name: '重试', exact: true }).click();
      await visible(page.getByText('站点不存在', { exact: true }));
      assert.equal(requested.some((url) => url.includes('/archive') || url.includes('/data/site/')), false);
    } finally { await close(); }
  });

  await t.test('navigation cancels an in-flight refresh and never shows the previous station', async () => {
    let hold = false;
    let release;
    let started;
    let finished;
    const pending = new Promise((resolve) => { release = resolve; });
    const requested = new Promise((resolve) => { started = resolve; });
    const settled = new Promise((resolve) => { finished = resolve; });
    const { page, close } = await session(['admin'], undefined, async (route, url) => {
      if (!url.pathname.endsWith('/7/overview') || !hold) return false;
      started();
      await pending;
      try { await route.fulfill({ json: overview('attention') }); } catch { /* Navigation may have aborted this request. */ }
      finished();
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.getByRole('heading', { name: row.name }));
      hold = true;
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      await requested;
      await page.getByRole('button', { name: /返回站点目录/ }).click();
      const second = page.getByRole('row').filter({ hasText: '隔离测试站乙' });
      await second.getByRole('button', { name: /监\s*测/ }).click();
      await page.waitForURL(`${baseURL}/sites/8`);
      await visible(page.getByRole('heading', { name: '隔离测试站乙' }));
      release();
      await settled;
      await absent(page, row.name);
      await absent(page, '服务端主原因:attention');
    } finally { release(); await close(); }
  });

  await t.test('a capability flag without aggregates stays unavailable and protocol fields are not displayed', async () => {
    const payload = overview();
    payload.monitoring.capabilities.trend = true;
    payload.monitoring.latest_values[0].business_metric = null;
    payload.monitoring.latest_values[0].protocol_factor = 'PROTOCOL-SECRET-TEST';
    payload.monitoring.instruments[0].business_metric = null;
    payload.monitoring.instruments[0].protocol_code = 'PROTOCOL-SECRET-TEST';
    payload.monitoring.recent_items = [{ title: '隔离近期事项', created_at: 'FAKE-BUSINESS-TIME' }];
    const { page, close } = await session(['reviewer'], undefined, async (route, url) => {
      if (!url.pathname.endsWith('/7/overview')) return false;
      await route.fulfill({ json: payload });
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.getByText('服务端已声明趋势能力，但当前未返回聚合事实，趋势暂不可用', { exact: true }));
      const trendCard = page.locator('.ant-card').filter({ has: page.locator('.ant-card-head-title').getByText('趋势', { exact: true }) });
      assert.equal(await trendCard.getByText('可用', { exact: true }).count(), 0);
      await absent(page, 'PROTOCOL-SECRET-TEST');
      await absent(page, 'FAKE-BUSINESS-TIME');
    } finally { await close(); }
  });

  await t.test('non-admin cannot discover or call access summary; all admin roles are recognized', async () => {
    for (const roles of [['reviewer'], ['operator'], ['reviewer', 'admin']]) {
      let calls = 0;
      const { page, close } = await session(roles, undefined, async (route, url) => {
        if (url.pathname !== '/api/station-monitoring/access-summary') return false;
        calls += 1;
        await route.fulfill({ json: summary });
        return true;
      });
      try {
        await page.goto(`${baseURL}/sites`);
        await visible(page.getByText(row.name, { exact: true }));
        assert.equal(await page.getByRole('button', { name: /接入观察/ }).count(), roles.includes('admin') ? 1 : 0);
        await page.goto(`${baseURL}/sites/data-access`);
        if (roles.includes('admin')) {
          await visible(page.getByRole('heading', { name: '监测数据接入观察' }));
          assert.ok(calls > 0);
        } else {
          await page.waitForURL(`${baseURL}/`);
          assert.equal(calls, 0);
        }
      } finally { await close(); }
    }
  });

  await t.test('access summary keeps separate counts and unknown missing facts; retry and refresh work', async () => {
    let fail = true;
    const { page, close } = await session(['admin'], undefined, async (route, url) => {
      if (url.pathname !== '/api/station-monitoring/access-summary') return false;
      await route.fulfill(fail ? { status: 503, json: { error: '摘要暂不可用' } } : { json: summary });
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites/data-access`);
      await visible(page.getByText('接入摘要加载失败', { exact: true }));
      fail = false;
      await page.getByRole('button', { name: '重试', exact: true }).click();
      await visible(page.getByText('形成观测站点', { exact: true }));
      for (const [label, value] of [['启用端点', '11'], ['已绑定站点', '7'], ['收到认证原文', '5'], ['已批准因子', '暂无'], ['已配置周期', '暂无'], ['有效值数量', '暂无']]) {
        const statistic = page.locator('.ant-statistic').filter({ has: page.getByText(label, { exact: true }) });
        assert.equal(await statistic.locator('.ant-statistic-content').innerText(), value);
      }
      await snapshot(page, 'access-desktop');
      fail = true;
      await page.getByRole('button', { name: /刷新/ }).click();
      await visible(page.getByText('刷新失败，当前保留上次成功结果', { exact: true }));
      await visible(page.getByText('11', { exact: true }));
    } finally { await close(); }
  });

  await t.test('directory distinguishes first monitoring failure, refresh retention and missing projection', async () => {
    let mode = 'failure';
    const { page, close } = await session(['admin'], undefined, async (route, url) => {
      if (url.pathname !== '/api/station-monitoring/sites') return false;
      await route.fulfill(mode === 'failure' ? { status: 503, json: { error: '监测投影暂不可用' } }
        : { json: { items: mode === 'missing' ? [] : [overview().site], summary: { interval_unconfigured: 1 } } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites`);
      await visible(page.getByText('监测状态加载失败，站点台账仍可使用', { exact: true }));
      mode = 'success';
      await page.getByRole('button', { name: '重新加载', exact: true }).click();
      const tableRow = page.getByRole('row').filter({ hasText: row.name });
      await visible(tableRow.getByText('周期未配置', { exact: true }));
      mode = 'failure';
      await page.getByRole('button', { name: /刷新/ }).click();
      await visible(page.getByText('监测状态刷新失败，当前保留上次成功结果', { exact: true }));
      await visible(tableRow.getByText('周期未配置', { exact: true }));
      mode = 'missing';
      await page.getByRole('button', { name: '重新加载', exact: true }).click();
      await visible(tableRow.getByText('监测状态待确认', { exact: true }));
      assert.equal(await tableRow.getByText('周期未配置', { exact: true }).count(), 0);
      await snapshot(page, 'directory-desktop');
    } finally { await close(); }
  });

  await t.test('existing create/import/archive/profile/reagent entrances remain usable', async () => {
    const { page, close } = await session();
    try {
      await page.goto(`${baseURL}/sites`);
      await page.getByRole('button', { name: /新增站点/ }).click();
      const createDialog = page.getByRole('dialog', { name: '新增站点' });
      await visible(createDialog);
      await createDialog.getByRole('button', { name: /取\s*消/ }).click();
      await createDialog.waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: /数据接入/ }).click();
      await visible(page.getByRole('tab', { name: /文件导入/ }));
      await page.keyboard.press('Escape');
      await page.goto(`${baseURL}/sites?archive=7`);
      await visible(page.getByText('站点业务状态', { exact: true }));
      await visible(page.getByRole('button', { name: /完善基础档案/ }));
      await visible(page.getByRole('tab', { name: /试剂库存/ }));
      await page.getByRole('button', { name: /完善基础档案/ }).click();
      await visible(page.getByRole('dialog', { name: /完善.*基础档案/ }));
    } finally { await close(); }
  });

  await t.test('global search station result ignores legacy archive path and enters monitoring', async () => {
    const { page, close } = await session(['reviewer']);
    try {
      await page.goto(`${baseURL}/sites`);
      await visible(page.getByText(row.name, { exact: true }));
      await page.keyboard.press('Control+k');
      await page.getByPlaceholder('搜索页面、站点、工单或设备').fill(row.name);
      await page.getByRole('button', { name: `站点：${row.name}`, exact: true }).click();
      await page.waitForURL(`${baseURL}/sites/7`);
      await visible(page.getByRole('heading', { name: row.name }));
    } finally { await close(); }
  });

  await t.test('mobile detail and access render nonblank with no document overflow', async () => {
    const { page, close } = await session(['admin'], { width: 390, height: 844 });
    try {
      for (const [route, heading, name] of [['/sites/7', row.name, 'detail-mobile'], ['/sites/data-access', '监测数据接入观察', 'access-mobile']]) {
        await page.goto(`${baseURL}${route}`);
        await visible(page.getByRole('heading', { name: heading }));
        await snapshot(page, name);
        const sizes = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
        assert.ok(sizes.scroll <= sizes.width + 1, JSON.stringify(sizes));
      }
    } finally { await close(); }
  });
});
