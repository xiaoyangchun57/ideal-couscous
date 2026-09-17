const ONLINE_API_BASE_URL = 'https://ops.hhyc-tec.cn';
const LOCAL_API_BASE_URL = 'http://192.168.2.107:5000';

function runtimeEnvVersion() {
  try {
    const accountInfo = wx.getAccountInfoSync && wx.getAccountInfoSync();
    return (accountInfo && accountInfo.miniProgram && accountInfo.miniProgram.envVersion) || '';
  } catch (_) {
    // Static checks and unit tests may not provide wx.
    return '';
  }
}

const API_PROFILE = runtimeEnvVersion() === 'develop' ? 'local' : 'online';

module.exports = {
  BASE_URL: API_PROFILE === 'local' ? LOCAL_API_BASE_URL : ONLINE_API_BASE_URL,
  API_PROFILE,
  LOCAL_API_BASE_URL,
  ONLINE_API_BASE_URL,
};
