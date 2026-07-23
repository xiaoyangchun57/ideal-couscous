const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');

const app = getApp();

function decorate(a) {
  return Object.assign({}, a, {
    level_cn: maps.map(maps.ALERT_LEVEL, a.level),
    level_cls: maps.alertLevelCls(a.level),
    status_cn: maps.map(maps.ALERT_STATUS, a.status),
    metric_cn: maps.metricCn(a.metric)
  });
}

Page({
  data: { tab: 'pending', list: [], loaded: false, sheet: { open: false, item: null }, acting: false },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.load();
  },

  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  onTab(e) {
    const t = e.currentTarget.dataset.t;
    this.setData({ tab: t });
    this.load();
  },

  load(done) {
    const status = this.data.tab === 'pending' ? 'pending' : '';
    api.alerts(status)
      .then(res => {
        this.setData({ list: (res || []).map(decorate), loaded: true });
        if (done) done();
      })
      .catch(() => { this.setData({ loaded: true }); if (done) done(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  onOpen(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.list.find(a => a.id === id);
    if (item) this.setData({ sheet: { open: true, item } });
  },
  onClose() { this.setData({ 'sheet.open': false }); },

  doAck() {
    const id = this.data.sheet.item.id;
    this.setData({ acting: true });
    api.acknowledgeAlert(id)
      .then(() => {
        this.setData({ acting: false, 'sheet.open': false });
        wx.showToast({ title: '已确认', icon: 'success' });
        this.load();
      })
      .catch(() => { this.setData({ acting: false }); wx.showToast({ title: '操作失败', icon: 'none' }); });
  }
});
