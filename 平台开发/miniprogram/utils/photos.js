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

module.exports = { chooseAndCompress, fileToBase64, persistFile, captureFlushedPhoto };
