const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const { requestLocation, locationErrorMessage, shouldOpenLocationSettings } = require('../../utils/location.js');
const { inventoryOptions, inventoryErrorMessage, buildPartsPayload } = require('../../utils/partsApplication.js');

const app = getApp();

const PARTS_FULFILLMENT_OPTIONS = [
  { key: 'stock', label: '库存领用' },
  { key: 'local_purchase', label: '附近急购' },
  { key: 'vendor_order', label: '厂家订购' }
];

Page({
  data: {
    siteId: null, site: null, readOnlySource: false, monitoringSource: false, monitoringLoading: false, monitoringError: '', checkingIn: false, online: true, syncCount: 0,
    partsOptions: [],
    partsInventoryStatus: 'idle', partsInventoryError: '',
    partsFulfillmentOptions: PARTS_FULFILLMENT_OPTIONS,
    partsApply: {
      open: false, fulfillmentIndex: 0, fulfillment_type: 'stock',
      part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0,
      submitting: false
    }
  },

  onLoad(options) {
    this._unloaded = false;
    this._inactive = false;
    this._hasShown = false;
    const id = options.site_id || app.globalData.selSiteId;
    const monitoringSource = options.source === 'responsible_sites_monitoring';
    const readOnlySource = options.source === 'inspection_readonly' || monitoringSource;
    this.setData({ siteId: id, readOnlySource, monitoringSource });
    if (id) this.loadSite(id);
  },

  onShow() {
    this._inactive = false;
    if (this._hasShown && this.data.siteId && (this.data.readOnlySource || !this.data.site)) this.loadSite(this.data.siteId);
    this._hasShown = true;
    this.refreshSyncState();
  },

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
    flushQueue().then((summary) => {
      wx.hideLoading();
      this.refreshSyncState(() => wx.showToast({
        title: summary && summary.rejected && summary.rejected.length ? '有操作被服务器拒绝' : (this.data.syncCount ? '仍有操作待同步' : '同步完成'),
        icon: summary && summary.rejected && summary.rejected.length ? 'none' : (this.data.syncCount ? 'none' : 'success')
      }));
    }).catch(() => {
      wx.hideLoading();
      this.refreshSyncState();
      wx.showToast({ title: '同步失败，请保持网络后重试', icon: 'none' });
    });
  },

  loadSite(id) {
    const requestId = (this._siteRequestId || 0) + 1;
    this._siteRequestId = requestId;
    if (this.data.readOnlySource) this.setData({ monitoringLoading: true, monitoringError: '' });
    const request = this.data.readOnlySource ? api.stationMonitoringOverview(id) : api.siteTasks(id);
    request.then(res => {
        if (this._unloaded || this._inactive || this._siteRequestId !== requestId) return;
        const source = this.data.readOnlySource ? (res.site || {}) : (res.site || {});
        const checkedIn = !!source.checked_in;
        this.setData({ site: Object.assign({}, source, { checked_in: checkedIn, can_check_in: !this.data.readOnlySource && !!(source.can_check_in && !checkedIn), checkin_sync_pending: false, monitoring: res.monitoring || { latest_values: [] } }), monitoringLoading: false, monitoringError: '' });
      })
      .catch(() => {
        if (this._unloaded || this._inactive || this._siteRequestId !== requestId) return;
        if (this.data.readOnlySource) this.setData({ monitoringLoading: false, monitoringError: '监测信息加载失败，请重试' });
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
    if (!this.data.readOnlySource) this.loadPartsInventory();
  },

  onRetryMonitoring() { if (this.data.siteId) this.loadSite(this.data.siteId); },

  onHide() {
    this._inactive = true;
    this._siteRequestId = (this._siteRequestId || 0) + 1;
  },

  onUnload() {
    this._unloaded = true;
    this._siteRequestId = (this._siteRequestId || 0) + 1;
    this._partsInventoryRequest = (this._partsInventoryRequest || 0) + 1;
  },

  loadPartsInventory() {
    if (this.data.partsInventoryStatus === 'loading') return;
    const requestId = (this._partsInventoryRequest || 0) + 1;
    this._partsInventoryRequest = requestId;
    this.setData({ partsInventoryStatus: 'loading', partsInventoryError: '' });
    api.partsInventory().then(parts => {
      if (this._partsInventoryRequest !== requestId) return;
      const partsOptions = inventoryOptions(parts);
      this.setData({ partsOptions, partsInventoryStatus: partsOptions.length ? 'ready' : 'empty' });
    }).catch(error => {
      if (this._partsInventoryRequest !== requestId) return;
      this.setData({ partsInventoryStatus: 'error', partsInventoryError: inventoryErrorMessage(error) });
    });
  },

  onRetryPartsInventory() { this.loadPartsInventory(); },

  onNavigate() {
    const s = this.data.site;
    if (!s || s.lat == null || s.lng == null) { wx.showToast({ title: '无坐标信息', icon: 'none' }); return; }
    wx.openLocation({
      latitude: s.lat, longitude: s.lng, name: s.name, address: s.code,
      fail() { wx.showToast({ title: '打开地图失败', icon: 'none' }); }
    });
  },

  onCheckIn() {
    const s = this.data.site;
    if (!s || this.data.checkingIn) return;
    if (s.can_check_in === false) {
      wx.showToast({ title: s.checkin_block_reason || '当前没有可执行的巡检任务', icon: 'none' });
      return;
    }
    this.setData({ checkingIn: true });
    wx.showLoading({ title: '定位中' });
    requestLocation().then(gps => {
      wx.hideLoading();
      const payload = { site_id: s.id, site_name: s.name, check_time: nowStr() };
      payload.lat = gps.lat;
      payload.lng = gps.lng;
      api.checkIn(payload)
        .then(() => this.setData({ site: Object.assign({}, this.data.site, { checked_in: true, can_check_in: false, checkin_sync_pending: false, rework_checkin_required: false }) }))
        .then(() => wx.showToast({ title: '打卡成功', icon: 'success' }))
        .catch((err) => {
          this.setData({ syncCount: queueCount() });
          if (err && err.queued) {
            this.setData({ site: Object.assign({}, this.data.site, { checked_in: true, can_check_in: false, checkin_sync_pending: true }) });
          } else {
            this.setData({ site: Object.assign({}, this.data.site, { checked_in: false, can_check_in: !!s.can_check_in, checkin_sync_pending: false }) });
          }
          wx.showToast({ title: err && err.queued ? '已离线保存，联网后自动同步' : ((err && err.error) || '打卡失败'), icon: 'none' });
        })
        .finally(() => this.setData({ checkingIn: false }));
    }).catch(error => {
      wx.hideLoading();
      const openSettings = shouldOpenLocationSettings(error);
      wx.showModal({
        title: '无法获取位置', content: locationErrorMessage(error), showCancel: false,
        confirmText: openSettings ? '去设置' : '知道了',
        success: () => { if (openSettings) wx.openSetting({}); }
      });
      this.setData({ checkingIn: false });
    });
  },

  onCalibrate() {
    const s = this.data.site;
    if (!s || !s.can_calibrate) return;
    wx.showLoading({ title: '定位中' });
    requestLocation().then(gps => {
      wx.hideLoading();
      const oldCoords = (s.lat != null && s.lng != null) ? `${Number(s.lat).toFixed(6)}, ${Number(s.lng).toFixed(6)}` : '未配置';
      const newCoords = `${Number(gps.lat).toFixed(6)}, ${Number(gps.lng).toFixed(6)}`;
      wx.showModal({
        title: '确认校准站点位置',
        content: `站点：${s.name}\n原坐标：${oldCoords}\n当前位置：${newCoords}\n校准会改变 300 米打卡范围，仅在确认站点无误且确实位于现场时操作。`,
        confirmText: '确认更新',
        cancelText: '取消',
        success: (modal) => {
          if (!modal.confirm) return;
          api.calibrate(s.id, gps.lat, gps.lng, { confirm: true, site_name: s.name })
            .then(res => {
              const d = (res && res.distance_m != null) ? res.distance_m : 0;
              wx.showToast({ title: '已校准 偏移' + d + 'm', icon: 'none' });
              this.loadSite(s.id);
            })
            .catch(error => wx.showModal({ title: '校准失败', content: (error && error.error) || '位置未修改，请重试', showCancel: false }));
        }
      });
    }).catch(error => {
      wx.hideLoading();
      wx.showToast({ title: locationErrorMessage(error), icon: 'none' });
    });
  },

  onOpenPartsApply() {
    const site = this.data.site;
    if (!site) return;
    this.setData({
      partsApply: {
        open: true, fulfillmentIndex: 0, fulfillment_type: 'stock',
        part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0,
        submitting: false, requestKey: 'parts_site_' + site.id + '_' + Date.now()
      }
    });
    if (this.data.partsInventoryStatus === 'idle' || this.data.partsInventoryStatus === 'error') this.loadPartsInventory();
  },

  onClosePartsApply() {
    if (this.data.partsApply.submitting) { wx.showToast({ title: '提交中，请稍候', icon: 'none' }); return; }
    this.setData({ 'partsApply.open': false });
  },

  onPartsFulfillmentPick(e) {
    if (this.data.partsApply.submitting) return;
    const index = parseInt(e.detail.value, 10) || 0;
    const selected = this.data.partsFulfillmentOptions[index] || this.data.partsFulfillmentOptions[0];
    const previousType = this.data.partsApply.fulfillment_type;
    const resetManualFields = selected.key === 'stock' || previousType === 'stock';
    this.setData({
      'partsApply.fulfillmentIndex': index,
      'partsApply.fulfillment_type': selected.key,
      'partsApply.index': 0,
      'partsApply.part_name': resetManualFields ? '' : this.data.partsApply.part_name,
      'partsApply.specification': resetManualFields ? '' : this.data.partsApply.specification,
      'partsApply.estimated_amount': resetManualFields ? '' : this.data.partsApply.estimated_amount
    });
  },
  onPartsFulfillmentSelect(e) {
    this.onPartsFulfillmentPick({ detail: { value: e.currentTarget.dataset.index } });
  },

  onPartsPick(e) {
    const index = parseInt(e.detail.value, 10) || 0;
    const option = this.data.partsOptions[index];
    const patch = { 'partsApply.index': index };
    if (option && option.id) patch['partsApply.part_name'] = option.part_name || '';
    this.setData(patch);
  },

  onPartsName(e) { this.setData({ 'partsApply.part_name': e.detail.value }); },
  onPartsSpecification(e) { this.setData({ 'partsApply.specification': e.detail.value }); },
  onPartsEstimatedAmount(e) { this.setData({ 'partsApply.estimated_amount': e.detail.value }); },
  onPartsQty(e) { this.setData({ 'partsApply.quantity': e.detail.value }); },
  onPartsReason(e) { this.setData({ 'partsApply.reason': e.detail.value }); },

  onSubmitPartsApply() {
    const site = this.data.site;
    const form = this.data.partsApply;
    if (form.submitting) return;
    if (!site) return;
    const result = buildPartsPayload(form, this.data.partsOptions, this.data.partsInventoryStatus);
    if (result.error) { wx.showToast({ title: result.error, icon: 'none' }); return; }
    this.setData({ 'partsApply.submitting': true });
    api.applyParts(Object.assign({ site_id: site.id, _idempotency_key: form.requestKey }, result.payload)).then(() => {
      this.setData({ 'partsApply.open': false, 'partsApply.submitting': false });
      wx.showToast({ title: '已提交审批', icon: 'success' });
    }).catch(err => {
      this.setData({ 'partsApply.submitting': false });
      wx.showToast({ title: (err && err.error) || '提交失败', icon: 'none' });
    });
  }
});
