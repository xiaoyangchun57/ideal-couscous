// 拍照/选图 + 压缩 + base64（弱网友好）
function chooseAndCompress(maxCount) {
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: maxCount || 1,
      mediaType: ['image'],
      sourceType: ['camera', 'album'],
      sizeType: ['compressed'],
      success(res) {
        const files = (res.tempFiles || []).map(f => f.tempFilePath);
        const tasks = files.map(compressOne);
        Promise.all(tasks).then(resolve).catch(reject);
      },
      fail: reject
    });
  });
}

// 巡检证据必须先保留原图，后端才能读取 EXIF 并在校验后生成压缩存储图。
function chooseInspectionPhotos(maxCount, captureSource) {
  if (captureSource !== 'supplement') {
    return Promise.reject({
      error: '正式现场拍照必须使用小程序内可信相机页',
      code: 'TRUSTED_CAMERA_PAGE_REQUIRED',
    });
  }
  const sourceType = ['album'];
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: maxCount || 1,
      mediaType: ['image'],
      sourceType,
      sizeType: ['original'],
      success(res) {
        const files = res && Array.isArray(res.tempFiles) ? res.tempFiles : [];
        const paths = files.map(file => file && file.tempFilePath)
          .filter(path => typeof path === 'string' && path.trim())
          .map(path => path.trim());
        if (!paths.length) {
          reject({ error: '未获取到有效照片，请重新选择' });
          return;
        }
        resolve(paths);
      },
      fail: reject
    });
  });
}

function isPhotoSelectionCancelled(error) {
  const text = String((error && (error.errMsg || error.message)) || '');
  return /cancel/i.test(text);
}

function photoCaptureErrorMessage(error) {
  if (error && error.error) return error.error;
  const text = String((error && (error.errMsg || error.message)) || '');
  if (/auth deny|auth denied|permission denied/i.test(text)) {
    return '相机权限未开启，请在小程序设置中允许使用相机后重试。';
  }
  if (/camera|chooseMedia/i.test(text)) {
    return '未能打开相机，请确认系统相机可用并允许微信使用相机。';
  }
  return '无法发起现场拍摄，请按提示确认到站状态、定位和网络后重试。';
}

function shouldOpenCameraSettings(error) {
  const text = String((error && (error.errMsg || error.message)) || '');
  return /auth deny|auth denied|permission denied/i.test(text);
}

function requestCaptureSessionWithLocation(requestLocationFn, createSessionFn) {
  return requestLocationFn()
    .catch(error => {
      throw Object.assign({}, error || {}, { capturePhase: 'location' });
    })
    .then(gps => createSessionFn(gps));
}

function captureSourceNeedsLocationSession(captureSource) {
  return captureSource === 'camera';
}

function photoActionOpeningTitle(captureSource) {
  if (captureSource === 'camera') return '正在打开相机';
  if (captureSource === 'supplement') return '正在打开补充材料';
  return '正在打开相机/相册';
}

function runPhotoActionOnce(owner, captureSource, action, onStart, onFinish) {
  if (owner && owner._photoActionPromise) return owner._photoActionPromise;
  if (!owner || typeof action !== 'function') {
    return Promise.reject(new Error('照片操作无法启动'));
  }
  if (typeof onStart === 'function') onStart(photoActionOpeningTitle(captureSource));
  const actionPromise = Promise.resolve().then(action);
  let trackedPromise;
  trackedPromise = actionPromise.finally(() => {
    if (owner._photoActionPromise !== trackedPromise) return;
    owner._photoActionPromise = null;
    if (typeof onFinish === 'function') onFinish();
  });
  owner._photoActionPromise = trackedPromise;
  return trackedPromise;
}

function validateReportPhotoPaths(paths) {
  const valid = Array.isArray(paths) && paths.length > 0
    && paths.every(path => typeof path === 'string' && path.trim());
  if (!valid) {
    const error = new Error('未获取到有效照片，请重新拍摄或选择');
    error.error = error.message;
    throw error;
  }
  return paths.map(path => path.trim());
}

