const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const apiPath = require.resolve('../services/api.js');
const minePagePath = require.resolve('../pages/mine/mine.js');

const toasts = [];
const navigations = [];
const tabBarShows = [];
global.wx = {
  hideTabBar() {},
  showTabBar(options) { tabBarShows.push(options); },
  showToast(options) { toasts.push(options); },
  showModal() {},
  navigateTo(options) { navigations.push(options); }
};
global.getApp = () => ({ globalData: { token: 'token' } });
let minePageDefinition;
global.Page = definition => { minePageDefinition = definition; };
delete require.cache[minePagePath];
require(minePagePath);
const api = require(apiPath);
delete global.Page;
delete global.getApp;

function source(relativePath) {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf8');
}

function viewWithBindtap(wxml, handler) {
  const marker = `bindtap="${handler}"`;
  const markerIndex = wxml.indexOf(marker);
  if (markerIndex < 0) return '';
  const start = wxml.lastIndexOf('<view', markerIndex);
  if (start < 0) return '';
  const tags = /<\/?view\b[^>]*>/g;
  tags.lastIndex = start;
  let depth = 0;
  for (let match = tags.exec(wxml); match; match = tags.exec(wxml)) {
    depth += match[0].startsWith('</') ? -1 : 1;
    if (depth === 0) return wxml.slice(start, tags.lastIndex);
  }
  return '';
}

function eventPage(data) {
  const page = Object.assign({}, minePageDefinition, {
    data: Object.assign({}, JSON.parse(JSON.stringify(minePageDefinition.data)), data || {})
  });
  page.setData = updates => {
    Object.entries(updates).forEach(([key, value]) => {
      const parts = key.split('.');
      let target = page.data;
      parts.slice(0, -1).forEach(part => { target = target[part]; });
      target[parts[parts.length - 1]] = value;
    });
  };
  return page;
}

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

test('monthly quality ignores old month responses and preserves a visible load error', async () => {
  const original = api.photoQualityMonthly;
  const requests = [];
  api.photoQualityMonthly = month => new Promise((resolve, reject) => requests.push({ month, resolve, reject }));
  try {
    const page = eventPage({ qualityMonth: '2026-09' });
    page._alive = true;
    const first = page.loadPhotoQuality();
    page.setData({ qualityMonth: '2026-10' });
    const second = page.loadPhotoQuality();
    requests[1].resolve({ deduction: 1, photos: [{ attachment_id: 2, display_url: '/uploads/two.jpg', reason: '模糊' }] });
    await second;
    requests[0].resolve({ deduction: 8, photos: [] });
    await first;
    assert.equal(page.data.qualityDeduction, 1);
    assert.equal(page.data.qualityPhotos[0].attachment_id, 2);
    const failed = page.loadPhotoQuality();
    requests[2].reject({ error: '网络失败' });
    await failed;
    assert.equal(page.data.qualityError, '网络失败');
    assert.equal(page.data.qualityLoading, false);
  } finally { api.photoQualityMonthly = original; }
});

test('monthly quality links the original inspection without changing its facts', () => {
  const page = eventPage({ qualityPhotos: [{ attachment_id: 2, plan_id: 10, site_id: 1 }] });
  page.openQualityInspection({ currentTarget: { dataset: { id: 2 } } });
  assert.equal(navigations.at(-1).url, '/pages/inspection/inspection?plan_id=10&site_id=1&source=photo_quality_history');
});

test('mine page does not duplicate parts application, issue, order or fulfillment responsibilities', () => {
  const mineJs = source('pages/mine/mine.js');
  const mineWxml = source('pages/mine/mine.wxml');

  assert.doesNotMatch(mineJs, /parts/i);
  assert.doesNotMatch(mineWxml, /parts|备件|领用|下单|到货/i);
});

test('mine does not duplicate the station tab entry', () => {
  const mineJs = source('pages/mine/mine.js');
  const mineWxml = source('pages/mine/mine.wxml');
  assert.doesNotMatch(mineJs, /goResponsibleSites/);
  assert.doesNotMatch(mineWxml, /bindtap="goResponsibleSites"|>负责站点</);
});

