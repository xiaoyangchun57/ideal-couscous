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
      last_received_at: '2026-09-15T08:00:00+08:00',
      last_valid_observation_at: '2026-09-10T07:00:00+08:00',
    },
    monitoring: {
      latest_values: ['normal', 'attention', 'interval_unconfigured'].includes(status)
        ? [{ business_metric: '酸碱度', standard_value: 7.25, standard_unit: 'pH', observed_at: '2026-09-10T07:00:00+08:00' }] : [],
      factors: [{ business_metric: '酸碱度', standard_unit: 'pH' }],
      axes: { communication: { state: 'fresh' }, data: { state: status } },
      capabilities: { latest: true, trend: false, factors: true },
    },
  };
}

function trend(metric = '酸碱度') {
  const unit = metric === 'ammonia' ? 'mg/L' : 'pH';
  return {
    site_id: 7,
    metric,
    factor_name_cn: metric === 'ammonia' ? '氨氮' : '酸碱度',
    standard_unit: unit,
    points: [
      ...[0, 4, 8, 12, 16, 20].map((hour, index) => ({
        scheduled_at: `2026-09-15T${String(hour).padStart(2, '0')}:00:00+08:00`,
        observed_at: `2026-09-15T${String(hour).padStart(2, '0')}:00:00+08:00`,
        value: 7.1 + index * 0.03, unit, quality: 'valid',
      })),
    ],
    coverage: {
      window_start: '2026-09-15T00:00:00+08:00', window_end: '2026-09-15T23:59:59+08:00',
      coverage_rate: 1, valid_points: 6, displayed_points: 6, expected_points: 6, gap_count: 0, missing_points: 0,
      late_points: 0, suspect_points: 0, duplicate_records: 0, conflict_slots: 0,
    },
    gaps: [],
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
      if (url.pathname === '/api/auth/me') body = {
        user: {
          id: 991020, username: 'web-test', role: roles[0], roles,
          capabilities: { station_monitoring_public: true },
        },
        site_ids: [7, 8],
      };
      else if (url.pathname === '/api/sites') body = roles.includes('admin') ? [row, { ...row, id: 8, name: '隔离测试站乙', code: 'WEB-TEST-8' }] : [row];
      else if (url.pathname === '/api/station-monitoring/sites') body = {
        scope: roles.includes('admin') ? 'all' : 'mine', available_scopes: roles.includes('admin') ? ['all', 'mine'] : ['mine'],
        items: roles.includes('admin') ? [overview().site, overview('normal', 8).site] : [overview().site], summary: { interval_unconfigured: 1, normal: roles.includes('admin') ? 1 : 0 },
      };
      else if (/\/station-monitoring\/sites\/\d+\/overview$/.test(url.pathname)) body = overview('interval_unconfigured', Number(url.pathname.split('/').at(-2)));
      else if (/\/station-monitoring\/sites\/\d+\/trend$/.test(url.pathname)) body = {
        ...trend(url.searchParams.get('metric')), site_id: Number(url.pathname.split('/').at(-2)),
      };
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
      await visible(page.getByText('数据周期未配置', { exact: true }));
      await visible(page.getByText('7.25 pH', { exact: true }));
      await visible(page.getByText('最后收到报文', { exact: true }));
      await visible(page.getByText('最后有效观测', { exact: true }));
      await visible(page.getByText('最近24小时趋势', { exact: true }));
      await visible(page.getByText('100.0% (6/6)', { exact: true }));
      await visible(page.getByText('0 段，缺 0 点', { exact: true }));
      await visible(page.getByText('pH', { exact: true }));
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

  await t.test('an empty trend response stays explicit and protocol fields are not displayed', async () => {
    const payload = overview();
    payload.monitoring.capabilities.trend = true;
    payload.monitoring.latest_values[0].business_metric = null;
    payload.monitoring.latest_values[0].protocol_factor = 'PROTOCOL-SECRET-TEST';
    payload.monitoring.factors[0].business_metric = null;
    payload.monitoring.factors[0].protocol_code = 'PROTOCOL-SECRET-TEST';
    const { page, close } = await session(['reviewer'], undefined, async (route, url) => {
      if (url.pathname.endsWith('/7/overview')) {
        await route.fulfill({ json: payload });
        return true;
      }
      if (url.pathname.endsWith('/7/trend')) {
        await route.fulfill({ json: { ...trend(url.searchParams.get('metric')), points: [] } });
        return true;
      }
      return false;
    });
    try {
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.getByText('当前因子在最近24小时内暂无有效观测', { exact: true }));
      await absent(page, 'PROTOCOL-SECRET-TEST');
      await absent(page, 'FAKE-BUSINESS-TIME');
    } finally { await close(); }
  });

  await t.test('trend failure is retryable and factor switching uses the selected server metric', async () => {
    const payload = overview();
    payload.monitoring.factors.push({ business_metric: 'ammonia', factor_name_cn: '氨氮', standard_unit: 'mg/L' });
    payload.factors = payload.monitoring.factors;
    let fail = true;
    const metrics = [];
    const { page, close } = await session(['reviewer'], undefined, async (route, url) => {
      if (url.pathname.endsWith('/7/overview')) {
        await route.fulfill({ json: payload });
        return true;
      }
      if (url.pathname.endsWith('/7/trend')) {
        metrics.push(url.searchParams.get('metric'));
        await route.fulfill(fail
          ? { status: 503, json: { error: '趋势服务暂不可用' } }
          : { json: trend(url.searchParams.get('metric')) });
        return true;
      }
      return false;
    });
    try {
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.getByText('趋势加载失败', { exact: true }));
      await visible(page.getByText('趋势服务暂不可用', { exact: true }));
      fail = false;
      await page.getByRole('button', { name: /重试/ }).click();
      await visible(page.getByText('100.0% (6/6)', { exact: true }));
      await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '趋势因子' }) }).click();
      await page.getByText('氨氮 (mg/L)', { exact: true }).click();
      await visible(page.getByText('mg/L', { exact: true }));
      assert.deepEqual(metrics.slice(-2), ['酸碱度', 'ammonia']);
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
        : { json: { scope: 'all', available_scopes: ['all', 'mine'], items: mode === 'missing' ? [] : [overview().site], summary: { interval_unconfigured: 1 } } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites`);
      await visible(page.getByText('监测状态加载失败，站点台账仍可使用', { exact: true }));
      mode = 'success';
      await page.getByRole('button', { name: '重新加载', exact: true }).click();
      const tableRow = page.getByRole('row').filter({ hasText: row.name });
      await visible(tableRow.getByText('数据周期未配置', { exact: true }));
      mode = 'failure';
      await page.getByRole('button', { name: /刷新/ }).click();
      await visible(page.getByText('监测状态刷新失败，当前保留上次成功结果', { exact: true }));
      await visible(tableRow.getByText('数据周期未配置', { exact: true }));
      mode = 'missing';
      await page.getByRole('button', { name: '重新加载', exact: true }).click();
      await visible(tableRow.getByText('监测状态待确认', { exact: true }));
      assert.equal(await tableRow.getByText('数据周期未配置', { exact: true }).count(), 0);
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

  await t.test('directory explicitly requests all for admins and mine for reviewers and operators', async () => {
    for (const roles of [['admin'], ['reviewer'], ['operator'], ['reviewer', 'admin']]) {
      const scopes = [];
      const { page, close } = await session(roles, undefined, async (_route, url) => {
        if (url.pathname === '/api/station-monitoring/sites') scopes.push(url.searchParams.get('scope'));
        return false;
      });
      try {
        await page.goto(`${baseURL}/sites`);
        await visible(page.getByText(row.name, { exact: true }));
        const expected = roles.includes('admin') ? 'all' : 'mine';
        assert.ok(scopes.length > 0);
        assert.ok(scopes.every((scope) => scope === expected));
        if (expected === 'all') {
          const other = page.getByRole('row').filter({ hasText: '隔离测试站乙' });
          await visible(other.getByText('数据正常', { exact: true }));
        }
        await visible(page.getByText(`监测范围：${expected === 'all' ? '全部有权站点' : '本人负责站点'}`, { exact: true }));
      } finally { await close(); }
    }
  });

  await t.test('server scope facts stay authoritative and a superseded refresh cannot overwrite the latest result', async () => {
    let calls = 0;
    let release;
    let started;
    let finished;
    const pending = new Promise((resolve) => { release = resolve; });
    const requested = new Promise((resolve) => { started = resolve; });
    const settled = new Promise((resolve) => { finished = resolve; });
    const { page, close } = await session(['admin'], undefined, async (route, url) => {
      if (url.pathname !== '/api/station-monitoring/sites') return false;
      assert.equal(url.searchParams.get('scope'), 'all');
      calls += 1;
      const current = calls;
      if (current === 2) { started(); await pending; }
      const result = {
        scope: current === 1 ? 'mine' : 'all', available_scopes: current === 1 ? ['mine'] : ['mine', 'all'],
        items: [{ ...overview(current === 3 ? 'attention' : 'normal').site, monitoring_reason: current === 3 ? '最新刷新事实' : '较早刷新事实' }], summary: {},
      };
      try { await route.fulfill({ json: result }); } catch { /* A newer refresh may have cancelled the held request. */ }
      if (current === 2) finished();
      return true;
    });
    try {
      await page.goto(`${baseURL}/sites`);
      await visible(page.getByText('监测范围：本人负责站点', { exact: true }));
      await page.getByRole('button', { name: /刷新/ }).click();
      await requested;
      await page.getByRole('button', { name: '重新加载', exact: true }).click();
      await visible(page.getByText('最新刷新事实', { exact: true }));
      release();
      await settled;
      await absent(page, '较早刷新事实');
      await visible(page.getByText('监测范围：全部有权站点', { exact: true }));
    } finally { release(); await close(); }
  });

  await t.test('data fact axes honor server labels and Chinese factor names hide metric identifiers', async () => {
    let axes;
    const payload = overview();
    for (const item of [...payload.monitoring.latest_values, ...payload.monitoring.factors]) {
      item.factor_name_cn = '服务端中文因子名'; item.business_metric = 'internal_metric_identifier';
    }
    payload.monitoring.capabilities.trend = true;
    payload.monitoring.trend = [{ factor_name_cn: '服务端中文因子名', business_metric: 'internal_metric_identifier', value: 0 }];
    const { page, close } = await session(['reviewer'], undefined, async (route, url) => {
      if (url.pathname.endsWith('/7/overview')) {
        await route.fulfill({ json: { ...payload, axes } });
        return true;
      }
      if (url.pathname.endsWith('/7/trend')) {
        await route.fulfill({ json: {
          ...trend('internal_metric_identifier'), metric: 'internal_metric_identifier',
          factor_name_cn: '服务端中文因子名',
        } });
        return true;
      }
      return false;
    });
    try {
      for (const [status, badge] of [['normal', 'success'], ['attention', 'warning'], ['missing', 'default'], ['unavailable', 'error']]) {
        axes = Object.fromEntries(['communication', 'data'].map((key) => [key, { status, state: 'unknown', status_label: `中文分轴:${key}:${status}` }]));
        await page.goto(`${baseURL}/sites/7`);
        for (const [key, label] of [['communication', '数据接收'], ['data', '观测数据']]) {
          const group = page.getByRole('group', { name: label, exact: true });
          await visible(group.getByText(`中文分轴:${key}:${status}`, { exact: true }));
          assert.match(await group.locator('.ant-badge-status-dot').getAttribute('class'), new RegExp(`ant-badge-status-${badge}`));
        }
      }
      for (const title of ['最新有效值', '最近24小时趋势', '监测因子']) {
        const card = page.locator('.ant-card').filter({ has: page.locator('.ant-card-head-title').getByText(title, { exact: true }) });
        assert.match(await card.innerText(), /服务端中文因子名/);
        assert.doesNotMatch(await card.innerText(), /internal_metric_identifier/);
      }
      axes = { communication: { status: 'fresh' }, data: { status: 'stale' } };
      await page.getByRole('button', { name: '刷新', exact: true }).click();
      for (const text of ['在配置周期内', '超出配置周期']) await visible(page.getByText(text, { exact: true }));
      await snapshot(page, 'server-axes-factor-names');
    } finally { await close(); }
  });

  await t.test('access breadcrumb uses its exact title while the site parent menu stays selected', async () => {
    const { page, close } = await session(['admin']);
    try {
      await page.goto(`${baseURL}/sites/data-access`);
      await visible(page.locator('.page-location').getByText('接入观察', { exact: true }));
      await visible(page.locator('.app-sidebar .ant-menu-item-selected').getByText('站点全景', { exact: true }));
      await visible(page.getByText('2026/9/15 08:00:00', { exact: true }));
      assert.equal(await page.getByText(summary.updated_at, { exact: true }).count(), 0);
      await page.goto(`${baseURL}/sites/7`);
      await visible(page.locator('.page-location').getByText('站点全景', { exact: true }));
      await page.goto(`${baseURL}/unknown`);
      await visible(page.locator('.page-location').getByText('页面不存在', { exact: true }));
    } finally { await close(); }
  });

  await t.test('desktop site table keeps core columns and actions reachable without toolbar growth', async () => {
    const rows = Array.from({ length: 37 }, (_, index) => ({
      ...row,
      id: index + 1,
      name: `隔离测试站${String(index + 1).padStart(2, '0')}`,
      code: `WEB-${String(index + 1).padStart(3, '0')}`,
      address: `测试地址${index + 1}号`,
    }));
    const monitoringItems = rows.map((item) => ({ ...overview(indexToStatus(item.id), item.id).site, ...item }));
    function indexToStatus(id) { return id % 2 ? 'normal' : 'attention'; }
    const { page, close } = await session(['admin'], { width: 1280, height: 720 }, async (route, url) => {
      if (url.pathname === '/api/sites') { await route.fulfill({ json: rows }); return true; }
      if (url.pathname === '/api/station-monitoring/sites') {
        await route.fulfill({ json: { scope: 'all', available_scopes: ['all', 'mine'], items: monitoringItems, summary: { normal: 19, attention: 18 } } });
        return true;
      }
      return false;
    });
    try {
      await page.goto(`${baseURL}/sites`);
      await visible(page.getByRole('columnheader', { name: '站点身份', exact: true }));
      for (const heading of ['监测状态', '关键时间', '负责人', '操作']) {
        await visible(page.getByRole('columnheader', { name: heading, exact: true }));
      }
      const action = page.getByRole('button', { name: /查看 隔离测试站01 的站点档案/ });
      const actionBox = await action.boundingBox();
      assert.ok(actionBox && actionBox.x + actionBox.width <= 1280, 'fixed action stays in the viewport');
      const tableBody = page.locator('.workspace-table .ant-table-body');
      const tableSizes = await tableBody.evaluate((element) => ({ clientHeight: element.clientHeight, scrollHeight: element.scrollHeight }));
      assert.ok(tableSizes.scrollHeight > tableSizes.clientHeight, JSON.stringify(tableSizes));
      const toolbar = page.locator('.workspace-toolbar');
      const before = await toolbar.evaluate((element) => element.getBoundingClientRect().height);
      await page.getByRole('textbox', { name: '站点搜索' }).fill('WEB-030');
      await visible(page.getByText('已筛选 1 条', { exact: true }));
      assert.equal(await page.getByText('当前结果', { exact: true }).count(), 0);
      const after = await toolbar.evaluate((element) => element.getBoundingClientRect().height);
      assert.ok(after <= before + 1, `toolbar height changed from ${before} to ${after}`);
      await snapshot(page, 'sites-desktop-table-filter');
    } finally { await close(); }
  });

  await t.test('short desktop monitoring body scroll reaches the final section', async () => {
    const { page, close } = await session(['admin'], { width: 1280, height: 600 });
    try {
      await page.goto(`${baseURL}/sites/7`);
      const region = page.getByRole('region', { name: '站点监测正文' });
      await visible(region);
      const sizes = await region.evaluate((element) => ({ clientHeight: element.clientHeight, scrollHeight: element.scrollHeight }));
      assert.ok(sizes.scrollHeight > sizes.clientHeight, JSON.stringify(sizes));
      await region.evaluate((element) => { element.scrollTop = element.scrollHeight; });
      const finalHeading = page.getByText('监测因子', { exact: true });
      await visible(finalHeading);
      const finalBox = await finalHeading.boundingBox();
      const regionBox = await region.boundingBox();
      assert.ok(finalBox && regionBox && finalBox.y >= regionBox.y && finalBox.y + finalBox.height <= regionBox.y + regionBox.height + 1);
      await snapshot(page, 'monitoring-short-desktop-bottom');
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
