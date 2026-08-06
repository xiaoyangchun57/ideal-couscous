const { getSites } = require('../../utils/auth.js');

const app = getApp();

Page({
  data: { sites: [] },

  onShow() {
    this.setData({ sites: getSites() || [] });
  },

  openSite(e) {
    const siteId = Number(e.currentTarget.dataset.id);
    if (!siteId) return;
    app.globalData.selSiteId = siteId;
    wx.navigateTo({ url: '/pages/site/site?site_id=' + siteId });
  }
});
