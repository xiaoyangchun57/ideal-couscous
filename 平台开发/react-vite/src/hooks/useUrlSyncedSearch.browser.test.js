import test from 'node:test';
import assert from 'node:assert/strict';
import process from 'node:process';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

// Run against Vite with an externally supplied Playwright runtime; no repo dependency is added.
// The URL write here is same-document (history.replaceState + popstate) because the pages own every
// filter write with { replace: true }: a real cross-document back would reload the app and could not
// exercise the hook at all.
const baseURL = process.env.URL_SEARCH_WEB_TEST_URL;
const playwrightModule = process.env.URL_SEARCH_WEB_PLAYWRIGHT_MODULE;
const evidenceDir = process.env.URL_SEARCH_WEB_EVIDENCE_DIR;
const require = createRequire(import.meta.url);
const row = { id: 7, name: '隔离搜索站甲', code: 'SEARCH-TEST-7', type: 'water_quality', status: 'active', district: '测试区', manager: '测试负责人' };
const site = {
  ...row,
  monitoring_status: 'normal',
  monitoring_status_label: '数据正常',
  monitoring_reason: '隔离测试事实',
  last_received_at: '2026-09-15T08:00:00+08:00',
  last_valid_observation_at: '2026-09-10T07:00:00+08:00',
};

test('url synced search real Web behavior with an isolated API fixture', {
  skip: !baseURL || !playwrightModule ? 'Set URL_SEARCH_WEB_TEST_URL and URL_SEARCH_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = require(playwrightModule);
  const browser = await chromium.launch({ channel: process.env.URL_SEARCH_WEB_BROWSER_CHANNEL || 'msedge', headless: true });
  t.after(() => browser.close());
  if (evidenceDir) await mkdir(evidenceDir, { recursive: true });

  async function session(handler) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-url-search-token'));
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
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
          id: 991020, username: 'url-search-test', role: 'admin', roles: ['admin'],
          capabilities: { station_monitoring_public: true },
        },
        site_ids: [7],
      };
      else if (url.pathname === '/api/sites') body = [row];
      else if (url.pathname === '/api/station-monitoring/sites') body = {
        scope: 'all', available_scopes: ['all', 'mine'], items: [site], summary: { normal: 1 },
      };
      else if (url.pathname === '/api/sites/data-sources' || url.pathname === '/api/users') body = [];
      else if (url.pathname === '/api/global-search') body = { results: [] };
      await route.fulfill({ json: body });
    });
    async function close() {
      if (errors.length) t.diagnostic(JSON.stringify(errors));
      await context.close();
      assert.deepEqual(errors, [], 'no React runtime errors');
      assert.deepEqual(writes, [], 'no business write requests');
    }
    return { page, close };
  }

  const searchValue = (page) => page.evaluate(() => {
    const element = document.querySelector('input[aria-label="站点搜索"]');
    return element ? element.value : null;
  });
  // Same-document URL write, as another module or an external link would do it.
  const externalSearch = (page, value) => page.evaluate((next) => {
    const url = new URL(location.href);
    if (next === null) url.searchParams.delete('q');
    else url.searchParams.set('q', next);
    window.history.replaceState(window.history.state, '', url);
    window.dispatchEvent(new window.PopStateEvent('popstate', { state: window.history.state }));
  }, value);
  const waitSearch = (page, expected) => page.waitForFunction(
    (value) => document.querySelector('input[aria-label="站点搜索"]')?.value === value, expected,
  );
  const startComposition = (page, draft) => page.evaluate((text) => {
    const element = document.querySelector('input[aria-label="站点搜索"]');
    const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    element.dispatchEvent(new window.CompositionEvent('compositionstart', { bubbles: true, data: '' }));
    setValue.call(element, text);
    element.dispatchEvent(new window.InputEvent('input', { bubbles: true, isComposing: true, data: text, inputType: 'insertCompositionText' }));
    return element.value;
  }, draft);
  const endComposition = (page, selected) => page.evaluate((text) => {
    const element = document.querySelector('input[aria-label="站点搜索"]');
    const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setValue.call(element, text);
    element.dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true, data: text }));
    element.dispatchEvent(new window.InputEvent('input', { bubbles: true, isComposing: false, data: null, inputType: 'insertText' }));
    return element.value;
  }, selected);
  async function snapshot(page, name) {
    if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage: true });
  }

  await t.test('a pinyin draft survives a URL write and commits only the selected text', async () => {
    const { page, close } = await session();
    try {
      await page.goto(`${baseURL}/sites?q=alpha`);
      await page.getByRole('textbox', { name: '站点搜索' }).waitFor({ state: 'visible' });
      await waitSearch(page, 'alpha');

      // Control: while not composing, an external URL write must still reach the input.
      await externalSearch(page, 'beta');
      await waitSearch(page, 'beta');
      await snapshot(page, 'url-search-1-external-applied');

      await page.getByRole('textbox', { name: '站点搜索' }).click();
      assert.equal(await startComposition(page, 'zhong'), 'zhong');
      await snapshot(page, 'url-search-2-composing');

      // The actual contract: a URL write during selection must not overwrite the draft.
      await externalSearch(page, 'gamma');
      await page.waitForTimeout(600);
      assert.equal(await searchValue(page), 'zhong', 'composition draft must survive a URL write');
      assert.match(page.url(), /q=gamma/);
      await snapshot(page, 'url-search-3-crossing-write');

      // Only the finished selection is written to the URL, and the user's text wins.
      assert.equal(await endComposition(page, '中文'), '中文');
      await waitSearch(page, '中文');
      await page.waitForFunction(() => location.search.includes('%E4%B8%AD%E6%96%87') || location.search.includes('q=中文'));
      assert.match(page.url(), /(q=%E4%B8%AD%E6%96%87|q=中文)/);
      await snapshot(page, 'url-search-4-committed');
    } finally { await close(); }
  });

  await t.test('ordinary typing still persists the URL and a clearing write still applies', async () => {
    const { page, close } = await session();
    try {
      await page.goto(`${baseURL}/sites`);
      const box = page.getByRole('textbox', { name: '站点搜索' });
      await box.waitFor({ state: 'visible' });
      assert.equal(await searchValue(page), '');
      await box.click();
      await box.pressSequentially('SEARCH-TEST-7');
      await page.waitForFunction(() => location.search.includes('q=SEARCH-TEST-7'));
      assert.equal(await searchValue(page), 'SEARCH-TEST-7');

      await externalSearch(page, null);
      await waitSearch(page, '');
      assert.equal(await searchValue(page), '');
      await snapshot(page, 'url-search-5-external-cleared');
    } finally { await close(); }
  });
});
