const api = require('../../services/api.js');
const { relativeTime } = require('../../utils/util.js');

const app = getApp();

// 订阅模板（首批：告警信息 + 审批结果）
// 注意：须在小程序后台「订阅消息」配置对应模板后，将真实模板 ID 填入此处
const SUBSCRIBE_TMPL = ['x_KtbMzoSIbxpUZGf040r9uvuNqd9pfhOynKaT72Ub4', '4MrY8lzIXYyujudoJGsG7gka5X_ySpxg5eVKVqC__mw'];

function decorate(n) {
  return {
    id: n.id,
    title: n.title,
    content: n.content || '',
    is_read: !!n.is_read,
    time: relativeTime(n.created_at)
  };
}

Page({
  data: { list: [], loaded: false, page: 1, loading: false, noMore: false },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
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
    api.notifications(page)
      .then(res => {
        const rows = (res && res.notifications) || [];
        const list = reset
          ? rows.map(decorate)
          : this.data.list.concat(rows.map(decorate));
        this.setData({
          list, page, loaded: true, loading: false,
          noMore: rows.length < 50
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loading: false, loaded: true });
        if (done) done();
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  onTap(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find(n => n.id === id);
    if (item && !item.is_read) {
      api.readNotification(id)
        .then(() => {
          const list = this.data.list.map(n => n.id === id ? Object.assign({}, n, { is_read: true }) : n);
          this.setData({ list });
        })
        .catch(() => {});
    }
  },

  onReadAll() {
    api.readAllNotifications()
      .then(() => {
        const list = this.data.list.map(n => Object.assign({}, n, { is_read: true }));
        this.setData({ list });
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
