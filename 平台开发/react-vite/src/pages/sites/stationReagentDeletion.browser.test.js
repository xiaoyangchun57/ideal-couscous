import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import process from 'node:process';

const baseURL = process.env.STATION_WEB_TEST_URL;
const playwrightModule = process.env.STATION_WEB_PLAYWRIGHT_MODULE;

test('isolated Web fixture: failed reagent deletion retains reason and key, retry refreshes once', {
  skip: !baseURL || !playwrightModule ? 'Set STATION_WEB_TEST_URL and STATION_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = createRequire(import.meta.url)(playwrightModule);
  const browser = await chromium.launch({ channel: process.env.STATION_WEB_BROWSER_CHANNEL || 'msedge', headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext();
  t.after(() => context.close());
  await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-delete-test'));
  const page = await context.newPage();
  const requests = [];
  let inventory = [{ id: 11, site_id: 7, reagent_id: 19, reagent_name: '测试试剂', current_qty: 1 }];
  let refreshes = 0;
  let releaseFirst;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'DELETE') {
      requests.push(JSON.parse(request.postData()));
      if (requests.length === 1) {
        await new Promise((resolve) => { releaseFirst = resolve; });
        await route.fulfill({ status: 503, json: { error: '服务端暂不可用，请重试', code: 'REAGENT_RETRYABLE' } });
      } else {
        inventory = [];
        await route.fulfill({ json: { ok: true, audit_id: 21 } });
      }
      return;
    }
    assert.equal(request.method(), 'GET', 'fixture must never write to real API');
    let data = {};
    if (pathname === '/api/auth/me') data = { user: { id: 1, username: 'admin', role: 'admin', roles: ['admin'], capabilities: { station_monitoring_public: true } }, site_ids: [7] };
    else if (pathname === '/api/sites') data = [{ id: 7, name: '隔离站点', code: 'TEST-7', type: 'water_quality' }];
    else if (pathname === '/api/station-monitoring/sites') data = { scope: 'all', available_scopes: ['all', 'mine'], items: [], summary: {} };
    else if (pathname === '/api/sites/7/archive') data = { id: 7, name: '隔离站点', code: 'TEST-7', equipment: [] };
    else if (pathname === '/api/reagent-inventory/7') { data = inventory; refreshes += 1; }
    else if (pathname === '/api/users' || pathname === '/api/sites/data-sources') data = [];
    await route.fulfill({ json: data });
  });

  await page.goto(`${baseURL}/sites?archive=7`);
  await page.getByRole('tab', { name: /试剂库存/ }).click();
  await page.getByRole('button', { name: '删除', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: /确认删除试剂库存/ });
  await dialog.waitFor({ state: 'visible' });
  const submit = dialog.getByRole('button', { name: '确认删除' });
  assert.equal(await submit.isDisabled(), true);
  await dialog.getByRole('textbox', { name: '删除原因' }).fill('入库信息录入有误');
  const firstRequest = page.waitForRequest((request) => request.method() === 'DELETE' && request.url().includes('/reagent-inventory/7/19'));
  await submit.click();
  await firstRequest;
  assert.equal(requests.length, 1);
  assert.equal(requests[0].reason, '入库信息录入有误');
  assert.ok(requests[0]._idempotency_key);
  await submit.dispatchEvent('click');
  assert.equal(requests.length, 1, 'while pending a second click cannot send another deletion');
  assert.equal(refreshes, 1, 'do not refresh before server confirms deletion');
  releaseFirst();
  await dialog.getByText('服务端暂不可用，请重试').waitFor();
  assert.equal(await dialog.getByRole('textbox', { name: '删除原因' }).inputValue(), '入库信息录入有误');
  assert.equal(requests.length, 1);
  await submit.click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await page.getByRole('row', { name: /测试试剂/ }).count(), 0);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], requests[1], 'retries preserve the same key and payload');
  assert.equal(refreshes, 2, 'refresh inventory after confirmed deletion');
});
