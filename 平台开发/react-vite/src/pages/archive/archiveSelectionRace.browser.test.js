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

test('late filter-wide selection cannot repopulate a changed station scope', {
  skip: !baseURL || !runtime ? 'Set STATION_WEB_TEST_URL and STATION_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = require(runtime);
  const browser = await chromium.launch({
    channel: process.env.STATION_WEB_BROWSER_CHANNEL || 'msedge', headless: true,
  });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-archive-token'));
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(15000);
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  let releaseOld;
  let markOldStarted;
  let markOldSettled;
  const oldPending = new Promise((resolve) => { releaseOld = resolve; });
  const oldStarted = new Promise((resolve) => { markOldStarted = resolve; });
  const oldSettled = new Promise((resolve) => { markOldSettled = resolve; });

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'GET') {
      if (url.pathname === '/api/auth/me') {
        await route.fulfill({ json: {
          user: { id: 991020, username: 'archive-test', role: 'admin', roles: ['admin'] },
          site_ids: [7],
        } });
        return;
      }
      if (url.pathname === '/api/sites') {
        await route.fulfill({ json: [
          { id: 7, name: '隔离档案测试站' },
          { id: 8, name: '隔离档案备用站' },
        ] });
        return;
      }
      if (url.pathname === '/api/attachments') {
        const siteId = Number(url.searchParams.get('site_id'));
        await route.fulfill({ json: {
          total: 1,
          items: [{
            id: siteId === 8 ? 202 : 151,
            item_name: siteId === 8 ? 'B站照片' : 'A站照片',
            stored_path: image,
            display_url: image,
            site_name: siteId === 8 ? '隔离档案备用站' : '隔离档案测试站',
            source_type: 'inspection',
            review_status: 'approved',
            taken_at: '2026-11-01 08:00:00',
          }],
        } });
        return;
      }
      await route.fulfill({ json: {} });
      return;
    }

    if (url.pathname !== '/api/attachments/download/resolve') {
      await route.fulfill({ status: 405, json: { error: '隔离验收禁止写入' } });
      return;
    }
    const body = request.postDataJSON();
    const siteId = Number(body?.selection?.filters?.site_id);
    if (siteId === 7) {
      markOldStarted();
      await oldPending;
      try {
        await route.fulfill({ json: {
          items: [{ attachment_id: 151, filename: 'site-a.jpg' }], failures: [],
        } });
      } catch { /* The browser may cancel the stale request after the scope changes. */ }
      markOldSettled();
      return;
    }
    await route.fulfill({ json: {
      items: [{ attachment_id: 202, filename: 'site-b.jpg' }], failures: [],
    } });
  });

  try {
    await page.goto(`${baseURL}/archive?site_id=7`, { waitUntil: 'domcontentloaded' });
    const selectAll = page.getByRole('button', { name: '选择筛选结果全部 (1)', exact: true });
    await selectAll.waitFor({ state: 'visible' });
    await selectAll.click();
    await oldStarted;

    await page.getByLabel('站点').first().click();
    await page.getByText('隔离档案备用站', { exact: true }).click();
    await page.waitForURL(/site_id=8/);
    const directZeroText = page.getByText('直接保存 (0)', { exact: true });
    await directZeroText.waitFor({ state: 'visible' });
    const directZeroButton = directZeroText.locator('xpath=ancestor::button[1]');

    releaseOld();
    await oldSettled;
    await page.waitForTimeout(100);
    assert.equal(await directZeroButton.isDisabled(), true);
    assert.equal(await page.getByText('已固定选择当前筛选结果 1 条', { exact: true }).count(), 0);

    await page.getByRole('button', { name: '选择筛选结果全部 (1)', exact: true }).click();
    const directOneText = page.getByText('直接保存 (1)', { exact: true });
    await directOneText.waitFor({ state: 'visible' });
    assert.equal(await directOneText.locator('xpath=ancestor::button[1]').isDisabled(), false);
    assert.deepEqual(pageErrors, []);

    if (evidenceDir) {
      await mkdir(evidenceDir, { recursive: true });
      await page.screenshot({
        path: path.join(evidenceDir, 'archive-selection-race-site-b.png'), fullPage: true,
      });
    }
  } finally {
    releaseOld();
    await context.close();
  }
});
