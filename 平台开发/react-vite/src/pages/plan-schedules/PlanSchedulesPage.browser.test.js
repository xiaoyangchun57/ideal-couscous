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

const schedule = {
  id: 42, user_id: 7, user_name: '运维甲', schedule_type: 'weekly', status: 'cancelled', version: 2,
  period_start: '2026-09-14', period_end: '2026-09-20', day_count: 0, site_count: 0,
  plan_data: {}, vehicle_days: {}, spare_parts: [], execution_status: 'cancelled', field_status: 'cancelled',
};

function scheduleDetail(cancellation) {
  return {
    ...schedule, site_map: {}, generated_site_tasks: [], remarks: '计划备注不能作为取消原因',
    cancellation,
  };
}

const purgeAudit = {
  plan_id: 41, plan_name: '误建九月周巡检', status_before_delete: 'draft', owner_name: '运维乙',
  period_start: '2026-09-07', period_end: '2026-09-13', site_ids: [7, 8], reason: '重复排程',
  operator_name: '管理员甲', purged_at: '2026-09-16 09:00:00',
};

test('plan cancellation and purge audit Web behavior with isolated contract fixtures', {
  skip: !baseURL || !playwrightModule ? 'Set STATION_WEB_TEST_URL and STATION_WEB_PLAYWRIGHT_MODULE' : false,
}, async (t) => {
  const { chromium } = require(playwrightModule);
  const browser = await chromium.launch({ channel: process.env.STATION_WEB_BROWSER_CHANNEL || 'msedge', headless: true });
  t.after(() => browser.close());
  if (evidenceDir) await mkdir(evidenceDir, { recursive: true });

  async function session(roles, { cancellation = null, auditResponse } = {}) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addInitScript(() => localStorage.setItem('water_ops_token', 'isolated-plan-audit-token'));
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    const auditRequests = [];
    const writes = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (request.method() === 'POST' && url.pathname === '/api/plan-schedules/validate') {
        writes.push(`${request.method()} ${url.pathname}`);
        await route.fulfill({ json: { errors: [], warnings: [] } });
        return;
      }
      if (request.method() !== 'GET') {
        writes.push(`${request.method()} ${url.pathname}`);
        await route.fulfill({ status: 405, json: { error: '隔离验证禁止业务写入' } });
        return;
      }
      if (url.pathname === '/api/auth/me') {
        await route.fulfill({ json: { user: { id: 7, username: 'plan-test', role: roles[0], roles }, site_ids: [7, 8] } });
      } else if (url.pathname === '/api/plan-schedules') {
        await route.fulfill({ json: [schedule] });
      } else if (url.pathname === '/api/plan-schedules/overview') {
        await route.fulfill({ json: { date: '2026-09-16', summary: {}, people: [] } });
      } else if (url.pathname === '/api/plan-schedules/follow-up-recommendations') {
        await route.fulfill({ json: { recommendations: [] } });
      } else if (url.pathname === '/api/plan-schedules/42') {
        await route.fulfill({ json: scheduleDetail(cancellation) });
      } else if (url.pathname === '/api/plan-schedules/purge-audits') {
        auditRequests.push(url.search);
        await auditResponse(route, url);
      } else {
        await route.fulfill({ json: {} });
      }
    });
    return {
      page, auditRequests, writes,
      async close(name) {
        if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage: true });
        await context.close();
        assert.deepEqual(errors, [], 'no React runtime errors');
        assert.deepEqual(writes.filter((item) => item !== 'POST /api/plan-schedules/validate'), [], 'no business write requests');
      },
    };
  }

  await t.test('admin sees read-only purge records with failure retry, empty, and populated states', async () => {
    let auditMode = 'failure';
    const currentCancellation = {
      reason: '路线临时调整', operator_id: 9, operator_name: '管理员甲', occurred_at: '2026-09-16 08:30:00',
    };
    const { page, auditRequests, close } = await session(['reviewer', 'admin'], {
      cancellation: currentCancellation,
      auditResponse: async (route, url) => {
        if (auditMode === 'failure') {
          await route.fulfill({ status: 503, json: { error: '删除记录接口暂不可用' } });
          return;
        }
        const items = auditMode === 'record' ? [purgeAudit] : [];
        await route.fulfill({ json: { items, total: items.length, page: Number(url.searchParams.get('page')), page_size: 20 } });
      },
    });
    try {
      await page.goto(`${baseURL}/plan-schedules`);
      await page.getByRole('heading', { name: '巡检计划' }).waitFor();
      await page.getByRole('button', { name: '删除记录' }).click();
      await page.getByText('删除记录接口暂不可用', { exact: true }).waitFor();
      auditMode = 'empty';
      await page.locator('.ant-drawer').getByText('刷新', { exact: true }).last().click();
      await page.getByText('暂无删除记录', { exact: true }).waitFor();
      await page.locator('.ant-drawer-close').click();

      auditMode = 'record';
      await page.getByRole('button', { name: '删除记录' }).click();
      await page.getByText('误建九月周巡检', { exact: true }).waitFor();
      await page.getByText('重复排程', { exact: true }).waitFor();
      await page.getByText('管理员甲', { exact: true }).waitFor();
      await page.getByText('#41 · 删除前 草稿', { exact: true }).waitFor();
      assert.ok(auditRequests.every((query) => query === '?page=1&page_size=20'));
      if (evidenceDir) {
        await page.locator('.ant-drawer-content-wrapper').waitFor({ state: 'visible' });
        await page.screenshot({ path: path.join(evidenceDir, 'plan-purge-audit-admin.png') });
      }
      await page.locator('.ant-drawer-close').click();

      await page.getByRole('button', { name: /查看运维甲的周巡检计划/ }).click();
      await page.getByText('路线临时调整', { exact: true }).waitFor();
      await page.getByText('2026-09-16 08:30:00', { exact: true }).waitFor();
      assert.equal(await page.getByText('计划备注不能作为取消原因', { exact: true }).count(), 1);
    } finally { await close('plan-audit-admin'); }
  });

  await t.test('operator cannot discover or request purge audits and missing cancellation facts stay explicit', async () => {
    const { page, auditRequests, close } = await session(['operator'], {
      cancellation: undefined,
      auditResponse: async (route) => route.fulfill({ status: 403, json: { error: '仅管理员可查看' } }),
    });
    try {
      await page.goto(`${baseURL}/plan-schedules`);
      await page.getByRole('heading', { name: '巡检计划' }).waitFor();
      assert.equal(await page.getByRole('button', { name: '删除记录' }).count(), 0);
      await page.getByRole('button', { name: /查看运维甲的周巡检计划/ }).click();
      await page.getByText('取消原因', { exact: true }).waitFor();
      const detailFacts = await page.locator('.ant-drawer .ant-descriptions').first().innerText();
      for (const label of ['取消原因', '取消操作人', '取消时间']) {
        assert.match(detailFacts, new RegExp(`${label}\\s+未记录`));
      }
      assert.equal(await page.getByText('计划备注不能作为取消原因', { exact: true }).count(), 1);
      assert.deepEqual(auditRequests, []);
    } finally { await close('plan-audit-operator-missing'); }
  });
});
