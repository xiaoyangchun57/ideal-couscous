const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const { captureFlushedPhoto } = require('../../utils/photos.js');

const app = getApp();

function decorate(a) {
  return Object.assign({}, a, {
    level_cn: maps.map(maps.ALERT_LEVEL, a.level),
    level_cls: maps.alertLevelCls(a.level),
    status_cn: maps.map(maps.ALERT_STATUS, a.status),
    metric_cn: maps.metricCn(a.metric)
  });
}

Page({
  data: { tab: 'pending', list: [], loaded: false, sheet: { open: false, item: null }, acting: false, online: true, syncCount: 0, canManage: false },

  onLoad() {
    const user = getUser() || {};
    const roles = user.roles || [user.role || ''];
    this.setData({ canManage: roles.includes('admin') });
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.refreshSyncState();
    this.load();
  },

  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  refreshSyncState(done) {
    wx.getNetworkType({
      success: (res) => {
        this.setData({ online: res.networkType !== 'none', syncCount: queueCount() });
        if (done) done();
      },
      fail: () => {
        this.setData({ syncCount: queueCount() });
        if (done) done();
      }
    });
  },

  onSyncNow() {
    if (!this.data.syncCount) return;
    wx.showLoading({ title: '同步中' });
    flushQueue(captureFlushedPhoto);
    setTimeout(() => {
      wx.hideLoading();
      this.refreshSyncState(() => {
        if (!this.data.syncCount) this.load();
        wx.showToast({ title: this.data.syncCount ? '仍有操作待同步' : '同步完成', icon: this.data.syncCount ? 'none' : 'success' });
      });
    }, 1000);
  },

  onTab(e) {
    const t = e.currentTarget.dataset.t;
    this.setData({ tab: t });
    this.load();
  },

  load(done) {
    const status = this.data.tab === 'pending' ? 'pending' : '';
    api.alerts(status)
      .then(res => {
        this.setData({ list: (res || []).map(decorate), loaded: true });
        if (done) done();
      })
      .catch(() => { this.setData({ loaded: true }); if (done) done(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  onOpen(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find(a => a.id === id);
    if (item) this.setData({ sheet: { open: true, item } });
  },
  onClose() { this.setData({ 'sheet.open': false }); },

  doAck() {
    if (!this.data.canManage) {
      wx.showToast({ title: '仅管理员可确认告警', icon: 'none' });
      return;
    }
    const id = this.data.sheet.item.id;
    this.setData({ acting: true });
    api.acknowledgeAlert(id)
      .then(() => {
        this.setData({ acting: false, 'sheet.open': false });
        wx.showToast({ title: '已确认', icon: 'success' });
        this.load();
      })
      .catch((err) => {
        const queued = !!(err && err.queued);
        this.setData({ acting: false, syncCount: queueCount(), ...(queued ? { 'sheet.open': false } : {}) });
        wx.showToast({ title: queued ? '已离线保存，联网后自动同步' : ((err && err.error) || '操作失败'), icon: 'none' });
      });
  }
});
