const { flushQueue } = require('./utils/request.js');
const { getToken } = require('./utils/auth.js');
const { captureFlushedPhoto } = require('./utils/photos.js');
const { flushLocalOps } = require('./utils/sync.js');
const api = require('./services/api.js');

function flushPendingOperations() {
  if (!getToken()) return;
  flushQueue(captureFlushedPhoto);
  flushLocalOps().catch(() => {});
}

function refreshNotificationBadge() {
  if (!getToken()) return;
  api.unreadCount()
    .then(res => {
      const count = Number(res && res.count) || 0;
      if (count > 0) {
        wx.setTabBarBadge({ index: 2, text: count > 99 ? '99+' : String(count) });
      } else {
        wx.removeTabBarBadge({ index: 2 });
      }
    })
    .catch(() => {});
}

App({
  globalData: {
    token: '',
    user: null,
    sites: [],
    selSiteId: null,   // 首页/巡检站间跳转的临时选中站点
    selPlanId: null,   // 排程详情跳入现场页时的临时预选执行包
    baseUrl: '',        // 运行时可由开发者工具注入，缺省读 config
    refreshNotificationBadge,
    notificationBadgeTimer: null
  },

  onLaunch() {
    this.globalData.token = wx.getStorageSync('token') || '';
    this.globalData.user = wx.getStorageSync('user') || null;
    this.globalData.sites = wx.getStorageSync('sites') || [];
    // 启动已持有 token 则静默绑定微信 openid（用于订阅消息），失败不影响正常使用
    if (this.globalData.token) {
      wx.login({
        success: (lres) => { if (lres.code) api.bindOpenId(lres.code).catch(() => {}); }
      });
    }
    // 网络恢复时自动重传失败队列 + 本地巡检闭环实体（弱网/离线策略，仅注册一次）
    wx.onNetworkStatusChange((res) => {
      if (res.isConnected && getToken()) {
        flushPendingOperations();
      }
    });
    // 网络在小程序启动前已经恢复时不会触发 onNetworkStatusChange，启动时也要回放一次。
    flushPendingOperations();
    refreshNotificationBadge();
    this.globalData.notificationBadgeTimer = setInterval(refreshNotificationBadge, 30000);
  },

  onShow() {
    // 从后台返回或登录后 reLaunch 时重试，避免弱网队列只等网络事件而长期不动。
    flushPendingOperations();
    refreshNotificationBadge();
  }
});
