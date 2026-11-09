const { getUser, getSites, clear } = require('../../utils/auth.js');
const maps = require('../../services/maps.js');
const api = require('../../services/api.js');
const { resolveUploadUrl } = require('../../utils/url.js');
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
  data: { realName: '', roleCn: '', phone: '', sitesCount: 0, unread: null, reviewTodo: 0, canReview: false, canUseVehicle: false,
    qualityOpen: false, qualityMonth: '', qualityLoading: false, qualityError: '', qualityDeduction: 0, qualityPhotos: [] },

  onLoad() {
    this._alive = true;
    this._unreadRequestId = 0;
    this._unreadRevision = currentUnreadRevision();
    const today = new Date();
    this.setData({ qualityMonth: today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0') });
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
    if (this.data.qualityOpen && !this._returnFromQualityInspection) this.loadPhotoQuality();
    this._returnFromQualityInspection = false;
    loadReviewTodoCount(u, api.auditPending)
      .then(count => this.setData({ reviewTodo: count }))
      .catch(() => this.setData({ reviewTodo: 0 }));
  },

  onUnload() {
    this._alive = false;
    this._qualityRequest = (this._qualityRequest || 0) + 1;
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

  togglePhotoQuality() {
    this.setData({ qualityOpen: !this.data.qualityOpen });
    if (this.data.qualityOpen) this.loadPhotoQuality();
  },
  changeQualityMonth(event) {
    this.setData({ qualityMonth: event.detail.value });
    this.loadPhotoQuality();
  },
  loadPhotoQuality() {
    const requestId = this._qualityRequest = (this._qualityRequest || 0) + 1;
    this.setData({ qualityLoading: true, qualityError: '', qualityPhotos: [] });
    return api.photoQualityMonthly(this.data.qualityMonth).then(result => {
      if (!this._alive || requestId !== this._qualityRequest) return;
      if (!result || !Array.isArray(result.photos) || typeof result.deduction !== 'number') throw new Error('照片明细数据不完整，请重试');
      this.setData({ qualityLoading: false, qualityDeduction: result.deduction,
        qualityPhotos: result.photos.map(photo => ({ ...photo, display_url: resolveUploadUrl(photo.display_url) })) });
    }).catch(error => {
      if (!this._alive || requestId !== this._qualityRequest) return;
      this.setData({ qualityLoading: false, qualityError: error.error || error.message || '明细加载失败，请重试' });
    });
  },
  previewQualityPhoto(event) {
    const url = event.currentTarget.dataset.url;
    if (url) wx.previewImage({ current: url, urls: this.data.qualityPhotos.map(photo => photo.display_url) });
  },
  openQualityInspection(event) {
    const photo = this.data.qualityPhotos.find(row => row.attachment_id === Number(event.currentTarget.dataset.id));
    const ids = photo && [photo.plan_id, photo.site_id, photo.item_id].map(Number);
    if (!ids || !ids.every(id => Number.isInteger(id) && id > 0)) {
      wx.showToast({ title: '原检查项信息不完整', icon: 'none' });
      return;
    }
    const target = { executionPlanId: ids[0], siteId: ids[1], itemId: ids[2], readOnly: true, source: 'photo_quality_history' };
    app.globalData.executionTarget = target;
    this._returnFromQualityInspection = true;
    wx.navigateTo({ url: '/pages/inspection/inspection', fail: () => {
      if (app.globalData.executionTarget === target) app.globalData.executionTarget = null;
      this._returnFromQualityInspection = false;
      wx.showToast({ title: '原检查项打开失败，请重试', icon: 'none' });
    } });
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
