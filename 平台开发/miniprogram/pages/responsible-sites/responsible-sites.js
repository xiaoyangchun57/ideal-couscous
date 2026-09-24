const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');

const app = getApp();

const MONITORING_UNAVAILABLE_CODES = new Set([
  'STATION_MONITORING_PUBLIC_DISABLED',
  'STATION_MONITORING_ADMIN_ONLY'
]);

function monitoringUnavailable(error) {
  return !!(error && error.status === 403 && MONITORING_UNAVAILABLE_CODES.has(error.code));
}

function projectSite(site) {
  const siteId = site.site_id != null ? site.site_id : site.id;
  return Object.assign({}, site, {
    id: siteId,
    site_id: siteId,
    type_cn: site.type_cn || maps.map(maps.SITE_TYPE, site.type, '其他站点')
  });
}

const REAGENT_FILTERS = new Set(['', 'expired', 'expiring', 'low_volume']);
const REAGENT_REASONS = {
  expired: '已过期', expiring: '临期', low_volume: '低余量',
  pending_qc: '待标定', failed_qc: '标定失败'
};
let operationSequence = 0;

function operationKey() {
  operationSequence += 1;
  return 'station-reagent-' + Date.now() + '-' + operationSequence + '-' + Math.random().toString(36).slice(2);
}

function projectReagent(item) {
  const reasons = Array.isArray(item.attention_reasons) ? item.attention_reasons : [];
  return Object.assign({}, item, {
    id: String(item.site_id) + ':' + String(item.reagent_id),
    key: String(item.site_id) + ':' + String(item.reagent_id),
    attention_reasons: reasons.filter(reason => REAGENT_REASONS[reason])
      .map(type => ({ type, label: REAGENT_REASONS[type] })),
    volume: item.current_qty == null ? '—' : item.current_qty,
    expiryText: item.remaining_days == null ? '未设置预计可用时间'
      : item.remaining_days <= 0 ? '已过期'
      : '预计剩余 ' + item.remaining_days + ' 天',
    canReplace: item.can_replace === true,
    canCalibrate: item.can_calibrate === true
  });
}

function emptySheet() {
  return { siteId: null, reagentId: null, siteName: '', reagentName: '', unit: '',
    currentVolume: null, newVolume: '', replaceTime: '', estDays: '', batchNo: '',
    standardValue: '', measuredValue: '', result: '', followUp: '', remark: '',
    errors: {}, serverError: '', submitting: false, success: false, key: null };
}

function messageFor(error, fallback) {
  return (error && (error.error || error.message)) || fallback;
}

