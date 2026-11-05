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
  const availableHeight = Math.max(240, height - 196);
  const ratioWidth = 3;
  const ratioHeight = 4;
  const scale = Math.min(availableWidth / ratioWidth, availableHeight / ratioHeight);
  const frameWidth = Math.floor(scale * ratioWidth);
  const frameHeight = Math.floor(scale * ratioHeight);
  return {
    orientation: 'portrait',
    ratio: '3:4',
    width: frameWidth,
    height: frameHeight,
    style: `width:${frameWidth}px;height:${frameHeight}px;`,
  };
}

function roundedPx(value) {
  return Math.round(value * 100) / 100;
}

function estimatedTextWidth(text, fontSize) {
  return Array.from(String(text || '')).reduce((width, char) => {
    if (/\s/.test(char)) return width + fontSize * 0.35;
    if (/^[\x00-\xff]$/.test(char)) return width + fontSize * 0.58;
    return width + fontSize;
  }, 0);
}

function wrapWatermarkText(text, maxWidth, fontSize, weight, measureText) {
  const value = String(text == null ? '' : text);
  const measure = typeof measureText === 'function'
    ? candidate => measureText(candidate, fontSize, weight)
    : candidate => estimatedTextWidth(candidate, fontSize);
  const lines = [];
  let current = '';
  Array.from(value).forEach((char) => {
    const candidate = current + char;
    if (current && measure(candidate) > maxWidth) {
      lines.push(current);
      current = char;
    } else {
      current = candidate;
    }
  });
  if (current || !lines.length) lines.push(current);
  return lines;
}

function trustedCameraWatermarkPreviewLayout(frameWidth, preview, measureText) {
  const width = positiveNumber(frameWidth, 351);
  const scale = width / 1080;
  const px = value => roundedPx(value * scale);
  const horizontalPadding = px(20);
  const innerWidth = Math.max(1, width - horizontalPadding * 2);
  const leftWidth = innerWidth * 0.43;
  const rightWidth = innerWidth - leftWidth;
  const siteFontSize = px(60);
  const timeFontSize = px(30);
  const detailFontSize = px(27);
  const siteLineHeight = px(70);
  const timeLineHeight = px(36);
  const detailLineHeight = px(33);
  const gap = px(12);
  const source = preview || {};
  const siteLines = wrapWatermarkText(
    source.watermarkSiteName || '当前站点', leftWidth, siteFontSize, '700', measureText);
  const photoLines = wrapWatermarkText(
    `照片名称：${source.watermarkPhotoName || '现场照片'}`,
    rightWidth, detailFontSize, '400', measureText);
  const coordinateLines = wrapWatermarkText(
    `位置坐标：${source.watermarkCoordinates || '确认中'}`,
    rightWidth, detailFontSize, '400', measureText);
  const codeLines = wrapWatermarkText(
    `防伪码：${source.watermarkCode || '待生成'}`,
    rightWidth, detailFontSize, '400', measureText);
  const leftHeight = siteLines.length * siteLineHeight + gap + timeLineHeight;
  const rightHeight = (photoLines.length + coordinateLines.length + codeLines.length)
    * detailLineHeight + gap * 2;
  const bandHeight = Math.max(px(150), Math.max(leftHeight, rightHeight) + px(18));
  return {
    bandHeight,
    bandStyle: `height:${roundedPx(bandHeight)}px;padding:${px(9)}px ${horizontalPadding}px;`,
    siteStyle: `font-size:${siteFontSize}px;line-height:${siteLineHeight}px;`,
    timeStyle: `font-size:${timeFontSize}px;line-height:${timeLineHeight}px;margin-top:${gap}px;`,
    detailStyle: `font-size:${detailFontSize}px;line-height:${detailLineHeight}px;`,
    detailGapStyle: `margin-top:${gap}px;`,
    siteLines,
    photoLines,
    coordinateLines,
    codeLines,
    contentWidths: { left: leftWidth, right: rightWidth },
  };
}

function createWatermarkTextMeasurer(wxApi) {
  if (!wxApi || typeof wxApi.createOffscreenCanvas !== 'function') return null;
  try {
    const canvas = wxApi.createOffscreenCanvas({ type: '2d', width: 1, height: 1 });
    const context = canvas && canvas.getContext && canvas.getContext('2d');
    if (!context || typeof context.measureText !== 'function') return null;
    return (text, fontSize, weight) => {
      context.font = `${weight} ${fontSize}px "HarmonyOS Sans SC", "PingFang SC", sans-serif`;
      return context.measureText(String(text || '')).width;
    };
  } catch (error) {
    return null;
  }
}

