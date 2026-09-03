const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../services/api.js');

function read(relativePath) {
  return fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
}

function loadPlanPage() {
  const pagePath = require.resolve('../pages/plan/plan.js');
  let definition = null;
  global.Page = value => { definition = value; };
  delete require.cache[pagePath];
  require(pagePath);
  return definition;
}

function pageInstance(definition) {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page.setData = updates => {
    Object.entries(updates).forEach(([key, value]) => {
      const parts = key.split('.');
      let target = page.data;
      while (parts.length > 1) {
        const part = parts.shift();
        target[part] = target[part] || {};
        target = target[part];
      }
      target[parts[0]] = value;
    });
  };
  return page;
}

test('the four PM-15 sheets use isolated roots with one calculated scroll surface', () => {
  const appWxss = read('../app.wxss');
  const reports = read('../pages/reports/reports.wxml');
  const workorder = read('../pages/workorder/workorder.wxml');
  const alert = read('../pages/alert/alert.wxml');
  const plan = read('../pages/plan/plan.wxml');

  assert.match(appWxss, /\.om-sheet \{/);
  assert.match(appWxss, /\.om-sheet\.om-sheet \.om-sheet-scroll \{/);
  assert.match(appWxss, /height: 88vh/);

  assert.match(reports, /reportSheet\.open}}" class="om-sheet report-compose-sheet"/);
  assert.match(reports, /class="om-sheet-scroll sheet-body report-compose-body"/);
  assert.match(reports, /class="sheet-bottom-bar"/);
  assert.doesNotMatch(reports, /report-compose-sheet[\s\S]*sheet-handle/);

  const workorderDetail = workorder.split('<!-- ===== 用车申请 Sheet ===== -->')[0];
  assert.match(workorderDetail, /class="om-sheet workorder-detail-sheet"/);
  assert.match(workorderDetail, /class="om-sheet-scroll sheet-scroll workorder-detail-scroll"/);
  assert.match(workorderDetail, /class="sheet-bottom-bar"/);
  assert.doesNotMatch(workorderDetail, /sheet-handle/);

  assert.match(alert, /class="om-sheet alert-detail-sheet"/);
  assert.match(alert, /class="om-sheet-scroll sheet-scroll alert-detail-scroll"/);
  assert.match(alert, /class="sheet-bottom-bar"/);
  assert.doesNotMatch(alert, /sheet-handle/);

  assert.match(plan, /class="om-sheet plan-favorite-sheet"/);
  assert.match(plan, /class="om-sheet-scroll plan-favorite-scroll"/);
  assert.match(plan, /class="sheet-actions om-sheet-actions"/);
  assert.doesNotMatch(plan, /sheet-handle/);
});

test('favorite-plan sheet owns the native TabBar only while open', () => {
  const calls = [];
  const toasts = [];
  const originalPlanSchedules = api.planSchedules;
  const originalPlanScheduleFavorites = api.planScheduleFavorites;
  global.getApp = () => ({ globalData: { token: 'token' } });
  global.wx = {
    hideTabBar(options) { calls.push({ action: 'hide', options }); },
    showTabBar(options) { calls.push({ action: 'show', options }); },
    showToast(options) { toasts.push(options); },
    reLaunch() {},
    getStorageSync(key) { return key === 'user' ? { roles: ['operator'] } : ''; },
  };

  api.planSchedules = () => Promise.resolve([]);
  api.planScheduleFavorites = () => Promise.resolve([]);
  try {
    const page = pageInstance(loadPlanPage());
    page.onLoad();
    page.data.canUseFavorites = true;
    page.data.favorites = [{ id: 7, name: '每日巡检', suggestedPeriodStart: '2026-09-03' }];
    assert.equal(page.onOpenFavorites(), true);
    assert.equal(calls.at(-1).action, 'hide');
    assert.equal(page.data.favoriteSheet.open, true);

    page.data.favoriteSheet.sheetState = 'submitting';
    assert.equal(page.onCloseFavorites(), false);
    assert.equal(page.data.favoriteSheet.open, true);
    assert.equal(calls.at(-1).action, 'hide');
    assert.deepEqual(toasts.at(-1), { title: '草稿正在创建中，请稍候', icon: 'none' });

    page.data.favoriteSheet.sheetState = 'idle';
    assert.equal(page.onCloseFavorites(), true);
    assert.equal(calls.at(-1).action, 'show');
    assert.equal(page.data.favoriteSheet.open, false);

    assert.equal(page.onOpenFavorites(), true);
    page.onHide();
    assert.equal(calls.at(-1).action, 'show');
    page.onShow();
    assert.equal(calls.at(-1).action, 'hide');

    page.onUnload();
    assert.equal(calls.at(-1).action, 'show');
  } finally {
    api.planSchedules = originalPlanSchedules;
    api.planScheduleFavorites = originalPlanScheduleFavorites;
  }
});
