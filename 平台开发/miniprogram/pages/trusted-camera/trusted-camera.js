const api = require('../../services/api.js');
const {
  requestLocation, locationErrorMessage, shouldOpenLocationSettings,
} = require('../../utils/location.js');
const {
  photoCaptureErrorMessage, shouldOpenCameraSettings,
} = require('../../utils/photos.js');

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function decodeOption(value) {
  const text = String(value || '');
  try { return decodeURIComponent(text); } catch (error) { return text; }
}

function inspectionWatermarkDefault(category) {
  const value = String(category || '').trim();
  return value !== 'log_books' && value !== '台账登记';
}

function trustedCameraFrameLayout(size) {
  const width = positiveNumber(size && (size.windowWidth || size.width), 375);
  const height = positiveNumber(size && (size.windowHeight || size.height), 667);
  const availableWidth = Math.max(240, width - 24);
  const availableHeight = Math.max(240, height - 210);
  const ratioWidth = 3;
  const ratioHeight = 4;
  const scale = Math.min(availableWidth / ratioWidth, availableHeight / ratioHeight);
  const frameWidth = Math.floor(scale * ratioWidth);
  const frameHeight = Math.floor(scale * ratioHeight);
  return {
    orientation: 'portrait',
    ratio: '3:4',
    style: `width:${frameWidth}px;height:${frameHeight}px;`,
  };
}

function preparationErrorMessage(error) {
  if (error && error.capturePhase === 'location') return locationErrorMessage(error);
  return (error && (error.error || error.message)) || '拍摄信息确认失败，请重试';
}

