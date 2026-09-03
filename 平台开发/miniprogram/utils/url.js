// 把相对上传路径补全为后端可访问 URL（小程序 <image> 和 previewImage 需要完整 http 地址）
const CONFIG = require('./config.js');

function resolveUploadUrl(path) {
  if (!path) return path;
  if (typeof path !== 'string') return path;
  if (/^https?:\/\//.test(path)) return path;
  return CONFIG.BASE_URL + path;
}

function uploadStoragePath(url) {
  if (typeof url !== 'string' || !url || url.trim() !== url || /[?#]/.test(url)) return '';
  if (url.startsWith('/uploads/')) return url.length > '/uploads/'.length ? url : '';
  const prefix = CONFIG.BASE_URL.replace(/\/$/, '');
  const absolutePrefix = prefix + '/uploads/';
  if (url.startsWith(absolutePrefix) && url.length > absolutePrefix.length) return url.slice(prefix.length);
  return '';
}

function prepareReportPhotoStoragePaths(urls) {
  if (!Array.isArray(urls)) return { ok: false, paths: [], reason: 'invalid_path' };
  const paths = [];
  for (const url of urls) {
    const path = uploadStoragePath(url);
    if (!path) return { ok: false, paths: [], reason: 'invalid_path' };
    if (paths.indexOf(path) === -1) paths.push(path);
  }
  if (paths.length < 1 || paths.length > 6) return { ok: false, paths: [], reason: 'invalid_count' };
  return { ok: true, paths };
}

module.exports = { resolveUploadUrl, uploadStoragePath, prepareReportPhotoStoragePaths };
