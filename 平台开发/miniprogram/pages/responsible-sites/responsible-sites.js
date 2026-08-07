const { getSites } = require('../../utils/auth.js');
const maps = require('../../services/maps.js');

const app = getApp();

Page({
  data: { sites: [] },

  onShow() {
    this.setData({
      sites: (getSites() || []).map(site => Object.assign({}, site, {
        type_cn: maps.map(maps.SITE_TYPE, site.type, '其他站点')
      }))
    });
  },

  openSite(e) {
    const siteId = Number(e.currentTarget.dataset.id);
    if (!siteId) return;
    app.globalData.selSiteId = siteId;
    wx.navigateTo({ url: '/pages/site/site?site_id=' + siteId });
  }
});
