const api = require('../../services/api.js');
const {
  chooseAndCompress, fileToBase64, runPhotoActionOnce,
  validateReportPhotoPaths, handlePhotoActionFailure, deletePendingReportPhotoOnce,
} = require('../../utils/photos.js');
const { resolveUploadUrl, uploadStoragePath, prepareReportPhotoStoragePaths } = require('../../utils/url.js');
const { requestLocation, locationErrorMessage } = require('../../utils/location.js');

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
    typeIndex: 0, description: '', photos: [], photoSiteId: null,
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

function displayPhotoUrl(path) {
  if (typeof path !== 'string') return '';
  const value = path.trim();
  if (value.startsWith('/uploads/')) return resolveUploadUrl(value);
  return /^https?:\/\/[^\s/]+(?:\/|$)/i.test(value) ? value : '';
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
    if (!selected && sites.length === 1 && !(current.photos || []).length) {
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
    if (Number(sheet.siteId) !== selected.id && (sheet.photos || []).length) {
      wx.showToast({ title: '请先移除已上传照片后再切换站点', icon: 'none' });
      return;
    }
    this.setData({
      'reportSheet.siteId': selected.id,
      'reportSheet.siteName': selected.name,
      'reportSheet.siteIndex': index,
      'reportSheet.photoSiteId': null,
    });
  },

  onReportType(e) { this.setData({ 'reportSheet.typeIndex': Number(e.detail.value) || 0 }); },

  onReportDescription(e) { this.setData({ 'reportSheet.description': e.detail.value || '' }); },

  onAddReportPhoto() {
    const sheet = this.data.reportSheet || {};
    const siteId = Number(sheet.siteId);
    const sites = sheet.sites || [];
    if (sheet.submitting || sheet.uploading) return;
    if (!sites.some(site => site.id === siteId)) {
      wx.showToast({ title: '请先选择授权站点', icon: 'none' });
      return;
    }
    const current = sheet.photos || [];
    const remaining = 6 - current.length;
    if (remaining <= 0) { wx.showToast({ title: '最多上传 6 张照片', icon: 'none' }); return; }
    this.setData({ 'reportSheet.uploading': true });
    return runPhotoActionOnce(this, 'media_picker', () => chooseAndCompress(remaining)
      .then(validateReportPhotoPaths)
      .then(paths => {
        wx.showLoading({ title: '上传中', mask: true });
        return Promise.allSettled(paths.map((path, index) => {
          const idempotencyKey = photoIdempotencyKey(siteId, path, index);
          return fileToBase64(path).then(image => api.uploadSitePhoto(
            siteId, image, idempotencyKey, { _idempotency_key: idempotencyKey }
          ));
        }));
      }).then(results => {
        if (!this.canUpdateReportDraft()) return { stale: true };
        const active = this.data.reportSheet || {};
        if (Number(active.siteId) !== siteId) return { stale: true };
        const uploaded = results.filter(result => result.status === 'fulfilled' && result.value && result.value.url)
          .map(result => resolveUploadUrl(String(result.value.url).trim()))
          .filter(url => uploadStoragePath(url));
        const photos = current.concat(uploaded.filter(url => current.indexOf(url) === -1));
        this.setData({
          'reportSheet.photos': photos,
          'reportSheet.photoSiteId': photos.length ? siteId : null,
        });
        if (uploaded.length !== results.length) {
          wx.showToast({ title: uploaded.length ? '部分照片上传失败' : '照片上传失败', icon: 'none' });
        }
        return { uploaded: uploaded.length };
      }).catch(error => handlePhotoActionFailure(error, message => new Promise(resolve => {
        wx.hideLoading();
        wx.showModal({ title: '照片处理未完成', content: message, showCancel: false, success: resolve, fail: resolve });
      }))), title => wx.showLoading({ title, mask: true }), () => wx.hideLoading())
      .then(result => {
        if (this.canUpdateReportDraft()) this.setData({ 'reportSheet.uploading': false });
        return result;
      }, error => {
        if (this.canUpdateReportDraft()) this.setData({ 'reportSheet.uploading': false });
        return { error };
      });
  },

  onPreviewReportPhoto(e) {
    const photos = (this.data.reportSheet && this.data.reportSheet.photos) || [];
    const current = e.currentTarget.dataset.url;
    if (current && photos.indexOf(current) !== -1) wx.previewImage({ current, urls: photos });
  },

  onRemoveReportPhoto(e) {
    const url = e.currentTarget.dataset.url;
    const storagePath = uploadStoragePath(url);
    const sheet = this.data.reportSheet || {};
    if (!url || !storagePath || sheet.submitting || sheet.uploading) {
      if (!storagePath) wx.showToast({ title: '照片地址无效，请重新上传', icon: 'none' });
      return;
    }
    return deletePendingReportPhotoOnce(
      this, url, storagePath, path => api.deletePendingSitePhoto(path),
      () => this.data.reportSheet.photos || []
    ).then(photos => {
      this.setData({
        'reportSheet.photos': photos,
        'reportSheet.photoSiteId': photos.length ? sheet.photoSiteId : null,
      });
      return { success: true };
    }).catch(error => {
      wx.showToast({ title: (error && error.error) || '照片删除失败，请重试', icon: 'none' });
      return { success: false, error };
    });
  },

  onSubmitReport() {
    const sheet = this.data.reportSheet || {};
    if (sheet.submitting || this._reportSubmitPromise) return this._reportSubmitPromise || Promise.resolve({ duplicate: true });
    const siteId = Number(sheet.siteId);
    const selectedSite = (sheet.sites || []).find(site => site.id === siteId);
    const description = String(sheet.description || '').trim();
    const reportType = (REPORT_TYPES[sheet.typeIndex] || REPORT_TYPES[0]).value;
    if (!selectedSite) { wx.showToast({ title: '请选择授权站点', icon: 'none' }); return Promise.resolve({ invalid: true }); }
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
    let storedPhotos = [];
    try { storedPhotos = JSON.parse(report.photo_urls || '[]'); } catch (_) {}
    const photos = (Array.isArray(storedPhotos) ? storedPhotos : []).map(storedPath => ({
      storedPath,
      url: displayPhotoUrl(storedPath),
    }));
    this.setData({ detail: Object.assign({}, report, { photos }) });
  },

  onCloseDetail() { this.setData({ detail: null }); },

  onPreviewPhoto(e) {
    const current = e.currentTarget.dataset.src;
    const photos = ((this.data.detail && this.data.detail.photos) || [])
      .map(photo => photo.url).filter(Boolean);
    if (current && photos.length) wx.previewImage({ current, urls: photos });
  },

  goOrder(e) {
    const orderNo = e.currentTarget.dataset.order;
    if (!orderNo) return;
    getApp().globalData.selWorkorderNo = orderNo;
    wx.navigateTo({ url: '/pages/workorder/workorder' });
  },
});
