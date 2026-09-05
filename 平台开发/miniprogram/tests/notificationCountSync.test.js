const assert = require('node:assert/strict');
const test = require('node:test');

const api = require('../services/api.js');
const {
  authoritativeUnreadCount,
  currentUnreadRevision,
  invalidateUnreadCount,
} = require('../utils/notificationCount.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

function setPath(target, key, value) {
  const parts = key.split('.');
  let current = target;
  while (parts.length > 1) {
    const part = parts.shift();
    if (!current[part] || typeof current[part] !== 'object') current[part] = {};
    current = current[part];
  }
  current[parts[0]] = value;
}

const app = { globalData: { token: 'token' } };
const navigations = [];
const toasts = [];
global.getApp = () => app;
global.wx = {
  getStorageSync(key) {
    if (key === 'user') return { id: 7, real_name: '万松', role: 'operator', roles: ['operator'] };
    if (key === 'sites') return [];
    return '';
  },
  navigateTo(options) { navigations.push(options); },
  switchTab(options) { navigations.push(options); },
  reLaunch() {},
  showModal() {},
  showToast(options) { toasts.push(options); },
  stopPullDownRefresh() {},
};

function loadPage(relativePath) {
  let definition;
  global.Page = page => { definition = page; };
  const modulePath = require.resolve(relativePath);
  delete require.cache[modulePath];
  require(modulePath);
  return definition;
}

const messageDefinition = loadPage('../pages/message/message.js');
const homeDefinition = loadPage('../pages/index/index.js');
const mineDefinition = loadPage('../pages/mine/mine.js');

function pageInstance(definition, callOnLoad = false) {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setDataCalls: 0,
  });
  page.setData = patch => {
    page.setDataCalls += 1;
    Object.keys(patch).forEach(key => setPath(page.data, key, patch[key]));
  };
  if (callOnLoad && page.onLoad) page.onLoad();
  return page;
}

function messageItem(id) {
  return {
    id, is_read: false, source_type: 'plan_schedule', source_id: String(id),
    title: '计划通知', content: '', payload_json: '',
  };
}

function stubAdjacentRequests() {
  api.myToday = () => Promise.resolve({ summary: {}, work_package: { has_plan: false } });
  api.auditPending = () => Promise.resolve([]);
  api.vehicleUseRecords = () => Promise.resolve([]);
}

test('only confirmed single and all-read operations invalidate cross-page unread counts', async () => {
  const originals = {
    readNotification: api.readNotification,
    readAllNotifications: api.readAllNotifications,
  };
  try {
    const page = pageInstance(messageDefinition);
    const firstRevision = currentUnreadRevision();
    page.data.list = [messageItem(1)];
    api.readNotification = () => Promise.resolve({ success: true });
    page.onTap({ currentTarget: { dataset: { id: 1 } } });
    await flush();
    assert.equal(currentUnreadRevision(), firstRevision + 1);
    assert.deepEqual(page.data.list, []);

    const failedRevision = currentUnreadRevision();
    page.data.list = [messageItem(2)];
    api.readNotification = () => Promise.reject(new Error('offline'));
    page.onTap({ currentTarget: { dataset: { id: 2 } } });
    await flush();
    assert.equal(currentUnreadRevision(), failedRevision);
    assert.equal(page.data.list.length, 1, 'failed single-read keeps the unread row and does not invalidate badges');

    const allRevision = currentUnreadRevision();
    page.data.list = [messageItem(3), messageItem(4)];
    api.readAllNotifications = () => Promise.resolve({ success: true });
    page.onReadAll();
    await flush();
    assert.equal(currentUnreadRevision(), allRevision + 1);
    assert.deepEqual(page.data.list, []);

    const failedAllRevision = currentUnreadRevision();
    page.data.list = [messageItem(5)];
    api.readAllNotifications = () => Promise.reject(new Error('offline'));
    page.onReadAll();
    await flush();
    assert.equal(currentUnreadRevision(), failedAllRevision);
    assert.equal(page.data.list.length, 1);
    assert.equal(toasts.at(-1).title, '操作失败');
  } finally {
    Object.assign(api, originals);
  }
});

