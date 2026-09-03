const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const api = require('../services/api.js');
const maps = require('../services/maps.js');
const app = { globalData: { token: 'token', selWorkorderNo: null } };
const navigationCalls = [];
const toastCalls = [];
const tabBarCalls = [];

global.getApp = () => app;
global.wx = {
  getStorageSync() { return ''; },
  navigateTo(options) { navigationCalls.push(options); },
  showToast(options) { toastCalls.push(options); },
  hideTabBar(options) { tabBarCalls.push({ action: 'hide', options }); },
  showTabBar(options) { tabBarCalls.push({ action: 'show', options }); },
  reLaunch() {},
  getNetworkType() {},
};

function loadPage(relativePath) {
  const pagePath = require.resolve(relativePath);
  let definition = null;
  global.Page = value => { definition = value; };
  delete require.cache[pagePath];
  require(pagePath);
  return definition;
}

function pageInstance(definition) {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
  });
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

function bottomBarCondition(view, className) {
  const escapedClass = className.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = view.match(new RegExp(`<view wx:if="\\{\\{(.+?)\\}\\}" class="${escapedClass}">`));
  assert.ok(match, `${className} must have an explicit render condition`);
  const evaluate = new Function('sheet', 'detailLoading', `return ${match[1]}`); // WXML conditions use JavaScript expressions.
  return (sheet, detailLoading) => Boolean(evaluate(sheet, detailLoading));
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

test('workorder filters form five exhaustive groups while cards keep exact stages', () => {
  const definition = loadPage('../pages/workorder/workorder.js');
  const page = pageInstance(definition);
  const statuses = ['pending', 'accepted', 'dispatched', 'in_progress', 'reviewing', 'resolved', 'closed'];
  const rows = statuses.map(status => maps.workorderCn({
    order_no: status,
    status,
    display_title: status,
  }));

  assert.deepEqual(page.data.tabs, [
    { key: 'all', label: '全部' },
    { key: 'pending', label: '待受理' },
    { key: 'in_progress', label: '进行中' },
    { key: 'reviewing', label: '待核验' },
    { key: 'closed', label: '已完成' },
  ]);
  assert.deepEqual(page.filter(rows, 'all').map(row => row.status), statuses);
  assert.deepEqual(page.filter(rows, 'pending').map(row => row.status), ['pending']);
  assert.deepEqual(page.filter(rows, 'in_progress').map(row => row.status), ['accepted', 'dispatched', 'in_progress']);
  assert.deepEqual(page.filter(rows, 'reviewing').map(row => row.status), ['reviewing']);
  assert.deepEqual(page.filter(rows, 'closed').map(row => row.status), ['resolved', 'closed']);

  const grouped = ['pending', 'in_progress', 'reviewing', 'closed']
    .flatMap(tab => page.filter(rows, tab).map(row => row.status));
  assert.deepEqual(grouped.sort(), statuses.slice().sort(), 'non-all groups neither lose nor duplicate a status');
  assert.deepEqual(rows.map(row => row.status_cn),
    ['待受理', '已受理', '已派发', '处置中', '审核中', '已解决', '已完成']);
});

test('alert primary action follows the visible WXML button into one exact workorder without writes', () => {
  const view = fs.readFileSync(path.join(__dirname, '../pages/alert/alert.wxml'), 'utf8');
  assert.match(view, /related_workorder_target\.order_no[\s\S]*class="sheet-bottom-bar"/);
  assert.match(view, /class="btn-primary btn-lg"[^>]*bindtap="onPrimaryAction"/);
  assert.match(view, /class="om-sheet-mask"[^>]*bindtap="onCloseMask"/);
  assert.doesNotMatch(view, /sheet-bottom-bar[\s\S]*btn-ghost btn-lg[\s\S]*关闭/);
  assert.doesNotMatch(view, /bindtap="doAck"/);

  const definition = loadPage('../pages/alert/alert.js');
  const page = pageInstance(definition);
  let writes = 0;
  const originalAcknowledge = api.acknowledgeAlert;
  api.acknowledgeAlert = () => { writes += 1; return Promise.resolve({}); };
  try {
    navigationCalls.length = 0;
    toastCalls.length = 0;
    tabBarCalls.length = 0;
    app.globalData.selWorkorderNo = null;
    page.data.sheet = {
      open: true,
      item: {
        primary_action: '查看关联工单',
        related_workorder_target: { order_no: 'WO-EXACT-1' },
        can_view: true,
      },
    };
    page.setAlertTabBarHidden(true);
    assert.equal(tabBarCalls.at(-1).action, 'hide');

    assert.equal(page.onPrimaryAction(), true);
    assert.equal(page.data.acting, true);
    assert.equal(app.globalData.selWorkorderNo, 'WO-EXACT-1');
    assert.equal(navigationCalls.length, 1);
    assert.equal(navigationCalls[0].url, '/pages/workorder/workorder');
    assert.equal(page.onPrimaryAction(), false);
    assert.equal(navigationCalls.length, 1, 'duplicate taps share the in-flight navigation');
    assert.equal(toastCalls.at(-1).title, '正在打开关联工单，请稍候');
    assert.equal(writes, 0, 'viewing a linked workorder never acknowledges the alert');

    app.globalData.selWorkorderNo = 'WO-NEWER';
    navigationCalls[0].fail({ errMsg: 'navigateTo:fail' });
    navigationCalls[0].complete();
    assert.equal(app.globalData.selWorkorderNo, 'WO-NEWER', 'failure clears only its own target');
    assert.equal(page.data.acting, false);
    assert.equal(tabBarCalls.at(-1).action, 'hide', 'failed navigation keeps the modal layer above TabBar');
    assert.equal(toastCalls.at(-1).title, '打开关联工单失败，请重试');

    app.globalData.selWorkorderNo = null;
    assert.equal(page.onPrimaryAction(), true);
    navigationCalls[1].fail({ errMsg: 'navigateTo:fail' });
    navigationCalls[1].complete();
    assert.equal(app.globalData.selWorkorderNo, null);

    const beforeMissing = navigationCalls.length;
    page.data.sheet.item = {
      primary_action: '查看关联工单',
      related_workorder_target: null,
      can_view: true,
      block_reason: '关联工单信息异常，请刷新后重试',
    };
    assert.equal(page.onPrimaryAction(), false);
    assert.equal(navigationCalls.length, beforeMissing);
    assert.equal(toastCalls.at(-1).title, '关联工单信息异常，请刷新后重试');

    page.data.sheet.item = {
      primary_action: '查看关联工单',
      related_workorder_target: { order_no: 'WO-FORBIDDEN' },
      can_view: false,
      block_reason: '当前角色无权查看关联工单',
    };
    assert.equal(page.onPrimaryAction(), false);
    assert.equal(navigationCalls.length, beforeMissing, 'a hidden unauthorized action cannot navigate when triggered directly');
    assert.equal(toastCalls.at(-1).title, '当前角色无权查看关联工单');
  } finally {
    api.acknowledgeAlert = originalAcknowledge;
  }
});

test('alert mask closes only while linked-workorder navigation is idle', () => {
  const definition = loadPage('../pages/alert/alert.js');
  const page = pageInstance(definition);
  toastCalls.length = 0;
  tabBarCalls.length = 0;
  app.globalData.selWorkorderNo = null;
  page.data.sheet = { open: true, item: { id: 7 } };
  page.setAlertTabBarHidden(true);

  assert.equal(page.onCloseMask(), true);
  assert.equal(page.data.sheet.open, false);
  assert.equal(tabBarCalls.at(-1).action, 'show');

  page.data.sheet.open = true;
  page.data.acting = true;
  app.globalData.selWorkorderNo = 'WO-IN-FLIGHT';
  assert.equal(page.onCloseMask(), false);
  assert.equal(page.data.sheet.open, true);
  assert.equal(app.globalData.selWorkorderNo, 'WO-IN-FLIGHT');
  assert.deepEqual(toastCalls.at(-1), {
    title: '正在打开关联工单，请稍候',
    icon: 'none',
  });
});

test('only current authorized actions render a detail bottom bar', () => {
  const workorderView = fs.readFileSync(path.join(__dirname, '../pages/workorder/workorder.wxml'), 'utf8');
  const alertView = fs.readFileSync(path.join(__dirname, '../pages/alert/alert.wxml'), 'utf8');
  const workorderBottomBar = bottomBarCondition(workorderView, 'sheet-bottom-bar');
  const alertBottomBar = bottomBarCondition(alertView, 'sheet-bottom-bar');

  for (const primary of ['accept', 'check_in', 'start', 'submit_review']) {
    assert.equal(workorderBottomBar({ item: { actions: { primary }, detailStale: false } }), true, primary);
  }
  for (const primary of [undefined, 'unknown', 'close']) {
    assert.equal(workorderBottomBar({ item: { actions: { primary }, detailStale: false } }), false, `workorder ${primary || 'empty'} is read-only`);
  }
  assert.equal(workorderBottomBar({ item: { actions: { primary: 'accept' }, detailStale: true } }), false, 'stale workorder has no bottom bar');
  assert.doesNotMatch(workorderView, /sheet-bottom-bar[\s\S]*btn-ghost btn-lg[\s\S]*>关闭</);

  const availableAlert = { item: { primary_action: '查看关联工单', related_workorder_target: { order_no: 'WO-1' }, can_view: true, detailStale: false } };
  assert.equal(alertBottomBar(availableAlert), true);
  assert.equal(alertBottomBar(availableAlert, true), false, 'detail loading keeps the action unavailable');
  assert.equal(alertBottomBar({ item: Object.assign({}, availableAlert.item, { can_view: false }) }), false, 'no permission');
  assert.equal(alertBottomBar({ item: Object.assign({}, availableAlert.item, { can_view: undefined }) }), false, 'missing permission');
  assert.equal(alertBottomBar({ item: Object.assign({}, availableAlert.item, { primary_action: '' }) }), false, 'closed alert');
  assert.equal(alertBottomBar({ item: Object.assign({}, availableAlert.item, { related_workorder_target: null }) }), false, 'broken relation');
  assert.equal(alertBottomBar({ item: Object.assign({}, availableAlert.item, { detailStale: true }) }), false, 'stale alert');
  assert.doesNotMatch(alertView, /sheet-bottom-bar[\s\S]*btn-ghost btn-lg[\s\S]*>\s*关闭/);
});

test('alert detail only enables the exact current response and ignores closed or unloaded requests', async () => {
  const definition = loadPage('../pages/alert/alert.js');
  const view = fs.readFileSync(path.join(__dirname, '../pages/alert/alert.wxml'), 'utf8');
  const alertBottomBar = bottomBarCondition(view, 'sheet-bottom-bar');
  const page = pageInstance(definition);
  const originalAlertDetail = api.alertDetail;
  try {
    navigationCalls.length = 0;
    toastCalls.length = 0;
    tabBarCalls.length = 0;

    const pending = deferred();
    api.alertDetail = () => pending.promise;
    page.openAlertDetail({
      id: 31,
      primary_action: '查看关联工单',
      related_workorder_target: { order_no: 'WO-SEED' },
      can_view: true,
    });
    assert.equal(page.data.detailLoading, true);
    assert.equal(alertBottomBar(page.data.sheet, page.data.detailLoading), false, 'list seed cannot render an action while detail is pending');
    assert.equal(page.onPrimaryAction(), false, 'direct action invocation is locked while the detail is pending');
    assert.equal(navigationCalls.length, 0);

    page.onClose();
    pending.resolve({ id: 31, primary_action: '查看关联工单', related_workorder_target: { order_no: 'WO-LATE' }, can_view: true });
    await settle();
    assert.equal(page.data.sheet.open, false, 'a closed sheet is not reopened by a late success');
    assert.equal(page.data.detailError, '', 'a late success cannot change a closed sheet');
    assert.equal(tabBarCalls.at(-1).action, 'show');

    const closedFailure = deferred();
    api.alertDetail = () => closedFailure.promise;
    page.openAlertDetail({ id: 32 });
    assert.equal(page.onCloseMask(), true);
    closedFailure.reject({ error: '不应显示的旧错误' });
    await settle();
    assert.equal(page.data.sheet.open, false, 'a closed sheet is not reopened by a late failure');
    assert.equal(page.data.detailError, '', 'a late failure cannot surface an obsolete error');

    const currentFailure = deferred();
    api.alertDetail = () => currentFailure.promise;
    page.openAlertDetail({ id: 33, station_name: '保留的站点' });
    currentFailure.reject({ error: '详情读取失败，请重试' });
    await settle();
    assert.equal(page.data.sheet.open, true, 'the current failed read keeps its readonly context open');
    assert.equal(page.data.sheet.item.id, 33);
    assert.equal(page.data.sheet.item.detailStale, true);
    assert.equal(page.data.detailError, '详情读取失败，请重试');
    assert.equal(alertBottomBar(page.data.sheet, page.data.detailLoading), false, 'a failed detail read has no action');

    const first = deferred();
    const second = deferred();
    api.alertDetail = id => (id === 41 ? second.promise : first.promise);
    page.openAlertDetail({ id: 40 });
    page.onClose();
    page.openAlertDetail({ id: 41 });
    first.resolve({ id: 40, primary_action: '查看关联工单', related_workorder_target: { order_no: 'WO-OLD' }, can_view: true });
    await settle();
    assert.equal(page.data.sheet.item.id, 41, 'an older request cannot replace a newly opened alert');
    assert.equal(page.data.detailLoading, true);
    second.resolve({ id: 41, primary_action: '查看关联工单', related_workorder_target: { order_no: 'WO-CURRENT' }, can_view: true });
    await settle();
    assert.equal(page.data.detailLoading, false);
    assert.equal(alertBottomBar(page.data.sheet, page.data.detailLoading), true, 'only the current exact detail enables the action');
    assert.equal(page.onPrimaryAction(), true);

    const unloaded = deferred();
    const unloadedPage = pageInstance(definition);
    api.alertDetail = () => unloaded.promise;
    unloadedPage.openAlertDetail({ id: 50 });
    let writesAfterUnload = 0;
    const setData = unloadedPage.setData;
    unloadedPage.setData = updates => { writesAfterUnload += 1; setData(updates); };
    unloadedPage.onUnload();
    unloaded.resolve({ id: 50, primary_action: '查看关联工单', related_workorder_target: { order_no: 'WO-UNLOADED' }, can_view: true });
    await settle();
    assert.equal(writesAfterUnload, 0, 'unloaded pages receive no late detail writes');
  } finally {
    api.alertDetail = originalAlertDetail;
  }
});

test('alert sheet owns TabBar only while its modal is visible', async () => {
  const definition = loadPage('../pages/alert/alert.js');
  const page = pageInstance(definition);
  const originalAlertDetail = api.alertDetail;
  const originalAlerts = api.alerts;
  try {
    tabBarCalls.length = 0;
    api.alertDetail = () => Promise.resolve({ id: 8, status: 'pending' });
    api.alerts = () => Promise.resolve([]);

    page.openAlertDetail({ id: 8 });
    assert.equal(tabBarCalls.at(-1).action, 'hide');
    page.onClose();
    assert.equal(tabBarCalls.at(-1).action, 'show');

    page.data.sheet = { open: true, item: { id: 8 } };
    page.setAlertTabBarHidden(true);
    page.onHide();
    assert.equal(tabBarCalls.at(-1).action, 'show');
    page.onShow();
    assert.equal(tabBarCalls.at(-1).action, 'hide');

    page.data.sheet.item = {
      primary_action: '查看关联工单',
      related_workorder_target: { order_no: 'WO-RETURN-1' },
      can_view: true,
    };
    page.setAlertTabBarHidden(true);
    assert.equal(page.onPrimaryAction(), true);
    const successfulNavigation = navigationCalls.at(-1);
    successfulNavigation.success();
    successfulNavigation.complete();
    assert.equal(tabBarCalls.at(-1).action, 'show');
    page.onShow();
    assert.equal(tabBarCalls.at(-1).action, 'hide');

    assert.equal(page.onPrimaryAction(), true);
    const failedNavigation = navigationCalls.at(-1);
    failedNavigation.fail({ errMsg: 'navigateTo:fail' });
    failedNavigation.complete();
    assert.equal(tabBarCalls.at(-1).action, 'hide');
  } finally {
    api.alertDetail = originalAlertDetail;
    api.alerts = originalAlerts;
  }
});
