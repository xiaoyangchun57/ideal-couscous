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
  const sourceType = captureSource === 'camera' ? ['camera'] : ['album'];
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: maxCount || 1,
      mediaType: ['image'],
      sourceType,
      sizeType: ['original'],
      success(res) {
        resolve((res.tempFiles || []).map(file => file.tempFilePath).filter(Boolean));
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

function collectInspectionPhotoUploadResults(results) {
  const summary = { urls: [], localPaths: [], localMetadata: [], issues: [] };
  (Array.isArray(results) ? results : []).forEach((result, index) => {
    if (result && result.status === 'fulfilled') {
      const value = result.value || {};
      if (value.url) summary.urls.push(value.url);
      else if (value.localPath) {
        summary.localPaths.push(value.localPath);
        summary.localMetadata.push(value.metadata || {});
      } else if (value.rejected) {
        summary.issues.push({ kind: 'rejected', index, value });
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
  chooseAndCompress, chooseInspectionPhotos, fileToBase64, persistFile, captureFlushedPhoto,
  isPhotoSelectionCancelled, photoCaptureErrorMessage, shouldOpenCameraSettings,
  requestCaptureSessionWithLocation, captureSourceNeedsLocationSession,
  collectInspectionPhotoUploadResults, processPhotoUploadIssues,
};
