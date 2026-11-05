import test from 'node:test';
import assert from 'node:assert/strict';
import process from 'node:process';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const baseURL = process.env.STATION_WEB_TEST_URL;
const playwrightModule = process.env.STATION_WEB_PLAYWRIGHT_MODULE;
const evidenceDir = process.env.STATION_WEB_EVIDENCE_DIR;
const require = createRequire(import.meta.url);

const plans = Array.from({ length: 24 }, (_, index) => ({
  id: index + 1,
  user_id: 1,
  user_name: '验收管理员',
  schedule_type: 'weekly',
  schedule_type_cn: '周巡检',
  status: 'approved',
  period_start: index < 20 ? `2026-10-${String(index + 12).padStart(2, '0')}` : `2026-11-0${index - 19}`,
  period_end: index < 20 ? `2026-10-${String(index + 12).padStart(2, '0')}` : `2026-11-0${index - 19}`,
  day_count: 1,
  site_count: 1,
  plan_data: {},
  execution_summary: {},
  execution_status: 'active',
  field_status: 'active',
}));

const alerts = Array.from({ length: 55 }, (_, index) => ({
  id: index + 1,
  site_id: 1,
  site_name: '隔离验收站',
  site_code: 'TEST-01',
  level: ['red', 'orange', 'yellow', 'blue'][index % 4],
  message: `隔离告警 ${String(index + 1).padStart(3, '0')}`,
  status: index % 3 === 0 ? 'pending' : index % 3 === 1 ? 'acknowledged' : 'resolved',
  created_at: `2026-11-03 ${String(23 - (index % 20)).padStart(2, '0')}:00:00`,
  metric: 'ph',
  flow_status: '',
  related_order_no: '',
}));

const workorders = Array.from({ length: 55 }, (_, index) => ({
  id: index + 1,
  order_no: `WO-LIST-${String(index + 1).padStart(3, '0')}`,
  site_id: 1,
  site_name: '隔离验收站',
  source: 'manual',
  level: 'normal',
  title: `隔离工单 ${index + 1}`,
  description: '分页验收',
  assignee: '验收管理员',
  status: index % 2 ? 'pending' : 'closed',
  created_at: `2026-11-03 10:${String(index % 60).padStart(2, '0')}:00`,
  can_operate: true,
  flow_events: [],
}));

const operationLogs = Array.from({ length: 55 }, (_, index) => ({
  id: `log-${index + 1}`,
  created_at: `2026-11-03 09:${String(59 - (index % 60)).padStart(2, '0')}:00`,
  operator: '验收管理员',
  action: index % 2 ? 'update' : '入库',
  target_type: index % 2 ? 'device' : 'part',
  details: `隔离日志 ${index + 1}`,
}));

function pageResult(items, url) {
  const page = Number(url.searchParams.get('page') || 1);
  const pageSize = Number(url.searchParams.get('page_size') || 20);
  const offset = (page - 1) * pageSize;
  return {
    items: items.slice(offset, offset + pageSize),
    total: items.length,
    page,
    page_size: pageSize,
    total_pages: Math.ceil(items.length / pageSize),
  };
}