function handlePhotoActionFailure(error, showError) {
  if (isPhotoSelectionCancelled(error)) return Promise.resolve({ cancelled: true });
  const message = photoCaptureErrorMessage(error);
  return Promise.resolve(typeof showError === 'function' ? showError(message) : undefined)
    .then(() => ({ cancelled: false, message }));
}

function deletePendingPhotoOnce(owner, displayUrl, storagePath, requestDelete, getPhotos) {
  if (!owner || !storagePath || typeof requestDelete !== 'function') {
    return Promise.reject({ error: '照片删除无法启动，请重试' });
  }
  if (!owner._pendingReportPhotoDeletes) owner._pendingReportPhotoDeletes = {};
  if (owner._pendingReportPhotoDeletes[storagePath]) {
    return owner._pendingReportPhotoDeletes[storagePath];
  }
  let trackedPromise;
  trackedPromise = Promise.resolve().then(() => requestDelete(storagePath)).then(response => {
    const confirmed = response && response.success === true && response.path === storagePath
      && (response.deleted === true || response.already_deleted === true);
    if (!confirmed) throw { error: '服务响应异常，照片仍保留，请重试' };
    const photos = typeof getPhotos === 'function' ? getPhotos() : [];
    return (Array.isArray(photos) ? photos : []).filter(item => item !== displayUrl);
  }).finally(() => {
    if (owner._pendingReportPhotoDeletes
        && owner._pendingReportPhotoDeletes[storagePath] === trackedPromise) {
      delete owner._pendingReportPhotoDeletes[storagePath];
    }
  });
  owner._pendingReportPhotoDeletes[storagePath] = trackedPromise;
  return trackedPromise;
}

function deletePendingReportPhotoOnce(owner, displayUrl, storagePath, requestDelete, getPhotos) {
  return deletePendingPhotoOnce(owner, displayUrl, storagePath, requestDelete, getPhotos);
}

function inspectionUploadTaskResult(response, context, resolveUrl) {
  const result = response || {};
  const details = context || {};
  const rawUrl = typeof result.url === 'string' ? result.url.trim() : '';
  if (result.supplemental === true && rawUrl) {
    const displayUrl = typeof result.display_url === 'string' && result.display_url.trim()
      ? result.display_url.trim() : rawUrl;
    const resolved = typeof resolveUrl === 'function' ? resolveUrl(displayUrl) : displayUrl;
    return {
      supplementUrl: resolved,
      supplementAttachment: {
        id: Number(result.id) || 0,
        url: resolved,
        storagePath: rawUrl,
      },
    };
  }
  if (result.accepted_for_review === false) {
    return Object.assign({ rejected: result }, details);
  }
  if (!rawUrl) return {};
  const displayUrl = typeof result.display_url === 'string' && result.display_url.trim()
    ? result.display_url.trim() : rawUrl;
  return { url: typeof resolveUrl === 'function' ? resolveUrl(displayUrl) : displayUrl };
}

