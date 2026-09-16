import test from 'node:test';
import assert from 'node:assert/strict';
import process from 'node:process';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const baseURL = process.env.STATION_WEB_TEST_URL;
const runtime = process.env.STATION_WEB_PLAYWRIGHT_MODULE;
const evidenceDir = process.env.STATION_WEB_EVIDENCE_DIR;
const require = createRequire(import.meta.url);
const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=';
const filters = new URLSearchParams({ view: 'grid', scope: 'history', keyword: '档案', site_id: '7', business_type: 'inspection', date_from: '2026-09-01', date_to: '2026-09-15' });

function payload(url, total = 205) {
  const page = Number(url.searchParams.get('page'));
  const start = (page - 1) * 100;
  const history = url.searchParams.get('history_archive') === '1';
  return {
    total,
    items: Array.from({ length: Math.max(0, Math.min(100, total - start)) }, (_, offset) => {
      const id = start + offset + 1;
      return {
        id, archive_name: `${history ? '历史' : '当前'}档案记录${id}`, stored_path: image,
        site_name: '隔离档案测试站', source_type: 'inspection', review_status: history ? 'rejected' : 'approved',
        taken_at: '2026-09-15 08:00:00', original_filename: `isolated-archive-${id}.png`,
      };
    }),
  };
}

