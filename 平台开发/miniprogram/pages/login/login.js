const api = require('../../services/api.js');
const { setAuth, getToken, clear } = require('../../utils/auth.js');
const { completeLoginSession } = require('../../utils/loginCompletion.js');

const app = getApp();

Page({
  data: {
    username: '', password: '', passwordFocused: false, showPassword: false, loading: false, error: '',
    mustChangePassword: false, newPassword: '', confirmPassword: '', showNewPassword: false, showConfirmPassword: false, pendingSites: [],
    restoring: true, recoveryFailed: false,
  },

  onLoad() {
    this._unloaded = false;
    if (getToken()) this.recoverSession();
    else this.setData({ restoring: false, recoveryFailed: false });
  },

  onUnload() {
    this._unloaded = true;
    this._restoreRequestId = (this._restoreRequestId || 0) + 1;
  },

  recoverSession() {
    if (this._restorePromise) return this._restorePromise;
    const token = getToken();
    if (!token) {
      this.setData({ restoring: false, recoveryFailed: false });
      return Promise.resolve();
    }
    const requestId = (this._restoreRequestId || 0) + 1;
    this._restoreRequestId = requestId;
    this.setData({ restoring: true, recoveryFailed: false, error: '' });
    this._restorePromise = api.restoreSession()
      .then(res => {
        if (this._unloaded || this._restoreRequestId !== requestId) return;
        if (!res || !res.user) throw { status: 502, error: '登录状态返回异常，请重试' };
        const user = Object.assign({}, res.user, {
          site_ids: Array.isArray(res.site_ids) ? res.site_ids : [],
        });
        setAuth(token, user, Array.isArray(res.sites) ? res.sites : []);
        wx.reLaunch({ url: '/pages/index/index' });
      })
      .catch(error => {
        if (this._unloaded || this._restoreRequestId !== requestId) return;
        if (error && error.status === 401) {
          clear();
          this.setData({
            restoring: false,
            recoveryFailed: false,
            error: '登录已失效，请重新登录',
          });
          return;
        }
        this.setData({
          restoring: false,
          recoveryFailed: true,
          error: (error && error.error) || '暂时无法恢复登录，请检查网络后重试',
        });
      })
      .finally(() => {
        if (this._restoreRequestId === requestId) this._restorePromise = null;
      });
    return this._restorePromise;
  },

  onRetryRecovery() { return this.recoverSession(); },

  onUseAnotherAccount() {
    this._restoreRequestId = (this._restoreRequestId || 0) + 1;
    this._restorePromise = null;
    clear();
    this.setData({ restoring: false, recoveryFailed: false, error: '' });
  },

  onUser(e) { this.setData({ username: e.detail.value, error: '' }); },
  onPass(e) { this.setData({ password: e.detail.value, error: '' }); },
  onTogglePassword() { this.setData({ showPassword: !this.data.showPassword }); },
  focusPassword() { this.setData({ passwordFocused: true }); },
  onPassFocus() { this.setData({ passwordFocused: true }); },
  onPassBlur() { this.setData({ passwordFocused: false }); },
  onNewPassword(e) { this.setData({ newPassword: e.detail.value, error: '' }); },
  onConfirmPassword(e) { this.setData({ confirmPassword: e.detail.value, error: '' }); },
  onToggleNewPassword() { this.setData({ showNewPassword: !this.data.showNewPassword }); },
  onToggleConfirmPassword() { this.setData({ showConfirmPassword: !this.data.showConfirmPassword }); },

  completeLogin(token, user, sites) {
    completeLoginSession({
      setAuth,
      bindWechat: () => wx.login({
        success: (lres) => { if (lres.code) api.bindOpenId(lres.code).catch(() => {}); }
      }),
      refreshBadge: () => {
        if (app.globalData.refreshNotificationBadge) {
          app.globalData.refreshNotificationBadge();
        }
      },
      navigateHome: () => wx.reLaunch({ url: '/pages/index/index' }),
    }, token, user, sites);
  },

  onLogin() {
    const username = this.data.username.trim();
    const password = this.data.password;
    if (!username || !password) {
      this.setData({ error: '请输入工号和密码' });
      return;
    }
    this.setData({ loading: true, passwordFocused: false, error: '' });
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