function collectInspectionPhotoUploadResults(results) {
  const summary = {
    urls: [], supplementUrls: [], localPaths: [], localMetadata: [],
    supplementAttachments: [], supplementLocalPaths: [], supplementLocalMetadata: [], issues: [],
  };
  (Array.isArray(results) ? results : []).forEach((result, index) => {
    if (result && result.status === 'fulfilled') {
      const value = result.value || {};
      const url = typeof value.url === 'string' ? value.url.trim() : '';
      const supplementUrl = typeof value.supplementUrl === 'string'
        ? value.supplementUrl.trim() : '';
      const localPath = typeof value.localPath === 'string' ? value.localPath.trim() : '';
      const businessRejected = value.rejected && typeof value.rejected === 'object';
      if (url) {
        summary.urls.push(url);
      } else if (supplementUrl) {
        summary.supplementUrls.push(supplementUrl);
        if (value.supplementAttachment) {
          summary.supplementAttachments.push(value.supplementAttachment);
        }
      } else if (localPath) {
        if (value.supplemental === true) {
          summary.supplementLocalPaths.push(localPath);
          summary.supplementLocalMetadata.push(value.metadata || {});
        } else {
          summary.localPaths.push(localPath);
          summary.localMetadata.push(value.metadata || {});
        }
      } else if (businessRejected) {
        summary.issues.push({ kind: 'rejected', index, value });
      } else {
        summary.issues.push({
          kind: 'failed',
          index,
          error: { error: '服务响应异常，请重试' },
        });
      }
    } else {
      summary.issues.push({
        kind: 'failed',
        index,
        error: (result && result.reason) || { error: '照片上传失败' },
      });
    }
  });
  return summary;
}

function inspectionPhotoIssueFeedback(issue) {
  if (issue && issue.kind === 'rejected') {
    const rejected = issue.value && issue.value.rejected || {};
    const reason = typeof rejected.reason === 'string' && rejected.reason.trim()
      ? rejected.reason.trim() : '无法确认拍摄信息';
    const nextAction = typeof rejected.next_action === 'string' && rejected.next_action.trim()
      ? rejected.next_action.trim() : '请重新拍摄或重新选择';
    return {
      title: '照片未采用',
      message: /重复/.test(reason)
        ? '与已有照片重复，请改拍其他照片。'
        : `${reason}。${nextAction}`,
    };
  }
  return { title: '上传未完成', message: photoCaptureErrorMessage(issue && issue.error) };
}

function inspectionPhotoIssueMessage(issue) {
  return inspectionPhotoIssueFeedback(issue).message;
}

function setInspectionPhotoIssueMessage(owner, issue) {
  const feedback = inspectionPhotoIssueFeedback(issue);
  if (owner && typeof owner.setData === 'function') {
    owner.setData({
      'sheet.photoResultTitle': feedback.title,
      'sheet.photoResultMessage': feedback.message,
    });
  }
  return feedback.message;
}

function processPhotoUploadIssues(issues, promptIssue, retainSupplement) {
  return (Array.isArray(issues) ? issues : []).reduce((chain, issue) => chain
    .then(() => Promise.resolve(promptIssue(issue)))
    .then(choice => {
      const canKeep = issue.kind === 'rejected'
        && issue.value && issue.value.rejected
        && issue.value.rejected.can_keep_as_supplement;
      if (choice === 'supplement' && canKeep) {
        return Promise.resolve(retainSupplement(issue.value));
      }
      return undefined;
    }), Promise.resolve());
}

function retainRejectedPhotoAsSupplement(failedPhoto, handlers) {
  const source = failedPhoto || {};
  const actions = handlers || {};
  const originalMetadata = source.metadata && typeof source.metadata === 'object'
    ? source.metadata : {};
  const originalKey = source.idempotencyKey || originalMetadata._idempotency_key || '';
  const idempotencyKey = String(originalKey).endsWith(':supplement')
    ? String(originalKey) : `${originalKey}:supplement`;
  const metadata = Object.assign({}, originalMetadata, {
    _idempotency_key: idempotencyKey,
    capture_source: 'supplement',
  });
  delete metadata.capture_session;
  delete metadata.gps_lat;
  delete metadata.gps_lng;
  delete metadata.taken_at;
  if (!source.path || !source.image || typeof actions.persist !== 'function'
      || typeof actions.upload !== 'function') {
    return Promise.reject({ error: '原照片保留信息不完整，请重新选择补充材料' });
  }
  return Promise.resolve().then(() => actions.persist(source.path)).then(localPath => {
    if (!localPath) throw { error: '原照片保留失败，请重新选择补充材料' };
    const draft = { localPath, metadata, supplemental: true };
    return Promise.resolve(typeof actions.onPersisted === 'function'
      ? actions.onPersisted(draft) : undefined).then(() => actions.upload(
      source.image, idempotencyKey, metadata)).then(result => {
      if (!result || !result.url || result.supplemental !== true) {
        throw { error: '补充材料保存结果异常，请重试上传' };
      }
      return Promise.resolve(typeof actions.cleanup === 'function'
        ? actions.cleanup(localPath) : undefined).then(() => ({
        supplementUrl: result.display_url || result.url,
        localPath,
        metadata,
        response: result,
      }));
    }).catch(error => Object.assign({}, draft, { uploadError: error }));
  });
}