function preparationErrorMessage(error) {
  if (error && error.capturePhase === 'location') return locationErrorMessage(error);
  return (error && (error.error || error.message)) || '拍摄信息确认失败，请重试';
}

function captureSessionLocation(session) {
  if (!session || session.location_verified !== true) return null;
  const lat = Number(session.gps_lat);
  const lng = Number(session.gps_lng);
  if (!Number.isFinite(lat) || lat < -90 || lat > 90
      || !Number.isFinite(lng) || lng < -180 || lng > 180) return null;
  return { lat, lng, source: String(session.location_source || '') };
}

function captureWatermarkPreview(session, location, siteName, itemName) {
  const lat = Number(location && location.lat);
  const lng = Number(location && location.lng);
  const hasCoordinates = Number.isFinite(lat) && Number.isFinite(lng);
  return {
    watermarkSiteName: String(siteName || '').trim() || '当前站点',
    watermarkCaptureTime: '拍摄后生成',
    watermarkPhotoName: String(itemName || '').trim() || '现场照片',
    watermarkCoordinates: hasCoordinates
      ? `${lat.toFixed(6)},${lng.toFixed(6)}` : '确认中',
    watermarkCode: String(session && session.anti_counterfeit_code || '').trim() || '待生成',
  };
}

const CAMERA_FLASH_OPTIONS = [
  { value: 'auto', label: '自动' },
  { value: 'on', label: '闪光' },
  { value: 'torch', label: '常亮' },
  { value: 'off', label: '关闭' },
];

