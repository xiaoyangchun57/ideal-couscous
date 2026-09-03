const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const { captureFlushedPhoto } = require('../../utils/photos.js');

const app = getApp();

function decorate(a) {
  const metricCn = maps.metricCn(a.metric);
  const isManual = a.metric === 'manual_report';
  // 人工上报类告警：message 是实际内容，替代"指标名"语义
  const display_metric = isManual
    ? (a.message || '人工上报告警')
    : (metricCn || a.metric || '告警详情');
  return Object.assign({}, a, {
    level_cn: maps.map(maps.ALERT_LEVEL, a.level),
    level_cls: maps.alertLevelCls(a.level),
    status_cn: maps.map(maps.ALERT_STATUS, a.status),
    metric_cn: metricCn,
    display_metric: display_metric,
    is_manual: isManual
  });
}

Page({
  data: { tab: 'pending', list: [], loaded: false, listStale: false, sheet: { open: false, item: null }, detailLoading: false, detailError: '', acting: false, online: true, syncCount: 0 },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.refreshSyncState();
    const focusedId = app.globalData.selAlertId;
    if (focusedId != null) {
      app.globalData.selAlertId = null;
      this.openAlertDetail({ id: focusedId });
    }
    this.setAlertTabBarHidden(!!(this.data.sheet && this.data.sheet.open));
    this.load();
  },

  onHide() { this.setAlertTabBarHidden(false); },

  onUnload() {
    this._unloaded = true;
    this.invalidateAlertDetailRequest();
    this.setAlertTabBarHidden(false);
  },

  setAlertTabBarHidden(hidden) {
    if (this._alertTabBarHidden === hidden) return;
    const method = hidden ? 'hideTabBar' : 'showTabBar';
    if (typeof wx[method] === 'function') wx[method]({ animation: false });
    this._alertTabBarHidden = hidden;
  },

  invalidateAlertDetailRequest() {
    this._detailRequest = (this._detailRequest || 0) + 1;
  },

  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  refreshSyncState(done) {
    wx.getNetworkType({
      success: (res) => {
        this.setData({ online: res.networkType !== 'none', syncCount: queueCount() });
        if (done) done();
      },
      fail: () => {
        this.setData({ syncCount: queueCount() });
        if (done) done();
      }
    });
  },

  onSyncNow() {
    if (!this.data.syncCount) return;
    wx.showLoading({ title: '同步中' });
    flushQueue(captureFlushedPhoto);
    setTimeout(() => {
      wx.hideLoading();
      this.refreshSyncState(() => {
        if (!this.data.syncCount) this.load();
        wx.showToast({ title: this.data.syncCount ? '仍有操作待同步' : '同步完成', icon: this.data.syncCount ? 'none' : 'success' });
      });
    }, 1000);
  },

  onTab(e) {
    const t = e.currentTarget.dataset.t;
    this.setData({ tab: t });
    this.load();
  },

  load(done) {
    const status = app.globalData.selAlertId != null ? '' : (this.data.tab === 'pending' ? 'pending' : '');
    api.alerts(status)
      .then(res => {
        const list = (res || []).map(decorate);
        this.setData({ list, loaded: true, listStale: false });
        if (done) done();
      })
      .catch(() => { this.setData({ loaded: true, listStale: this.data.list.length > 0 }); if (done) done(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  openAlertDetail(seed) {
    const alertId = seed && seed.id;
    if (alertId === undefined || alertId === null || alertId === '') return;
    const requestId = (this._detailRequest || 0) + 1;
    this._detailRequest = requestId;
    const loadingItem = Object.assign({}, seed || {}, {
      primary_action: null,
      related_workorder_target: null,
      can_view: false,
      detailStale: true,
    });
    this.setData({ sheet: { open: true, item: loadingItem }, detailLoading: true, detailError: '' });
    this.setAlertTabBarHidden(true);
    api.alertDetail(alertId).then(item => {
      if (!this.isCurrentAlertDetailRequest(requestId, alertId)) return;
      this.setData({ sheet: { open: true, item: Object.assign(decorate(item || {}), { detailStale: false }) }, detailLoading: false });
    }).catch(err => {
      if (!this.isCurrentAlertDetailRequest(requestId, alertId)) return;
      const message = (err && err.error) || '告警详情加载失败';
      this.setData({ detailLoading: false, detailError: message, 'sheet.item.detailStale': true });
      wx.showToast({ title: message, icon: 'none' });
    });
  },

  isCurrentAlertDetailRequest(requestId, alertId) {
    const item = this.data.sheet && this.data.sheet.item;
    return !this._unloaded && this._detailRequest === requestId && this.data.sheet.open && item
      && String(item.id) === String(alertId);
  },

  onOpen(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find(a => a.id === id);
    if (item) this.openAlertDetail(item);
  },
  onClose() {
    if (this.data.acting) { wx.showToast({ title: '正在打开关联工单，请稍候', icon: 'none' }); return; }
    this.invalidateAlertDetailRequest();
    this.setData({ 'sheet.open': false, detailLoading: false });
    this.setAlertTabBarHidden(false);
  },
  onCloseMask() {
    if (this.data.acting) {
      wx.showToast({ title: '正在打开关联工单，请稍候', icon: 'none' });
      return false;
    }
    this.invalidateAlertDetailRequest();
    this.setData({ 'sheet.open': false, detailLoading: false });
    this.setAlertTabBarHidden(false);
    return true;
  },

  onPrimaryAction() {
    if (this.data.acting) {
      wx.showToast({ title: '正在打开关联工单，请稍候', icon: 'none' });
      return false;
    }
    const item = this.data.sheet.item || {};
    const target = item.related_workorder_target || {};
    const orderNo = String(target.order_no || '').trim();
    if (!this.data.sheet.open || this.data.detailLoading || item.detailStale || item.can_view !== true || !item.primary_action || !orderNo) {
      wx.showToast({
        title: item.block_reason || '关联工单信息不可用，请刷新后重试',
        icon: 'none'
      });
      return false;
    }
    this.setData({ acting: true });
    app.globalData.selWorkorderNo = orderNo;
    wx.navigateTo({
      url: '/pages/workorder/workorder',
      success: () => this.setAlertTabBarHidden(false),
      fail: () => {
        if (app.globalData.selWorkorderNo === orderNo) app.globalData.selWorkorderNo = null;
        this.setAlertTabBarHidden(true);
        wx.showToast({ title: '打开关联工单失败，请重试', icon: 'none' });
      },
      complete: () => this.setData({ acting: false })
    });
    return true;
  }
});
