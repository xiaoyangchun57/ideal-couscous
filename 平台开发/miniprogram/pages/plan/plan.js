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
    creatingFollowUpKey: '',
    favorites: [],
    favoriteSheet: { open: false, index: 0, periodStart: '', submitting: false }
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
      api.planScheduleFollowUpRecommendations().catch(() => ({ recommendations: [] })),
      api.planScheduleFavorites().catch(() => [])
    ])
      .then(([res, recommendationResult, followUpResult, favoriteResult]) => {
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
          })),
          favorites: (favoriteResult || []).map(item => Object.assign({}, item, {
            type_cn: maps.map(maps.SCHEDULE_TYPE, item.schedule_type, item.schedule_type)
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

  onOpenFavorites() {
    const favorites = this.data.favorites || [];
    if (!favorites.length) {
      wx.showToast({ title: '暂无常用计划，可在计划详情中收藏', icon: 'none' });
      return;
    }
    this.setData({ favoriteSheet: {
      open: true, index: 0, periodStart: favorites[0].suggested_period_start || '', submitting: false
    } });
  },

  onCloseFavorites() {
    if (!this.data.favoriteSheet.submitting) this.setData({ 'favoriteSheet.open': false });
  },

  onFavoritePick(e) {
    const index = Number(e.detail.value) || 0;
    const favorite = this.data.favorites[index] || {};
    this.setData({ 'favoriteSheet.index': index,
      'favoriteSheet.periodStart': favorite.suggested_period_start || this.data.favoriteSheet.periodStart });
  },

  onFavoriteDate(e) {
    this.setData({ 'favoriteSheet.periodStart': e.detail.value });
  },

  onCreateFavoriteDraft() {
    const sheet = this.data.favoriteSheet;
    const favorite = (this.data.favorites || [])[sheet.index];
    if (!favorite || !sheet.periodStart || sheet.submitting) return;
    this.setData({ 'favoriteSheet.submitting': true });
    api.createDraftFromPlanScheduleFavorite(favorite.id, sheet.periodStart)
      .then(res => {
        const scheduleId = res && res.schedule && res.schedule.id;
        if (!scheduleId) throw new Error('草稿创建结果无效');
        this.setData({ 'favoriteSheet.open': false, 'favoriteSheet.submitting': false });
        wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + scheduleId });
      })
      .catch(err => {
        this.setData({ 'favoriteSheet.submitting': false });
        wx.showToast({ title: (err && (err.error || err.message)) || '生成草稿失败', icon: 'none' });
      });
  },

  onDeleteFavorite() {
    const favorite = (this.data.favorites || [])[this.data.favoriteSheet.index];
    if (!favorite || this.data.favoriteSheet.submitting) return;
    wx.showModal({ title: '删除常用计划', content: '仅删除收藏模板，不影响原计划和已有草稿。', confirmColor: '#ef4444',
      success: result => {
        if (!result.confirm) return;
        api.deletePlanScheduleFavorite(favorite.id).then(() => {
          this.setData({ 'favoriteSheet.open': false });
          wx.showToast({ title: '已删除收藏', icon: 'success' });
          this.load();
        }).catch(err => wx.showToast({ title: (err && err.error) || '删除失败', icon: 'none' }));
      }
    });
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
  ,
  onDelete(e) {
    const id = e.currentTarget.dataset.id;
    wx.showModal({ title: '删除草稿', content: '仅删除未提交或已退回的草稿，确定继续？', confirmColor: '#ef4444', success: (r) => {
      if (!r.confirm) return;
      api.deletePlanSchedule(id).then(() => { wx.showToast({ title: '草稿已删除', icon: 'success' }); this.load(); })
        .catch(err => wx.showToast({ title: (err && err.error) || '删除失败', icon: 'none' }));
    }});
  }
});
