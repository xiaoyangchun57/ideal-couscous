const api = require('../../services/api.js');
const { relativeTime } = require('../../utils/util.js');
const { planScheduleDetailUrl } = require('../../utils/notificationTarget.js');
const { hasMoreFromResponse, appendDistinctById } = require('../../utils/pagedList.js');

const app = getApp();

// 订阅模板（首批：告警信息 + 审批结果）
// 注意：须在小程序后台「订阅消息」配置对应模板后，将真实模板 ID 填入此处
const SUBSCRIBE_TMPL = ['x_KtbMzoSIbxpUZGf040r9uvuNqd9pfhOynKaT72Ub4', '4MrY8lzIXYyujudoJGsG7gka5X_ySpxg5eVKVqC__mw'];

function decorate(n) {
  return {
    id: n.id,
    source_type: n.source_type || '',
    source_id: n.source_id || '',
    title: n.title,
    content: n.content || '',
    is_read: !!n.is_read,
    time: relativeTime(n.created_at)
  };
}

Page({
  data: { list: [], loaded: false, page: 1, loading: false, noMore: false, view: 'current' },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    if (app.globalData.refreshNotificationBadge) app.globalData.refreshNotificationBadge();
    this.load(true);
  },

  onPullDownRefresh() {
    this.load(true, () => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (this.data.loading || this.data.noMore) return;
    this.load(false);
  },

  load(reset, done) {
    if (this.data.loading) { if (done) done(); return; }
    const page = reset ? 1 : this.data.page + 1;
    this.setData({ loading: true });
    const status = this.data.view === 'history' ? 'read' : 'unread';
    api.notifications(page, status)
      .then(res => {
        const rows = (res && res.notifications) || [];
        const decoratedRows = rows.map(decorate);
        const list = reset
          ? decoratedRows
          : appendDistinctById(this.data.list, decoratedRows);
        this.setData({
          list, page, loaded: true, loading: false,
          noMore: !hasMoreFromResponse(res, rows.length, 50)
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loading: false, loaded: true });
        if (done) done();
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  onSwitchView(e) {
    const view = e.currentTarget.dataset.view;
    if (!view || view === this.data.view) return;
    this.setData({ view, list: [], page: 1, loaded: false, noMore: false });
    this.load(true);
  },

  openBusinessTarget(item) {
    const sourceType = item.source_type;
    if (sourceType === 'vehicle_use_expiry') {
      wx.navigateTo({ url: '/pages/vehicle/vehicle' });
      return;
    }
    if (sourceType === 'workorder' || sourceType === 'workorder_review') {
      getApp().globalData.selWorkorderNo = item.source_id;
      wx.navigateTo({ url: '/pages/workorder/workorder' });
      return;
    }
    if (sourceType === 'inspection' || sourceType === 'reagent_qc') {
      wx.switchTab({ url: '/pages/inspection/inspection' });
      return;
    }
    if (sourceType === 'inspection_rework') {
      getApp().globalData.selPlanId = Number(item.source_id) || item.source_id;
      wx.switchTab({ url: '/pages/inspection/inspection' });
      return;
    }
    if (sourceType === 'plan_schedule') {
      wx.navigateTo({ url: planScheduleDetailUrl(item.source_id) });
      return;
    }
    if (sourceType === 'alert' || sourceType === 'manual_report') {
      wx.navigateTo({ url: '/pages/alert/alert' });
      return;
    }
    if (['inspection_review', 'inspection_review_batch', 'photo_review', 'attachment_review', 'attachment_review_batch', 'data_review', 'parts_request', 'spare_part_request', 'vehicle_application'].includes(sourceType)) {
      wx.navigateTo({ url: '/pages/review/view' });
    }
  },

  onTap(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find(n => n.id === id);
    if (!item) return;
    const open = () => this.openBusinessTarget(item);
    if (!item.is_read) {
      api.readNotification(id)
        .then(() => {
          const list = this.data.view === 'current'
            ? this.data.list.filter(n => n.id !== id)
            : this.data.list.map(n => n.id === id ? Object.assign({}, n, { is_read: true }) : n);
          this.setData({ list });
          if (app.globalData.refreshNotificationBadge) app.globalData.refreshNotificationBadge();
          open();
        })
        .catch(open);
    } else {
      open();
    }
  },

  onReadAll() {
    api.readAllNotifications()
      .then(() => {
        const list = this.data.view === 'current' ? [] : this.data.list;
        this.setData({ list });
        if (app.globalData.refreshNotificationBadge) app.globalData.refreshNotificationBadge();
        wx.showToast({ title: '已全部已读', icon: 'success' });
      })
      .catch(() => wx.showToast({ title: '操作失败', icon: 'none' }));
  },

  onSubscribe() {
    wx.requestSubscribeMessage({
      tmplIds: SUBSCRIBE_TMPL,
      success() { wx.showToast({ title: '订阅成功', icon: 'success' }); },
      fail() {
        wx.showModal({
          title: '订阅提示',
          content: '请先在微信公众平台/小程序后台「订阅消息」中配置模板，并将真实模板 ID 填入 message.js 的 SUBSCRIBE_TMPL。',
          showCancel: false
        });
      }
    });
  }
});
