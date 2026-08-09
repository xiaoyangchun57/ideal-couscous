// 后端基础地址（HTTPS + 已备案域名）
// 开发阶段：在微信开发者工具「详情 → 本地设置」勾选「不校验合法域名」
// 生产阶段：改为真实 HTTPS 域名，并在小程序后台配置 request 合法域名
//
// 开发者工具用于本地回归，真机预览和正式版始终访问线上后端。
// 这样本地模拟数据不会与线上数据混淆，也不会把 127.0.0.1 带入真机版本。
const USE_LOCAL_API_IN_DEVTOOLS = true;
const LOCAL_API_BASE_URL = 'http://127.0.0.1:5000';

// 微信开发者工具在不同版本中可能报告 devtools、windows 或 mac。
// 真机只会报告 ios/android，因此桌面平台可以安全地视为本地开发工具。
const DEVTOOLS_PLATFORMS = ['devtools', 'windows', 'mac'];
let runtimePlatform = '';
try {
  if (typeof wx !== 'undefined' && wx.getSystemInfoSync) {
    runtimePlatform = wx.getSystemInfoSync().platform || '';
  }
} catch (_) {
  // Runtime information may be unavailable during static checks.
}
const isDevtools = DEVTOOLS_PLATFORMS.indexOf(runtimePlatform) !== -1;

const CONFIG = {
  BASE_URL: USE_LOCAL_API_IN_DEVTOOLS && isDevtools
    ? LOCAL_API_BASE_URL
    : 'https://ops.hhyc-tec.cn',
  API_PROFILE: USE_LOCAL_API_IN_DEVTOOLS && isDevtools ? 'local' : 'online'
};

module.exports = CONFIG;
