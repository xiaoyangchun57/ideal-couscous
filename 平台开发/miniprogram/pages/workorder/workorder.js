const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const {
  fileToBase64, captureFlushedPhoto, photoCaptureErrorMessage,
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
    sheet: { open: false, item: null }, detailLoading: false, detailError: '', relatedLoading: false, isAdmin: false, acting: false,
    resolutionNote: '',
    workorderPhotoDraft: null,
    online: true, syncCount: 0,
    // 关联下拉选项（可选，不指定则纯文字兜底）
    vehicleOptions: [{ id: 0, label: '暂无可用车辆' }],
    partsOptions: [],
    partsInventoryStatus: 'idle', partsInventoryError: '',
    // 极简申请弹层（含关联下标）
    vehicleApply: { open: false, requiresVehicle: false, reason: '', index: 0, submitting: false, requestKey: '' },
    partsFulfillmentOptions: [
      { key: 'stock', label: '使用现有库存' },
      { key: 'local_purchase', label: '附近紧急购买' },
      { key: 'vendor_order', label: '厂家订购' }
    ],
    partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false, requestKey: '' }
  },

  onLoad(options) {
    this._alive = true;
    this._workorderPhotoDrafts = Object.create(null);
    const orderNo = String(options && options.order_no || '').trim();
    if (orderNo && orderNo.length <= 100) app.globalData.selWorkorderNo = orderNo;
    const notificationId = Number(options && options.notification_id);
    this._resultNotificationId = Number.isInteger(notificationId) && notificationId > 0 ? notificationId : null;
  },

  onUnload() {
    this._alive = false;
    this._trustedCameraRequest = null;
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
    this.setData({
      sheet: { open: true, item: seed || null },
      detailLoading: true,
      detailError: '',
      workorderPhotoDraft: this._photoDraftForOrder(orderNo),
    });
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
         workorderPhotoDraft: this._photoDraftForOrder(orderNo),
      });
      this._markResultNotificationRead();
      return api.workorderRelated(orderNo).catch(() => ({ unavailable: true }));
    }).then(related => {
      if (!related || this._detailRequest !== requestId || !this.data.sheet.item
          || String(this.data.sheet.item.order_no) !== orderNo) return;
      this.setData({
        'sheet.item.related': {
          parts: Array.isArray(related.parts) ? related.parts : [],
          recycles: Array.isArray(related.recycles) ? related.recycles : [],
          vehicles: Array.isArray(related.vehicles) ? related.vehicles : [],
        },
        'sheet.item.relatedUnavailable': !!related.unavailable,
      });
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
  onCloseVehicle() {
    if (this.data.vehicleApply.submitting) { wx.showToast({ title: '提交中，请稍候', icon: 'none' }); return; }
    this.setData({ 'vehicleApply.open': false });
  },
  onCloseParts() {
    if (this.data.partsApply.submitting) { wx.showToast({ title: '提交中，请稍候', icon: 'none' }); return; }
    this.setData({ 'partsApply.open': false });
  },
  onResolutionNote(e) { this.setData({ resolutionNote: e.detail.value }); },

  _refreshRelated(orderNo) {
    return api.workorderRelated(orderNo).then(related => {
      const item = this.data.sheet && this.data.sheet.item;
      if (!item || String(item.order_no || '') !== String(orderNo || '')) return related;
      this.setData({
        'sheet.item.related': {
          parts: Array.isArray(related && related.parts) ? related.parts : [],
          recycles: Array.isArray(related && related.recycles) ? related.recycles : [],
          vehicles: Array.isArray(related && related.vehicles) ? related.vehicles : [],
        },
        'sheet.item.relatedUnavailable': false,
      });
      return related;
    }).catch(error => {
      const item = this.data.sheet && this.data.sheet.item;
      if (item && String(item.order_no || '') === String(orderNo || '')) {
        this.setData({ 'sheet.item.relatedUnavailable': true });
      }
      throw error;
    });
  },

  _refreshOpenWorkorderDetail(orderNo, successTip) {
    const current = this.data.sheet && this.data.sheet.item;
    return api.workorderDetail(orderNo).then(item => {
      const openItem = this.data.sheet && this.data.sheet.item;
      if (!openItem || String(openItem.order_no || '') !== String(orderNo || '')) return null;
      const mapped = maps.workorderCn(item || {});
      const flowEvents = Array.isArray(mapped.flow_events) ? mapped.flow_events : [];
      this.setData({
        sheet: { open: true, item: Object.assign({}, mapped, {
          related: openItem.related || (current && current.related),
          relatedUnavailable: !!openItem.relatedUnavailable,
          flowEvents,
          flowUnavailable: !Array.isArray(mapped.flow_events),
          detailStale: false,
        }) },
        detailLoading: false,
        detailError: '',
        acting: false,
        workorderPhotoDraft: this._photoDraftForOrder(orderNo),
      });
      if (successTip) wx.showToast({ title: successTip, icon: 'success' });
      this._refreshRelated(orderNo).catch(() => {});
      return mapped;
    });
  },

  onRefreshWorkorderDetail() {
    const item = this.data.sheet && this.data.sheet.item;
    if (!item || !item.order_no || this.data.detailLoading) return;
    this.setData({ detailLoading: true, detailError: '正在刷新工单详情' });
    this._refreshOpenWorkorderDetail(item.order_no)
      .catch(error => {
        this.setData({
          detailLoading: false,
          detailError: (error && error.error) || '详情刷新失败，签到结果已保留，请稍后重试',
          'sheet.item.detailStale': true,
        });
        wx.showToast({ title: '详情刷新失败，请稍后重试', icon: 'none' });
      });
  },

  onRefreshRelated() {
    const item = this.data.sheet && this.data.sheet.item;
    if (!item || !item.order_no || this.data.relatedLoading) return Promise.resolve();
    this.setData({ relatedLoading: true });
    return this._refreshRelated(item.order_no)
      .then(() => {
        this.setData({ relatedLoading: false });
        wx.showToast({ title: '关联资源已刷新', icon: 'success' });
      })
      .catch(error => {
        this.setData({ relatedLoading: false });
        wx.showToast({ title: (error && error.error) || '关联资源刷新失败', icon: 'none' });
      });
  },

  afterAction(tip) {
    this.setData({ acting: false, 'sheet.open': false,
      vehicleApply: { open: false, requiresVehicle: false, reason: '', index: 0, submitting: false, requestKey: '' },
      partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false, requestKey: '' } });
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
    return requestLocation().then(gps => {
      wx.hideLoading();
      const payload = { order_no: item.order_no, site_id: item.site_id, site_name: item.site_name, check_time: nowStr() };
      payload.lat = gps.lat;
      payload.lng = gps.lng;
      this.setData({ acting: true });
      api.checkIn(payload)
        .then(() => {
          const checkedInAt = nowStr();
          this.setData({
            acting: false,
            detailLoading: true,
            detailError: '签到已成功，正在刷新工单动作',
            'sheet.item.check_in_time': checkedInAt,
            'sheet.item.checked_in': true,
            'sheet.item.detailStale': true,
          });
          return this._refreshOpenWorkorderDetail(item.order_no, '已到场签到')
            .catch(error => {
              this.setData({
                acting: false,
                detailLoading: false,
                detailError: (error && error.error)
                  ? `签到已成功；${error.error}` : '签到已成功，但详情刷新失败，请点击刷新详情',
                'sheet.item.detailStale': true,
              });
              wx.showToast({ title: '签到成功，请刷新详情', icon: 'none' });
            });
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

  _photoDraftForOrder(orderNo) {
    const drafts = this._workorderPhotoDrafts || Object.create(null);
    return drafts[String(orderNo || '')] || null;
  },

  _setWorkorderPhotoDraft(draft) {
    if (!draft || !draft.orderNo) return;
    if (!this._workorderPhotoDrafts) this._workorderPhotoDrafts = Object.create(null);
    this._workorderPhotoDrafts[draft.orderNo] = draft;
    const item = this.data.sheet && this.data.sheet.item;
    if (item && String(item.order_no) === draft.orderNo) {
      this.setData({ workorderPhotoDraft: draft });
    }
  },

  _clearWorkorderPhotoDraft(orderNo, idempotencyKey) {
    const key = String(orderNo || '');
    const draft = this._photoDraftForOrder(key);
    if (!draft || (idempotencyKey && draft.idempotencyKey !== idempotencyKey)) return;
    delete this._workorderPhotoDrafts[key];
    const item = this.data.sheet && this.data.sheet.item;
    if (item && String(item.order_no) === key) this.setData({ workorderPhotoDraft: null });
  },

  _matchesCurrentWorkorder(context) {
    const item = this.data.sheet && this.data.sheet.item;
    return this._alive !== false && !!item && !!this.data.sheet.open
      && String(item.order_no || '') === context.orderNo
      && String(item.site_id || '') === context.siteId;
  },

  _openTrustedCamera(item) {
    const orderNo = String(item.order_no || '').trim();
    const siteId = String(item.site_id || '').trim();
    if (!orderNo || !siteId) {
      wx.showToast({ title: '工单站点信息不完整，请刷新后重试', icon: 'none' });
      return;
    }
    const requestId = 'workorder_camera_' + orderNo + '_' + Date.now()
      + '_' + ((this._trustedCameraSequence || 0) + 1);
    this._trustedCameraSequence = (this._trustedCameraSequence || 0) + 1;
    const context = { requestId, orderNo, siteId };
    this._trustedCameraRequest = context;
    const label = '工单 ' + orderNo + ' · ' + String(item.display_title || '工单事项');
    const url = '/pages/trusted-camera/trusted-camera?request_id=' + encodeURIComponent(requestId)
      + '&site_id=' + encodeURIComponent(siteId)
      + '&order_no=' + encodeURIComponent(orderNo)
      + '&site_name=' + encodeURIComponent(String(item.site_name || ''))
      + '&item_name=' + encodeURIComponent(label);
    wx.navigateTo({
      url,
      events: {
        trustedCameraCaptured: capture => this._handleTrustedCameraCaptured(capture, context),
        trustedCameraCancelled: () => {
          if (this._trustedCameraRequest === context) this._trustedCameraRequest = null;
        },
      },
      fail: () => {
        if (this._trustedCameraRequest === context) this._trustedCameraRequest = null;
        wx.showToast({ title: '现场相机打开失败，请重试', icon: 'none' });
      },
    });
  },

  _handleTrustedCameraCaptured(capture, context) {
    if (!capture || String(capture.requestId || '') !== context.requestId
        || this._trustedCameraRequest !== context || !this._matchesCurrentWorkorder(context)) return;
    this._trustedCameraRequest = null;
    if (!capture.path || !capture.captureSession) {
      wx.showToast({ title: '拍摄结果不完整，请重新拍摄', icon: 'none' });
      return;
    }
    const localId = String(capture.path).replace(/[^a-zA-Z0-9_-]/g, '').slice(-24);
    const idempotencyKey = 'workorder_photo_' + context.orderNo + '_' + Date.now()
      + '_' + localId;
    const draft = {
      requestId: context.requestId,
      orderNo: context.orderNo,
      siteId: context.siteId,
      path: capture.path,
      idempotencyKey,
      metadata: {
        capture_source: 'camera',
        capture_session: capture.captureSession,
        gps_lat: capture.gpsLat,
        gps_lng: capture.gpsLng,
        taken_at: nowStr(),
        capture_client: 'trusted_camera_v1',
        capture_frame_orientation: capture.frameOrientation,
        capture_frame_ratio: capture.frameRatio,
        capture_frame_mode: capture.frameMode,
        display_watermark: capture.displayWatermark !== false,
      },
      status: 'processing',
      error: '',
    };
    this._setWorkorderPhotoDraft(draft);
    this._uploadWorkorderPhotoDraft(draft);
  },

  _uploadWorkorderPhotoDraft(draft) {
    const stored = this._photoDraftForOrder(draft && draft.orderNo);
    if (!draft || !stored || stored.idempotencyKey !== draft.idempotencyKey) return;
    const processing = Object.assign({}, draft, { status: 'processing', error: '' });
    this._setWorkorderPhotoDraft(processing);
    fileToBase64(processing.path)
      .then(image => api.uploadWorkorderImage(
        processing.orderNo, image, processing.idempotencyKey, processing.metadata
      ))
      .then(result => {
        if (this._photoDraftForOrder(processing.orderNo) !== processing) return;
        const url = resolveUploadUrl(result && result.url);
        if (typeof url !== 'string' || !url) {
          throw { error: '影像上传结果无效，请重试' };
        }
        this._clearWorkorderPhotoDraft(processing.orderNo, processing.idempotencyKey);
        const context = { orderNo: processing.orderNo, siteId: processing.siteId };
        if (!this._matchesCurrentWorkorder(context)) return;
        const arr = (this.data.sheet.item.images_arr || []).slice();
        if (!arr.includes(url)) arr.push(url);
        this.setData({
          'sheet.item.images_arr': arr,
          'sheet.item.has_images': arr.length > 0,
          'sheet.item.images': JSON.stringify(arr),
        });
      })
      .catch(error => {
        const current = this._photoDraftForOrder(processing.orderNo);
        if (!current || current.idempotencyKey !== processing.idempotencyKey) return;
        const message = photoCaptureErrorMessage(error);
        this._setWorkorderPhotoDraft(Object.assign({}, processing, {
          status: 'failed', error: message,
        }));
        if (this._matchesCurrentWorkorder(processing)) {
          wx.showToast({ title: message, icon: 'none' });
        }
      });
  },

  onRetryWorkorderPhoto() {
    const item = this.data.sheet && this.data.sheet.item;
    const draft = this.data.workorderPhotoDraft;
    if (!item || !draft || draft.status !== 'failed'
        || String(item.order_no || '') !== draft.orderNo
        || String(item.site_id || '') !== draft.siteId) return;
    this._uploadWorkorderPhotoDraft(draft);
  },

  // 处置影像拍摄（受信相机返回后追加到工单 images）
  onUploadImage() {
    const item = this.data.sheet.item;
    if (!item) return;
    const remain = 6 - (item.images_arr ? item.images_arr.length : 0);
    if (remain <= 0) { wx.showToast({ title: '最多 6 张', icon: 'none' }); return; }
    const draft = this._photoDraftForOrder(item.order_no);
    if (draft) {
      wx.showToast({
        title: draft.status === 'failed' ? '请先重试未上传的照片' : '照片正在处理中',
        icon: 'none',
      });
      return;
    }
    this._openTrustedCamera(item);
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
    const draft = this.data.workorderPhotoDraft;
    const currentDraft = draft
      && String(item.order_no || '') === draft.orderNo
      && String(item.site_id || '') === draft.siteId ? draft : null;
    if (currentDraft) {
      wx.showToast({
        title: currentDraft.status === 'failed'
          ? '有照片上传失败，请先重试上传' : '照片仍在处理中，请完成上传后提交',
        icon: 'none',
      });
      return;
    }
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
  onApplyVehicle() {
    this.setData({ vehicleApply: {
      open: true, requiresVehicle: false, reason: '', index: 0,
      submitting: false, requestKey: 'vehicle_workorder_' + Date.now(),
    } });
  },
  onVehicleRequirementChange(e) {
    if (this.data.vehicleApply.submitting) return;
    this.setData({ 'vehicleApply.requiresVehicle': !!e.detail.value });
  },
  onVehicleReason(e) { this.setData({ 'vehicleApply.reason': e.detail.value }); },
  onVehiclePick(e) { this.setData({ 'vehicleApply.index': parseInt(e.detail.value, 10) }); },
  submitVehicle() {
    const va = this.data.vehicleApply;
    if (va.submitting) return;
    if (!va.requiresVehicle) {
      this.setData({ 'vehicleApply.open': false });
      return Promise.resolve(false);
    }
    const reason = (va.reason || '').trim();
    if (!reason) { wx.showToast({ title: '请填写用车事由', icon: 'none' }); return; }
    const item = this.data.sheet.item;
    const opt = this.data.vehicleOptions[va.index];
    const vehicle_id = opt && opt.id ? opt.id : null;
    if (!vehicle_id) { wx.showToast({ title: '请选择可用车辆', icon: 'none' }); return; }
    this.setData({ 'vehicleApply.submitting': true });
    wx.showLoading({ title: '提交中' });
    return api.applyVehicle({
      site_id: item.site_id, work_order_no: item.order_no, reason, vehicle_id,
      _idempotency_key: va.requestKey,
    })
      .then(() => this._refreshRelated(item.order_no).then(() => true).catch(() => false))
      .then(relatedRefreshed => {
        wx.hideLoading();
        this.setData({ 'vehicleApply.open': false, 'vehicleApply.submitting': false });
        wx.showToast({
          title: relatedRefreshed ? '用车申请已提交' : '申请成功，关联资源刷新失败',
          icon: relatedRefreshed ? 'success' : 'none',
        });
      })
      .catch((err) => {
        wx.hideLoading();
        this.setData({ 'vehicleApply.submitting': false });
        this.handleWriteFailure(err, '提交失败');
      });
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
    return api.applyParts(Object.assign({ site_id: item.site_id, work_order_no: item.order_no,
      _idempotency_key: pa.requestKey }, result.payload))
      .then(() => this._refreshRelated(item.order_no).then(() => true).catch(() => false))
      .then(relatedRefreshed => {
        wx.hideLoading();
        this.setData({ 'partsApply.open': false, 'partsApply.submitting': false });
        wx.showToast({
          title: relatedRefreshed ? '已提交审批' : '申请成功，关联资源刷新失败',
          icon: relatedRefreshed ? 'success' : 'none',
        });
      })
      .catch((err) => { wx.hideLoading(); this.setData({ 'partsApply.submitting': false }); this.handleWriteFailure(err, '提交失败'); });
  }
});
