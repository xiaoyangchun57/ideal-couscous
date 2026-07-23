const api = require('../../services/api.js');
const { setAuth } = require('../../utils/auth.js');

Page({
  data: { username: '', password: '', loading: false, error: '' },

  onUser(e) { this.setData({ username: e.detail.value, error: '' }); },
  onPass(e) { this.setData({ password: e.detail.value, error: '' }); },

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
          // 静默绑定微信 openid（用于订阅消息下发），失败不影响登录
          wx.login({
            success: (lres) => { if (lres.code) api.bindOpenId(lres.code).catch(() => {}); }
          });
          wx.reLaunch({ url: '/pages/index/index' });
        } else {
          this.setData({ loading: false, error: (res && res.error) || '登录失败' });
        }
      })
      .catch(err => {
        this.setData({ loading: false, error: (err && err.error) || '网络异常，请重试' });
      });
  }
});
