const api = require('../../services/api.js');
const { relativeTime } = require('../../utils/util.js');
const { resolveNotificationTarget } = require('../../utils/notificationTarget.js');
const { hasMoreFromResponse, appendDistinctById } = require('../../utils/pagedList.js');
const {
  getMessageViewState,
  shouldShowNoMore,
  shouldShowLoadMore,
  getMessageLoadError
} = require('../../utils/messageViewState.js');

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
    payload_json: n.payload_json || '',
    is_read: !!n.is_read,
    time: relativeTime(n.created_at)
  };
}

Page({
  data: {
    list: [], loaded: false, page: 1, loading: false, noMore: false,
    view: 'current', viewState: 'loading', errorMessage: '', retryReset: true,
    showNoMore: false, showLoadMore: false
  },

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
    const loadToken = (this._loadToken || 0) + 1;
    this._loadToken = loadToken;
    const page = reset ? 1 : this.data.page + 1;
    this.setData(Object.assign({ loading: true, errorMessage: '', retryReset: reset }, this._statePatch({
      loading: true,
      errorMessage: ''
    })));
    const status = this.data.view === 'history' ? 'read' : 'unread';
    api.notifications(page, status)
      .then(res => {
        if (loadToken !== this._loadToken) { if (done) done(); return; }
        const rows = (res && res.notifications) || [];
        const decoratedRows = rows.map(decorate);
        const list = reset
          ? decoratedRows
          : appendDistinctById(this.data.list, decoratedRows);
        const noMore = !hasMoreFromResponse(res, rows.length, 50);
        this.setData(Object.assign({
          list, page, loaded: true, loading: false, noMore, errorMessage: ''
        }, this._statePatch({ list, loaded: true, loading: false, errorMessage: '', noMore })));
        if (done) done();
      })
      .catch(err => {
        if (loadToken !== this._loadToken) { if (done) done(); return; }
        const errorMessage = getMessageLoadError(err);
        this.setData(Object.assign({ loading: false, loaded: true, errorMessage }, this._statePatch({
          loaded: true,
          loading: false,
          errorMessage
        })));
        if (done) done();
      });
  },

  _statePatch(options) {
    const opts = options || {};
    const list = opts.list === undefined ? this.data.list : opts.list;
    const loading = opts.loading === undefined ? this.data.loading : opts.loading;
    const loaded = opts.loaded === undefined ? this.data.loaded : opts.loaded;
    const errorMessage = opts.errorMessage === undefined ? this.data.errorMessage : opts.errorMessage;
    const noMore = opts.noMore === undefined ? this.data.noMore : opts.noMore;
    return {
      viewState: getMessageViewState({ list, loading, loaded, error: errorMessage }),
      showNoMore: shouldShowNoMore(list, noMore),
      showLoadMore: shouldShowLoadMore(list, noMore, loading, errorMessage)
    };
  },

  onRetry() { this.load(this.data.retryReset); },

  onSwitchView(e) {
    const view = e.currentTarget.dataset.view;
    if (!view || view === this.data.view) return;
    this.setData({
      view, list: [], page: 1, loaded: false, loading: false, noMore: false,
      errorMessage: '', retryReset: true, viewState: 'loading',
      showNoMore: false, showLoadMore: false
    });
    this.load(true);
  },

  openBusinessTarget(item) {
    const target = resolveNotificationTarget(item);
    if (target.kind === 'invalid') {
      wx.showModal({ title: '无法打开通知对象', content: target.message, showCancel: false });
      return;
    }
    if (target.kind === 'tab') {
      if (target.planId) getApp().globalData.selPlanId = Number(target.planId) || target.planId;
      if (target.itemId) getApp().globalData.selItemId = Number(target.itemId) || target.itemId;
      if (target.siteId) getApp().globalData.selSiteId = Number(target.siteId) || target.siteId;
      wx.switchTab({ url: target.page });
      return;
    }
    if (target.kind === 'page' && target.workorderNo) {
      getApp().globalData.selWorkorderNo = target.workorderNo;
    }
    if (target.kind === 'page' || target.kind === 'review') {
      wx.navigateTo({ url: target.page });
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
          this.setData(Object.assign({ list, noMore: list.length ? this.data.noMore : false }, this._statePatch({
            list,
            noMore: list.length ? this.data.noMore : false
          })));
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
        const noMore = this.data.view === 'current' ? false : this.data.noMore;
        this.setData(Object.assign({ list, noMore }, this._statePatch({ list, noMore })));
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
