const assert = require('node:assert/strict');

const api = require('../services/api.js');
const pagePath = require.resolve('../pages/message/message.js');

const app = { globalData: { token: 'token', selAlertId: null, selWorkorderNo: null, executionTarget: null, vehicleTarget: null } };
const toasts = [];
const modals = [];
const navigations = [];
const subscriptions = [];
let definition;

global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  showToast(options) { toasts.push(options); },
  showModal(options) { modals.push(options); },
  navigateTo(options) { navigations.push(options); },
  switchTab(options) { navigations.push(options); },
  requestSubscribeMessage(options) { subscriptions.push(options); },
  stopPullDownRefresh() {},
  reLaunch() {}
};

delete require.cache[pagePath];
require(pagePath);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

async function flush() {
  await new Promise(resolve => setImmediate(resolve));
  await Promise.resolve();
}

function pageInstance() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page._alive = true;
  page._viewEpoch = 1;
  page.setData = patch => Object.assign(page.data, patch);
  return page;
}

function message(id, sourceType, sourceId, isRead) {
  return {
    id,
    source_type: sourceType,
    source_id: sourceId,
    payload_json: '',
    is_read: !!isRead,
    title: '通知',
    content: ''
  };
}

const original = {
  readNotification: api.readNotification,
  readAllNotifications: api.readAllNotifications,
  notifications: api.notifications
};