Page({
  data: {
    activeTab: 'stations',
    sites: [], loading: false, error: '', scope: 'mine', keyword: '', keywordInput: '',
    availableScopes: ['mine'], canViewAll: false, scopeCounts: { mine: 0, all: null },
    monitoringEnabled: false, monitoringPublic: false,
    emptyTitle: '暂未分配负责站点', emptyDescription: '当前账号下暂无监测站点，如有疑问请联系管理员',
    activeFilter: '', reagentEnabled: false, reagentLoading: false, reagentError: '',
    reagentNoViewPermission: false, reagentItems: [],
    replaceSheetVisible: false, replaceSheet: emptySheet(),
    calibrateSheetVisible: false, calibrateSheet: emptySheet()
  },

  onLoad() { this._unloaded = false; },

  onShow() {
    this._inactive = false;
    const target = app.globalData.stationHubTarget;
    // Consume only the documented one-shot target; direct tab returns keep the current mode/filter.
    if (target != null) {
      app.globalData.stationHubTarget = null;
      if (target && target.view === 'stations' && Object.keys(target).length === 1) {
        this.setData({ activeTab: 'stations' });
      } else if (target && target.view === 'reagents' && REAGENT_FILTERS.has(target.filter)
          && Object.keys(target).length === 2) {
        this.setData({ activeTab: 'reagents', activeFilter: target.filter });
      }
    }
    this._monitoringAvailable = getUser()?.capabilities?.station_monitoring_public === true;
    if (!this._monitoringAvailable) {
      this.setData({ monitoringEnabled: false, monitoringPublic: false });
    }
    if (this.data.activeTab === 'reagents') this.loadReagents();
    else this.loadSites(this.data.scope || 'mine', this.data.keyword || '');
  },

  loadSites(scope, keyword) {
    const requestedScope = scope || 'mine';
    const requestedKeyword = String(keyword || '').trim();
    this._retryRequest = { scope: requestedScope, keyword: requestedKeyword };
    const requestId = (this._sitesRequestId || 0) + 1;
    this._sitesRequestId = requestId;
    this.setData({ loading: true, error: '' });
    let usingMonitoring = this._monitoringAvailable === true;
    const options = { scope: requestedScope, keyword: requestedKeyword };
    const request = usingMonitoring ? api.stationMonitoringSites : api.responsibleSites;
    return request(options).catch(error => {
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return null;
      if (!usingMonitoring || !monitoringUnavailable(error)) throw error;
      usingMonitoring = false;
      this._monitoringAvailable = false;
      this.setData({ monitoringEnabled: false, monitoringPublic: false });
      return api.responsibleSites(options);
    }).then(res => {
      if (!res) return;
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
      const availableScopes = Array.isArray(res.available_scopes) ? res.available_scopes : ['mine'];
      const resolvedScope = res.scope === 'all' ? 'all' : 'mine';
      const resolvedKeyword = requestedKeyword;
      this.setData({
        sites: (res.items || []).map(projectSite),
        loading: false, scope: resolvedScope, keyword: resolvedKeyword,
        keywordInput: resolvedKeyword, availableScopes,
        monitoringEnabled: usingMonitoring, monitoringPublic: usingMonitoring,
        canViewAll: availableScopes.includes('all'), scopeCounts: res.scope_counts || { mine: 0, all: null },
        emptyTitle: resolvedScope === 'all' && resolvedKeyword ? '未找到匹配站点' : (resolvedScope === 'all' ? '暂无站点' : '暂未分配负责站点'),
        emptyDescription: resolvedScope === 'all' && resolvedKeyword ? '请更换站点名称或编号后重试' : (resolvedScope === 'all' ? '当前暂无可查看站点' : '当前账号下暂无负责站点，如有疑问请联系管理员')
      });
    }).catch(() => {
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
      this.setData({ loading: false, error: usingMonitoring ? '站点监测信息加载失败，请重试' : '站点目录加载失败，请重试' });
    });
  },

  onHide() {
    this._inactive = true;
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
    this._reagentsRequestId = (this._reagentsRequestId || 0) + 1;
  },

  onUnload() {
    this._unloaded = true;
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
    this._reagentsRequestId = (this._reagentsRequestId || 0) + 1;
  },

  onPullDownRefresh() {
    const request = this.data.activeTab === 'reagents'
      ? this.loadReagents() : this.loadSites(this.data.scope, this.data.keyword);
    Promise.resolve(request).finally(() => {
      if (typeof wx.stopPullDownRefresh === 'function') wx.stopPullDownRefresh();
    });
  },

  onTabSites() {
    if (this.data.activeTab === 'stations') return;
    this._reagentsRequestId = (this._reagentsRequestId || 0) + 1;
    this.setData({ activeTab: 'stations' });
    this.loadSites(this.data.scope, this.data.keyword);
  },

  onTabReagent() {
    if (this.data.activeTab === 'reagents') return;
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
    this.setData({ activeTab: 'reagents' });
    this.loadReagents();
  },

  onReagentFilterTap(e) {
    const filter = e.currentTarget.dataset.filter;
    if (!REAGENT_FILTERS.has(filter)) return;
    this.setData({ activeFilter: filter });
    this.applyReagentFilter();
  },

  applyReagentFilter() {
    const filter = this.data.activeFilter;
    const items = this._reagentSource || [];
    this.setData({ reagentItems: items.filter(item => !filter ||
      item.attention_reasons.some(reason => reason.type === filter)) });
  },

  loadReagents() {
    const requestId = (this._reagentsRequestId || 0) + 1;
    this._reagentsRequestId = requestId;
    this.setData({ reagentEnabled: true, reagentLoading: true, reagentError: '' });
    return api.reagentOverview().then(res => {
      if (this._unloaded || this._inactive || this._reagentsRequestId !== requestId || this.data.activeTab !== 'reagents') return;
      this._reagentSource = (Array.isArray(res.items) ? res.items : []).map(projectReagent);
      this.setData({ reagentLoading: false, reagentEnabled: true, reagentNoViewPermission: false });
      this.applyReagentFilter();
    }).catch(error => {
      if (this._unloaded || this._inactive || this._reagentsRequestId !== requestId || this.data.activeTab !== 'reagents') return;
      if (error && (error.status === 403 || error.status === 401)) {
        this._reagentSource = [];
        this.setData({ reagentLoading: false, reagentEnabled: true,
          reagentNoViewPermission: true, reagentItems: [], reagentError: '',
          replaceSheetVisible: false, replaceSheet: emptySheet(),
          calibrateSheetVisible: false, calibrateSheet: emptySheet() });
      } else {
        this.setData({ reagentLoading: false, reagentEnabled: true,
          reagentNoViewPermission: false,
          reagentError: messageFor(error, '试剂情况加载失败，请重试') });
      }
    });
  },

  onReagentRetry() { return this.loadReagents(); },

  noop() {},

  _reagentFor(e) {
    const item = (this._reagentSource || []).find(row => row.id === String(e.currentTarget.dataset.id));
    return item && this.data.reagentItems.some(row => row.id === item.id) ? item : null;
  },

  onOpenReplaceSheet(e) {
    const item = this._reagentFor(e);
    if (!item || !item.canReplace) return;
    this.setData({ replaceSheetVisible: true, replaceSheet: Object.assign(emptySheet(), {
      siteId: item.site_id, reagentId: item.reagent_id, siteName: item.site_name,
      reagentName: item.reagent_name, currentVolume: item.current_qty, unit: item.unit
    }) });
  },

  onOpenCalibrateSheet(e) {
    const item = this._reagentFor(e);
    if (!item || !item.canCalibrate) return;
    this.setData({ calibrateSheetVisible: true, calibrateSheet: Object.assign(emptySheet(), {
      siteId: item.site_id, reagentId: item.reagent_id, siteName: item.site_name,
      reagentName: item.reagent_name, replaceTime: item.last_replaced_at
    }) });
  },

  onCloseReplaceSheet() {
    if (this.data.replaceSheet.submitting) return;
    this.setData({ replaceSheetVisible: false, replaceSheet: emptySheet() });
  },

  onCloseCalibrateSheet() {
    if (this.data.calibrateSheet.submitting) return;
    this.setData({ calibrateSheetVisible: false, calibrateSheet: emptySheet() });
  },

  _editSheet(name, field, value) {
    const sheet = this.data[name];
    if (sheet.submitting || sheet.success || sheet[field] === value) return;
    this.setData({ [name]: Object.assign({}, sheet, {
      [field]: value, errors: {}, serverError: '', key: null
    }) });
  },

  onReplaceVolumeInput(e) { this._editSheet('replaceSheet', 'newVolume', e.detail.value); },
  onReplaceTimeChange(e) { this._editSheet('replaceSheet', 'replaceTime', e.detail.value); },
  onReplaceEstDaysInput(e) { this._editSheet('replaceSheet', 'estDays', e.detail.value); },
  onReplaceBatchNoInput(e) { this._editSheet('replaceSheet', 'batchNo', e.detail.value); },
  onReplaceRemarkInput(e) { this._editSheet('replaceSheet', 'remark', e.detail.value); },
  onCalibrateStandardInput(e) { this._editSheet('calibrateSheet', 'standardValue', e.detail.value); },
  onCalibrateMeasuredInput(e) { this._editSheet('calibrateSheet', 'measuredValue', e.detail.value); },
  onCalibrateResultTap(e) { this._editSheet('calibrateSheet', 'result', e.currentTarget.dataset.result); },
  onCalibrateFollowUpTap(e) { this._editSheet('calibrateSheet', 'followUp', e.currentTarget.dataset.follow); },
  onCalibrateRemarkInput(e) { this._editSheet('calibrateSheet', 'remark', e.detail.value); },

  onSubmitReplace() {
    const sheet = this.data.replaceSheet;
    if (!this.data.replaceSheetVisible || sheet.submitting || sheet.success) return;
    const errors = {};
    if (!(Number(sheet.newVolume) > 0) || !Number.isFinite(Number(sheet.newVolume))) errors.newVolume = '请输入大于 0 的有效余量';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(sheet.replaceTime) || Number.isNaN(Date.parse(sheet.replaceTime))) errors.replaceTime = '请选择有效更换日期';
    if (!/^\d+$/.test(String(sheet.estDays)) || Number(sheet.estDays) < 1) errors.estDays = '请输入正整数天数';
    if (Object.keys(errors).length) { this.setData({ replaceSheet: Object.assign({}, sheet, { errors }) }); return; }
    const key = sheet.key || operationKey();
    const payload = { site_id: sheet.siteId, reagent_id: sheet.reagentId,
      new_qty: Number(sheet.newVolume), replaced_at: sheet.replaceTime,
      expected_duration_days: Number(sheet.estDays), new_batch_no: sheet.batchNo,
      remark: sheet.remark, _idempotency_key: key };
    this.setData({ replaceSheet: Object.assign({}, sheet, { key, submitting: true, serverError: '', errors: {} }) });
    return api.reagentReplacement(payload).then(() => {
      if (this._unloaded || !this.data.replaceSheetVisible || this.data.replaceSheet.key !== key) return;
      this.setData({ replaceSheet: Object.assign({}, this.data.replaceSheet, { submitting: false, success: true }) });
      if (!this._inactive) this.loadReagents();
    }).catch(error => {
      if (this._unloaded || this.data.replaceSheet.key !== key) return;
      this.setData({ replaceSheet: Object.assign({}, this.data.replaceSheet, {
        submitting: false, serverError: messageFor(error, '更换未保存，请重试')
      }) });
    });
  },

  onSubmitCalibrate() {
    const sheet = this.data.calibrateSheet;
    if (!this.data.calibrateSheetVisible || sheet.submitting || sheet.success) return;
    const errors = {};
    if (sheet.standardValue === '' || !Number.isFinite(Number(sheet.standardValue))) errors.standardValue = '请输入有效标样值';
    if (sheet.measuredValue === '' || !Number.isFinite(Number(sheet.measuredValue))) errors.measuredValue = '请输入有效实测值';
    if (!['pass', 'fail'].includes(sheet.result)) errors.result = '请选择标定结果';
    if (sheet.result === 'fail' && !['recalibrate', 'repair'].includes(sheet.followUp)) errors.followUp = '请选择后续动作';
    if (Object.keys(errors).length) { this.setData({ calibrateSheet: Object.assign({}, sheet, { errors }) }); return; }
    const key = sheet.key || operationKey();
    const payload = { site_id: sheet.siteId, reagent_id: sheet.reagentId,
      standard_value: Number(sheet.standardValue), measured_value: Number(sheet.measuredValue),
      passed: sheet.result === 'pass' ? 1 : 0,
      fail_action: sheet.result === 'fail' ? (sheet.followUp === 'recalibrate' ? 'calibrate' : 'repair') : '',
      remark: sheet.remark, _idempotency_key: key };
    this.setData({ calibrateSheet: Object.assign({}, sheet, { key, submitting: true, serverError: '', errors: {} }) });
    return api.reagentCalibration(payload).then(() => {
      if (this._unloaded || !this.data.calibrateSheetVisible || this.data.calibrateSheet.key !== key) return;
      this.setData({ calibrateSheet: Object.assign({}, this.data.calibrateSheet, { submitting: false, success: true }) });
      if (!this._inactive) this.loadReagents();
    }).catch(error => {
      if (this._unloaded || this.data.calibrateSheet.key !== key) return;
      this.setData({ calibrateSheet: Object.assign({}, this.data.calibrateSheet, {
        submitting: false, serverError: messageFor(error, '标定未保存，请重试')
      }) });
    });
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
