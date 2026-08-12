// Production is the default for every runtime, including the WeChat devtools.
// Local API access is an explicit, disposable devtools-only override.
const ONLINE_API_BASE_URL = 'https://ops.hhyc-tec.cn';
const API_OVERRIDE_STORAGE_KEY = 'api_base_url_override';
const DEVTOOLS_PLATFORMS = ['devtools', 'windows', 'mac'];

function runtimePlatform() {
  try {
    if (typeof wx !== 'undefined' && wx.getSystemInfoSync) {
      return wx.getSystemInfoSync().platform || '';
    }
  } catch (_) {
    // Static checks and unit tests may not provide wx.
  }
  return '';
}

function isDevtoolsRuntime() {
  return DEVTOOLS_PLATFORMS.indexOf(runtimePlatform()) !== -1;
}

function readOverride() {
  if (!isDevtoolsRuntime()) return '';
  try {
    return wx.getStorageSync(API_OVERRIDE_STORAGE_KEY) || '';
  } catch (_) {
    return '';
  }
}

function setApiBaseOverride(baseUrl) {
  if (!isDevtoolsRuntime()) return false;
  if (typeof baseUrl !== 'string' || !/^http:\/\/127\.0\.0\.1:\d+$/.test(baseUrl)) return false;
  try {
    wx.setStorageSync(API_OVERRIDE_STORAGE_KEY, baseUrl);
    return true;
  } catch (_) {
    return false;
  }
}

function clearApiOverride() {
  try {
    if (typeof wx !== 'undefined' && wx.removeStorageSync) {
      wx.removeStorageSync(API_OVERRIDE_STORAGE_KEY);
    }
  } catch (_) {
    // Clearing an already absent override is intentionally idempotent.
  }
}

const overrideUrl = readOverride();
const localOverride = /^http:\/\/127\.0\.0\.1:\d+$/.test(overrideUrl) && isDevtoolsRuntime();

module.exports = {
  BASE_URL: localOverride ? overrideUrl : ONLINE_API_BASE_URL,
  API_PROFILE: localOverride ? 'local' : 'online',
  ONLINE_API_BASE_URL,
  API_OVERRIDE_STORAGE_KEY,
  setApiBaseOverride,
  clearApiOverride,
};