async function main() {
  try {
    api.notifications = () => Promise.resolve({ notifications: [] });
    let reads = 0;
    const read = deferred();
    api.readNotification = () => { reads += 1; return read.promise; };
    const unreadPage = pageInstance();
    unreadPage.data.list = [message(1, 'workorder', 'WO-1', false)];
    unreadPage.onTap({ currentTarget: { dataset: { id: 1 } } });
    unreadPage.onTap({ currentTarget: { dataset: { id: 1 } } });
    assert.equal(reads, 1, 'double-tapping an unread message sends one read request');
    assert.equal(toasts.at(-1).title, '正在打开，请稍候');
    read.resolve({ success: true });
    await flush();
    assert.equal(unreadPage.data.list.length, 0, 'only a confirmed read removes the current row');
    assert.equal(navigations.length, 1, 'the read chain navigates once');
    navigations.pop().fail();
    assert.equal(unreadPage.data.list.length, 1, 'a navigation failure restores a current-page retry row');
    assert.equal(unreadPage.data.list[0].is_read, true, 'the restored row retains the confirmed read fact');
    unreadPage.onTap({ currentTarget: { dataset: { id: 1 } } });
    assert.equal(reads, 1, 'retrying after a confirmed read never sends a second read request');
    assert.equal(navigations.length, 1, 'the restored row starts one navigation retry');
    navigations.pop().success();
    assert.equal(unreadPage.data.list.length, 0, 'a successful retry removes the temporary retry row');

    const failedRead = deferred();
    api.readNotification = () => failedRead.promise;
    const failedReadPage = pageInstance();
    failedReadPage.data.list = [message(2, 'workorder', 'WO-2', false)];
    failedReadPage.onTap({ currentTarget: { dataset: { id: 2 } } });
    failedRead.reject(new Error('offline'));
    await flush();
    assert.equal(failedReadPage.data.list.length, 1, 'failed reads preserve the unread row');
    assert.equal(navigations.length, 1, 'a failed read still permits the business target to open');
    navigations.pop().success();

    const readPage = pageInstance();
    readPage.data.list = [message(3, 'workorder', 'WO-3', true)];
    readPage.onTap({ currentTarget: { dataset: { id: 3 } } });
    readPage.onTap({ currentTarget: { dataset: { id: 3 } } });
    assert.equal(navigations.length, 1, 'double-tapping an already-read message navigates once');
    navigations.pop().success();

    const pageTarget = pageInstance();
    pageTarget.openBusinessTarget(message(4, 'inspection', '9', true));
    assert.deepEqual(app.globalData.executionTarget, { executionPlanId: 9, source: 'inspection' });
    navigations.pop().fail();
    assert.equal(app.globalData.executionTarget, null, 'page navigation failure clears its exact execution target');

    const tabTarget = pageInstance();
    tabTarget.openBusinessTarget(message(5, 'alert', '77', true));
    assert.equal(app.globalData.selAlertId, 77);
    app.globalData.selAlertId = 88;
    navigations.pop().fail();
    assert.equal(app.globalData.selAlertId, 88, 'a late navigation failure does not clear a newer alert target');

    const reviewTarget = pageInstance();
    app.globalData.executionTarget = { executionPlanId: 50, source: 'newer' };
    reviewTarget.openBusinessTarget(message(6, 'parts_request', '12', true));
    navigations.pop().fail();
    assert.deepEqual(app.globalData.executionTarget, { executionPlanId: 50, source: 'newer' }, 'review failures do not disturb unrelated global targets');
    assert.equal(toasts.at(-1).title, '打开消息目标失败，请重试');
    reviewTarget.openBusinessTarget(message(6, 'parts_request', '12', true));
    assert.equal(navigations.length, 1, 'navigation failure releases the retry gate');
    navigations.pop().success();

    const vehicleTargetPage = pageInstance();
    vehicleTargetPage.openBusinessTarget(message(61, 'vehicle_use_expiry', '91', true));
    const writtenVehicleTarget = app.globalData.vehicleTarget;
    assert.deepEqual(writtenVehicleTarget, {
      applicationId: 91, expectedAction: 'extend', source: 'vehicle_use_expiry'
    });
    app.globalData.vehicleTarget = {
      applicationId: 92, expectedAction: 'extend', source: 'newer'
    };
    navigations.pop().fail();
    assert.equal(app.globalData.vehicleTarget.applicationId, 92,
      'a late vehicle navigation failure does not clear a newer target');

    vehicleTargetPage.openBusinessTarget(message(62, 'vehicle_use_expiry', '93', true));
    assert.equal(app.globalData.vehicleTarget.applicationId, 93);
    navigations.pop().fail();
    assert.equal(app.globalData.vehicleTarget, null,
      'vehicle navigation failure clears only the target written by that navigation');

    const invalidVehiclePage = pageInstance();
    const invalidVehicleNavigationCount = navigations.length;
    invalidVehiclePage.openBusinessTarget(message(63, 'vehicle_use_expiry', '', true));
    assert.equal(navigations.length, invalidVehicleNavigationCount);
    assert.match(modals.at(-1).content, /申请编号/);

    const invalidPage = pageInstance();
    const navigationCount = navigations.length;
    invalidPage.openBusinessTarget(message(7, 'unknown', '12', true));
    assert.equal(navigations.length, navigationCount, 'invalid targets produce no navigation');
    assert.deepEqual(app.globalData.executionTarget, { executionPlanId: 50, source: 'newer' }, 'invalid targets write no global target');

    let allReads = 0;
    const all = deferred();
    api.readAllNotifications = () => { allReads += 1; return all.promise; };
    const allPage = pageInstance();
    allPage.data.list = [message(8, 'plan_schedule', '1', false)];
    allPage.onReadAll();
    allPage.onReadAll();
    assert.equal(allReads, 1, 'double-tapping all-read sends one request');
    assert.equal(toasts.at(-1).title, '正在处理，请稍候');
    all.resolve({ success: true });
    await flush();
    assert.deepEqual(allPage.data.list, []);

    const retryAll = deferred();
    api.readAllNotifications = () => retryAll.promise;
    allPage.data.list = [message(9, 'plan_schedule', '1', false)];
    allPage.onReadAll();
    retryAll.reject(new Error('offline'));
    await flush();
    assert.equal(allPage.data.list.length, 1, 'failed all-read preserves rows');
    allPage.data.view = 'history';
    allPage.onReadAll();
    assert.equal(allReads, 1, 'history and empty paths do not issue an all-read request');
    allPage.data.view = 'current';
    allPage.data.list = [];
    allPage.onReadAll();
    assert.equal(allReads, 1, 'empty current view does not issue an all-read request');

    const oldRead = deferred();
    api.readNotification = () => oldRead.promise;
    const staleReadPage = pageInstance();
    staleReadPage.data.list = [message(10, 'workorder', 'WO-10', false)];
    staleReadPage.onTap({ currentTarget: { dataset: { id: 10 } } });
    staleReadPage.onSwitchView({ currentTarget: { dataset: { view: 'history' } } });
    await flush();
    staleReadPage.data.list = [message(11, 'workorder', 'WO-11', true)];
    oldRead.resolve({ success: true });
    await flush();
    assert.equal(staleReadPage.data.list[0].id, 11, 'a previous view read result cannot remove a newer view row');
    assert.equal(navigations.length, 0, 'a hidden or switched-away chain does not navigate later');

    const hiddenNavigation = pageInstance();
    hiddenNavigation.openBusinessTarget(message(12, 'alert', '12', true));
    hiddenNavigation.onHide();
    navigations.pop().fail();
    assert.equal(app.globalData.selAlertId, null, 'hidden-page navigation failure still clears its own target');
    assert.notEqual(toasts.at(-1).title, '打开消息目标失败，请重试', 'hidden-page failure does not show a stale toast');

    const subscribePage = pageInstance();
    subscribePage.onSubscribe();
    subscribePage.onSubscribe();
    assert.equal(subscriptions.length, 1, 'subscription requests are also gated');
    assert.equal(toasts.at(-1).title, '正在处理，请稍候');
    const templateIds = subscriptions.pop().tmplIds;
    subscribePage._subscribing = false;
    subscribePage.onSubscribe();
    const accepted = subscriptions.pop();
    accepted.success({ [templateIds[0]]: 'accept', [templateIds[1]]: 'reject' });
    assert.equal(toasts.at(-1).title, '订阅成功');
    subscribePage.onSubscribe();
    const rejected = subscriptions.pop();
    rejected.success({ [templateIds[0]]: 'reject', [templateIds[1]]: 'ban' });
    assert.equal(modals.at(-1).content, '可在小程序设置中重新开启消息通知');
    subscribePage.onSubscribe();
    subscriptions.pop().fail({ errMsg: 'cancel' });
    assert.equal(toasts.at(-1).title, '暂时无法订阅消息，请稍后重试');
    assert.doesNotMatch(JSON.stringify(toasts.concat(modals)), /message\.js|SUBSCRIBE_TMPL|公众平台|模板 ID/);

    console.log('messageConnection tests passed');
  } finally {
    Object.assign(api, original);
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
