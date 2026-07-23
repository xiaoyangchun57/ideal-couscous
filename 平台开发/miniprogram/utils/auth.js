// 登录态持久化（本地存储）
const KEYS = { token: 'token', user: 'user', sites: 'sites' };

function setAuth(token, user, sites) {
  wx.setStorageSync(KEYS.token, token);
  wx.setStorageSync(KEYS.user, user);
  wx.setStorageSync(KEYS.sites, sites || []);
  const app = getApp();
  if (app) {
    app.globalData.token = token;
    app.globalData.user = user;
    app.globalData.sites = sites || [];
  }
}

function getToken() {
  return wx.getStorageSync(KEYS.token) || '';
}

function getUser() {
  return wx.getStorageSync(KEYS.user) || null;
}

function getSites() {
  return wx.getStorageSync(KEYS.sites) || [];
}

function isLogin() {
  return !!getToken();
}

function clear() {
  wx.removeStorageSync(KEYS.token);
  wx.removeStorageSync(KEYS.user);
  wx.removeStorageSync(KEYS.sites);
  const app = getApp();
  if (app) {
    app.globalData.token = '';
    app.globalData.user = null;
    app.globalData.sites = [];
  }
}

module.exports = { setAuth, getToken, getUser, getSites, isLogin, clear };
