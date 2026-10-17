const ONLINE_API_BASE_URL = 'https://ops.hhyc-tec.cn';
const DEFAULT_LOCAL_API_BASE_URL = 'http://127.0.0.1:5000';
const PREPARED_REAL_DEVICE_API_BASE_URL = 'http://192.168.2.108:5000'; // local-api-prepared-value

function runtimeEnvVersion() {
  try {
    const accountInfo = wx.getAccountInfoSync && wx.getAccountInfoSync();
    return (accountInfo && accountInfo.miniProgram && accountInfo.miniProgram.envVersion) || '';
  } catch (_) {
    // Static checks and unit tests may not provide wx.
    return '';
  }
}

function runtimePlatform() {
  try {
    const info = wx.getSystemInfoSync && wx.getSystemInfoSync();
    return String((info && info.platform) || '').toLowerCase();
  } catch (_) {
    return '';
  }
}

const API_PROFILE = runtimeEnvVersion() === 'develop' ? 'local' : 'online';

function normalizePreparedLocalApiBaseUrl(value) {
  if (typeof value !== 'string') return '';
  const candidate = value.trim().replace(/\/+$/, '');
  const match = candidate.match(/^http:\/\/([^/:]+)(?::(\d{1,5}))?$/i);
  if (!match) return '';
  const host = match[1].toLowerCase();
  const octets = host.split('.').map(Number);
  const privateIpv4 = octets.length === 4
    && octets.every(part => Number.isInteger(part) && part >= 0 && part <= 255)
    && (octets[0] === 10
      || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
      || (octets[0] === 192 && octets[1] === 168));
  const localHost = host === 'localhost' || host === '127.0.0.1';
  const port = match[2] ? Number(match[2]) : 80;
  return privateIpv4 && !localHost && port > 0 && port <= 65535 ? candidate : '';
}

function resolveLocalApiBaseUrl() {
  const platform = runtimePlatform();
  if (!['ios', 'android'].includes(platform)) {
    return DEFAULT_LOCAL_API_BASE_URL;
  }
  const prepared = normalizePreparedLocalApiBaseUrl(PREPARED_REAL_DEVICE_API_BASE_URL);
  if (prepared) return prepared;
  throw new Error(
    'REAL_DEVICE_LOCAL_API_NOT_PREPARED: run the local API preparation script before debugging',
  );
}

const LOCAL_API_BASE_URL = API_PROFILE === 'local'
  ? resolveLocalApiBaseUrl()
  : DEFAULT_LOCAL_API_BASE_URL;

module.exports = {
  BASE_URL: API_PROFILE === 'local' ? LOCAL_API_BASE_URL : ONLINE_API_BASE_URL,
  API_PROFILE,
  DEFAULT_LOCAL_API_BASE_URL,
  LOCAL_API_BASE_URL,
  ONLINE_API_BASE_URL,
  PREPARED_REAL_DEVICE_API_BASE_URL,
};
