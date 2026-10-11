const { flushQueue } = require('./utils/request.js');
const { getToken } = require('./utils/auth.js');
const { captureFlushedPhoto } = require('./utils/photos.js');
const { flushLocalOps } = require('./utils/sync.js');

let updateManagerRegistered = false;
let updateReadyPrompted = false;
let updateFailedNotified = false;
let updateApplied = false;
let updateCheckHasUpdate = null;

function flushPendingOperations() {
  if (!getToken()) return;
  flushQueue(captureFlushedPhoto);
  flushLocalOps().catch(() => {});
}

function registerUpdateHandler(updateManager, method, handler) {
  if (typeof updateManager[method] !== 'function') return;
  try {
    updateManager[method](handler);
  } catch (error) {
    // Older runtimes may expose an incomplete update manager. Startup must continue.
  }
}

function registerUpdateManager() {
  if (updateManagerRegistered || typeof wx.getUpdateManager !== 'function') return;
  let updateManager;
  try {
    updateManager = wx.getUpdateManager();
  } catch (error) {
    return;
  }
  if (!updateManager) return;
  updateManagerRegistered = true;

  registerUpdateHandler(updateManager, 'onCheckForUpdate', (result) => {
    updateCheckHasUpdate = Boolean(result && result.hasUpdate);
  });
  registerUpdateHandler(updateManager, 'onUpdateReady', () => {
    if (updateReadyPrompted) return;
    updateReadyPrompted = true;
    if (typeof wx.showModal !== 'function') return;
    try {
      wx.showModal({
        title: '发现新版本',
        content: '新版本已准备好，更新后将重启小程序。请先确认正在填写的内容已保存。',
        confirmText: '立即更新',
        cancelText: '稍后',
        success(result) {
          if (!result || !result.confirm || updateApplied) return;
          updateApplied = true;
          try {
            updateManager.applyUpdate();
          } catch (error) {
            // Applying is intentionally not retried in-process to avoid a restart loop.
          }
        },
      });
    } catch (error) {
      // A prompt failure must not block launch or discard in-progress business input.
    }
  });
  registerUpdateHandler(updateManager, 'onUpdateFailed', () => {
    if (updateFailedNotified) return;
    updateFailedNotified = true;
    if (typeof wx.showModal !== 'function') return;
    try {
      wx.showModal({
        title: '更新失败',
        content: '新版本下载失败，请检查网络，关闭小程序后重新进入。',
        showCancel: false,
        confirmText: '知道了',
      });
    } catch (error) {
      // Update feedback is best-effort and independent of normal startup.
    }
  });
}

App({
  globalData: {
    token: '',
    user: null,
    sites: [],
    selSiteId: null,   // 首页/巡检站间跳转的临时选中站点
    executionTarget: null, // 巡检入口的精确执行包/日期/站点/检查项快照
    vehicleTarget: null, // “我的用车”一次性精确申请/动作目标
    stationHubTarget: null, // 首页进入站点 Tab 的一次性模式/筛选目标
    baseUrl: ''        // 运行时可由开发者工具注入，缺省读 config
  },

  onLaunch() {
    registerUpdateManager();
    this.globalData.token = wx.getStorageSync('token') || '';
    this.globalData.user = wx.getStorageSync('user') || null;
    this.globalData.sites = wx.getStorageSync('sites') || [];
    // 微信绑定只由用户在订阅动作后显式确认，启动不得改变共享业务账号的绑定归属。
    // 网络恢复时自动重传失败队列 + 本地巡检闭环实体（弱网/离线策略，仅注册一次）
    wx.onNetworkStatusChange((res) => {
      if (res.isConnected && getToken()) {
        flushPendingOperations();
      }
    });
    // 网络在小程序启动前已经恢复时不会触发 onNetworkStatusChange，启动时也要回放一次。
    flushPendingOperations();
  },

  onShow() {
    // 从后台返回或登录后 reLaunch 时重试，避免弱网队列只等网络事件而长期不动。
    flushPendingOperations();
  }
});
