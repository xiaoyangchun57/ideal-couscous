// 把相对上传路径补全为后端可访问 URL（小程序 <image> 和 previewImage 需要完整 http 地址）
const CONFIG = require('./config.js');

function resolveUploadUrl(path) {
  if (!path) return path;
  if (typeof path !== 'string') return path;
  if (/^https?:\/\//.test(path)) return path;
  return CONFIG.BASE_URL + path;
}

module.exports = { resolveUploadUrl };