Page({
  data: {
    siteName: '',
    itemName: '',
    cameraStyle: 'width:351px;height:468px;',
    frameOrientation: 'portrait',
    frameRatio: '3:4',
    previewReady: false,
    preparing: true,
    sessionReady: false,
    ready: false,
    capturing: false,
    displayWatermark: true,
    statusText: '正在确认位置与拍摄信息',
    errorText: '',
    showSettings: false,
    previewElapsedMs: 0,
  },

  onLoad(options) {
    this._alive = true;
    this._prepareVersion = 0;
    this._entryStartedAt = Date.now();
    this._requestId = String(options && options.request_id || '');
    const orderNo = String(options && options.order_no || '').trim();
    const siteId = Number(options && options.site_id);
    const planId = Number(options && options.plan_id);
    const itemId = Number(options && options.item_id);
    const captureContext = String(options && options.capture_context || '').trim();
    const itemCategory = decodeOption(options && options.item_category);
    const inspectionMode = siteId > 0 && planId > 0 && itemId > 0 && !orderNo;
    const workorderMode = siteId > 0 && !!orderNo && !(planId > 0) && !(itemId > 0);
    const manualReportMode = siteId > 0 && captureContext === 'manual_report'
      && !orderNo && !(planId > 0) && !(itemId > 0);
    this._context = { siteId, planId, itemId, orderNo, captureContext, itemCategory };
    this._contextValid = inspectionMode || workorderMode || manualReportMode;
    this._eventChannel = this.getOpenerEventChannel ? this.getOpenerEventChannel() : null;
    this.setData({
      siteName: decodeOption(options && options.site_name),
      itemName: decodeOption(options && options.item_name),
      displayWatermark: inspectionMode ? inspectionWatermarkDefault(itemCategory) : true,
    });
    this._applyFrameLayout();
    if (this._contextValid) {
      this._prepareCaptureSession();
    } else {
      const message = '拍摄业务信息不完整，请返回原业务页面重试';
      this.setData({
        preparing: false,
        sessionReady: false,
        ready: false,
        errorText: message,
        statusText: message,
      });
    }
  },

  onReady() {
    this._cameraContext = wx.createCameraContext();
  },

  onResize(event) {
    this._applyFrameLayout(event && event.size);
  },

  onUnload() {
    this._emitCancelled();
    this._alive = false;
    this._prepareVersion = (this._prepareVersion || 0) + 1;
  },

  _applyFrameLayout(size) {
    let windowSize = size;
    if (!windowSize) {
      if (wx.getWindowInfo) windowSize = wx.getWindowInfo();
      else if (wx.getSystemInfoSync) windowSize = wx.getSystemInfoSync();
    }
    const layout = trustedCameraFrameLayout(windowSize || {});
    this.setData({
      cameraStyle: layout.style,
      frameOrientation: layout.orientation,
      frameRatio: layout.ratio,
    });
  },

  _syncReady() {
    const ready = Boolean(this.data.previewReady && this.data.sessionReady
      && !this.data.errorText && !this.data.capturing);
    this.setData({
      ready,
      statusText: ready ? '拍摄信息已确认，可以拍照'
        : (this.data.errorText || (this.data.previewReady
          ? '正在确认位置与拍摄信息' : '正在启动相机预览')),
    });
  },

  onCameraInit() {
    if (!this._alive) return;
    this.setData({
      previewReady: true,
      previewElapsedMs: Math.max(0, Date.now() - this._entryStartedAt),
    }, () => this._syncReady());
  },

  onCameraError(event) {
    const error = event && event.detail ? event.detail : event;
    const message = photoCaptureErrorMessage(error);
    this.setData({
      previewReady: false,
      ready: false,
      errorText: message,
      statusText: message,
      showSettings: shouldOpenCameraSettings(error),
    });
  },

  _prepareCaptureSession() {
    if (!this._contextValid) return;
    const version = (this._prepareVersion || 0) + 1;
    this._prepareVersion = version;
    this._session = null;
    this._verifiedLocation = null;
    this.setData({
      preparing: true,
      sessionReady: false,
      ready: false,
      errorText: '',
      showSettings: false,
      statusText: this.data.previewReady
        ? '正在确认位置与拍摄信息' : '正在启动相机预览',
    });
    const payload = {
      site_id: this._context.siteId,
      capture_source: 'camera',
      prepare_only: true,
    };
    if (this._context.orderNo) {
      payload.order_no = this._context.orderNo;
    } else if (this._context.captureContext === 'manual_report') {
      payload.capture_context = 'manual_report';
    } else {
      payload.plan_id = this._context.planId;
      payload.item_id = this._context.itemId;
    }
    const sessionTask = api.createPhotoCaptureSession(payload);
    const locationTask = requestLocation();
    Promise.all([sessionTask, locationTask])
      .then(([session, location]) => api.bindPhotoCaptureSessionLocation({
        capture_session: session && session.capture_session,
        gps_lat: location.lat,
        gps_lng: location.lng,
      }).then(bound => ({
        session: Object.assign({}, session, bound || {}), location,
      })))
      .then(result => {
        if (!this._alive || version !== this._prepareVersion) return;
        this._session = result.session;
        this._verifiedLocation = result.location;
        this.setData({ preparing: false, sessionReady: true }, () => this._syncReady());
      })
      .catch(error => {
        if (!this._alive || version !== this._prepareVersion) return;
        const message = preparationErrorMessage(error);
        this.setData({
          preparing: false,
          sessionReady: false,
          ready: false,
          errorText: message,
          statusText: message,
          showSettings: (error && error.capturePhase === 'location')
            ? shouldOpenLocationSettings(error) : false,
        });
      });
  },

  onRetryPreparation() {
    if (this.data.preparing || this.data.capturing) return;
    this._prepareCaptureSession();
  },

  onOpenSettings() {
    wx.openSetting({});
  },

  onDisplayWatermarkChange(event) {
    if (this.data.capturing || this._captureCommitted) return;
    this.setData({ displayWatermark: !(event && event.detail && event.detail.value === false) });
  },

  onTakePhoto() {
    if (this.data.capturing || this._captureCommitted) return;
    if (!this.data.ready || !this._session || !this._verifiedLocation) {
      wx.showToast({ title: this.data.errorText || '正在确认拍摄信息，请稍候', icon: 'none' });
      return;
    }
    const camera = this._cameraContext || wx.createCameraContext();
    const displayWatermark = this.data.displayWatermark !== false;
    this.setData({ capturing: true, ready: false, statusText: '正在拍摄' });
    camera.takePhoto({
      quality: 'high',
      success: result => {
        const path = result && result.tempImagePath;
        if (!path || !this._alive) {
          this.setData({ capturing: false }, () => this._syncReady());
          return;
        }
        this._captureCommitted = true;
        if (this._eventChannel && this._eventChannel.emit) {
          this._eventChannel.emit('trustedCameraCaptured', {
            requestId: this._requestId,
            path,
            captureSession: this._session.capture_session,
            gpsLat: this._verifiedLocation.lat,
            gpsLng: this._verifiedLocation.lng,
            frameOrientation: this.data.frameOrientation,
            frameRatio: this.data.frameRatio,
            frameMode: 'center_cover',
            displayWatermark,
            previewElapsedMs: this.data.previewElapsedMs,
          });
        }
        wx.navigateBack();
      },
      fail: error => {
        if (!this._alive) return;
        const message = photoCaptureErrorMessage(error);
        this.setData({
          capturing: false,
          errorText: message,
          showSettings: shouldOpenCameraSettings(error),
        }, () => this._syncReady());
      },
    });
  },

  onCancel() {
    if (this.data.capturing) return;
    this._emitCancelled();
    wx.navigateBack();
  },

  _emitCancelled() {
    if (this._captureCommitted || this._cancelEmitted) return;
    this._cancelEmitted = true;
    if (this._eventChannel && this._eventChannel.emit) {
      this._eventChannel.emit('trustedCameraCancelled', {
        requestId: this._requestId,
        errMsg: 'trustedCamera:fail cancel',
      });
    }
  },
});

module.exports = { trustedCameraFrameLayout, preparationErrorMessage, inspectionWatermarkDefault };
