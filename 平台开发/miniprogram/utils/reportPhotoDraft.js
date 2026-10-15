const { resolveUploadUrl, uploadStoragePath } = require('./url.js');

function errorMessage(error) {
  return String((error && (error.reason || error.error || error.message || error.errMsg)) || '照片上传失败，请重试');
}

function failureDetails(error) {
  const code = String((error && error.code) || '');
  return {
    code,
    error: errorMessage(error),
    nextAction: String((error && error.next_action) || ''),
    requiresNewCapture: code === 'CAPTURE_SESSION_REQUIRED',
  };
}

function storedPhotoItem(value, index) {
  const displayUrl = typeof value === 'string' ? resolveUploadUrl(value) : '';
  const storedPath = uploadStoragePath(displayUrl);
  if (!storedPath) return null;
  return {
    key: 'stored_' + index + '_' + storedPath,
    status: 'uploaded',
    storedPath,
    displayUrl,
    localPath: '',
    idempotencyKey: '',
    metadata: {},
    error: '',
    nextAction: '',
    failureCode: '',
    requiresNewCapture: false,
    attachmentId: null,
    mediaError: '',
    mediaRefreshing: false,
    mediaAutoRefreshUsed: false,
  };
}

function normalizeReportPhotoItems(sheet) {
  if (sheet && Array.isArray(sheet.photoItems) && sheet.photoItems.length) {
    return sheet.photoItems.map(item => Object.assign({}, item));
  }
  return ((sheet && sheet.photos) || [])
    .map(storedPhotoItem)
    .filter(Boolean);
}

function createPendingReportPhoto(path, idempotencyKey, metadata) {
  return {
    key: idempotencyKey,
    status: 'uploading',
    storedPath: '',
    displayUrl: path,
    localPath: path,
    idempotencyKey,
    metadata: Object.assign({}, metadata || {}, { _idempotency_key: idempotencyKey }),
    error: '',
    nextAction: '',
    failureCode: '',
    requiresNewCapture: false,
    attachmentId: null,
    mediaError: '',
    mediaRefreshing: false,
    mediaAutoRefreshUsed: false,
  };
}

function uploadedReportPhoto(item, response) {
  const storedPath = uploadStoragePath(response && response.url);
  const displayUrl = resolveUploadUrl((response && response.display_url) || '');
  if (!storedPath || !displayUrl) {
    const responseFailure = failureDetails(response);
    const hasServerFailure = !!(responseFailure.code || responseFailure.nextAction
      || (response && response.reason));
    return failedReportPhoto(
      item,
      hasServerFailure ? response : { error: '照片保存结果不完整，请重试上传' }
    );
  }
  return Object.assign({}, item, {
    status: 'uploaded',
    storedPath,
    displayUrl,
    localPath: '',
    error: '',
    nextAction: '',
    failureCode: '',
    requiresNewCapture: false,
    attachmentId: Number(response.id) || null,
    mediaError: '',
    mediaRefreshing: false,
    mediaAutoRefreshUsed: false,
  });
}

function failedReportPhoto(item, error) {
  const details = failureDetails(error);
  return Object.assign({}, item, {
    status: 'failed',
    displayUrl: item.localPath || item.displayUrl || '',
    error: details.error,
    nextAction: details.nextAction,
    failureCode: details.code,
    requiresNewCapture: details.requiresNewCapture,
  });
}

function refreshedReportPhoto(item, response, autoRefreshUsed) {
  const storedPath = uploadStoragePath(response && response.stored_path);
  const displayUrl = resolveUploadUrl((response && response.display_url) || '');
  const attachmentId = Number(response && response.id);
  if (!storedPath || !displayUrl || !attachmentId
      || storedPath !== uploadStoragePath(item && item.storedPath)
      || (Number(item && item.attachmentId) && Number(item.attachmentId) !== attachmentId)) {
    return null;
  }
  return Object.assign({}, item, {
    storedPath,
    displayUrl,
    attachmentId,
    mediaError: '',
    mediaRefreshing: false,
    mediaAutoRefreshUsed: !!autoRefreshUsed,
  });
}

function reportCaptureMetadata(capture) {
  return {
    capture_context: 'manual_report',
    capture_source: 'camera',
    capture_session: capture.captureSession,
    gps_lat: capture.gpsLat,
    gps_lng: capture.gpsLng,
    capture_client: 'trusted_camera_v1',
    capture_frame_orientation: capture.frameOrientation,
    capture_frame_ratio: capture.frameRatio,
    capture_frame_mode: capture.frameMode,
    display_watermark: capture.displayWatermark !== false,
  };
}

function reportPhotoSheetPatch(items, siteId) {
  const normalized = (items || []).filter(Boolean);
  const photos = normalized
    .filter(item => item.status === 'uploaded' && uploadStoragePath(item.storedPath))
    .map(item => uploadStoragePath(item.storedPath));
  return {
    'reportSheet.photoItems': normalized,
    'reportSheet.photos': photos,
    'reportSheet.photoSiteId': normalized.length ? siteId : null,
  };
}

function reportPhotoDisplayUrls(items) {
  return (items || [])
    .map(item => item.displayUrl || item.localPath || '')
    .filter(Boolean);
}

function reportPhotoItemsFromMedia(photoUrls, photoMedia) {
  const mediaByPath = new Map();
  (Array.isArray(photoMedia) ? photoMedia : []).forEach(item => {
    const storedPath = uploadStoragePath(item && item.stored_path);
    if (storedPath) mediaByPath.set(storedPath, item);
  });
  return (Array.isArray(photoUrls) ? photoUrls : []).map((value, index) => {
    const storedPath = uploadStoragePath(value);
    if (!storedPath) return null;
    const media = mediaByPath.get(storedPath) || {};
    return {
      key: 'report_media_' + index + '_' + storedPath,
      storedPath,
      displayUrl: resolveUploadUrl(media.display_url || ''),
      attachmentId: Number(media.id) || null,
      mediaError: '',
      mediaRefreshing: false,
      mediaAutoRefreshUsed: false,
    };
  }).filter(Boolean);
}

module.exports = {
  createPendingReportPhoto,
  errorMessage,
  failedReportPhoto,
  normalizeReportPhotoItems,
  reportPhotoDisplayUrls,
  reportPhotoItemsFromMedia,
  reportPhotoSheetPatch,
  refreshedReportPhoto,
  reportCaptureMetadata,
  uploadedReportPhoto,
};
