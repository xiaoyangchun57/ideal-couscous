const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');

const app = getApp();

Page({
  data: {
    sites: [], loading: false, error: '', scope: 'mine', keyword: '', keywordInput: '',
    availableScopes: ['mine'], canViewAll: false, scopeCounts: { mine: 0, all: null },
    monitoringPublic: false,
    emptyTitle: '暂未分配负责站点', emptyDescription: '当前账号下暂无监测站点，如有疑问请联系管理员'
  },

  onLoad() { this._unloaded = false; },

  onShow() {
    this._inactive = false;
    this.setData({ monitoringPublic: getUser()?.capabilities?.station_monitoring_public === true });
    this.loadSites(this.data.scope || 'mine', this.data.keyword || '');
  },

  loadSites(scope, keyword) {
    const requestedScope = scope || 'mine';
    const requestedKeyword = String(keyword || '').trim();
    this._retryRequest = { scope: requestedScope, keyword: requestedKeyword };
    const requestId = (this._sitesRequestId || 0) + 1;
    this._sitesRequestId = requestId;
    this.setData({ loading: true, error: '' });
    const request = this.data.monitoringPublic ? api.stationMonitoringSites : api.responsibleSites;
    request({ scope: requestedScope, keyword: requestedKeyword }).then(res => {
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
      const availableScopes = Array.isArray(res.available_scopes) ? res.available_scopes : ['mine'];
      const resolvedScope = res.scope === 'all' ? 'all' : 'mine';
      const resolvedKeyword = requestedKeyword;
      this.setData({
        sites: (res.items || []).map(site => Object.assign({}, site, { type_cn: maps.map(maps.SITE_TYPE, site.type, '其他站点') })),
        loading: false, scope: resolvedScope, keyword: resolvedKeyword,
        keywordInput: resolvedKeyword, availableScopes,
        canViewAll: availableScopes.includes('all'), scopeCounts: res.scope_counts || { mine: 0, all: null },
        emptyTitle: resolvedScope === 'all' && resolvedKeyword ? '未找到匹配站点' : (resolvedScope === 'all' ? '暂无站点' : '暂未分配负责站点'),
        emptyDescription: resolvedScope === 'all' && resolvedKeyword ? '请更换站点名称或编号后重试' : (resolvedScope === 'all' ? '当前暂无可查看站点' : '当前账号下暂无负责站点，如有疑问请联系管理员')
      });
    }).catch(() => {
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
      this.setData({ loading: false, error: this.data.monitoringPublic ? '站点监测信息加载失败，请重试' : '站点目录加载失败，请重试' });
    });
  },

  onHide() {
    this._inactive = true;
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
  },

  onUnload() {
    this._unloaded = true;
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
  },

  onRetry() {
    const retry = this._retryRequest || { scope: this.data.scope, keyword: this.data.keyword };
    this.loadSites(retry.scope, retry.keyword);
  },

  onScopeMine() { this.loadSites('mine', ''); },

  onScopeAll() { if (this.data.canViewAll) this.loadSites('all', ''); },

  onKeywordInput(e) { this.setData({ keywordInput: e.detail.value || '' }); },

  onSearch() { if (this.data.scope === 'all') this.loadSites('all', this.data.keywordInput); },

  onClearSearch() {
    this.setData({ keywordInput: '' });
    if (this.data.scope === 'all') this.loadSites('all', '');
  },

  openSite(e) {
    const siteId = Number(e.currentTarget.dataset.id);
    if (!siteId) return;
    app.globalData.selSiteId = siteId;
    const source = this.data.monitoringPublic ? 'responsible_sites_monitoring' : 'responsible_sites_profile';
    wx.navigateTo({ url: '/pages/site/site?site_id=' + siteId + '&source=' + source });
  }
});
