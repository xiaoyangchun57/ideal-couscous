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
        id, item_name: `${history ? '历史' : '当前'}档案记录${id}`, stored_path: image,
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

  async function session(viewport = { width: 1440, height: 1000 }, handler, writeHandler) {
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
        if (writeHandler && await writeHandler(route, url)) return;
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
        await page.getByRole('textbox', { name: '影像搜索', exact: true }).fill('即时筛选');
        await page.getByText('表格', { exact: true }).click();
        await visible(page.getByRole('row').filter({ hasText: '历史档案记录1' }));
        assert.equal(await page.getByRole('textbox', { name: '影像搜索', exact: true }).inputValue(), '即时筛选');
        assert.equal(new URL(page.url()).searchParams.get('keyword'), '即时筛选');
        assert.equal(new URL(page.url()).searchParams.has('page'), false);
        const table = page.locator('.archive-results .ant-table-body');
        await scrollToLast(page, table, page.getByRole('row').filter({ hasText: '历史档案记录100' }), 'table');
        await screenshot(page, `archive-table-${name}-bottom`);
        await page.getByText('网格', { exact: true }).click();
        await visible(region.getByText('历史档案记录1', { exact: true }));
        assert.equal(await page.getByRole('textbox', { name: '影像搜索', exact: true }).inputValue(), '即时筛选');
        for (const [index, url] of requests.entries()) {
          assert.equal(url.searchParams.get('keyword'), index < 2 ? '档案' : '即时筛选');
          assert.equal(url.searchParams.get('site_id'), '7');
          assert.equal(url.searchParams.get('business_type'), 'inspection');
          assert.equal(url.searchParams.get('date_from'), '2026-09-01');
          assert.equal(url.searchParams.get('date_to'), '2026-09-15');
          assert.equal(url.searchParams.get('history_archive'), '1');
          assert.equal(url.searchParams.get('include_voided'), '1');
          assert.equal(url.searchParams.get('limit'), '100');
        }
        assert.deepEqual(requests.map((url) => url.searchParams.get('page')), ['1', '2', '1'], 'changing a pending keyword resets to page one while view-only switches do not refetch');
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

  await t.test('unknown batch result cannot be dismissed and retries the identical request', async () => {
    let attachmentLoads = 0;
    const purgeRequests = [];
    const { page, close } = await session(undefined, async (route, url) => {
      attachmentLoads += 1;
      await route.fulfill({ json: attachmentLoads > 1 ? { total: 0, items: [] } : payload(url, 1) });
      return true;
    }, async (route, url) => {
      if (url.pathname === '/api/attachments/purge-batch/preview') {
        await route.fulfill({ json: {
          requested_count: 1, purgeable_count: 1, blocked_count: 0, can_purge: true, items: [],
        } });
        return true;
      }
      if (url.pathname !== '/api/attachments/purge-batch') return false;
      purgeRequests.push(route.request().postDataJSON());
      if (purgeRequests.length === 1) await route.abort('failed');
      else await route.fulfill({ json: {
        success: true, idempotent_replay: true, purged_count: 1, temporary_cleanup_pending: 0,
      } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?scope=history`);
      await visible(page.getByRole('row').filter({ hasText: '历史档案记录1' }));
      await page.getByRole('button', { name: '选择当前页', exact: true }).click();
      await page.getByRole('button', { name: '批量清理 (1)', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '批量彻底清理历史影像' });
      await visible(dialog);
      await dialog.getByRole('textbox', { name: '批量清理原因', exact: true }).fill('隔离测试误传');
      await dialog.getByRole('button', { name: '确认清理 1 条', exact: true }).click();
      await visible(dialog.getByText(/未能确认服务端处理结果/));
      assert.equal(await page.locator('.ant-modal-footer button').first().isDisabled(), true);
      await page.keyboard.press('Escape');
      assert.equal(await dialog.isVisible(), true, 'unknown result dialog cannot be dismissed');
      await page.locator('.ant-modal-footer button').last().click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(purgeRequests.length, 2);
      assert.deepEqual(purgeRequests[1], purgeRequests[0], 'retry reuses ids, reason and idempotency key');
      assert.equal(await page.getByRole('button', { name: '批量清理 (0)', exact: true }).isDisabled(), true);
      assert.ok(attachmentLoads >= 2, 'successful replay refreshes the archive list');
    } finally { await close(); }
  });

  await t.test('batch preview hides a non-JSON server error body', async () => {
    const html = '<!doctype html><title>405 Method Not Allowed</title><pre>C:\\internal\\server.py</pre>';
    const { page, close } = await session(undefined, undefined, async (route, url) => {
      if (url.pathname !== '/api/attachments/purge-batch/preview') return false;
      await route.fulfill({ status: 405, contentType: 'text/html', body: html });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?scope=history`);
      await visible(page.getByRole('row').filter({ hasText: '历史档案记录1' }));
      await page.reload({ waitUntil: 'networkidle' });
      await visible(page.getByRole('row').filter({ hasText: '历史档案记录1' }));
      await page.getByRole('button', { name: '选择当前页', exact: true }).click();
      await page.getByRole('button', { name: '批量清理 (100)', exact: true }).click();
      await visible(page.getByText('服务接口暂不可用，请刷新页面后重试（HTTP 405）', { exact: true }));
      assert.equal(await page.getByText(/doctype|internal|server\.py|Method Not Allowed/i).count(), 0);
    } finally { await close(); }
  });

  await t.test('blocked batch preview shows only reasons and a return action', async () => {
    const { page, close } = await session(undefined, async (route, url) => {
      const fixture = payload(url, 2);
      await route.fulfill({ json: { total: 2, items: fixture.items.map((item, index) => ({
        ...item,
        item_name: '同名现场照片',
        site_name: index === 0 ? '东区站' : '西区站',
        taken_at: index === 0 ? '2026-09-15 08:00:00' : '2026-09-15 09:30:00',
      })) } });
      return true;
    }, async (route, url) => {
      if (url.pathname !== '/api/attachments/purge-batch/preview') return false;
      await route.fulfill({ json: {
        requested_count: 2,
        purgeable_count: 0,
        blocked_count: 3,
        can_purge: false,
        items: [
          { attachment_id: 1, can_purge: false, block_reason: '当前有效证据' },
          { attachment_id: 2, can_purge: false, block_reason: '仍被业务记录引用' },
          { attachment_id: 999, can_purge: false, block_reason: '记录已不在当前列表' },
        ],
      } });
      return true;
    });
    try {
      await page.goto(`${baseURL}/archive?scope=history`);
      await visible(page.getByRole('row').filter({ hasText: '同名现场照片' }).first());
      await page.getByRole('button', { name: '选择当前页', exact: true }).click();
      await page.getByRole('button', { name: '批量清理 (2)', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '批量彻底清理历史影像' });
      await visible(page.getByText('有 3 条不可清理，请返回移除后重试', { exact: true }));
      assert.equal(await dialog.getByText('同名现场照片', { exact: true }).count(), 2);
      await visible(page.getByText('东区站 · 2026-09-15 08:00:00 · 记录 #1', { exact: true }));
      await visible(page.getByText('西区站 · 2026-09-15 09:30:00 · 记录 #2', { exact: true }));
      await visible(page.getByText('阻断原因：当前有效证据', { exact: true }));
      await visible(page.getByText('阻断原因：仍被业务记录引用', { exact: true }));
      await visible(page.getByText('未找到对应照片（记录 #999）', { exact: true }));
      await visible(page.getByText('当前列表中没有这条记录，请返回刷新后重新选择', { exact: true }));
      await visible(page.getByText('阻断原因：记录已不在当前列表', { exact: true }));
      assert.equal(await page.getByRole('textbox', { name: '批量清理原因', exact: true }).count(), 0);
      assert.equal(await page.getByRole('button', { name: /确认清理 0 条/ }).count(), 0);
      assert.equal(await page.locator('.ant-modal-footer button').count(), 1);
      assert.equal(await page.locator('.ant-modal-footer button').first().textContent(), '返 回');
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
