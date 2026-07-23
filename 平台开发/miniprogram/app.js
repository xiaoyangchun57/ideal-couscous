const { flushQueue } = require('./utils/request.js');
const { getToken } = require('./utils/auth.js');
const { captureFlushedPhoto } = require('./utils/photos.js');
const { flushLocalOps } = require('./utils/sync.js');
const api = require('./services/api.js');

App({
  globalData: {
    token: '',
    user: null,
    sites: [],
    selSiteId: null,   // 首页/巡检站间跳转的临时选中站点
    baseUrl: ''         // 运行时可由开发者工具注入，缺省读 config
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
        flushQueue(captureFlushedPhoto);
        flushLocalOps().catch(() => {});
      }
    });
  }
});
