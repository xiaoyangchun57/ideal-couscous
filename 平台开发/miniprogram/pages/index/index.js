const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { todayStr } = require('../../utils/util.js');
const maps = require('../../services/maps.js');
const { homeSummary, homeSite, homeSiteSelection } = require('../../utils/homeTaskState.js');

const app = getApp();

Page({
  data: {
    realName: '', today: '', loaded: false,
    summary: null, sites: [], upcoming: [], workorders: [], alerts: [], reviewCount: 0, canReview: false,
    workPackage: null
  },

  onLoad() {
    const u = getUser();
    const reviewRoles = ['admin', 'reviewer'];
    const roles = (u && u.roles) || [u && u.role];
    this.setData({
      realName: (u && u.real_name) || '运维人员',
      today: todayStr(),
      canReview: reviewRoles.some(role => roles.includes(role)),
    });
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
        const summary4 = homeSummary(res.summary);
        const workPackage = res.work_package || null;
        const planEntrySummary = workPackage && workPackage.has_plan
          ? `今日作业 ${workPackage.sites.length} 个站点${workPackage.readiness.departure_confirmed ? ' · 已准备' : ` · ${workPackage.readiness.departure_pending_count} 项待确认`}`
          : '今日暂无作业包 · 查看全部计划';
        this.setData({
          loaded: true,
          summary4,
          sites: (res.sites || []).map(homeSite),
          upcoming: (res.upcoming || []).map(item => Object.assign({}, item, {
            site_names_text: (item.site_names || []).join('、')
          })),
          workorders: (res.workorders || []).map(maps.workorderCn),
          alerts: (res.alerts || []).map(a => Object.assign({}, a, { level_cls: maps.alertLevelCls(a.level) })),
          workPackage,
          planEntrySummary
        });
        if (this.data.canReview) {
          api.auditPending()
            .then(r => this.setData({ reviewCount: Array.isArray(r) ? r.length : 0 }))
            .catch(() => {});
        }
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
    const site = (this.data.sites || []).find(item => String(item.site_id) === String(id));
    const target = homeSiteSelection(site || { site_id: id });
    app.globalData.selSiteId = target.siteId;
    app.globalData.selPlanId = target.planId;
    app.globalData.selItemId = target.itemId;
    api.trackEvent('inspection.station_opened', { site_id: id, entry: 'home' });
    wx.switchTab({ url: '/pages/inspection/inspection' });
  },
  onUpcomingTap(e) {
    wx.navigateTo({ url: '/pages/plan-detail/plan-detail?id=' + e.currentTarget.dataset.id });
  },
  goInspection() { wx.switchTab({ url: '/pages/inspection/inspection' }); },
  goWorkorder() { wx.navigateTo({ url: '/pages/workorder/workorder' }); },
  goAlert() { wx.navigateTo({ url: '/pages/alert/alert' }); },
  goReview() { wx.navigateTo({ url: '/pages/review/view' }); },
  goPlan() { wx.navigateTo({ url: '/pages/plan/plan' }); }
  ,
  goVehicle() { wx.navigateTo({ url: '/pages/vehicle/vehicle' }); }
});