test('image archive grid and table behavior with isolated server pagination', {
  skip: !baseURL || !runtime ? 'Set STATION_WEB_TEST_URL and STATION_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = require(runtime);
  const browser = await chromium.launch({ channel: process.env.STATION_WEB_BROWSER_CHANNEL || 'msedge', headless: true });
  t.after(() => browser.close());
  if (evidenceDir) await mkdir(evidenceDir, { recursive: true });

  async function session(viewport = { width: 1440, height: 1000 }, handler) {
    const context = await browser.newContext({ viewport });
    await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-archive-token'));
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    const writes = [];
    const requests = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() !== 'GET') {
        writes.push(route.request().method());
        await route.fulfill({ status: 405, json: { error: '隔离验收禁止写入' } });
        return;
      }
      if (url.pathname === '/api/attachments') {
        requests.push(url);
        if (handler && await handler(route, url)) return;
        await route.fulfill({ json: payload(url) });
        return;
      }
      const body = url.pathname === '/api/auth/me'
        ? { user: { id: 991020, username: 'archive-test', role: 'admin', roles: ['admin'] }, site_ids: [7] }
        : url.pathname === '/api/sites' ? [{ id: 7, name: '隔离档案测试站' }] : {};
      await route.fulfill({ json: body });
    });
    return {
      page, requests,
      close: async () => {
        if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, 'archive-latest-session.png'), fullPage: true });
        await context.close();
        assert.deepEqual(errors, [], 'no page runtime exceptions');
        assert.deepEqual(writes, [], 'no business writes');
      },
    };
  }

  async function visible(locator) { await locator.first().waitFor({ state: 'visible' }); }
  async function screenshot(page, name) {
    if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage: true });
  }
  async function scrollToLast(page, container, last, label) {
    const sizes = await container.evaluate((element) => ({ height: element.clientHeight, scroll: element.scrollHeight, bottom: element.getBoundingClientRect().bottom }));
    assert.ok(sizes.height > 0 && sizes.scroll > sizes.height, `${label}: ${JSON.stringify(sizes)}`);
    assert.ok(sizes.bottom <= page.viewportSize().height + 1, `${label} stays inside the workspace`);
    await container.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    const box = await last.boundingBox();
    const frame = await container.boundingBox();
    assert.ok(box && frame && box.y + box.height <= frame.y + frame.height + 2 && box.y >= frame.y - 2, `${label} last item is reachable`);
  }

  for (const [name, viewport] of [['desktop', { width: 1440, height: 1000 }], ['narrow', { width: 390, height: 844 }]]) {
    await t.test(`${name}: grid scroll reaches item 100, pagination reaches 101, table retains state and scrolling`, async () => {
      const { page, requests, close } = await session(viewport);
      try {
        await page.goto(`${baseURL}/archive?${filters}`);
        const region = page.getByRole('region', { name: '影像档案网格' });
        await visible(region.getByText('历史档案记录100', { exact: true }));
        assert.equal(await region.locator('.ant-card').count(), 100);
        const loaded = await region.locator('img').first().evaluate((element) => element.complete && element.naturalWidth > 0);
        assert.equal(loaded, true, 'fixture bitmap renders');
        await scrollToLast(page, region, region.locator('.ant-card').last(), 'grid');
        await visible(page.getByText('共 205 条', { exact: true }));
        await screenshot(page, `archive-grid-${name}-bottom`);
        await page.getByTitle('2', { exact: true }).click();
        await visible(region.getByText('历史档案记录101', { exact: true }));
        assert.equal(new URL(page.url()).searchParams.get('page'), '2');
        await screenshot(page, `archive-grid-${name}-page2`);
        await page.getByRole('textbox', { name: '影像搜索', exact: true }).fill('尚未提交的筛选');
        await page.getByText('表格', { exact: true }).click();
        await visible(page.getByRole('row').filter({ hasText: '历史档案记录101' }));
        assert.equal(await page.getByRole('textbox', { name: '影像搜索', exact: true }).inputValue(), '尚未提交的筛选');
        assert.equal(new URL(page.url()).searchParams.get('page'), '2');
        const table = page.locator('.archive-results .ant-table-body');
        await scrollToLast(page, table, page.getByRole('row').filter({ hasText: '历史档案记录200' }), 'table');
        await screenshot(page, `archive-table-${name}-bottom`);
        await page.getByText('网格', { exact: true }).click();
        await visible(region.getByText('历史档案记录101', { exact: true }));
        assert.equal(await page.getByRole('textbox', { name: '影像搜索', exact: true }).inputValue(), '尚未提交的筛选');
        for (const url of requests) {
          assert.equal(url.searchParams.get('keyword'), '档案');
          assert.equal(url.searchParams.get('site_id'), '7');
          assert.equal(url.searchParams.get('business_type'), 'inspection');
          assert.equal(url.searchParams.get('date_from'), '2026-09-01');
          assert.equal(url.searchParams.get('date_to'), '2026-09-15');
          assert.equal(url.searchParams.get('history_archive'), '1');
          assert.equal(url.searchParams.get('include_voided'), '1');
          assert.equal(url.searchParams.get('limit'), '100');
        }
        assert.deepEqual(requests.map((url) => url.searchParams.get('page')), ['1', '2'], 'view switches do not reset pages or refetch all records');
        const widths = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth, right: document.querySelector('.workspace-page').getBoundingClientRect().right }));
        assert.ok(widths.scroll <= widths.width + 1 && widths.right <= widths.width + 1, JSON.stringify(widths));
      } finally { await close(); }
    });
  }

  await t.test('server total shrink corrects an invalid page without showing a false empty result', async () => {
    const { page, requests, close } = await session(undefined, async (route, url) => {
      await route.fulfill({ json: payload(url, 51) });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?${filters}&page=3`);
      await visible(page.getByText('历史档案记录51', { exact: true }));
      assert.deepEqual(requests.map((url) => url.searchParams.get('page')), ['3', '1']);
      assert.equal(new URL(page.url()).searchParams.has('page'), false);
      await visible(page.getByText('共 51 条', { exact: true }));
    } finally { await close(); }
  });

  await t.test('failed pagination keeps URL filters and page available for retry', async () => {
    let fail = true;
    const { page, close } = await session(undefined, async (route, url) => {
      if (url.searchParams.get('page') !== '2' || !fail) return false;
      await route.fulfill({ status: 503, json: { error: '档案分页暂不可用' } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?${filters}`);
      await visible(page.getByText('历史档案记录1', { exact: true }));
      await page.getByTitle('2', { exact: true }).click();
      await visible(page.getByText('档案分页暂不可用', { exact: true }));
      assert.equal(new URL(page.url()).searchParams.get('page'), '2');
      assert.equal(await page.getByRole('textbox', { name: '影像搜索', exact: true }).inputValue(), '档案');
      fail = false;
      await page.getByRole('button', { name: /刷新/ }).click();
      await visible(page.getByText('历史档案记录101', { exact: true }));
    } finally { await close(); }
  });

  await t.test('internal source values render as Chinese business labels in table and detail', async () => {
    const { page, close } = await session(undefined, async (route, url) => {
      const item = payload(url, 1).items[0];
      await route.fulfill({ json: { total: 1, items: [{ ...item, source_type: 'manual_report', capture_source: 'unknown' }] } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?scope=history`);
      await visible(page.getByRole('row').filter({ hasText: '人工上报' }));
      assert.equal(await page.getByText('manual_report', { exact: true }).count(), 0);
      assert.equal(await page.getByText('unknown', { exact: true }).count(), 0);
      await page.getByRole('button', { name: '详情', exact: true }).click();
      await visible(page.getByText('待确认', { exact: true }));
      await visible(page.getByText('人工上报', { exact: true }));
    } finally { await close(); }
  });

  await t.test('filtered empty result explains the filter and keeps a reset action', async () => {
    const { page, close } = await session(undefined, async (route) => {
      await route.fulfill({ json: { total: 0, items: [] } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?scope=history&keyword=不存在`);
      await visible(page.getByText('没有符合当前筛选条件的记录', { exact: true }));
      await visible(page.getByText('重置筛选', { exact: true }));
    } finally { await close(); }
  });

  await t.test('switching history/current scope cancels pending pagination and ignores stale records', async () => {
    let release;
    let started;
    let finished;
    const pending = new Promise((resolve) => { release = resolve; });
    const requested = new Promise((resolve) => { started = resolve; });
    const settled = new Promise((resolve) => { finished = resolve; });
    const { page, close } = await session(undefined, async (route, url) => {
      if (url.searchParams.get('page') !== '2' || url.searchParams.get('history_archive') !== '1') return false;
      started(); await pending;
      try { await route.fulfill({ json: payload(url) }); } catch { /* Scope navigation may abort the previous page. */ }
      finished();
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?${filters}`);
      await visible(page.getByText('历史档案记录1', { exact: true }));
      await page.getByTitle('2', { exact: true }).click();
      await requested;
      await page.getByText('当前档案', { exact: true }).click();
      await visible(page.getByText('当前档案记录1', { exact: true }));
      release(); await settled;
      assert.equal(await page.getByText('历史档案记录101', { exact: true }).count(), 0);
      assert.equal(new URL(page.url()).searchParams.has('page'), false);
      assert.equal(new URL(page.url()).searchParams.has('scope'), false);
    } finally { release(); await close(); }
  });
});
