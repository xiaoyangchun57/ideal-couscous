const api = require('../../services/api.js');
const {
  chooseReportAlbumPhotos, fileToBase64, persistFile, runPhotoActionOnce,
  validateReportPhotoPaths, handlePhotoActionFailure, deletePendingReportPhotoOnce,
} = require('../../utils/photos.js');
const { uploadStoragePath, prepareReportPhotoStoragePaths } = require('../../utils/url.js');
const { requestLocation, locationErrorMessage } = require('../../utils/location.js');
const {
  createPendingReportPhoto, errorMessage, failedReportPhoto, normalizeReportPhotoItems,
  reportPhotoDisplayUrls, reportPhotoItemsFromMedia, reportPhotoSheetPatch,
  refreshedReportPhoto, reportCaptureMetadata, uploadedReportPhoto,
} = require('../../utils/reportPhotoDraft.js');

const STATUS = {
  open: '待处置', dispatched: '已派单', verified: '已核实',
  resolved: '已解决', archived: '已归档',
};
const TYPE = {
  sensory: '感官异常', equipment: '设备异常', environment: '环境异常',
  violation: '违规操作', pollution: '污染事件',
};
const REPORT_TYPES = [
  { value: 'sensory', label: '感官异常' },
  { value: 'equipment', label: '设备异常' },
  { value: 'environment', label: '环境异常' },
  { value: 'violation', label: '违规操作' },
  { value: 'pollution', label: '污染事件' },
];

function emptyReportSheet() {
  return {
    open: false, sites: [], siteId: null, siteName: '', siteIndex: -1,
    typeIndex: 0, description: '', photos: [], photoItems: [], photoSiteId: null,
    uploading: false, submitting: false,
  };
}

function reportSite(row) {
  const id = Number(row && row.id);
  if (!Number.isInteger(id) || id <= 0) return null;
  const name = String((row && (row.name || row.code)) || '').trim();
  return name ? { id, name } : null;
}

function photoIdempotencyKey(siteId, path, index) {
  const safePath = String(path || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-24);
  return 'manual_report_photo_' + siteId + '_' + Date.now() + '_' + index + '_'
    + safePath + '_' + Math.floor(Math.random() * 1e6);
}