test('home and mine clear confirmed stale badges then reread the authoritative count on show', async () => {
  const originals = {
    unreadCount: api.unreadCount,
    myToday: api.myToday,
    auditPending: api.auditPending,
    vehicleUseRecords: api.vehicleUseRecords,
  };
  try {
    stubAdjacentRequests();
    const home = pageInstance(homeDefinition, true);
    const mine = pageInstance(mineDefinition, true);
    home.setData({ notificationsState: 'ready', unreadCount: 8, unreadDisplay: '8' });
    mine.setData({ unread: 8 });
    invalidateUnreadCount();

    const homeCount = deferred();
    const mineCount = deferred();
    const counts = [homeCount, mineCount];
    api.unreadCount = () => counts.shift().promise;
    home.onShow();
    mine.onShow();
    assert.deepEqual(
      [home.data.notificationsState, home.data.unreadCount, home.data.unreadDisplay, mine.data.unread],
      ['loading', null, '', null],
      'confirmed stale badges disappear before the replacement count returns',
    );

    homeCount.resolve({ count: 3 });
    mineCount.resolve({ count: 3 });
    await flush();
    assert.deepEqual([home.data.unreadCount, home.data.unreadDisplay, mine.data.unread], [3, '3', 3]);
  } finally {
    Object.assign(api, originals);
  }
});

test('consecutive onShow calls accept only the latest unread response on both pages', async () => {
  const originals = {
    unreadCount: api.unreadCount,
    myToday: api.myToday,
    auditPending: api.auditPending,
    vehicleUseRecords: api.vehicleUseRecords,
  };
  try {
    stubAdjacentRequests();
    const home = pageInstance(homeDefinition, true);
    const homeOld = deferred();
    const homeNew = deferred();
    let homeCalls = 0;
    api.unreadCount = () => (++homeCalls === 1 ? homeOld.promise : homeNew.promise);
    home.onShow();
    home.onShow();
    homeNew.resolve({ count: 2 });
    await flush();
    homeOld.resolve({ count: 9 });
    await flush();
    assert.equal(home.data.unreadCount, 2);

    const mine = pageInstance(mineDefinition, true);
    const mineOld = deferred();
    const mineNew = deferred();
    let mineCalls = 0;
    api.unreadCount = () => (++mineCalls === 1 ? mineOld.promise : mineNew.promise);
    mine.onShow();
    mine.onShow();
    mineNew.resolve({ count: 1 });
    await flush();
    mineOld.resolve({ count: 7 });
    await flush();
    assert.equal(mine.data.unread, 1);
  } finally {
    Object.assign(api, originals);
  }
});

test('unloaded pages ignore unread responses and count failures never fabricate zero', async () => {
  const originalUnread = api.unreadCount;
  try {
    const home = pageInstance(homeDefinition, true);
    const mine = pageInstance(mineDefinition, true);
    const homePending = deferred();
    const minePending = deferred();
    api.unreadCount = () => homePending.promise;
    home.loadNotifications();
    api.unreadCount = () => minePending.promise;
    mine.loadUnreadCount();
    const homeWrites = home.setDataCalls;
    const mineWrites = mine.setDataCalls;
    home.onUnload();
    mine.onUnload();
    homePending.resolve({ count: 6 });
    minePending.resolve({ count: 6 });
    await flush();
    assert.equal(home.setDataCalls, homeWrites);
    assert.equal(mine.setDataCalls, mineWrites);

    const failedHome = pageInstance(homeDefinition, true);
    const failedMine = pageInstance(mineDefinition, true);
    failedHome.setData({ notificationsState: 'ready', unreadCount: 5, unreadDisplay: '5' });
    failedMine.setData({ unread: 5 });
    api.unreadCount = () => Promise.reject(new Error('offline'));
    await Promise.all([failedHome.loadNotifications(), failedMine.loadUnreadCount()]);
    assert.deepEqual(
      [failedHome.data.notificationsState, failedHome.data.unreadCount, failedHome.data.unreadDisplay],
      ['unavailable', null, ''],
    );
    assert.equal(failedMine.data.unread, null);
  } finally {
    api.unreadCount = originalUnread;
  }
});

test('unread count projection accepts explicit zero and rejects missing or invalid counts', () => {
  assert.equal(authoritativeUnreadCount({ count: 0 }), 0);
  assert.equal(authoritativeUnreadCount({ count: 12 }), 12);
  assert.equal(authoritativeUnreadCount({}), null);
  assert.equal(authoritativeUnreadCount({ count: -1 }), null);
  assert.equal(authoritativeUnreadCount({ count: 'unknown' }), null);
});
