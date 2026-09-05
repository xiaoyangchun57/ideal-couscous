const { getUser, getSites, clear } = require('../../utils/auth.js');
const maps = require('../../services/maps.js');
const api = require('../../services/api.js');
const { canReview, loadReviewTodoCount } = require('../../utils/reviewAccess.js');
const {
  authoritativeUnreadCount,
  currentUnreadRevision,
} = require('../../utils/notificationCount.js');

const app = getApp();

function canUseVehicle(user) {
  const roles = Array.isArray(user && user.roles) ? user.roles.slice() : [];
  if (user && user.role) roles.push(user.role);
  return roles.some(role => role === 'operator' || role === 'admin');
}

Page({
  data: { realName: '', roleCn: '', phone: '', sitesCount: 0, unread: null, reviewTodo: 0, canReview: false, canUseVehicle: false },

  onLoad() {
    this._alive = true;
    this._unreadRequestId = 0;
    this._unreadRevision = currentUnreadRevision();
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this._alive = true;
    this._vehicleNavigationPending = false;
    const u = getUser() || {};
    const reviewAllowed = canReview(u);
    this.setData({
      realName: u.real_name || '运维人员',
      roleCn: maps.map(maps.ROLE, u.role, '运维人员'),
      phone: u.phone || '未绑定',
      sitesCount: (getSites() || []).length,
      canReview: reviewAllowed,
      canUseVehicle: canUseVehicle(u),
    });
    this._prepareUnreadRefresh();
    this.loadUnreadCount();
    loadReviewTodoCount(u, api.auditPending)
      .then(count => this.setData({ reviewTodo: count }))
      .catch(() => this.setData({ reviewTodo: 0 }));
  },

  onUnload() {
    this._alive = false;
    this._unreadRequestId = (this._unreadRequestId || 0) + 1;
  },

  _prepareUnreadRefresh() {
    const revision = currentUnreadRevision();
    if (this._unreadRevision === revision) return;
    this._unreadRevision = revision;
    this.setData({ unread: null });
  },

  loadUnreadCount() {
    const requestId = (this._unreadRequestId || 0) + 1;
    const unreadRevision = currentUnreadRevision();
    this._unreadRequestId = requestId;
    return api.unreadCount()
      .then(response => {
        if (this._alive === false || requestId !== this._unreadRequestId
          || unreadRevision !== currentUnreadRevision()) return;
        this._unreadRevision = unreadRevision;
        this.setData({ unread: authoritativeUnreadCount(response) });
      })
      .catch(() => {
        if (this._alive === false || requestId !== this._unreadRequestId
          || unreadRevision !== currentUnreadRevision()) return;
        this._unreadRevision = unreadRevision;
        this.setData({ unread: null });
      });
  },

  goMessage() { wx.navigateTo({ url: '/pages/message/message' }); },
  goReports() { wx.navigateTo({ url: '/pages/reports/reports' }); },
  goReview() { wx.navigateTo({ url: '/pages/review/view' }); },
  goVehicle() {
    if (!this.data.canUseVehicle) {
      wx.showToast({ title: '当前账号无用车操作权限', icon: 'none' });
      return;
    }
    if (this._vehicleNavigationPending) {
      wx.showToast({ title: '正在打开，请稍候', icon: 'none' });
      return;
    }
    this._vehicleNavigationPending = true;
    wx.navigateTo({
      url: '/pages/vehicle/vehicle',
      fail: () => {
        this._vehicleNavigationPending = false;
        if (this._alive !== false) wx.showToast({ title: '我的用车打开失败，请重试', icon: 'none' });
      },
    });
  },
  goResponsibleSites() { wx.navigateTo({ url: '/pages/responsible-sites/responsible-sites' }); },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出当前账号吗？',
      confirmText: '退出',
      success(res) {
        if (res.confirm) {
          const done = () => {
            clear();
            wx.reLaunch({ url: '/pages/login/login' });
          };
          api.logout().then(done).catch(done);
        }
      }
    });
  }
});
