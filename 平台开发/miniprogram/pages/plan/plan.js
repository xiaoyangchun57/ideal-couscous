const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');

const app = getApp();

Page({
  data: {
    loaded: false,
    list: [],       // 排程列表（含中文映射）
    filter: 'all'   // all | active（进行中=草稿/待审/变更中）| done（已通过/已归档）
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
    api.planSchedules()
      .then(res => {
        const list = (Array.isArray(res) ? res : []).map(item => {
          return Object.assign({}, item, {
            status_cn: maps.map(maps.PLAN_SCHEDULE_STATUS, item.status, item.status),
            status_cls: maps.PLAN_SCHEDULE_STATUS_CLS[item.status] || 'gray',
            type_cn: maps.map(maps.SCHEDULE_TYPE, item.schedule_type, item.schedule_type),
            period_text: (item.period_start || '').slice(5) + ' ~ ' + (item.period_end || '').slice(5)
          });
        });
        this.setData({ loaded: true, list });
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

  // 编辑（仅 draft/rejected 可进入编辑）
  onEdit(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + id });
  }
});