function compressOne(path) {
  return new Promise((resolve) => {
    wx.compressImage({
      src: path,
      quality: 70,
      success(r) { resolve(r.tempFilePath); },
      fail() { resolve(path); } // 压缩失败降级用原图
    });
  });
}

function fileToBase64(filePath) {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager();
    fs.readFile({
      filePath,
      encoding: 'base64',
      success(r) { resolve('data:image/jpeg;base64,' + r.data); },
      fail: reject
    });
  });
}

function persistFile(tempFilePath) {
  return new Promise((resolve) => {
    if (!tempFilePath || tempFilePath.indexOf('wxfile://usr/') === 0) {
      resolve(tempFilePath);
      return;
    }
    wx.saveFile({
      tempFilePath,
      success(r) { resolve(r.savedFilePath || tempFilePath); },
      fail() { resolve(tempFilePath); }
    });
  });
}

function persistFileStrict(tempFilePath) {
  return new Promise((resolve, reject) => {
    if (!tempFilePath) {
      reject({ error: '原照片保存失败，请立即重新选择补充材料' });
      return;
    }
    if (tempFilePath.indexOf('wxfile://usr/') === 0) {
      resolve(tempFilePath);
      return;
    }
    wx.saveFile({
      tempFilePath,
      success(r) {
        if (r && r.savedFilePath) resolve(r.savedFilePath);
        else reject({ error: '原照片保存失败，请立即重新选择补充材料' });
      },
      fail(cause) {
        reject({ error: '原照片保存失败，请立即重新选择补充材料', cause });
      }
    });
  });
}

// 失败队列重传成功回调：把已上传照片的 URL 回填到当前巡检页（若详情面板仍打开）
// 用于「照片上传接入失败队列」——弱网时上传请求入队，网络恢复重试成功后此处补回 UI
const { resolveUploadUrl } = require('./url.js');
function captureFlushedPhoto(task, resp) {
  if (!task || !task.url || task.url.indexOf('upload-site-photo') === -1) return;
  if (!resp || !resp.url) return;
  const url = resolveUploadUrl(resp.url);
  const pages = getCurrentPages();
  const cur = pages[pages.length - 1];
  if (cur && cur.route && cur.route.indexOf('inspection') !== -1 && cur.data && cur.data.sheet && cur.data.sheet.open) {
    const photos = cur.data.sheet.photos || [];
    if (photos.indexOf(url) === -1) cur.setData({ 'sheet.photos': photos.concat(url) });
  }
}

module.exports = {
  chooseAndCompress, chooseInspectionPhotos, fileToBase64, persistFile, persistFileStrict,
  captureFlushedPhoto,
  isPhotoSelectionCancelled, photoCaptureErrorMessage, shouldOpenCameraSettings,
  requestCaptureSessionWithLocation, captureSourceNeedsLocationSession,
  collectInspectionPhotoUploadResults, processPhotoUploadIssues,
  retainRejectedPhotoAsSupplement,
  inspectionPhotoIssueFeedback, inspectionPhotoIssueMessage, setInspectionPhotoIssueMessage,
  photoActionOpeningTitle, runPhotoActionOnce,
  validateReportPhotoPaths, handlePhotoActionFailure,
  deletePendingReportPhotoOnce,
  deletePendingPhotoOnce, inspectionUploadTaskResult,
};
