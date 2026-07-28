const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const { queueCount, flushQueue } = require('../../utils/request.js');

const app = getApp();

function getGps() {
  return new Promise((resolve) => {
    wx.getLocation({
      type: 'gcj02',
      success(res) { resolve({ lat: res.latitude, lng: res.longitude }); },
      fail() { resolve(null); }
    });
  });
}

Page({
  data: { siteId: null, site: null, checkingIn: false, online: true, syncCount: 0 },

  onLoad(options) {
    const id = options.site_id || app.globalData.selSiteId;
    this.setData({ siteId: id });
    if (id) this.loadSite(id);
  },

  onShow() {
    this.refreshSyncState();
  },

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
    flushQueue();
    setTimeout(() => {
      wx.hideLoading();
      this.refreshSyncState(() => wx.showToast({
        title: this.data.syncCount ? '仍有操作待同步' : '同步完成',
        icon: this.data.syncCount ? 'none' : 'success'
      }));
    }, 1000);
  },

  loadSite(id) {
    api.siteTasks(id)
      .then(res => { this.setData({ site: res.site || null }); })
      .catch(() => wx.showToast({ title: '加载失败', icon: 'none' }));
  },

  onNavigate() {
    const s = this.data.site;
    if (!s || s.lat == null || s.lng == null) { wx.showToast({ title: '无坐标信息', icon: 'none' }); return; }
    wx.openLocation({
      latitude: s.lat, longitude: s.lng, name: s.name, address: s.code,
      fail() { wx.showToast({ title: '打开地图失败', icon: 'none' }); }
    });
  },

  onCheckIn() {
    const s = this.data.site;
    if (!s || this.data.checkingIn) return;
    this.setData({ checkingIn: true });
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      const payload = { site_id: s.id, site_name: s.name, check_time: nowStr() };
      if (gps) { payload.lat = gps.lat; payload.lng = gps.lng; }
      api.checkIn(payload)
        .then(() => wx.showToast({ title: '打卡成功', icon: 'success' }))
        .catch((err) => {
          this.setData({ syncCount: queueCount() });
          wx.showToast({ title: err && err.queued ? '已离线保存，联网后自动同步' : '打卡失败', icon: 'none' });
        })
        .finally(() => this.setData({ checkingIn: false }));
    });
  },

  onCalibrate() {
    const s = this.data.site;
    if (!s) return;
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      if (!gps) { wx.showToast({ title: '定位失败', icon: 'none' }); return; }
      api.calibrate(s.id, gps.lat, gps.lng)
        .then(res => {
          const d = (res && res.distance_m != null) ? res.distance_m : 0;
          wx.showToast({ title: '已校准 偏移' + d + 'm', icon: 'none' });
          this.loadSite(s.id);
        })
        .catch(() => wx.showToast({ title: '校准失败', icon: 'none' }));
    });
  }
});