function normalizeCameraFlashMode(value) {
  const mode = String(value || '').trim();
  return CAMERA_FLASH_OPTIONS.some(option => option.value === mode) ? mode : 'auto';
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
    flashMode: 'auto',
    flashModeLabel: '自动',
    flashOptionIndex: 0,
    flashOptions: CAMERA_FLASH_OPTIONS,
    displayWatermark: true,
    watermarkBandStyle: 'min-height:48.75px;padding:2.93px 6.5px;',
    watermarkSiteStyle: 'font-size:19.5px;',
    watermarkTimeStyle: 'font-size:9.75px;margin-top:3.9px;',
    watermarkDetailStyle: 'font-size:8.78px;',
    watermarkDetailGapStyle: 'margin-top:3.9px;',
    watermarkSiteLines: ['当前站点'],
    watermarkPhotoLines: ['照片名称：现场照片'],
    watermarkCoordinateLines: ['位置坐标：确认中'],
    watermarkCodeLines: ['防伪码：待生成'],
    watermarkSiteName: '当前站点',
    watermarkCaptureTime: '拍摄后生成',
    watermarkPhotoName: '现场照片',
    watermarkCoordinates: '确认中',
    watermarkCode: '待生成',
    statusText: '正在确认拍摄信息',
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
    const siteName = decodeOption(options && options.site_name);
    const itemName = decodeOption(options && options.item_name);
    this.setData(Object.assign({
      siteName,
      itemName,
      displayWatermark: inspectionMode ? inspectionWatermarkDefault(itemCategory) : true,
    }, captureWatermarkPreview(null, null, siteName, itemName)));
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
    this._frameWidth = layout.width;
    this._watermarkTextMeasurer = this._watermarkTextMeasurer
      || createWatermarkTextMeasurer(wx);
    const watermarkLayout = trustedCameraWatermarkPreviewLayout(
      layout.width, this.data, this._watermarkTextMeasurer);
    this.setData({
      cameraStyle: layout.style,
      frameOrientation: layout.orientation,
      frameRatio: layout.ratio,
      watermarkBandStyle: watermarkLayout.bandStyle,
      watermarkSiteStyle: watermarkLayout.siteStyle,
      watermarkTimeStyle: watermarkLayout.timeStyle,
      watermarkDetailStyle: watermarkLayout.detailStyle,
      watermarkDetailGapStyle: watermarkLayout.detailGapStyle,
      watermarkSiteLines: watermarkLayout.siteLines,
      watermarkPhotoLines: watermarkLayout.photoLines,
      watermarkCoordinateLines: watermarkLayout.coordinateLines,
      watermarkCodeLines: watermarkLayout.codeLines,
    });
  },

  _syncWatermarkPreview(session, location) {
    const preview = captureWatermarkPreview(
      session, location, this.data.siteName, this.data.itemName);
    this.setData(preview, () => this._refreshWatermarkLayout());
  },

  _refreshWatermarkLayout() {
    const layout = trustedCameraWatermarkPreviewLayout(
      this._frameWidth || 351, this.data, this._watermarkTextMeasurer);
    this.setData({
      watermarkBandStyle: layout.bandStyle,
      watermarkSiteStyle: layout.siteStyle,
      watermarkTimeStyle: layout.timeStyle,
      watermarkDetailStyle: layout.detailStyle,
      watermarkDetailGapStyle: layout.detailGapStyle,
      watermarkSiteLines: layout.siteLines,
      watermarkPhotoLines: layout.photoLines,
      watermarkCoordinateLines: layout.coordinateLines,
      watermarkCodeLines: layout.codeLines,
    });
  },

  _syncReady() {
    const ready = Boolean(this.data.previewReady && this.data.sessionReady
      && !this.data.errorText && !this.data.capturing && !this._flashUpdating);
    this.setData({
      ready,
      statusText: ready ? '拍摄信息已确认，可以拍照'
        : (this.data.errorText || (this.data.previewReady
          ? '正在确认拍摄信息' : '正在启动相机预览')),
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
    this.setData(Object.assign({
      preparing: true,
      sessionReady: false,
      ready: false,
      errorText: '',
      showSettings: false,
      statusText: this.data.previewReady
        ? '正在确认拍摄信息' : '正在启动相机预览',
    }, captureWatermarkPreview(null, null, this.data.siteName, this.data.itemName)));
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
    api.createPhotoCaptureSession(payload)
      .then(session => {
        const serverLocation = captureSessionLocation(session);
        if (serverLocation) return { session, location: serverLocation };
        return requestLocation()
          .catch(error => { throw Object.assign({}, error || {}, { capturePhase: 'location' }); })
          .then(location => api.bindPhotoCaptureSessionLocation({
            capture_session: session && session.capture_session,
            gps_lat: location.lat,
            gps_lng: location.lng,
          }).then(bound => ({
            session: Object.assign({}, session, bound || {}),
            location: captureSessionLocation(bound) || location,
          })));
      })
      .then(result => {
        if (!this._alive || version !== this._prepareVersion) return;
        this._session = result.session;
        this._verifiedLocation = result.location;
        this._syncWatermarkPreview(result.session, result.location);
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
    if (this._captureFailed && this.data.previewReady && this.data.sessionReady
        && this._session && this._verifiedLocation) {
      this._captureFailed = false;
      this.setData({ errorText: '', showSettings: false }, () => this._syncReady());
      return;
    }
    this._prepareCaptureSession();
  },

  onOpenSettings() {
    wx.openSetting({});
  },

  onDisplayWatermarkChange(event) {
    if (this.data.capturing || this._captureCommitted) return;
    const detailValue = event && event.detail && event.detail.value;
    const displayWatermark = typeof detailValue === 'boolean'
      ? detailValue : !this.data.displayWatermark;
    this.setData({ displayWatermark });
  },

  onFlashModeChange(event) {
    if (this.data.capturing || this._captureCommitted) return;
    const selectedIndex = Number(event && event.detail && event.detail.value);
    const selectedOption = Number.isInteger(selectedIndex)
      ? CAMERA_FLASH_OPTIONS[selectedIndex] : null;
    const mode = normalizeCameraFlashMode(selectedOption ? selectedOption.value
      : (event && event.currentTarget && event.currentTarget.dataset
        && event.currentTarget.dataset.mode));
    if (mode === this.data.flashMode) return;
    const optionIndex = CAMERA_FLASH_OPTIONS.findIndex(option => option.value === mode);
    this._flashUpdating = true;
    const flashRevision = this._flashRevision = (this._flashRevision || 0) + 1;
    const recoverCapture = this._captureFailed && !this.data.showSettings;
    this.setData({
      ready: false,
      flashMode: mode,
      flashModeLabel: CAMERA_FLASH_OPTIONS[optionIndex].label,
      flashOptionIndex: optionIndex,
      errorText: recoverCapture ? '' : this.data.errorText,
    }, () => {
      if (!this._alive || flashRevision !== this._flashRevision) return;
      this._flashUpdating = false;
      if (recoverCapture) this._captureFailed = false;
      this._syncReady();
    });
  },

  onTakePhoto() {
    if (this.data.capturing || this._captureCommitted || this._flashUpdating) return;
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
        this._captureFailed = true;
        console.warn('[trusted-camera] takePhoto failed', {
          flash: this.data.flashMode,
          errMsg: error && error.errMsg,
          errCode: error && error.errCode,
        });
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

module.exports = {
  trustedCameraFrameLayout, preparationErrorMessage, inspectionWatermarkDefault,
  captureSessionLocation, captureWatermarkPreview, normalizeCameraFlashMode,
  trustedCameraWatermarkPreviewLayout, wrapWatermarkText, createWatermarkTextMeasurer,
};