test('mine report entry keeps the existing route and names the business it opens', () => {
  const mineWxml = source('pages/mine/mine.wxml');
  const reportsWxml = source('pages/reports/reports.wxml');
  const page = eventPage();
  const navigationCount = navigations.length;

  page.goReports();

  assert.equal(navigations.length, navigationCount + 1);
  assert.equal(navigations.at(-1).url, '/pages/reports/reports');
  const reportEntry = viewWithBindtap(mineWxml, 'goReports');
  assert.ok(reportEntry, 'expected one complete view bound to goReports');
  assert.match(reportEntry, /我的异常上报/);
  assert.match(reportsWxml, /我的异常上报/);
  assert.doesNotMatch(mineWxml, /数据统计/);
});

test('workorder and inspection keep their existing parts application entries', () => {
  const workorderWxml = source('pages/workorder/workorder.wxml');
  const inspectionJs = source('pages/inspection/inspection.js');
  const inspectionWxml = source('pages/inspection/inspection.wxml');

  assert.match(workorderWxml, /bindtap="onApplyParts"[^>]*>申请备件<\/button>/);
  assert.match(inspectionWxml, /bindtap="onOpenPartsApply"[\s\S]{0,120}>\s*<text class="ip-quick-label">备件申请<\/text>/);
  assert.match(inspectionJs, /onOpenPartsApply\(\)/);
  assert.match(inspectionWxml, /bindtap="onSubmitPartsApply"[^>]*>提交申请<\/button>/);
});

test('inspection keeps the existing on-site parts issue flow', () => {
  const inspectionJs = source('pages/inspection/inspection.js');
  const inspectionWxml = source('pages/inspection/inspection.wxml');

  assert.match(inspectionWxml, /bindtap="onDepartureIssueParts"/);
  assert.match(inspectionJs, /onDepartureIssueParts\(\)[\s\S]{0,160}onOpenPartsIssue\(\)/);
  assert.match(inspectionJs, /onOpenPartsIssue\(\)/);
  assert.match(inspectionJs, /onSubmitPartsIssue\(\)/);
  assert.match(inspectionWxml, /bindtap="onSubmitPartsIssue"[^>]*>确认发放<\/button>/);
});

test('mine exposes one stable vehicle entry only to operator or admin roles', () => {
  const mineJs = source('pages/mine/mine.js');
  const mineWxml = source('pages/mine/mine.wxml');
  assert.match(mineJs, /Array\.isArray\(user && user\.roles\)/);
  assert.match(mineJs, /role === 'operator' \|\| role === 'admin'/);
  assert.match(mineWxml, /bindtap="goVehicle"[^>]*wx:if="\{\{canUseVehicle\}\}"|wx:if="\{\{canUseVehicle\}\}"[^>]*bindtap="goVehicle"/);
  assert.match(mineWxml, /我的用车/);
  assert.match(mineWxml, /安排、行程与还车/);
  assert.doesNotMatch(mineWxml, /车辆使用中|点击还车|行程中 · 变更计划/);
});

test('mine vehicle entry is navigation-only, de-duplicated and retryable', () => {
  const mineJs = source('pages/mine/mine.js');
  const mineWxml = source('pages/mine/mine.wxml');
  assert.doesNotMatch(mineJs, /vehicleUseRecords|submitVehicleInspection|returnVehicle|onSubmitReturnVehicle|returnSheet/);
  assert.doesNotMatch(mineWxml, /returnSheet|onOpenReturnVehicle|onSubmitReturnVehicle|还车验车/);

  const page = eventPage({ canUseVehicle: true });
  const before = navigations.length;
  page.goVehicle();
  page.goVehicle();
  assert.equal(navigations.length, before + 1);
  assert.equal(navigations.at(-1).url, '/pages/vehicle/vehicle');
  assert.equal(toasts.at(-1).title, '正在打开，请稍候');
  navigations.at(-1).fail();
  assert.equal(toasts.at(-1).title, '我的用车打开失败，请重试');
  page.goVehicle();
  assert.equal(navigations.length, before + 2);

  const reviewer = eventPage({ canUseVehicle: false });
  const reviewerBefore = navigations.length;
  reviewer.goVehicle();
  assert.equal(navigations.length, reviewerBefore);
  assert.equal(toasts.at(-1).title, '当前账号无用车操作权限');
});
