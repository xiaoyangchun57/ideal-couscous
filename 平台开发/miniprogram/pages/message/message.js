const api = require('../../services/api.js');
const { relativeTime } = require('../../utils/util.js');
const { resolveNotificationTarget } = require('../../utils/notificationTarget.js');
const { invalidateUnreadCount } = require('../../utils/notificationCount.js');
const { hasMoreFromResponse, appendDistinctById } = require('../../utils/pagedList.js');
const {
  getMessageViewState,
  shouldShowNoMore,
  shouldShowLoadMore,
  getMessageLoadError
} = require('../../utils/messageViewState.js');

const app = getApp();
const SUBSCRIPTION_LABELS = {
  alert: '监测告警',
  approval_pending: '待审批',
  approval_result: '审批结果'
};

function subscribeFailureFeedback(error) {
  const rawCode = error && (error.errCode !== undefined ? error.errCode : error.err_code);
  const errCode = rawCode === undefined || rawCode === null ? '' : String(rawCode);
  const errMsg = String(error && (error.errMsg || error.err_msg || error.message) || '').toLowerCase();
  const known = {
    10002: ['WECHAT_SERVICE_RETRY', '微信订阅服务暂不可用，请稍后重试'],
    10003: ['WECHAT_SERVICE_RETRY', '微信订阅服务暂不可用，请稍后重试'],
    10004: ['TEMPLATE_INVALID', '订阅模板无效，请联系管理员核对配置'],
    10005: ['SUBSCRIBE_UI_UNAVAILABLE', '当前无法展示订阅界面，请返回小程序前台后重试'],
    20001: ['TEMPLATE_DATA_MISSING', '订阅模板数据不可用，请联系管理员核对配置'],
    20002: ['TEMPLATE_TYPE_MISMATCH', '订阅模板类型不匹配，请联系管理员核对配置'],
    20003: ['TEMPLATE_UNAVAILABLE', '订阅配置暂不可用，请稍后重试'],
    20004: ['MESSAGE_SWITCH_DISABLED', '微信消息通知总开关已关闭，请在微信设置中开启后重试'],
    20005: ['TEMPLATE_UNAVAILABLE', '订阅配置暂不可用，请稍后重试'],
    20013: ['TEMPLATE_SUBSCRIBE_NOT_ALLOWED', '当前订阅模板不支持此订阅方式，请联系管理员核对配置']
  };
  if (known[errCode]) {
    return { category: known[errCode][0], errCode, message: known[errCode][1] };
  }
  if (/network|timeout|service/.test(errMsg)) {
    return { category: 'WECHAT_UNAVAILABLE', errCode, message: '微信订阅服务暂不可用，请稍后重试' };
  }
  return { category: 'UNKNOWN', errCode, message: '订阅失败，请重试' };
}

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

  onLoad(options) {
    this._alive = true;
    const notificationId = Number(options && options.notification_id);
    this._focusNotificationId = Number.isInteger(notificationId) && notificationId > 0
      ? notificationId : null;
  },

  onShow() {
    this._alive = true;
    this._viewEpoch = (this._viewEpoch || 0) + 1;
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.load(true);
  },

  onHide() {
    this._alive = false;
    this._loadToken = (this._loadToken || 0) + 1;
    this._viewEpoch = (this._viewEpoch || 0) + 1;
  },

  onUnload() { this.onHide(); },

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
    const viewEpoch = this._viewEpoch || 0;
    const page = reset ? 1 : this.data.page + 1;
    this.setData(Object.assign({ loading: true, errorMessage: '', retryReset: reset }, this._statePatch({
      loading: true,
      errorMessage: ''
    })));
    const status = this.data.view === 'history' ? 'read' : 'unread';
    api.notifications(page, status, this._focusNotificationId)
      .then(res => {
        if (!this._isActiveView(viewEpoch) || loadToken !== this._loadToken) return;
        const rows = (res && res.notifications) || [];
        const decoratedRows = rows.map(decorate);
        const focusedView = this._focusNotificationId && decoratedRows.length
          ? (decoratedRows[0].is_read ? 'history' : 'current') : this.data.view;
        const focusError = this._focusNotificationId && !decoratedRows.length
          ? '该消息不存在或当前账号无权查看' : '';
        const list = reset
          ? decoratedRows
          : appendDistinctById(this.data.list, decoratedRows);
        const noMore = !hasMoreFromResponse(res, rows.length, 50);
        this.setData(Object.assign({
          list, page, view: focusedView, loaded: true, loading: false, noMore,
          errorMessage: focusError
        }, this._statePatch({ list, loaded: true, loading: false,
          errorMessage: focusError, noMore })));
        if (this._focusNotificationId && decoratedRows.length && !decoratedRows[0].is_read) {
          this._markFocusedNotificationRead(decoratedRows[0].id);
        }
        if (done) done();
      })
      .catch(err => {
        if (!this._isActiveView(viewEpoch) || loadToken !== this._loadToken) return;
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

  _isActiveView(viewEpoch) {
    return this._alive !== false && (viewEpoch === undefined || viewEpoch === (this._viewEpoch || 0));
  },

  _markFocusedNotificationRead(notificationId) {
    if (this._focusedReadId === notificationId) return;
    this._focusedReadId = notificationId;
    api.readNotification(notificationId).then(() => {
      if (this._alive === false) return;
      invalidateUnreadCount();
      this.setData({ list: this.data.list.map(item => Number(item.id) === Number(notificationId)
        ? Object.assign({}, item, { is_read: true }) : item) });
    }).catch(() => { this._focusedReadId = null; });
  },

  onRetry() { this.load(this.data.retryReset); },

  onSwitchView(e) {
    const view = e.currentTarget.dataset.view;
    if (!view || view === this.data.view) return;
    this._viewEpoch = (this._viewEpoch || 0) + 1;
    this.setData({
      view, list: [], page: 1, loaded: false, loading: false, noMore: false,
      errorMessage: '', retryReset: true, viewState: 'loading',
      showNoMore: false, showLoadMore: false
    });
    this.load(true);
  },

  openBusinessTarget(item, done) {
    const target = resolveNotificationTarget(item);
    if (target.kind === 'invalid') {
      if (this._alive !== false) wx.showModal({ title: '无法打开通知对象', content: target.message, showCancel: false });
      if (done) done({ success: false });
      return;
    }
    const navigationKey = String(item && item.id || target.page);
    this._navigationByMessageId = this._navigationByMessageId || {};
    if (this._navigationByMessageId[navigationKey]) {
      if (this._alive !== false) wx.showToast({ title: '正在打开，请稍候', icon: 'none' });
      return;
    }
    this._navigationByMessageId[navigationKey] = true;
    const globalData = getApp().globalData;
    const writtenTargets = [];
    const writeTarget = (key, value) => {
      globalData[key] = value;
      writtenTargets.push({ key, value });
    };
    if (target.alertId) {
      const alertId = Number(target.alertId) || target.alertId;
      globalData.selAlertId = alertId;
      writtenTargets.push({ key: 'selAlertId', value: alertId });
    }
    if (target.executionTarget) {
      globalData.executionTarget = target.executionTarget;
      writtenTargets.push({ key: 'executionTarget', value: target.executionTarget });
    }
    if (target.vehicleTarget) writeTarget('vehicleTarget', target.vehicleTarget);
    if (target.kind === 'tab') {
      this._navigateToTarget('switchTab', target.page, navigationKey, writtenTargets, done);
      return;
    }
    if (target.kind === 'page' && target.workorderNo) {
      writeTarget('selWorkorderNo', target.workorderNo);
    }
    if (target.kind === 'page' || target.kind === 'review') {
      this._navigateToTarget('navigateTo', target.page, navigationKey, writtenTargets, done);
    }
  },

  _navigateToTarget(method, url, navigationKey, writtenTargets, done) {
    let finished = false;
    const finish = result => {
      if (finished) return;
      finished = true;
      if (this._navigationByMessageId) delete this._navigationByMessageId[navigationKey];
      if (done) done(result);
    };
    wx[method]({
      url,
      fail: () => {
        writtenTargets.forEach(target => {
          if (getApp().globalData[target.key] === target.value) getApp().globalData[target.key] = null;
        });
        if (this._alive !== false) wx.showToast({ title: '打开消息目标失败，请重试', icon: 'none' });
        finish({ success: false });
      },
      success: () => finish({ success: true })
    });
  },

  onTap(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find(n => n.id === id);
    if (!item) return;
    this._openingMessageIds = this._openingMessageIds || {};
    if (this._openingMessageIds[id]) {
      wx.showToast({ title: '正在打开，请稍候', icon: 'none' });
      return;
    }
    this._openingMessageIds[id] = true;
    const viewEpoch = this._viewEpoch || 0;
    const originalIndex = this.data.list.findIndex(row => row.id === id);
    const retryItem = Object.assign({}, item, { is_read: true });
    let readConfirmed = !!item.is_read;
    const open = () => this.openBusinessTarget(retryItem, result => {
      delete this._openingMessageIds[id];
      if (!this._isActiveView(viewEpoch) || this.data.view !== 'current') return;
      if (result && result.success && readConfirmed) {
        const list = this.data.list.filter(row => row.id !== id);
        this.setData(Object.assign({ list, noMore: list.length ? this.data.noMore : false }, this._statePatch({
          list,
          noMore: list.length ? this.data.noMore : false
        })));
      } else if (!this.data.list.some(row => row.id === id)) {
        const list = this.data.list.slice();
        list.splice(Math.max(0, originalIndex), 0, retryItem);
        this.setData(Object.assign({ list }, this._statePatch({ list })));
      }
    });
    if (!item.is_read) {
      api.readNotification(id)
        .then(() => {
          invalidateUnreadCount();
          readConfirmed = true;
          if (!this._isActiveView(viewEpoch)) {
            delete this._openingMessageIds[id];
            return;
          }
          const list = this.data.view === 'current'
            ? this.data.list.filter(n => n.id !== id)
            : this.data.list.map(n => n.id === id ? Object.assign({}, n, { is_read: true }) : n);
          this.setData(Object.assign({ list, noMore: list.length ? this.data.noMore : false }, this._statePatch({
            list,
            noMore: list.length ? this.data.noMore : false
          })));
          open();
        })
        .catch(() => {
          if (!this._isActiveView(viewEpoch)) {
            delete this._openingMessageIds[id];
            return;
          }
          open();
        });
    } else {
      open();
    }
  },

  onReadAll() {
    if (this._readAllPending) {
      wx.showToast({ title: '正在处理，请稍候', icon: 'none' });
      return;
    }
    if (this.data.view !== 'current' || !this.data.list.length) {
      wx.showToast({ title: '暂无未读消息', icon: 'none' });
      return;
    }
    this._readAllPending = true;
    const viewEpoch = this._viewEpoch || 0;
    api.readAllNotifications()
      .then(() => {
        this._readAllPending = false;
        invalidateUnreadCount();
        if (!this._isActiveView(viewEpoch) || this.data.view !== 'current') return;
        const list = this.data.view === 'current' ? [] : this.data.list;
        const noMore = this.data.view === 'current' ? false : this.data.noMore;
        this.setData(Object.assign({ list, noMore }, this._statePatch({ list, noMore })));
        wx.showToast({ title: '已全部已读', icon: 'success' });
      })
      .catch(() => {
        this._readAllPending = false;
        if (this._isActiveView(viewEpoch)) wx.showToast({ title: '操作失败', icon: 'none' });
      });
  },

  onSubscribe() {
    if (this._subscribing) {
      wx.showToast({ title: '正在处理，请稍候', icon: 'none' });
      return;
    }
    this._subscribing = true;
    const viewEpoch = this._viewEpoch || 0;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      this._subscribing = false;
    };
    api.subscriptionTemplates()
      .then(config => {
        if (!this._isActiveView(viewEpoch)) { finish(); return; }
        const templates = Array.isArray(config && config.templates)
          ? config.templates.filter(item => item && item.purpose && item.template_id)
          : [];
        if (!templates.length) throw new Error('NO_SUBSCRIPTION_TEMPLATE');
        wx.requestSubscribeMessage({
          tmplIds: templates.map(item => item.template_id),
          success: result => {
            const accepted = templates.filter(item => result && result[item.template_id] === 'accept');
            const banned = templates.filter(item => result && result[item.template_id] === 'ban');
            const acceptedLabels = accepted.map(item => SUBSCRIPTION_LABELS[item.purpose] || '相关通知');
            const unacceptedLabels = templates.filter(item => result && result[item.template_id] !== 'accept')
              .map(item => SUBSCRIPTION_LABELS[item.purpose] || '相关通知');
            if (!accepted.length) {
              finish();
              if (!this._isActiveView(viewEpoch)) return;
              wx.showModal({
                title: '订阅提示',
                content: (banned.length ? '消息通知已被关闭，可在小程序设置中重新开启。' : '')
                  + '本次未订阅：' + unacceptedLabels.join('、') + '。可再次点击授权。',
                showCancel: false
              });
              return;
            }
            if (!this._isActiveView(viewEpoch)) { finish(); return; }
            wx.login({
              success: loginResult => {
                if (!loginResult || !loginResult.code) {
                  finish();
                  if (this._isActiveView(viewEpoch)) wx.showToast({ title: '微信账号绑定失败，请重试', icon: 'none' });
                  return;
                }
                api.bindOpenId(loginResult.code)
                  .then(boundResult => {
                    finish();
                    if (!this._isActiveView(viewEpoch)) return;
                    if (!boundResult || boundResult.bound !== true) {
                      wx.showToast({ title: (boundResult && (boundResult.error || boundResult.warn)) || '微信账号绑定失败，请重试', icon: 'none' });
                      return;
                    }
                    if (accepted.length === templates.length) {
                      wx.showModal({ title: '订阅结果',
                        content: '已完成本次授权，可接收下一次' + acceptedLabels.join('、') + '提醒。',
                        showCancel: false });
                    } else {
                      wx.showModal({ title: '订阅结果',
                        content: '已订阅：' + acceptedLabels.join('、') + '；未订阅：'
                          + unacceptedLabels.join('、') + '。可再次点击授权。',
                        showCancel: false });
                    }
                  })
                  .catch(error => {
                    finish();
                    const message = error && (error.error || error.warn);
                    if (this._isActiveView(viewEpoch)) wx.showToast({
                      title: message || '微信账号绑定失败，请重试', icon: 'none'
                    });
                  });
              },
              fail: () => {
                finish();
                if (this._isActiveView(viewEpoch)) wx.showToast({ title: '微信账号绑定失败，请重试', icon: 'none' });
              }
            });
          },
          fail: error => {
            finish();
            const feedback = subscribeFailureFeedback(error);
            this._lastSubscribeFailure = feedback;
            console.warn('[subscription]', feedback.category, feedback.errCode || 'NO_CODE');
            if (this._isActiveView(viewEpoch)) wx.showToast({ title: feedback.message, icon: 'none' });
          }
        });
      })
      .catch(() => {
        finish();
        if (this._isActiveView(viewEpoch)) wx.showToast({ title: '订阅配置加载失败，请重试', icon: 'none' });
      });
  }
});
