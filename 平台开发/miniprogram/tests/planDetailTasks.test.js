const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const pagePath = path.resolve(__dirname, '../pages/plan-detail/plan-detail.js');
const templatePath = path.resolve(__dirname, '../pages/plan-detail/plan-detail.wxml');

global.getApp = () => ({ globalData: { token: 'token' } });
global.Page = () => {};

delete require.cache[pagePath];
const { normalizeGeneratedTasks, planHeaderPresentation } = require(pagePath);

test('detail header uses overall execution status while change review stays authoritative', () => {
  assert.deepEqual(planHeaderPresentation({
    status: 'approved', execution_status: 'partial', execution_status_cn: '部分完成',
  }), { label: '部分完成', cls: 'orange' });
  assert.deepEqual(planHeaderPresentation({
    status: 'change_submitted', execution_status: 'partial', execution_status_cn: '部分完成',
  }), { label: '变更待审', cls: 'orange' });
  assert.deepEqual(planHeaderPresentation({
    status: 'submitted', execution_status: 'pending', execution_status_cn: '待执行',
  }), { label: '待审批', cls: 'blue' });
});

test('candidate site tasks win over legacy packages and keep same-day sites distinct', () => {
  const result = normalizeGeneratedTasks({
    generated_site_tasks: [
      { plan_id: 91, execution_date: '2026-08-31', site_id: 10, site_name: '青云', status: 'pending', status_cn: '待执行' },
      { plan_id: 91, execution_date: '2026-08-31', site_id: 11, site_name: '扬子洲', status: 'partial', status_cn: '部分完成' },
      { plan_id: 92, execution_date: '2026-09-01', site_id: 12, site_name: '室内站', status: 'completed', status_cn: '已完成' },
      { plan_id: 93, execution_date: '2026-09-02', site_id: 10, site_name: '青云', status: 'change_pending', status_cn: '变更待审' },
    ],
    generated_plans: [{ id: 91, plan_name: '旧内部包', status: 'active' }],
  });

  assert.equal(result.mode, 'site');
  assert.equal(result.items.length, 4);
  assert.equal(new Set(result.items.map(item => item.key)).size, 4);
  assert.deepEqual(result.items.map(item => item.display_name), [
    '2026-08-31 · 青云', '2026-08-31 · 扬子洲',
    '2026-09-01 · 室内站', '2026-09-02 · 青云',
  ]);
  assert.deepEqual(result.items.map(item => item.status_cn),
    ['待执行', '部分完成', '已完成', '变更待审']);
  assert.equal(result.items.some(item => item.display_name === '旧内部包'), false);
});

test('r9 response without site task field keeps an explicit legacy package fallback', () => {
  const result = normalizeGeneratedTasks({
    generated_plans: [
      { id: 91, plan_name: '万松·周检-20260831', generate_date: '2026-08-31', status: 'active' },
    ],
  });

  assert.equal(result.mode, 'legacy');
  assert.equal(result.items.length, 1);
  assert.equal(result.items[0].display_name, '万松·周检-20260831');
  assert.equal(result.items[0].status_cn, '执行中');
  assert.equal(result.items[0].legacy, true);
});

test('an authoritative empty candidate list does not fall back or crash', () => {
  const result = normalizeGeneratedTasks({
    generated_site_tasks: [],
    generated_plans: [{ id: 91, plan_name: '不应回退的旧包', status: 'active' }],
  });
  assert.deepEqual(result, { mode: 'site', items: [] });
});

test('task list uses the date-site key and labels the legacy fallback', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  assert.match(template, /wx:key="key"/);
  assert.match(template, /generatedTaskMode === 'legacy'/);
  assert.match(template, /旧版执行包数据/);
});

delete global.getApp;
delete global.Page;
