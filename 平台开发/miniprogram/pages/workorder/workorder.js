const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const {
  chooseInspectionPhotos, fileToBase64, captureFlushedPhoto,
  isPhotoSelectionCancelled, photoCaptureErrorMessage, shouldOpenCameraSettings,
} = require('../../utils/photos.js');
const { resolveUploadUrl, uploadStoragePath } = require('../../utils/url.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const { requestLocation, locationErrorMessage, shouldOpenLocationSettings } = require('../../utils/location.js');
const { inventoryOptions, inventoryErrorMessage, buildPartsPayload } = require('../../utils/partsApplication.js');
const { invalidateUnreadCount } = require('../../utils/notificationCount.js');

const app = getApp();

// 状态分组的筛选映射（覆盖后端全部 7 种状态，避免漏显）
const TAB_GROUPS = {
  all: null,
  pending: ['pending'],
  in_progress: ['accepted', 'dispatched', 'in_progress'],
  reviewing: ['reviewing'],
  closed: ['closed', 'resolved']
};

Page({
  data: {
    list: [], all: [], loaded: false,
    tab: 'all',
    tabs: [
      { key: 'all', label: '全部' },
      { key: 'pending', label: '待受理' },
      { key: 'in_progress', label: '进行中' },
      { key: 'reviewing', label: '待核验' },
      { key: 'closed', label: '已完成' }
    ],
    sheet: { open: false, item: null }, detailLoading: false, detailError: '', isAdmin: false, acting: false,
    resolutionNote: '',
    online: true, syncCount: 0,
    // 关联下拉选项（可选，不指定则纯文字兜底）
    vehicleOptions: [{ id: 0, label: '暂无可用车辆' }],
    partsOptions: [],
    partsInventoryStatus: 'idle', partsInventoryError: '',
    // 极简申请弹层（含关联下标）
    vehicleApply: { open: false, reason: '', index: 0, noVehicleRequired: false, exceptionReason: '' },
    partsFulfillmentOptions: [
      { key: 'stock', label: '使用现有库存' },
      { key: 'local_purchase', label: '附近紧急购买' },
      { key: 'vendor_order', label: '厂家订购' }
    ],
    partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false, requestKey: '' }
  },

  onLoad(options) {
    const orderNo = String(options && options.order_no || '').trim();
    if (orderNo && orderNo.length <= 100) app.globalData.selWorkorderNo = orderNo;
    const notificationId = Number(options && options.notification_id);
    this._resultNotificationId = Number.isInteger(notificationId) && notificationId > 0 ? notificationId : null;
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    const u = getUser() || {};
    const roles = u.roles || [u.role || ''];
    this.setData({
      isAdmin: roles.includes('admin')
    });
    this.refreshSyncState();
    const focusedOrderNo = app.globalData.selWorkorderNo;
    if (focusedOrderNo) {
      app.globalData.selWorkorderNo = null;
      this.openWorkorderSheet({ order_no: focusedOrderNo });
    }
    this.load();
    this.loadLists();
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

  handleWriteFailure(err, fallback, closePath) {
    const queued = !!(err && err.queued);
    const patch = { acting: false, syncCount: queueCount() };
    if (queued && closePath) patch[closePath] = false;
    this.setData(patch);
    wx.showToast({
      title: queued ? '已离线保存，联网后自动同步' : ((err && err.error) || fallback),
      icon: 'none'
    });
  },

  filter(all, tab) {
    const set = TAB_GROUPS[tab];
    if (!set) return all;
    return all.filter(w => set.indexOf(w.status) >= 0);
  },

  openWorkorderSheet(seed) {
    const orderNo = String(seed && seed.order_no || '').trim();
    if (!orderNo) return;
    const requestId = (this._detailRequest || 0) + 1;
    this._detailRequest = requestId;
    this.setData({ sheet: { open: true, item: seed || null }, detailLoading: true, detailError: '' });
    api.workorderDetail(orderNo).then(item => {
      if (this._detailRequest !== requestId) return null;
      const mapped = maps.workorderCn(item || {});
      const flowEvents = Array.isArray(mapped.flow_events) ? mapped.flow_events : [];
      this.setData({
        sheet: { open: true, item: Object.assign({}, mapped, {
          flowEvents, flowUnavailable: !Array.isArray(mapped.flow_events), detailStale: false,
        }) },
        detailLoading: false,
        resolutionNote: mapped.remark || '',
      });
      this._markResultNotificationRead();
      return api.workorderRelated(orderNo).catch(() => ({ unavailable: true }));
    }).then(related => {
      if (!related || this._detailRequest !== requestId || !this.data.sheet.item
          || String(this.data.sheet.item.order_no) !== orderNo) return;
      this.setData({ 'sheet.item.related': related, 'sheet.item.relatedUnavailable': !!related.unavailable });
    }).catch(err => {
      if (this._detailRequest !== requestId) return;
      const hasSeed = !!(seed && seed.order_no);
      this.setData({ detailLoading: false, detailError: (err && err.error) || '工单详情加载失败',
        'sheet.item.detailStale': hasSeed });
      wx.showToast({ title: (err && err.error) || '工单详情加载失败', icon: 'none' });
    });
  },

  _markResultNotificationRead() {
    const notificationId = this._resultNotificationId;
    if (!notificationId || this._resultNotificationRead) return;
    this._resultNotificationRead = true;
    api.readNotification(notificationId).then(() => invalidateUnreadCount())
      .catch(() => { this._resultNotificationRead = false; });
  },

  load(one) {
    api.workorders()
      .then(res => {
        const all = (res || []).map(maps.workorderCn);
        this.setData({ all, list: this.filter(all, this.data.tab), loaded: true });
        if (one) one();
      })
      .catch(() => { this.setData({ loaded: true }); if (one) one(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  onTab(e) {
    const tab = e.currentTarget.dataset.t;
    if (tab === this.data.tab) return;
    this.setData({ tab, list: this.filter(this.data.all, tab) });
  },

  onOpen(e) {
    const no = e.currentTarget.dataset.no;
    const item = this.data.list.find(w => w.order_no === no);
    if (item) this.openWorkorderSheet(item);
  },
  onClose() {
    if (this.data.acting) { wx.showToast({ title: '操作提交中，请稍候', icon: 'none' }); return; }
    this.setData({ 'sheet.open': false });
  },
  onCloseVehicle() { this.setData({ 'vehicleApply.open': false }); },
  onCloseParts() {
    if (this.data.partsApply.submitting) { wx.showToast({ title: '提交中，请稍候', icon: 'none' }); return; }
    this.setData({ 'partsApply.open': false });
  },
  onResolutionNote(e) { this.setData({ resolutionNote: e.detail.value }); },

  afterAction(tip) {
    this.setData({ acting: false, 'sheet.open': false, vehicleApply: { open: false, reason: '', index: 0, noVehicleRequired: false, exceptionReason: '' }, partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0 } });
    wx.showToast({ title: tip, icon: 'success' });
    this.load();
  },

  doAccept() {
    const item = this.data.sheet.item || {};
    if (item.detailStale || item.actions && item.actions.primary !== 'accept') { wx.showToast({ title: item.block_reason || '请刷新后重试', icon: 'none' }); return; }
    const no = item.order_no;
    this.setData({ acting: true });
    api.updateWorkorderStatus(no, 'accepted')
      .then(() => this.afterAction('已接单'))
      .catch((err) => this.handleWriteFailure(err, '操作失败', 'sheet.open'));
  },

  // 到场签到：GPS 围栏由后端校验（距站点 ≤500m）
  onCheckIn() {
    const item = this.data.sheet.item;
    if (!item || item.detailStale || item.actions && item.actions.primary !== 'check_in') { wx.showToast({ title: (item && item.block_reason) || '请刷新后重试', icon: 'none' }); return; }
    wx.showLoading({ title: '定位中' });
    requestLocation().then(gps => {
      wx.hideLoading();
      const payload = { order_no: item.order_no, site_id: item.site_id, site_name: item.site_name, check_time: nowStr() };
      payload.lat = gps.lat;
      payload.lng = gps.lng;
      api.checkIn(payload)
        .then(() => {
          wx.showToast({ title: '已到场签到', icon: 'success' });
          this.setData({ 'sheet.item.check_in_time': nowStr(), 'sheet.item.checked_in': true });
        })
        .catch((err) => this.handleWriteFailure(err, '签到失败'));
    }).catch(error => {
      wx.hideLoading();
      const openSettings = shouldOpenLocationSettings(error);
      wx.showModal({
        title: '无法获取位置', content: locationErrorMessage(error), showCancel: false,
        confirmText: openSettings ? '去设置' : '知道了',
        success: () => { if (openSettings) wx.openSetting({}); }
      });
    });
  },

  doStart() {
    const item = this.data.sheet.item;
    if (!item || item.detailStale || item.actions && item.actions.primary !== 'start') { wx.showToast({ title: (item && item.block_reason) || '请刷新后重试', icon: 'none' }); return; }
    const no = item.order_no;
    this.setData({ acting: true });
    api.updateWorkorderStatus(no, 'in_progress', { client: 'mobile' })
      .then(() => this.afterAction('处置中'))
      .catch((err) => this.handleWriteFailure(err, '操作失败', 'sheet.open'));
  },

  // 处置影像上传（追加到工单 images）
  onUploadImage() {
    const item = this.data.sheet.item;
    if (!item) return;
    const remain = 6 - (item.images_arr ? item.images_arr.length : 0);
    if (remain <= 0) { wx.showToast({ title: '最多 6 张', icon: 'none' }); return; }
    requestLocation().catch(error => { throw Object.assign({}, error || {}, { capturePhase: 'location' }); })
      .then(gps => api.createPhotoCaptureSession({
        site_id: item.site_id, order_no: item.order_no,
        gps_lat: gps.lat, gps_lng: gps.lng,
      }).then(session => chooseInspectionPhotos(1, 'camera')
        .then(paths => ({ paths, session, gps }))))
      .then(({ paths, session, gps }) => {
        if (!paths.length) return;
        wx.showLoading({ title: '上传中' });
        const tasks = paths.map((p, index) => {
          const localId = String(p || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-24);
          const idempotencyKey = 'workorder_photo_' + item.order_no + '_' + Date.now()
            + '_' + index + '_' + localId;
          return fileToBase64(p).then(b64 => api.uploadWorkorderImage(
          item.order_no, b64, idempotencyKey, {
            capture_source: 'camera', capture_session: session.capture_session, taken_at: nowStr(),
            ...(gps ? { gps_lat: gps.lat, gps_lng: gps.lng } : {}),
          }
        ).then(r => resolveUploadUrl(r.url)));
        });
        Promise.allSettled(tasks).then(results => {
          wx.hideLoading();
          const urls = results.filter(r => r.status === 'fulfilled' && r.value).map(r => r.value);
          if (urls.length) {
            const arr = (this.data.sheet.item.images_arr || []).concat(urls);
            this.setData({ 'sheet.item.images_arr': arr, 'sheet.item.has_images': arr.length > 0, 'sheet.item.images': JSON.stringify(arr) });
          }
          if (results.some(r => r.status === 'rejected')) {
            this.setData({ syncCount: queueCount() });
            const firstFailure = results.find(r => r.status === 'rejected');
            const message = queueCount() ? '部分影像待同步'
              : photoCaptureErrorMessage(firstFailure && firstFailure.reason);
            wx.showToast({ title: message, icon: 'none' });
          }
        }).catch(() => wx.hideLoading());
      }).catch((error) => {
        wx.hideLoading();
        if (isPhotoSelectionCancelled(error)) return;
        const locationFailure = error && error.capturePhase === 'location';
        const openSettings = locationFailure
          ? shouldOpenLocationSettings(error) : shouldOpenCameraSettings(error);
        wx.showModal({
          title: locationFailure ? '无法获取位置' : '现场拍摄未启动',
          content: locationFailure ? locationErrorMessage(error) : photoCaptureErrorMessage(error),
          showCancel: false,
          confirmText: openSettings ? '去设置' : '知道了',
          success: () => { if (openSettings) wx.openSetting({}); },
        });
      });
  },

  onPreviewImage(e) {
    const src = e.currentTarget.dataset.src;
    const urls = this.data.sheet.item.images_arr || [];
    wx.previewImage({ urls, current: src });
  },

  onDeleteImage(e) {
    const item = this.data.sheet.item;
    const src = e.currentTarget.dataset.src;
    if (!item || !src) return;
    wx.showModal({
      title: '删除影像', content: '确认删除这张处置影像？', confirmColor: '#d14343',
      success: (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: '删除中' });
        const storedPath = uploadStoragePath(src);
        if (!storedPath) {
          wx.hideLoading();
          wx.showToast({ title: '影像地址无效，请刷新后重试', icon: 'none' });
          return;
        }
        api.deleteWorkorderImage(item.order_no, storedPath)
          .then(() => {
            wx.hideLoading();
            const arr = (this.data.sheet.item.images_arr || []).filter(url => url !== src);
            this.setData({ 'sheet.item.images_arr': arr, 'sheet.item.has_images': arr.length > 0, 'sheet.item.images': JSON.stringify(arr) });
            wx.showToast({ title: '已删除', icon: 'success' });
          })
          .catch((err) => { wx.hideLoading(); wx.showToast({ title: (err && err.error) || '删除失败', icon: 'none' }); });
      }
    });
  },

  doReview() {
    const item = this.data.sheet.item;
    if (!item || item.detailStale || item.actions && item.actions.primary !== 'submit_review') { wx.showToast({ title: (item && item.block_reason) || '请刷新后重试', icon: 'none' }); return; }
    if (!item.has_images) { wx.showToast({ title: '请先上传处置影像', icon: 'none' }); return; }
    const resolutionNote = (this.data.resolutionNote || '').trim();
    if (!resolutionNote) { wx.showToast({ title: '请填写现场处置说明', icon: 'none' }); return; }
    const no = item.order_no;
    this.setData({ acting: true });
    api.submitWorkorderReview(no, resolutionNote)
      .then(() => this.afterAction('已提交核验'))
      .catch((err) => this.handleWriteFailure(err, '操作失败', 'sheet.open'));
  },

  doApprove() {
    const no = this.data.sheet.item.order_no;
    this.setData({ acting: true });
    api.approveWorkorder(no)
      .then(() => this.afterAction('已核验通过'))
      .catch((err) => this.handleWriteFailure(err, '操作失败', 'sheet.open'));
  },

  doReject() {
    const no = this.data.sheet.item.order_no;
    wx.showModal({
      title: '退回修改',
      editable: true,
      placeholderText: '请填写需补充或整改的内容',
      content: '请说明退回原因，现场人员会据此补充整改。',
      confirmText: '退回',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ acting: true });
        api.rejectWorkorder(no, res.content)
          .then(() => this.afterAction('已退回'))
          .catch((err) => this.handleWriteFailure(err, '操作失败', 'sheet.open'));
      }
    });
  },

  // ---- 关联下拉数据（可选，不指定则纯文字兜底） ----
  loadLists() {
    api.vehicles()
      .then(vs => {
        const availableVehicles = (vs || []).filter(v => v.dispatchable).map(v => ({
          id: v.id,
          label: (v.plate_no || '未上牌') + (v.model ? '（车型：' + v.model + '）' : '')
        }));
        const vehicleOptions = availableVehicles.length ? availableVehicles : [{ id: 0, label: '暂无可用车辆' }];
        this.setData({ vehicleOptions });
      })
      .catch(() => {});
    this.loadPartsInventory();
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

  // ---- 工单资源申请弹层 ----
  onApplyVehicle() { this.setData({ vehicleApply: { open: true, reason: '', index: 0, noVehicleRequired: false, exceptionReason: '' } }); },
  onVehicleReason(e) { this.setData({ 'vehicleApply.reason': e.detail.value }); },
  onVehiclePick(e) { this.setData({ 'vehicleApply.index': parseInt(e.detail.value, 10) }); },
  onNoVehicleRequired(e) { this.setData({ 'vehicleApply.noVehicleRequired': !!e.detail.value }); },
  onVehicleExceptionReason(e) { this.setData({ 'vehicleApply.exceptionReason': e.detail.value }); },
  submitVehicle() {
    const va = this.data.vehicleApply;
    const reason = (va.reason || '').trim();
    if (!reason) { wx.showToast({ title: '请填写用车事由', icon: 'none' }); return; }
    const item = this.data.sheet.item;
    const opt = this.data.vehicleOptions[va.index];
    const vehicle_id = (!va.noVehicleRequired && opt && opt.id) ? opt.id : null;
    const exceptionReason = (va.exceptionReason || '').trim();
    if (!va.noVehicleRequired && !vehicle_id) { wx.showToast({ title: '请选择可用车辆', icon: 'none' }); return; }
    if (va.noVehicleRequired && !exceptionReason) { wx.showToast({ title: '请填写无需用车原因', icon: 'none' }); return; }
    wx.showLoading({ title: '提交中' });
    api.applyVehicle({
      site_id: item.site_id, work_order_no: item.order_no, reason, vehicle_id,
      no_vehicle_required: va.noVehicleRequired,
      vehicle_exception_reason: va.noVehicleRequired ? exceptionReason : ''
    })
      .then(() => { wx.hideLoading(); wx.showToast({ title: '用车申请已提交', icon: 'success' }); this.setData({ 'vehicleApply.open': false }); })
      .catch((err) => { wx.hideLoading(); this.handleWriteFailure(err, '提交失败', 'vehicleApply.open'); });
  },

  onApplyParts() {
    this.setData({ partsApply: { open: true, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false, requestKey: 'parts_workorder_' + Date.now() } });
    if (this.data.partsInventoryStatus === 'idle' || this.data.partsInventoryStatus === 'error') this.loadPartsInventory();
  },
  onPartsFulfillmentPick(e) {
    if (this.data.partsApply.submitting) return;
    const idx = parseInt(e.detail.value, 10) || 0;
    const selected = this.data.partsFulfillmentOptions[idx] || this.data.partsFulfillmentOptions[0];
    const previousType = this.data.partsApply.fulfillment_type;
    const resetManualFields = selected.key === 'stock' || previousType === 'stock';
    this.setData({
      'partsApply.fulfillmentIndex': idx,
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
  onPartsName(e) { this.setData({ 'partsApply.part_name': e.detail.value }); },
  onPartsSpecification(e) { this.setData({ 'partsApply.specification': e.detail.value }); },
  onPartsEstimatedAmount(e) { this.setData({ 'partsApply.estimated_amount': e.detail.value }); },
  onPartsQty(e) { this.setData({ 'partsApply.quantity': e.detail.value }); },
  onPartsReason(e) { this.setData({ 'partsApply.reason': e.detail.value }); },
  onPartsPick(e) {
    const idx = parseInt(e.detail.value, 10);
    const opt = this.data.partsOptions[idx];
    const patch = { 'partsApply.index': idx };
    if (opt && opt.id) patch['partsApply.part_name'] = opt.part_name || '';  // 选库存项自动带出名称
    this.setData(patch);
  },
  submitParts() {
    const pa = this.data.partsApply;
    if (pa.submitting) return;
    const item = this.data.sheet.item;
    const result = buildPartsPayload(pa, this.data.partsOptions, this.data.partsInventoryStatus);
    if (result.error) { wx.showToast({ title: result.error, icon: 'none' }); return; }
    this.setData({ 'partsApply.submitting': true });
    wx.showLoading({ title: '提交中' });
    api.applyParts(Object.assign({ site_id: item.site_id, work_order_no: item.order_no,
      _idempotency_key: pa.requestKey }, result.payload))
      .then(() => { wx.hideLoading(); wx.showToast({ title: '已提交审批', icon: 'success' }); this.setData({ 'partsApply.open': false, 'partsApply.submitting': false }); })
      .catch((err) => { wx.hideLoading(); this.setData({ 'partsApply.submitting': false }); this.handleWriteFailure(err, '提交失败'); });
  }
});
