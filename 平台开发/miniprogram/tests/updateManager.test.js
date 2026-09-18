const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');

function loadApp(options = {}) {
  const callbacks = {};
  const modals = [];
  const networkListeners = [];
  let appDefinition;
  let applyCount = 0;
  let flushQueueCount = 0;
  let flushLocalOpsCount = 0;
  const updateManager = {
    onCheckForUpdate(handler) { callbacks.check = handler; },
    onUpdateReady(handler) { callbacks.ready = handler; },
    onUpdateFailed(handler) { callbacks.failed = handler; },
    applyUpdate() { applyCount += 1; },
  };
  const wx = {
    getStorageSync() { return ''; },
    onNetworkStatusChange(handler) { networkListeners.push(handler); },
    showModal(modal) {
      modals.push(modal);
      if (modal.success && options.modalResult) modal.success(options.modalResult);
    },
  };
  if (options.updateManager === 'throw') {
    wx.getUpdateManager = () => { throw new Error('unsupported'); };
  } else if (options.updateManager !== false) {
    wx.getUpdateManager = () => updateManager;
  }
  const sandbox = {
    App(definition) { appDefinition = definition; },
    wx,
    require(modulePath) {
      if (modulePath === './utils/request.js') {
        return { flushQueue() { flushQueueCount += 1; } };
      }
      if (modulePath === './utils/auth.js') {
        return { getToken: () => options.hasToken !== false };
      }
      if (modulePath === './utils/photos.js') {
        return { captureFlushedPhoto() {} };
      }
      if (modulePath === './utils/sync.js') {
        return { flushLocalOps() { flushLocalOpsCount += 1; return Promise.resolve(); } };
      }
      throw new Error(`Unexpected module: ${modulePath}`);
    },
  };
  vm.runInNewContext(appSource, sandbox, { filename: 'app.js' });
  const app = {
    globalData: JSON.parse(JSON.stringify(appDefinition.globalData)),
    onLaunch: appDefinition.onLaunch,
    onShow: appDefinition.onShow,
  };
  return {
    app,
    callbacks,
    modals,
    networkListeners,
    get applyCount() { return applyCount; },
    get flushQueueCount() { return flushQueueCount; },
    get flushLocalOpsCount() { return flushLocalOpsCount; },
  };
}

for (const updateManager of [false, 'throw']) {
  const harness = loadApp({ updateManager });
  assert.doesNotThrow(() => harness.app.onLaunch.call(harness.app));
  assert.equal(harness.flushQueueCount, 1);
  assert.equal(harness.flushLocalOpsCount, 1);
}

{
  const harness = loadApp();
  harness.app.onLaunch.call(harness.app);
  harness.callbacks.check({ hasUpdate: false });
  assert.equal(harness.modals.length, 0, 'checking without a new version stays silent');
  harness.app.onShow.call(harness.app);
  harness.app.onShow.call(harness.app);
  assert.equal(harness.networkListeners.length, 1);
  assert.equal(harness.applyCount, 0);
}

{
  const harness = loadApp({ modalResult: { confirm: true, cancel: false } });
  harness.app.onLaunch.call(harness.app);
  harness.callbacks.ready();
  harness.callbacks.ready();
  assert.equal(harness.modals.length, 1);
  assert.equal(harness.modals[0].title, '发现新版本');
  assert.equal(harness.modals[0].content,
    '新版本已准备好，更新后将重启小程序。请先确认正在填写的内容已保存。');
  assert.equal(harness.modals[0].confirmText, '立即更新');
  assert.equal(harness.modals[0].cancelText, '稍后');
  assert.equal(harness.applyCount, 1);
}

{
  const harness = loadApp({ modalResult: { confirm: false, cancel: true } });
  harness.app.onLaunch.call(harness.app);
  harness.callbacks.ready();
  harness.callbacks.ready();
  harness.app.onShow.call(harness.app);
  assert.equal(harness.modals.length, 1);
  assert.equal(harness.applyCount, 0);
}

{
  const harness = loadApp();
  harness.app.onLaunch.call(harness.app);
  harness.callbacks.failed();
  harness.callbacks.failed();
  harness.app.onShow.call(harness.app);
  assert.equal(harness.modals.length, 1);
  assert.equal(harness.modals[0].content,
    '新版本下载失败，请检查网络，关闭小程序后重新进入。');
  assert.equal(harness.flushQueueCount, 2);
  assert.equal(harness.flushLocalOpsCount, 2);
}

{
  const harness = loadApp();
  harness.app.onLaunch.call(harness.app);
  const listeners = { ...harness.callbacks };
  harness.app.onLaunch.call(harness.app);
  harness.app.onShow.call(harness.app);
  assert.equal(harness.callbacks.check, listeners.check);
  assert.equal(harness.callbacks.ready, listeners.ready);
  assert.equal(harness.callbacks.failed, listeners.failed);
}

console.log('update manager tests passed');