test('LIST-R1 pagination stays reachable at both acceptance viewports', {
  skip: !baseURL || !playwrightModule ? 'Set STATION_WEB_TEST_URL and STATION_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = require(playwrightModule);
  const browser = await chromium.launch({
    channel: process.env.STATION_WEB_BROWSER_CHANNEL || 'msedge',
    headless: true,
  });
  t.after(() => browser.close());
  if (evidenceDir) await mkdir(evidenceDir, { recursive: true });

  for (const viewport of [{ width: 1366, height: 768 }, { width: 1700, height: 900 }]) {
    await t.test(`${viewport.width}x${viewport.height}`, async () => {
      const context = await browser.newContext({ viewport });
      await context.addInitScript(() => localStorage.setItem('water_ops_token', 'list-r1-browser-token'));
      const page = await context.newPage();
      const pageErrors = [];
      page.on('pageerror', (error) => pageErrors.push(error.message));
      await page.route('**/api/**', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (request.method() !== 'GET') {
          await route.fulfill({ status: 405, json: { error: '隔离浏览器验收禁止写入' } });
          return;
        }
        if (url.pathname === '/api/auth/me') {
          await route.fulfill({ json: { user: { id: 1, username: 'list-admin', real_name: '验收管理员', role: 'admin', roles: ['admin'] }, site_ids: [1] } });
        } else if (url.pathname === '/api/plan-schedules/overview') {
          await route.fulfill({ json: { date: '2026-11-03', summary: {}, people: [] } });
        } else if (url.pathname === '/api/plan-schedules/follow-up-recommendations') {
          await route.fulfill({ json: { recommendations: [] } });
        } else if (url.pathname === '/api/plan-schedules') {
          await route.fulfill({ json: pageResult(plans, url) });
        } else if (url.pathname === '/api/alerts/statistics') {
          await route.fulfill({ json: { total: 55, by_status: { pending: 19, acknowledged: 18, resolved: 18 }, by_level: {} } });
        } else if (url.pathname === '/api/alerts') {
          await route.fulfill({ json: { ...pageResult(alerts, url), incident_groups: [] } });
        } else if (url.pathname === '/api/workorders') {
          await route.fulfill({ json: { ...pageResult(workorders, url), counts: { pending: 28, closed: 27 } } });
        } else if (url.pathname === '/api/operation-logs') {
          await route.fulfill({ json: pageResult(operationLogs, url) });
        } else if (url.pathname === '/api/parts/dashboard') {
          await route.fulfill({ json: { total_parts: 0, low_stock: 0, device_count: 0, latest_operations: [] } });
        } else if (['/api/sites', '/api/devices', '/api/assignees', '/api/notifications'].includes(url.pathname)) {
          await route.fulfill({ json: [] });
        } else {
          await route.fulfill({ json: {} });
        }
      });

      async function verifyList(name, target, total) {
        await page.goto(`${baseURL}${target}`);
        const pager = page.locator('.ant-pagination').last();
        const waitForPagerText = async (text) => page.waitForFunction((expected) => {
          const pagers = document.querySelectorAll('.ant-pagination');
          const current = pagers[pagers.length - 1];
          return current?.innerText.replace(/\s+/g, '').includes(expected);
        }, text);
        await pager.waitFor({ state: 'visible' });
        const firstBox = await pager.boundingBox();
        assert.ok(firstBox && firstBox.y + firstBox.height <= viewport.height + 1, `${name} first-page controls are reachable`);
        assert.match((await pager.innerText()).replace(/\s+/g, ''), new RegExp(`共${total}条，当前第1—20条`));
        await pager.locator('.ant-pagination-next button').click();
        await page.waitForFunction(() => new URL(location.href).searchParams.get('page') === '2');
        await page.waitForTimeout(100);
        const secondBox = await pager.boundingBox();
        assert.ok(secondBox && secondBox.y + secondBox.height <= viewport.height + 1, `${name} next-page controls are reachable`);
        assert.match((await pager.innerText()).replace(/\s+/g, ''), new RegExp(`共${total}条，当前第21—${Math.min(40, total)}条`));

        const lastPage = Math.ceil(total / 20);
        if (lastPage > 2) {
          await pager.locator(`.ant-pagination-item-${lastPage}`).click();
          await page.waitForFunction((expected) => Number(new URL(location.href).searchParams.get('page') || 1) === expected, lastPage);
        }
        await waitForPagerText(`当前第${((lastPage - 1) * 20) + 1}—${total}条`);
        assert.match(
          (await pager.innerText()).replace(/\s+/g, ''),
          new RegExp(`共${total}条，当前第${((lastPage - 1) * 20) + 1}—${total}条`),
        );
        const lastBox = await pager.boundingBox();
        assert.ok(lastBox && lastBox.y + lastBox.height <= viewport.height + 1, `${name} last-page controls are reachable`);

        if (name === 'plans') {
          await page.locator('button', { hasText: '详情' }).last().click();
          await page.locator('.ant-drawer-content').waitFor({ state: 'visible' });
          await page.keyboard.press('Escape');
        }

        await pager.locator('.ant-pagination-options .ant-select-selector').click();
        await page.getByText('50 条/页', { exact: true }).click();
        await page.waitForFunction(() => new URL(location.href).searchParams.get('page_size') === '50');
        await waitForPagerText(`当前第1—${Math.min(50, total)}条`);
        assert.equal(Number(new URL(page.url()).searchParams.get('page') || 1), 1);
        assert.match((await pager.innerText()).replace(/\s+/g, ''), new RegExp(`共${total}条，当前第1—${Math.min(50, total)}条`));
        if (evidenceDir) {
          await page.screenshot({ path: path.join(evidenceDir, `${name}-${viewport.width}x${viewport.height}.png`) });
        }
      }

      await verifyList('plans', '/plan-schedules', 24);
      await verifyList('alerts', '/alerts?range=all', 55);
      await verifyList('workorders', '/workorders', 55);
      await verifyList('operation-logs', '/equipment?tab=operation-logs', 55);
      assert.deepEqual(pageErrors, []);
      await context.close();
    });
  }
});
