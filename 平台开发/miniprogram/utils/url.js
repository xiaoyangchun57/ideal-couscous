// 把相对上传路径补全为后端可访问 URL（小程序 <image> 和 previewImage 需要完整 http 地址）
const CONFIG = require('./config.js');

function resolveUploadUrl(path) {
  if (!path) return path;
  if (typeof path !== 'string') return path;
  if (/^https?:\/\//.test(path)) return path;
  return CONFIG.BASE_URL + path;
}

function uploadStoragePath(url) {
  if (typeof url !== 'string' || !url) return '';
  if (url.startsWith('/uploads/')) return url;
  const prefix = CONFIG.BASE_URL.replace(/\/$/, '');
  if (url.startsWith(prefix + '/uploads/')) return url.slice(prefix.length);
  return '';
}

module.exports = { resolveUploadUrl, uploadStoragePath };
