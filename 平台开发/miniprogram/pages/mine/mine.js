const { getUser, getSites, clear } = require('../../utils/auth.js');
const maps = require('../../services/maps.js');
const api = require('../../services/api.js');

const app = getApp();

Page({
  data: { realName: '', roleCn: '', phone: '', sitesCount: 0, unread: 0 },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    const u = getUser() || {};
    this.setData({
      realName: u.real_name || '运维人员',
      roleCn: maps.map(maps.ROLE, u.role, '运维人员'),
      phone: u.phone || '未绑定',
      sitesCount: (getSites() || []).length
    });
    api.unreadCount()
      .then(r => this.setData({ unread: (r && r.count) || 0 }))
      .catch(() => {});
  },

  goMessage() { wx.navigateTo({ url: '/pages/message/message' }); },
  goReview() { wx.navigateTo({ url: '/pages/review/view' }); },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出当前账号吗？',
      confirmText: '退出',
      success(res) {
        if (res.confirm) {
          clear();
          wx.reLaunch({ url: '/pages/login/login' });
        }
      }
    });
  }
});
