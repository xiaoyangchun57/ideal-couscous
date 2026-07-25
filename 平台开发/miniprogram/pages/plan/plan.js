const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');

const app = getApp();

Page({
  data: {
    loaded: false,
    list: [],       // 排程列表（含中文映射）
    filter: 'all',  // all | active（进行中=草稿/待审/变更中）| done（已通过/已归档）
    recommendations: [],
    creatingRecommendationKey: '',
    followUpRecommendations: [],
    creatingFollowUpKey: ''
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
    Promise.all([
      api.planSchedules(),
      api.planScheduleDraftRecommendations().catch(() => ({ recommendations: [] })),
      api.planScheduleFollowUpRecommendations().catch(() => ({ recommendations: [] }))
    ])
      .then(([res, recommendationResult, followUpResult]) => {
        const list = (Array.isArray(res) ? res : []).map(item => {
          return Object.assign({}, item, {
            status_cn: maps.map(maps.PLAN_SCHEDULE_STATUS, item.status, item.status),
            status_cls: maps.PLAN_SCHEDULE_STATUS_CLS[item.status] || 'gray',
            type_cn: maps.map(maps.SCHEDULE_TYPE, item.schedule_type, item.schedule_type),
            period_text: (item.period_start || '').slice(5) + ' ~ ' + (item.period_end || '').slice(5)
          });
        });
        this.setData({
          loaded: true,
          list,
          recommendations: (recommendationResult.recommendations || []).map(item => Object.assign({}, item, {
            recommendation_key: item.user_id + '-' + item.schedule_type + '-' + item.period_start
          })),
          followUpRecommendations: (followUpResult.recommendations || []).map(item => Object.assign({}, item, {
            follow_up_key: item.user_id + '-' + item.site_id + '-' + item.anomaly_type
          }))
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loaded: true });
        if (done) done();
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  onFilterTap(e) {
    this.setData({ filter: e.currentTarget.dataset.f });
  },

  // 过滤后的列表（wxml 用 wxs 或 computed；这里简化为前端过滤）
  getFilteredList() {
    const { list, filter } = this.data;
    if (filter === 'active') {
      return list.filter(i => ['draft', 'submitted', 'rejected', 'modifying', 'change_submitted'].includes(i.status));
    }
    if (filter === 'done') {
      return list.filter(i => ['approved', 'archived'].includes(i.status));
    }
    return list;
  },

  onItemTap(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/plan-detail/plan-detail?id=' + id });
  },

  onNewPlan() {
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit' });
  },

  // 建议必须由人显式确认后才落为草稿；创建后直接进入现有排程编辑页。
  onCreateRecommendedDraft(e) {
    const { userId, scheduleType, periodStart } = e.currentTarget.dataset;
    const key = userId + '-' + scheduleType + '-' + periodStart;
    if (this.data.creatingRecommendationKey) return;
    this.setData({ creatingRecommendationKey: key });
    api.createPlanScheduleFromRecommendation({
      user_id: userId,
      schedule_type: scheduleType,
      period_start: periodStart
    })
      .then(res => {
        const scheduleId = res && res.schedule && res.schedule.id;
        if (!scheduleId) throw new Error('草稿创建结果无效');
        wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + scheduleId });
      })
      .catch(err => {
        wx.showToast({ title: err && err.error ? err.error : '创建草稿失败，请刷新后重试', icon: 'none' });
      })
      .finally(() => this.setData({ creatingRecommendationKey: '' }));
  },

  onCreateFollowUpDraft(e) {
    const { userId, siteId, anomalyType } = e.currentTarget.dataset;
    const key = userId + '-' + siteId + '-' + anomalyType;
    if (this.data.creatingFollowUpKey) return;
    this.setData({ creatingFollowUpKey: key });
    api.createPlanScheduleFollowUpDraft({
      user_id: userId,
      site_id: siteId,
      anomaly_type: anomalyType
    })
      .then(res => {
        const scheduleId = res && res.schedule && res.schedule.id;
        if (!scheduleId) throw new Error('复查草稿创建结果无效');
        wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + scheduleId });
      })
      .catch(err => {
        wx.showToast({ title: err && err.error ? err.error : '创建复查草稿失败，请刷新后重试', icon: 'none' });
      })
      .finally(() => this.setData({ creatingFollowUpKey: '' }));
  },

  // 编辑（仅 draft/rejected 可进入编辑）
  onEdit(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + id });
  }
});
