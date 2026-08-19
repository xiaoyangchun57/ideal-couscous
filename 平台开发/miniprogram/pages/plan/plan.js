const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const { rolesForUser } = require('../../utils/reviewAccess.js');

const app = getApp();
const ACTIVE_SCHEDULE_STATUSES = ['draft', 'submitted', 'rejected', 'modifying', 'change_submitted'];
const EXECUTION_FILTER_STATUSES = ['pending', 'partial', 'rework', 'completed', 'change_pending'];

function planFilterGroup(plan) {
  const source = plan || {};
  if (source.status === 'archived') return 'done';
  if (source.status === 'approved') {
    if (EXECUTION_FILTER_STATUSES.includes(source.execution_status)) {
      return source.execution_status === 'completed' ? 'done' : 'active';
    }
    return source.execution_completed === true ? 'done' : 'active';
  }
  if (ACTIVE_SCHEDULE_STATUSES.includes(source.status)) return 'active';
  return 'active';
}

Page({
  data: {
    loaded: false,
    list: [],       // 排程列表（含中文映射）
    filter: 'all',  // all | active（进行中=草稿/待审/现场未完成）| done（现场已完成/已归档）
    canTeamView: false,
    isTeamView: false,
    followUpRecommendations: [],
    followUpRecommendationsExpanded: false,
    creatingFollowUpKey: '',
    favorites: [],
    canUseFavorites: false,
    favoriteSheet: { open: false, index: 0, periodStart: '', submitting: false }
  },

  onShow() {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    const user = getUser() || {};
    const roles = rolesForUser(user);
    this.setData({ canUseFavorites: roles.includes('operator'), canTeamView: roles.includes('admin') });
    this.load();
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh());
  },

  load(done) {
    Promise.all([
      api.planSchedules(),
      api.planScheduleFollowUpRecommendations(this.data.isTeamView).catch(() => ({ recommendations: [] })),
      this.data.canUseFavorites ? api.planScheduleFavorites().catch(() => []) : Promise.resolve([])
    ])
      .then(([res, followUpResult, favoriteResult]) => {
        const list = (Array.isArray(res) ? res : []).map(item => {
          return Object.assign({}, item, {
            status_cn: maps.map(maps.PLAN_SCHEDULE_STATUS, item.status, item.status),
            status_cls: maps.PLAN_SCHEDULE_STATUS_CLS[item.status] || 'gray',
            type_cn: maps.map(maps.SCHEDULE_TYPE, item.schedule_type, item.schedule_type),
            period_text: (item.period_start || '').slice(5) + ' ~ ' + (item.period_end || '').slice(5),
            filter_group: planFilterGroup(item)
          });
        });
        this.setData({
          loaded: true,
          list,
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

  onItemTap(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/plan-detail/plan-detail?id=' + id });
  },

  onNewPlan() {
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit' });
  },

  onOpenFavorites() {
    if (!this.data.canUseFavorites) return;
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

  onToggleRecommendationScope() {
    if (!this.data.canTeamView) return;
    this.setData({
      isTeamView: !this.data.isTeamView,
      followUpRecommendationsExpanded: false,
      loaded: false
    }, () => this.load());
  },

  onToggleFollowUpRecommendations() {
    this.setData({ followUpRecommendationsExpanded: !this.data.followUpRecommendationsExpanded });
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

  onNotifyFollowUpOwner(e) {
    const { userId, siteId, anomalyType } = e.currentTarget.dataset;
    const key = userId + '-' + siteId + '-' + anomalyType;
    if (this.data.creatingFollowUpKey) return;
    this.setData({ creatingFollowUpKey: key });
    api.createPlanScheduleFollowUpDraft({
      action: 'notify_owner', user_id: userId, site_id: siteId, anomaly_type: anomalyType
    }).then(result => wx.showToast({ title: result.notified ? '已通知负责人' : '已有未读通知', icon: 'none' }))
      .catch(err => wx.showToast({ title: (err && err.error) || '通知失败', icon: 'none' }))
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

module.exports = { planFilterGroup };
