import test from 'node:test';
import assert from 'node:assert/strict';
import process from 'node:process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const frontend = process.env.STATION_WEB_TEST_URL;
const backend = process.env.STATION_WEB_BACKEND_URL;
const token = process.env.STATION_WEB_BACKEND_TOKEN;
const siteId = Number(process.env.STATION_WEB_INTEGRATED_SITE_ID);

test('real Edge displays formal observations from an isolated Flask monitoring API', {
  skip: !frontend || !backend || !token || !siteId || !process.env.STATION_WEB_PLAYWRIGHT_MODULE,
}, async () => {
  const { chromium } = require(process.env.STATION_WEB_PLAYWRIGHT_MODULE);
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-web-test-token'));
  const page = await context.newPage();
  try {
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      assert.equal(route.request().method(), 'GET', 'no browser-side business writes');
      if (url.pathname === '/api/auth/me') {
        await route.fulfill({ json: { user: { id: 1, username: 'monitor-admin', role: 'admin', roles: ['admin'],
          capabilities: { station_monitoring_public: true } }, site_ids: [siteId] } });
      } else if (url.pathname === '/api/sites') {
        await route.fulfill({ json: [
          { id: siteId, name: '隔离监测站', code: 'MON-1', type: 'water' },
          { id: siteId + 1, name: '未接入站', code: 'MON-2', type: 'water' },
        ] });
      } else if (url.pathname === '/api/station-monitoring/sites') {
        const response = await fetch(`${backend}${url.pathname}${url.search}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        await route.fulfill({ status: response.status, body: await response.text(),
          contentType: 'application/json' });
      } else {
        await route.fulfill({ json: [] });
      }
    });
    await page.goto(`${frontend}/sites`);
    const connected = page.getByRole('row').filter({ hasText: '隔离监测站' });
    const unconnected = page.getByRole('row').filter({ hasText: '未接入站' });
    await connected.getByText('水温：8.8 degC', { exact: true }).waitFor();
    await connected.getByText(/^观测：2020-06-12/).waitFor();
    await unconnected.getByText('暂无已形成的有效观测', { exact: true }).waitFor();
    assert.equal(await unconnected.getByText('水温：8.8 degC', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('columnheader', { name: '最后数据' }).count(), 1);
    assert.equal(await page.getByRole('columnheader', { name: '关键时间' }).count(), 1);
  } finally {
    await context.close();
    await browser.close();
  }
});
