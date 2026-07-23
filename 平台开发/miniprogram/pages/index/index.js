const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { todayStr } = require('../../utils/util.js');
const maps = require('../../services/maps.js');

const app = getApp();

Page({
  data: {
    realName: '', today: '', loaded: false,
    summary: null, sites: [], workorders: [], alerts: [], reviewCount: 0,
    workPackage: null
  },

  onLoad() {
    const u = getUser();
    this.setData({ realName: (u && u.real_name) || '运维人员', today: todayStr() });
  },

  onShow() {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this.load();
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh());
  },

  load(done) {
    api.myToday()
      .then(res => {
        const summary4 = res.summary ? {
          sites: res.summary.total_sites || 0,
          pending: res.summary.pending_items || 0,
          workorders: res.summary.pending_workorders || 0,
          alerts: (res.summary.pending_alerts || 0) + (res.summary.abnormal_items || 0)
        } : null;
        this.setData({
          loaded: true,
          summary4,
          sites: res.sites || [],
          workorders: (res.workorders || []).map(maps.workorderCn),
          alerts: (res.alerts || []).map(a => Object.assign({}, a, { level_cls: maps.alertLevelCls(a.level) })),
          workPackage: res.work_package || null
        });
        // 待我审核计数（管理者/审批者）
        api.auditPending()
          .then(r => this.setData({ reviewCount: Array.isArray(r) ? r.length : 0 }))
          .catch(() => {});
        if (done) done();
      })
      .catch(() => {
        this.setData({ loaded: true });
        if (done) done();
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  onSiteTap(e) {
    const id = e.currentTarget.dataset.id;
    app.globalData.selSiteId = id;
    api.trackEvent('inspection.station_opened', { site_id: id, entry: 'home' });
    wx.switchTab({ url: '/pages/inspection/inspection' });
  },
  goInspection() { wx.switchTab({ url: '/pages/inspection/inspection' }); },
  goWorkorder() { wx.switchTab({ url: '/pages/workorder/workorder' }); },
  goAlert() { wx.switchTab({ url: '/pages/alert/alert' }); },
  goReview() { wx.navigateTo({ url: '/pages/review/view' }); },
  goPlan() { wx.navigateTo({ url: '/pages/plan/plan' }); }
});
