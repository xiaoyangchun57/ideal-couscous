const LOCATION_SCOPE = 'scope.userLocation';

function authorizePrivacy() {
  if (!wx.getPrivacySetting || !wx.requirePrivacyAuthorize) return Promise.resolve();
  return new Promise((resolve, reject) => {
    wx.getPrivacySetting({
      success(result) {
        if (!result.needAuthorization) return resolve();
        wx.requirePrivacyAuthorize({ success: resolve, fail: reject });
      },
      fail: reject
    });
  });
}

function authorizeLocation() {
  return new Promise((resolve, reject) => {
    wx.authorize({ scope: LOCATION_SCOPE, success: resolve, fail: reject });
  });
}

function readLocation() {
  return new Promise((resolve, reject) => {
    wx.getLocation({
      type: 'gcj02',
      isHighAccuracy: true,
      highAccuracyExpireTime: 3000,
      success(res) { resolve({ lat: res.latitude, lng: res.longitude }); },
      fail: reject
    });
  });
}

function requestLocation() {
  return authorizePrivacy().then(authorizeLocation).then(readLocation);
}

function locationErrorMessage(error) {
  const text = String((error && (error.errMsg || error.message)) || '');
  if (/privacy|private information/i.test(text)) return '请先同意位置使用说明后重试。';
  if (/auth deny|auth denied|authorize:fail|permission denied/i.test(text)) {
    return '定位权限未开启。请在系统弹窗中允许，或在小程序设置中开启定位。';
  }
  if (/system permission|location service|gps|定位服务/i.test(text)) {
    return '请打开手机系统定位服务后重试。';
  }
  return '暂时无法获取当前位置，请确认网络和定位服务后重试。';
}

function shouldOpenLocationSettings(error) {
  const text = String((error && (error.errMsg || error.message)) || '');
  return /auth deny|auth denied|authorize:fail|permission denied/i.test(text);
}

module.exports = { requestLocation, locationErrorMessage, shouldOpenLocationSettings };
