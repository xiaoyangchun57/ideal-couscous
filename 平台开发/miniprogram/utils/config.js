// Production is the default for every runtime, including the WeChat devtools.
// Local API access is an explicit, disposable devtools-only override.
const ONLINE_API_BASE_URL = 'https://ops.hhyc-tec.cn';
const API_OVERRIDE_STORAGE_KEY = 'api_base_url_override';
const API_OVERRIDE_TTL_MS = 24 * 60 * 60 * 1000;
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
    const stored = wx.getStorageSync(API_OVERRIDE_STORAGE_KEY);
    // Legacy string overrides had no expiry and are unsafe after the local
    // service disappears. Clear them once and return to the online default.
    if (typeof stored === 'string') {
      if (stored) clearApiOverride();
      return '';
    }
    if (!stored || typeof stored !== 'object') return '';
    if (!Number.isFinite(Number(stored.expires_at)) || Number(stored.expires_at) <= Date.now()) {
      clearApiOverride();
      return '';
    }
    return stored.url || '';
  } catch (_) {
    return '';
  }
}

function setApiBaseOverride(baseUrl, now) {
  if (!isDevtoolsRuntime()) return false;
  if (typeof baseUrl !== 'string' || !/^http:\/\/127\.0\.0\.1:\d+$/.test(baseUrl)) return false;
  try {
    wx.setStorageSync(API_OVERRIDE_STORAGE_KEY, {
      url: baseUrl,
      expires_at: Number(now == null ? Date.now() : now) + API_OVERRIDE_TTL_MS
    });
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
  API_OVERRIDE_TTL_MS,
  setApiBaseOverride,
  clearApiOverride,
};
