const { getUser, getSites, clear } = require('../../utils/auth.js');
const maps = require('../../services/maps.js');
const api = require('../../services/api.js');

const app = getApp();

Page({
  data: { realName: '', roleCn: '', phone: '', sitesCount: 0, unread: 0, partsRequests: [], issuablePartsRequests: [], partsSheet: { open: false, index: 0, quantity: 1, submitting: false } },

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
    api.myPartsRequests().then(rows => {
      const partsRequests = rows || [];
      this.setData({ partsRequests, issuablePartsRequests: partsRequests.filter(r => r.status === 'approved' && (r.items || []).some(i => i.remaining_quantity > 0)) });
    }).catch(() => {});
  },

  goMessage() { wx.navigateTo({ url: '/pages/message/message' }); },
  goReports() { wx.navigateTo({ url: '/pages/reports/reports' }); },
  goReview() { wx.navigateTo({ url: '/pages/review/view' }); },
  onOpenParts() { this.setData({ 'partsSheet.open': true, 'partsSheet.index': 0, 'partsSheet.quantity': 1 }); },
  onCloseParts() { this.setData({ 'partsSheet.open': false }); },
  onPartsPick(e) { this.setData({ 'partsSheet.index': Number(e.detail.value) || 0, 'partsSheet.quantity': 1 }); },
  onIssueQty(e) { this.setData({ 'partsSheet.quantity': e.detail.value }); },
  onIssueParts() {
    const request = (this.data.issuablePartsRequests || [])[this.data.partsSheet.index];
    const item = request && (request.items || []).find(i => i.remaining_quantity > 0);
    const qty = Number(this.data.partsSheet.quantity || 0);
    if (!request || !item || qty <= 0 || qty > item.remaining_quantity) { wx.showToast({ title: '领用数量不能超过预留量', icon: 'none' }); return; }
    this.setData({ 'partsSheet.submitting': true });
    api.issuePartsRequest(request.id, [{ part_id: item.part_id, quantity: qty }]).then(() => {
      this.setData({ 'partsSheet.open': false, 'partsSheet.submitting': false });
      this.onShow(); wx.showToast({ title: '已记录现场领用', icon: 'success' });
    }).catch(() => this.setData({ 'partsSheet.submitting': false }));
  },

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
