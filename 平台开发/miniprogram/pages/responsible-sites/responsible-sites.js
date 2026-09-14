const api = require('../../services/api.js');
const maps = require('../../services/maps.js');

const app = getApp();

Page({
  data: { sites: [], loading: false, error: '' },

  onShow() {
    this.setData({ loading: true, error: '' });
    api.stationMonitoringSites().then(res => {
      this.setData({ sites: (res.items || []).map(site => Object.assign({}, site, { type_cn: maps.map(maps.SITE_TYPE, site.type, '其他站点') })), loading: false });
    }).catch(() => this.setData({ loading: false, error: '站点监测信息加载失败，请重试' }));
  },

  onRetry() { this.onShow(); },

  openSite(e) {
    const siteId = Number(e.currentTarget.dataset.id);
    if (!siteId) return;
    app.globalData.selSiteId = siteId;
    wx.navigateTo({ url: '/pages/site/site?site_id=' + siteId + '&source=responsible_sites_monitoring' });
  }
});
