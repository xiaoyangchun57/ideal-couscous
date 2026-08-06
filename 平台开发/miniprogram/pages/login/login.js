const api = require('../../services/api.js');
const { setAuth } = require('../../utils/auth.js');

Page({
  data: {
    username: '', password: '', loading: false, error: '',
    mustChangePassword: false, newPassword: '', confirmPassword: '', pendingSites: [],
  },

  onUser(e) { this.setData({ username: e.detail.value, error: '' }); },
  onPass(e) { this.setData({ password: e.detail.value, error: '' }); },
  onNewPassword(e) { this.setData({ newPassword: e.detail.value, error: '' }); },
  onConfirmPassword(e) { this.setData({ confirmPassword: e.detail.value, error: '' }); },

  completeLogin(token, user, sites) {
    setAuth(token, user, sites);
    wx.login({
      success: (lres) => { if (lres.code) api.bindOpenId(lres.code).catch(() => {}); }
    });
    wx.reLaunch({ url: '/pages/index/index' });
  },

  onLogin() {
    const username = this.data.username.trim();
    const password = this.data.password;
    if (!username || !password) {
      this.setData({ error: '请输入工号和密码' });
      return;
    }
    this.setData({ loading: true, error: '' });
    api.login(username, password)
      .then(res => {
        if (res && res.success && res.token) {
          setAuth(res.token, res.user, res.sites);
          if (res.must_change_password || (res.user && res.user.must_change_password)) {
            this.setData({
              loading: false,
              mustChangePassword: true,
              pendingSites: res.sites || [],
              error: '',
            });
            return;
          }
          this.completeLogin(res.token, res.user, res.sites || []);
        } else {
          this.setData({ loading: false, error: (res && res.error) || '登录失败' });
        }
      })
      .catch(err => {
        this.setData({ loading: false, error: (err && err.error) || '网络异常，请重试' });
      });
  },

  onChangePassword() {
    const newPassword = this.data.newPassword;
    if (newPassword.length < 8) {
      this.setData({ error: '新密码至少8位' });
      return;
    }
    if (newPassword !== this.data.confirmPassword) {
      this.setData({ error: '两次输入的新密码不一致' });
      return;
    }
    this.setData({ loading: true, error: '' });
    api.changePassword(this.data.password, newPassword)
      .then(res => {
        if (!res || !res.token || !res.user) {
          this.setData({ loading: false, error: (res && res.error) || '密码修改失败' });
          return;
        }
        this.completeLogin(res.token, res.user, this.data.pendingSites || []);
      })
      .catch(err => {
        this.setData({ loading: false, error: (err && err.error) || '密码修改失败，请重试' });
      });
  }
});