Page({
  data: {
    loading: true, reports: [], filter: '', detail: null,
    filters: ['全部', '待处理', '已核实', '已解决', '已归档'],
    reportTypes: REPORT_TYPES,
    reportSiteLoading: false,
    reportSiteLoadError: '',
    reportSheet: emptyReportSheet(),
  },

  onLoad() {
    this._unloaded = false;
    this._visible = true;
    this._reportDraftVersion = 0;
    this._trustedCameraSequence = 0;
  },

  onShow() {
    this._unloaded = false;
    this._visible = true;
    this.load();
    if (this._reportSuccessNotice) {
      const notice = this._reportSuccessNotice;
      this._reportSuccessNotice = null;
      this.showReportSuccess(notice.orderNo, notice.locationWarning);
    }
  },

  onHide() {
    this._visible = false;
    this._reportSiteRequest = (this._reportSiteRequest || 0) + 1;
    this._reportSitePromise = null;
  },

  onUnload() {
    this._unloaded = true;
    this._reportDraftVersion = (this._reportDraftVersion || 0) + 1;
    this.onHide();
  },

  load() {
    this.setData({ loading: true });
    api.manualReports().then((rows) => {
      const reports = (rows || []).map((row) => ({
        ...row,
        type_cn: TYPE[row.report_type] || row.report_type || '现场异常',
        status_cn: STATUS[row.status] || row.status || '待处理',
        photo_count: (() => { try { return JSON.parse(row.photo_urls || '[]').length; } catch (_) { return 0; } })(),
      }));
      this.setData({ reports, loading: false });
    }).catch(() => this.setData({ loading: false }));
  },

  onFilter(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const statuses = ['', 'dispatched', 'verified', 'resolved', 'archived'];
    this.setData({ filter: statuses[index] || '' });
  },

  onGoReport() {
    if (this.data.reportSheet.open) return Promise.resolve({ alreadyOpen: true });
    return this.loadAuthorizedReportSites();
  },

  onRetryReportSites() { return this.loadAuthorizedReportSites(); },

  loadAuthorizedReportSites() {
    if (this._reportSitePromise) return this._reportSitePromise;
    const requestId = (this._reportSiteRequest || 0) + 1;
    this._reportSiteRequest = requestId;
    this.setData({ reportSiteLoading: true, reportSiteLoadError: '' });
    let requestPromise;
    requestPromise = api.sites().then(rows => {
      if (!this.isCurrentReportSiteRequest(requestId)) return { stale: true };
      const seen = {};
      const sites = (Array.isArray(rows) ? rows : []).map(reportSite).filter(site => {
        if (!site || seen[site.id]) return false;
        seen[site.id] = true;
        return true;
      });
      if (!sites.length) {
        this.setData({ reportSiteLoading: false, reportSiteLoadError: '' });
        wx.showModal({ title: '无法发起上报', content: '当前没有可上报的授权站点。', showCancel: false });
        return { empty: true };
      }
      this.openReportSheetForSites(sites);
      return { sites };
    }).catch(error => {
      if (!this.isCurrentReportSiteRequest(requestId)) return { stale: true };
      const message = (error && error.error) || '授权站点加载失败，请重试';
      this.setData({ reportSiteLoading: false, reportSiteLoadError: message });
      wx.showToast({ title: message, icon: 'none' });
      return { error };
    }).finally(() => {
      if (this._reportSitePromise === requestPromise) this._reportSitePromise = null;
    });
    this._reportSitePromise = requestPromise;
    return requestPromise;
  },

  isCurrentReportSiteRequest(requestId) {
    return this._visible !== false && !this._unloaded && this._reportSiteRequest === requestId;
  },

  canUpdateReportDraft() { return !this._unloaded; },

  openReportSheetForSites(sites) {
    const current = this.data.reportSheet || emptyReportSheet();
    const selected = sites.find(site => site.id === Number(current.siteId));
    const next = Object.assign({}, current, {
      open: true, sites, siteIndex: selected ? sites.indexOf(selected) : -1,
      siteId: selected ? selected.id : null,
      siteName: selected ? selected.name : '',
      uploading: false, submitting: false,
    });
    if (!selected && sites.length === 1 && !normalizeReportPhotoItems(current).length) {
      next.siteId = sites[0].id;
      next.siteName = sites[0].name;
      next.siteIndex = 0;
      next.photoSiteId = null;
    }
    this.setData({ reportSiteLoading: false, reportSiteLoadError: '', reportSheet: next });
  },

  onCloseReport() {
    const sheet = this.data.reportSheet || {};
    if (sheet.submitting || sheet.uploading) {
      wx.showToast({ title: sheet.submitting ? '正在提交上报，请稍候' : '照片上传中，请稍候', icon: 'none' });
      return;
    }
    // Keep the draft in page state so an accidental close does not discard evidence or input.
    this.setData({ 'reportSheet.open': false });
  },

  onReportSite(e) {
    const sites = (this.data.reportSheet && this.data.reportSheet.sites) || [];
    const index = Number(e && e.detail && e.detail.value);
    const selected = sites[index];
    const sheet = this.data.reportSheet || {};
    if (!selected || sheet.submitting || sheet.uploading) return;
    if (Number(sheet.siteId) === selected.id) return;
    if (normalizeReportPhotoItems(sheet).length) {
      wx.showToast({ title: '请先提交或移除当前异常上报照片', icon: 'none' });
      return;
    }
    this.setData({
      'reportSheet.siteId': selected.id,
      'reportSheet.siteName': selected.name,
      'reportSheet.siteIndex': index,
      'reportSheet.photoItems': [],
      'reportSheet.photos': [],
      'reportSheet.photoSiteId': null,
    });
  },

  onReportType(e) { this.setData({ 'reportSheet.typeIndex': Number(e.detail.value) || 0 }); },

  onReportDescription(e) { this.setData({ 'reportSheet.description': e.detail.value || '' }); },

  reportPhotoContext(options) {
    const sheet = this.data.reportSheet || {};
    const siteId = Number(sheet.siteId);
    const sites = sheet.sites || [];
    if (sheet.submitting || sheet.uploading) return null;
    if (!sites.some(site => site.id === siteId)) {
      wx.showToast({ title: '请先选择授权站点', icon: 'none' });
      return null;
    }
    const photoItems = normalizeReportPhotoItems(sheet);
    const remaining = 6 - photoItems.length;
    if (remaining <= 0 && !(options && options.allowFull)) {
      wx.showToast({ title: '最多上传 6 张照片', icon: 'none' });
      return null;
    }
    return {
      siteId,
      siteName: String(sheet.siteName || ''),
      photoItems,
      remaining,
      draftVersion: this._reportDraftVersion || 0,
    };
  },

  reportPhotoContextIsCurrent(context) {
    const sheet = this.data.reportSheet || {};
    return !this._unloaded && !!sheet.open
      && Number(sheet.siteId) === context.siteId
      && (this._reportDraftVersion || 0) === context.draftVersion;
  },

  uploadReportPhotoPaths(paths, context, metadata) {
    return Promise.resolve(paths)
      .then(validateReportPhotoPaths)
      .then(validPaths => Promise.all(validPaths.map(path => persistFile(path))))
      .then(savedPaths => {
        if (!this.reportPhotoContextIsCurrent(context)) return { stale: true };
        wx.showLoading({ title: '上传中', mask: true });
        const pending = savedPaths.map((path, index) => {
          const idempotencyKey = photoIdempotencyKey(context.siteId, path, index);
          return createPendingReportPhoto(path, idempotencyKey, metadata);
        });
        const replaced = context.replaceKey
          ? context.photoItems.find(item => item.key === context.replaceKey) : null;
        const baseItems = context.replaceKey
          ? context.photoItems.filter(item => item.key !== context.replaceKey) : context.photoItems;
        const items = baseItems.concat(pending);
        this.setData(reportPhotoSheetPatch(items, context.siteId));
        if (replaced) this.cleanupReportLocalPhoto(replaced.localPath);
        return this.uploadReportPhotoItems(pending, context);
      });
  },

  uploadReportPhotoItems(pending, context) {
    return Promise.allSettled(pending.map(item => fileToBase64(item.localPath)
      .then(image => api.uploadSitePhoto(
        context.siteId, image, item.idempotencyKey, item.metadata
      )))).then(results => {
          const successful = results.filter(result => (
            result.status === 'fulfilled' && result.value && result.value.url
          ));
          if (!this.reportPhotoContextIsCurrent(context)) {
            return Promise.allSettled(successful.map(result => (
              api.deletePendingSitePhoto(String(result.value.url).trim())
            ))).then(() => ({ stale: true }));
          }
          const outcomeByKey = new Map(pending.map((item, index) => {
            const result = results[index];
            const next = result.status === 'fulfilled'
              ? uploadedReportPhoto(item, result.value)
              : failedReportPhoto(item, result.reason);
            return [item.key, next];
          }));
          const currentItems = normalizeReportPhotoItems(this.data.reportSheet);
          const merged = currentItems.map(item => outcomeByKey.get(item.key) || item);
          this.setData(reportPhotoSheetPatch(merged, context.siteId));
          const failed = merged.filter(item => outcomeByKey.has(item.key) && item.status === 'failed');
          pending.forEach((item, index) => {
            const outcome = outcomeByKey.get(item.key);
            if (outcome && outcome.status === 'uploaded') this.cleanupReportLocalPhoto(item.localPath);
          });
          if (failed.length) {
            const captureExpired = failed.some(item => item.requiresNewCapture);
            wx.showToast({
              title: captureExpired ? '拍摄授权已失效，请重新拍摄'
                : (failed.length === pending.length ? '照片上传失败，可重试或移除' : '部分照片上传失败，可重试'),
              icon: 'none',
            });
          }
          return {
            uploaded: pending.length - failed.length,
            failed: failed.length,
          };
        });
  },

  cleanupReportLocalPhoto(path) {
    if (!path || typeof wx.removeSavedFile !== 'function' || path.indexOf('wxfile://usr/') !== 0) return;
    wx.removeSavedFile({ filePath: path, fail() {} });
  },

  refreshReportPhotoMedia(key, explicit) {
    const sheet = this.data.reportSheet || {};
    const items = normalizeReportPhotoItems(sheet);
    const target = items.find(item => item.key === key);
    const siteId = Number(sheet.siteId);
    const draftVersion = this._reportDraftVersion || 0;
    if (!target || target.status !== 'uploaded') return Promise.resolve(target || null);
    if (!target.attachmentId) {
      const next = items.map(item => item.key === key
        ? Object.assign({}, item, { mediaError: '照片展示地址无法刷新，请移除后重新上传' }) : item);
      this.setData(reportPhotoSheetPatch(next, Number(sheet.siteId)));
      return Promise.reject({ error: '照片展示地址无法刷新，请移除后重新上传' });
    }
    this._reportMediaRefreshPromises = this._reportMediaRefreshPromises || {};
    if (this._reportMediaRefreshPromises[key]) return this._reportMediaRefreshPromises[key];
    if (!explicit && target.mediaAutoRefreshUsed) {
      const next = items.map(item => item.key === key
        ? Object.assign({}, item, { mediaError: '照片加载失败，点击重试', mediaRefreshing: false }) : item);
      this.setData(reportPhotoSheetPatch(next, Number(sheet.siteId)));
      return Promise.reject({ error: '照片加载失败，点击重试' });
    }
    const loading = Object.assign({}, target, {
      mediaRefreshing: true,
      mediaError: '',
      mediaAutoRefreshUsed: explicit ? target.mediaAutoRefreshUsed : true,
    });
    this.setData(reportPhotoSheetPatch(
      items.map(item => item.key === key ? loading : item), Number(sheet.siteId)
    ));
    let refreshPromise;
    refreshPromise = api.attachmentDetail(target.attachmentId).then(response => {
      const currentSheet = this.data.reportSheet || {};
      const currentItems = normalizeReportPhotoItems(currentSheet);
      const current = currentItems.find(item => item.key === key);
      if (!this.canUpdateReportDraft()
          || (this._reportDraftVersion || 0) !== draftVersion
          || Number(currentSheet.siteId) !== siteId || !current) return { stale: true };
      const refreshed = current && refreshedReportPhoto(current, response, !explicit);
      if (!refreshed) throw { error: '照片展示地址已变化，请重试' };
      this.setData(reportPhotoSheetPatch(
        currentItems.map(item => item.key === key ? refreshed : item), Number(currentSheet.siteId)
      ));
      return refreshed;
    }).catch(error => {
      const currentSheet = this.data.reportSheet || {};
      const currentItems = normalizeReportPhotoItems(currentSheet);
      if (!this.canUpdateReportDraft()
          || (this._reportDraftVersion || 0) !== draftVersion
          || Number(currentSheet.siteId) !== siteId
          || !currentItems.some(item => item.key === key)) return { stale: true };
      const next = currentItems.map(item => item.key === key ? Object.assign({}, item, {
        mediaRefreshing: false,
        mediaError: errorMessage(error) || '照片加载失败，点击重试',
      }) : item);
      this.setData(reportPhotoSheetPatch(next, Number(currentSheet.siteId)));
      wx.showToast({ title: '照片加载失败，点击重试', icon: 'none' });
      throw error;
    }).finally(() => {
      if (this._reportMediaRefreshPromises[key] === refreshPromise) {
        delete this._reportMediaRefreshPromises[key];
      }
    });
    this._reportMediaRefreshPromises[key] = refreshPromise;
    return refreshPromise;
  },

  onReportPhotoMediaError(e) {
    const key = e.currentTarget.dataset.key;
    return this.refreshReportPhotoMedia(key, false).catch(() => null);
  },

  onRetryReportPhotoMedia(e) {
    const key = e.currentTarget.dataset.key;
    return this.refreshReportPhotoMedia(key, true).catch(() => null);
  },

  onRetryReportPhoto(e) {
    const key = e.currentTarget.dataset.key;
    const sheet = this.data.reportSheet || {};
    if (!key || sheet.uploading || sheet.submitting) return;
    const items = normalizeReportPhotoItems(sheet);
    const target = items.find(item => item.key === key && item.status === 'failed');
    if (target && target.requiresNewCapture) return this.confirmReportPhotoRecapture(target);
    if (!target || !target.localPath || !target.idempotencyKey) {
      wx.showToast({ title: '原照片已失效，请移除后重新拍摄', icon: 'none' });
      return;
    }
    const context = this.reportPhotoContext({ allowFull: true });
    if (!context) return;
    const uploading = Object.assign({}, target, { status: 'uploading', error: '' });
    this.setData(Object.assign(
      { 'reportSheet.uploading': true },
      reportPhotoSheetPatch(items.map(item => item.key === key ? uploading : item), context.siteId)
    ));
    return this.uploadReportPhotoItems([uploading], context)
      .then(result => this.finishReportPhotoAction(result),
        error => this.finishReportPhotoAction(null, error));
  },

  onRemoveReportPhoto(e) {
    const key = e.currentTarget.dataset.key;
    const sheet = this.data.reportSheet || {};
    const items = normalizeReportPhotoItems(sheet);
    const target = items.find(item => item.key === key);
    if (!target || sheet.submitting || sheet.uploading) return;
    const removeLocal = () => {
      this.cleanupReportLocalPhoto(target.localPath);
      const next = items.filter(item => item.key !== key);
      this.setData(reportPhotoSheetPatch(next, Number(sheet.siteId)));
      return { success: true };
    };
    if (target.status !== 'uploaded') return Promise.resolve(removeLocal());
    const storagePath = uploadStoragePath(target.storedPath);
    if (!storagePath) {
      wx.showToast({ title: '照片地址无效，请重新上传', icon: 'none' });
      return;
    }
    return deletePendingReportPhotoOnce(
      this, target.displayUrl, storagePath, path => api.deletePendingSitePhoto(path),
      () => (this.data.reportSheet && this.data.reportSheet.photos) || []
    ).then(removeLocal).catch(error => {
      wx.showToast({ title: errorMessage(error), icon: 'none' });
      return { success: false, error };
    });
  },

  onPreviewReportPhoto(e) {
    const key = e.currentTarget.dataset.key;
    const items = normalizeReportPhotoItems(this.data.reportSheet || {});
    const target = items.find(item => item.key === key);
    if (!target) return;
    if (target.status !== 'uploaded') {
      const urls = reportPhotoDisplayUrls(items);
      const current = target.displayUrl || target.localPath;
      if (current && urls.indexOf(current) !== -1) wx.previewImage({ current, urls });
      return;
    }
    return this.refreshReportPhotoMedia(key, true).then(() => {
      const refreshedItems = normalizeReportPhotoItems(this.data.reportSheet || {});
      const refreshed = refreshedItems.find(item => item.key === key);
      const urls = reportPhotoDisplayUrls(refreshedItems);
      if (refreshed && refreshed.displayUrl && urls.indexOf(refreshed.displayUrl) !== -1) {
        wx.previewImage({ current: refreshed.displayUrl, urls });
      }
    }).catch(() => null);
  },

  finishReportPhotoAction(result, error) {
    if (this.canUpdateReportDraft()) this.setData({ 'reportSheet.uploading': false });
    return error ? { error } : result;
  },

  onAddReportPhoto() {
    const context = this.reportPhotoContext();
    if (!context) return;
    this.setData({ 'reportSheet.uploading': true });
    return runPhotoActionOnce(this, 'supplement', () => chooseReportAlbumPhotos(context.remaining)
      .then(paths => this.uploadReportPhotoPaths(paths, context, { capture_source: 'unknown' }))
      .catch(error => handlePhotoActionFailure(error, message => new Promise(resolve => {
        wx.hideLoading();
        wx.showModal({ title: '照片处理未完成', content: message, showCancel: false, success: resolve, fail: resolve });
      }))), title => wx.showLoading({ title, mask: true }), () => wx.hideLoading())
      .then(result => this.finishReportPhotoAction(result),
        error => this.finishReportPhotoAction(null, error));
  },

  openReportTrustedCamera(context) {
    const sequence = (this._trustedCameraSequence || 0) + 1;
    this._trustedCameraSequence = sequence;
    context.requestId = 'manual_report_camera_' + context.siteId + '_' + Date.now() + '_' + sequence;
    this._trustedCameraRequest = context;
    const url = '/pages/trusted-camera/trusted-camera?request_id=' + encodeURIComponent(context.requestId)
      + '&site_id=' + encodeURIComponent(String(context.siteId))
      + '&capture_context=manual_report'
      + '&site_name=' + encodeURIComponent(context.siteName)
      + '&item_name=' + encodeURIComponent('异常上报现场');
    return new Promise((resolve, reject) => {
      let settled = false;
      const settle = (handler, value) => {
        if (settled) return;
        settled = true;
        if (this._trustedCameraRequest === context) this._trustedCameraRequest = null;
        handler(value);
      };
      wx.navigateTo({
        url,
        events: {
          trustedCameraCaptured: capture => {
            if (!capture || String(capture.requestId || '') !== context.requestId
                || this._trustedCameraRequest !== context
                || !this.reportPhotoContextIsCurrent(context)) {
              settle(reject, { error: '拍摄结果与当前上报草稿不一致，请重新拍摄' });
              return;
            }
            settle(resolve, capture);
          },
          trustedCameraCancelled: result => {
            if (!result || String(result.requestId || '') !== context.requestId) return;
            settle(reject, { errMsg: 'trustedCamera:fail cancel' });
          },
        },
        fail: error => settle(reject, error || { error: '现场相机页面打开失败' }),
      });
    });
  },

  captureAndUploadReportPhoto(context) {
    return this.openReportTrustedCamera(context).then(capture => {
      if (!capture.path || !capture.captureSession) {
        throw { error: '拍摄结果不完整，请重新拍摄' };
      }
      return this.uploadReportPhotoPaths([capture.path], context, reportCaptureMetadata(capture));
    });
  },

  confirmReportPhotoRecapture(target) {
    return new Promise(resolve => {
      wx.showModal({
        title: '拍摄授权已失效',
        content: target.error + (target.nextAction ? '\n' + target.nextAction : ''),
        confirmText: '重新拍摄',
        cancelText: '保留照片',
        success: result => resolve(!!result.confirm),
        fail: () => resolve(false),
      });
    }).then(confirm => {
      if (!confirm) return { cancelled: true };
      const context = this.reportPhotoContext({ allowFull: true });
      if (!context) return { cancelled: true };
      context.replaceKey = target.key;
      this.setData({ 'reportSheet.uploading': true });
      return runPhotoActionOnce(this, 'camera', () => this.captureAndUploadReportPhoto(context)
        .catch(error => handlePhotoActionFailure(error, message => new Promise(resolve => {
          wx.hideLoading();
          wx.showModal({ title: '照片处理未完成', content: message, showCancel: false, success: resolve, fail: resolve });
        }))), title => wx.showLoading({ title, mask: true }), () => wx.hideLoading())
        .then(result => this.finishReportPhotoAction(result),
          error => this.finishReportPhotoAction(null, error));
    });
  },

  onTakeReportPhoto() {
    const context = this.reportPhotoContext();
    if (!context) return;
    this.setData({ 'reportSheet.uploading': true });
    return runPhotoActionOnce(this, 'camera', () => this.captureAndUploadReportPhoto(context)
      .catch(error => handlePhotoActionFailure(error, message => new Promise(resolve => {
        wx.hideLoading();
        wx.showModal({ title: '照片处理未完成', content: message, showCancel: false, success: resolve, fail: resolve });
      }))), title => wx.showLoading({ title, mask: true }), () => wx.hideLoading())
      .then(result => this.finishReportPhotoAction(result),
        error => this.finishReportPhotoAction(null, error));
  },

  onSubmitReport() {
    const sheet = this.data.reportSheet || {};
    if (sheet.submitting || this._reportSubmitPromise) return this._reportSubmitPromise || Promise.resolve({ duplicate: true });
    const siteId = Number(sheet.siteId);
    const selectedSite = (sheet.sites || []).find(site => site.id === siteId);
    const description = String(sheet.description || '').trim();
    const reportType = (REPORT_TYPES[sheet.typeIndex] || REPORT_TYPES[0]).value;
    if (!selectedSite) { wx.showToast({ title: '请选择授权站点', icon: 'none' }); return Promise.resolve({ invalid: true }); }
    const photoItems = normalizeReportPhotoItems(sheet);
    const unfinished = photoItems.find(item => item.status !== 'uploaded');
    if (unfinished) {
      wx.showToast({ title: unfinished.status === 'failed' ? '请重试或移除上传失败照片' : '照片仍在上传，请稍候', icon: 'none' });
      return Promise.resolve({ invalid: true });
    }
    if (!description || !(sheet.photos || []).length) {
      wx.showToast({ title: '请填写说明并拍摄现场照片', icon: 'none' });
      return Promise.resolve({ invalid: true });
    }
    if (Number(sheet.photoSiteId) !== siteId) {
      wx.showToast({ title: '照片与当前站点不一致，请重新上传', icon: 'none' });
      return Promise.resolve({ invalid: true });
    }
    const preparedPhotos = prepareReportPhotoStoragePaths(sheet.photos);
    if (!preparedPhotos.ok) {
      wx.showToast({
        title: preparedPhotos.reason === 'invalid_count' ? '请上传 1 至 6 张现场照片' : '照片地址无效，请重新上传',
        icon: 'none',
      });
      return Promise.resolve({ invalid: true });
    }
    this.setData({ 'reportSheet.submitting': true });
    let locationWarning = '';
    const submitPromise = requestLocation().catch(error => {
      locationWarning = locationErrorMessage(error);
      return null;
    }).then(gps => api.submitManualReport({
      site_id: siteId,
      report_type: reportType,
      description,
      photo_urls: preparedPhotos.paths,
      gps_lat: gps && gps.lat,
      gps_lng: gps && gps.lng,
    })).then(response => {
      const orderNo = String((response && response.order_no) || '').trim();
      if (!orderNo) throw { error: '工单编号未返回，请重试' };
      if (!this.canUpdateReportDraft()) return { success: true, orderNo, stale: true };
      this._reportDraftVersion = (this._reportDraftVersion || 0) + 1;
      this.setData({ reportSheet: emptyReportSheet() });
      if (this._visible !== false) {
        this.load();
        this.showReportSuccess(orderNo, locationWarning);
      } else {
        this._reportSuccessNotice = { orderNo, locationWarning };
      }
      return { success: true, orderNo };
    }).catch(error => {
      if (this.canUpdateReportDraft()) {
        this.setData({ 'reportSheet.submitting': false });
        if (this._visible !== false) {
          wx.showModal({ title: '上报失败', content: (error && error.error) || '提交未完成，请检查网络后重试', showCancel: false });
        }
      }
      return { success: false, error };
    }).finally(() => {
      if (this._reportSubmitPromise === submitPromise) this._reportSubmitPromise = null;
    });
    this._reportSubmitPromise = submitPromise;
    return submitPromise;
  },

  showReportSuccess(orderNo, locationWarning) {
    wx.showModal({
      title: '异常已上报',
      content: '已生成工单：' + orderNo + (locationWarning ? '\n未获取定位：' + locationWarning : ''),
      showCancel: false,
    });
  },

  onOpenDetail(e) {
    const report = this.data.reports.find(item => item.id === Number(e.currentTarget.dataset.id));
    if (!report) return;
    this._reportDetailAutoRefreshUsed = false;
    this.setData({ detail: this.reportDetailValue(report, { mediaRefreshing: true, mediaError: '' }) });
    return this.refreshReportDetail(report.id, false).catch(() => null);
  },

  reportDetailValue(report, extra) {
    let storedPhotos = [];
    try { storedPhotos = JSON.parse(report.photo_urls || '[]'); } catch (_) {}
    const photos = reportPhotoItemsFromMedia(
      Array.isArray(storedPhotos) ? storedPhotos : [], report.photo_media
    ).map(item => ({ storedPath: item.storedPath, url: item.displayUrl }));
    return Object.assign({}, report, { photos }, extra || {});
  },

  refreshReportDetail(reportId, automatic) {
    const requestId = (this._reportDetailRequestId || 0) + 1;
    this._reportDetailRequestId = requestId;
    if (this.data.detail && Number(this.data.detail.id) === Number(reportId)) {
      this.setData({ 'detail.mediaRefreshing': true, 'detail.mediaError': '' });
    }
    return api.manualReports().then(rows => {
      if (requestId !== this._reportDetailRequestId || !this.data.detail
          || Number(this.data.detail.id) !== Number(reportId)) return { stale: true };
      const row = (Array.isArray(rows) ? rows : []).find(item => Number(item.id) === Number(reportId));
      if (!row) throw { error: '上报记录已变化，请返回列表刷新' };
      const current = this.data.reports.find(item => Number(item.id) === Number(reportId)) || {};
      const refreshed = Object.assign({}, current, row, {
        type_cn: TYPE[row.report_type] || row.report_type || '现场异常',
        status_cn: STATUS[row.status] || row.status || '待处理',
      });
      const reports = this.data.reports.map(item => Number(item.id) === Number(reportId) ? refreshed : item);
      this.setData({
        reports,
        detail: this.reportDetailValue(refreshed, { mediaRefreshing: false, mediaError: '' }),
      });
      this._reportDetailAutoRefreshUsed = !!automatic;
      return refreshed;
    }).catch(error => {
      if (requestId === this._reportDetailRequestId && this.data.detail
          && Number(this.data.detail.id) === Number(reportId)) {
        this.setData({
          'detail.mediaRefreshing': false,
          'detail.mediaError': errorMessage(error) || '照片加载失败，点击重试',
        });
        wx.showToast({ title: '照片加载失败，点击重试', icon: 'none' });
      }
      throw error;
    });
  },

  onCloseDetail() {
    this._reportDetailRequestId = (this._reportDetailRequestId || 0) + 1;
    this.setData({ detail: null });
  },

  onDetailPhotoMediaError() {
    const detail = this.data.detail;
    if (!detail) return;
    if (this._reportDetailAutoRefreshUsed) {
      this.setData({ 'detail.mediaRefreshing': false, 'detail.mediaError': '照片加载失败，点击重试' });
      return;
    }
    this._reportDetailAutoRefreshUsed = true;
    return this.refreshReportDetail(detail.id, true).catch(() => null);
  },

  onRetryDetailMedia() {
    const detail = this.data.detail;
    if (!detail) return;
    this._reportDetailAutoRefreshUsed = false;
    return this.refreshReportDetail(detail.id, false).catch(() => null);
  },

  onPreviewPhoto(e) {
    const detail = this.data.detail;
    const storedPath = e.currentTarget.dataset.storedPath;
    if (!detail || !storedPath) return;
    return this.refreshReportDetail(detail.id, false).then(() => {
      const photos = ((this.data.detail && this.data.detail.photos) || []).filter(photo => photo.url);
      const current = photos.find(photo => photo.storedPath === storedPath);
      if (current) wx.previewImage({ current: current.url, urls: photos.map(photo => photo.url) });
    }).catch(() => null);
  },

  goOrder(e) {
    const orderNo = e.currentTarget.dataset.order;
    if (!orderNo) return;
    getApp().globalData.selWorkorderNo = orderNo;
    wx.navigateTo({ url: '/pages/workorder/workorder' });
  },
});
