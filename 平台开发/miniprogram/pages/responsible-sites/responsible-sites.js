const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const { presentLatestCycle } = require('../../utils/stationMonitoringPresentation.js');
const {
  normalizeStationHubTarget,
  localBusinessDate,
  localBusinessDateTime,
  projectReagentOverview,
  projectReagentScope,
  projectReagentSiteGroups,
  stableKey,
} = require('../../utils/round1Wiring.js');

const app = getApp();

Page({
  data: {
    sites: [], loading: false, error: '', scope: 'mine', pendingScope: null, keyword: '', keywordInput: '',
    availableScopes: ['mine'], canViewAll: false, scopeCounts: { mine: 0, all: null },
    monitoringPublic: false, monitoringEnabled: false, sitesNoPermission: false,
    activeTab: 'stations', activeFilter: '', reagentEnabled: true,
    reagentLoading: false, reagentError: '', reagentNoViewPermission: false, siteGroups: [],
    replaceSheetVisible: false,
    replaceSheet: { submitting: false, success: false, serverError: '', errors: {}, scrollIntoView: '', focusField: '', targetId: null, siteId: null, reagentId: null, siteName: '', reagentName: '', currentVolume: 0, unit: '', newVolume: '', replaceTime: '', estDays: '', batchNo: '', remark: '', idempotencyKey: null },
    calibrateSheetVisible: false,
    calibrateSheet: { submitting: false, success: false, serverError: '', errors: {}, scrollIntoView: '', focusField: '', targetId: null, siteId: null, reagentId: null, siteName: '', reagentName: '', replaceTime: '', standardValue: '', measuredValue: '', result: 'pass', followUp: '', remark: '', idempotencyKey: null },
    emptyTitle: '暂未分配负责站点', emptyDescription: '当前账号下暂无监测站点，如有疑问请联系管理员'
  },

  onLoad() {
    this._unloaded = false;
    this._reagentRequestId = 0;
    this._reagentExpansion = { mine: {}, all: {} };
    this._reagentScrollTop = 0;
    // 统一范围意图代次：站点/试剂两个模式共享
    // 任何范围切换或模式切换都递增，过期请求不得修改共享 scope/pendingScope/可见数据
    this._scopeGeneration = 0;
    // 已提交范围的缓存标记：各模式最后一次成功加载的范围
    // 用于切回已有缓存的模式时判断是否可直接复用
    this._loadedScope = { stations: null, reagents: null };
    this._stationScope = null;
  },

  onPageScroll(event) { this._reagentScrollTop = Number(event && event.scrollTop) || 0; },

  onShow() {
    this._inactive = false;
    this.setMaintenanceSheetTabBarHidden(
      !!(this.data.replaceSheetVisible || this.data.calibrateSheetVisible));
    const monitoringPublic = getUser()?.capabilities?.station_monitoring_public === true;
    this.setData({ monitoringPublic, monitoringEnabled: monitoringPublic });
    const target = normalizeStationHubTarget(app.globalData.stationHubTarget);
    if (app.globalData.stationHubTarget) app.globalData.stationHubTarget = null;
    if (target) {
      this._stationReturnSnapshot = null;
      this.setData({ activeTab: target.view, activeFilter: target.filter || '' });
    } else if (this._stationReturnSnapshot) {
      const snapshot = this._stationReturnSnapshot;
      this._stationReturnSnapshot = null;
      this._stationScope = snapshot.scope;
      this._reagentScrollTop = snapshot.scrollTop || 0;
      this.setData({
        activeTab: snapshot.activeTab,
        scope: snapshot.scope,
        pendingScope: snapshot.pendingScope,
        keywordInput: snapshot.keywordInput,
        keyword: snapshot.keyword,
        sites: snapshot.sites,
      });
      if (snapshot.scrollTop > 0 && wx.pageScrollTo) {
        wx.pageScrollTo({ scrollTop: snapshot.scrollTop, duration: 0 });
      }
    }
    if (this.data.activeTab === 'reagents') this.loadReagents();
    else {
      const scope = this._stationScope || (monitoringPublic ? 'all' : (this.data.scope || 'mine'));
      this.loadSites(scope, this.data.keyword || '');
    }
  },

  loadSites(scope, keyword) {
    const requestedScope = scope || 'mine';
    const requestedKeyword = String(keyword || '').trim();
    this._retryRequest = { scope: requestedScope, keyword: requestedKeyword };
    const requestId = (this._sitesRequestId || 0) + 1;
    this._sitesRequestId = requestId;
    // 统一范围代次：跨模式过期请求不得写共享状态
    const scopeGen = ++this._scopeGeneration;
    this.setData({ loading: true, error: '' });
    const request = this.data.monitoringPublic ? api.stationMonitoringSites : api.responsibleSites;
    request({ scope: requestedScope, keyword: requestedKeyword }).then(res => {
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
      // 跨模式/范围过期保护：代次不匹配则静默丢弃
      if (scopeGen !== this._scopeGeneration) return;
      const availableScopes = Array.isArray(res.available_scopes) ? res.available_scopes : ['mine'];
      const resolvedScope = res.scope === 'all' ? 'all' : 'mine';
      const resolvedKeyword = requestedKeyword;
      this._loadedScope.stations = resolvedScope;
      this._stationScope = resolvedScope;
      this.setData({
        sites: (res.items || []).map(site => Object.assign({}, site, {
          type_cn: maps.map(maps.SITE_TYPE, site.type, '其他站点'),
          latest_cycle: presentLatestCycle(site.latest_cycle),
        })),
        loading: false, scope: resolvedScope, pendingScope: null, keyword: resolvedKeyword,
        keywordInput: resolvedKeyword, availableScopes,
        canViewAll: availableScopes.includes('all'), scopeCounts: res.scope_counts || { mine: 0, all: null },
        monitoringEnabled: this.data.monitoringPublic,
        sitesNoPermission: false,
        emptyTitle: resolvedScope === 'all' && resolvedKeyword ? '未找到匹配站点' : (resolvedScope === 'all' ? '暂无站点' : '暂未分配负责站点'),
        emptyDescription: resolvedScope === 'all' && resolvedKeyword ? '请更换站点名称或编号后重试' : (resolvedScope === 'all' ? '当前暂无可查看站点' : '当前账号下暂无负责站点，如有疑问请联系管理员')
      });
    }).catch(error => {
      if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
      if (scopeGen !== this._scopeGeneration) return;
      if (this.data.monitoringPublic && error && error.status === 403) {
        return api.responsibleSites({ scope: requestedScope, keyword: requestedKeyword }).then(res => {
          if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
          if (scopeGen !== this._scopeGeneration) return;
          const availableScopes = Array.isArray(res.available_scopes) ? res.available_scopes : ['mine'];
          const resolvedScope = res.scope === 'all' ? 'all' : 'mine';
          this._loadedScope.stations = resolvedScope;
          this._stationScope = resolvedScope;
          this.setData({
            sites: (res.items || []).map(site => Object.assign({}, site, {
              type_cn: maps.map(maps.SITE_TYPE, site.type, '其他站点'),
              monitoring_status: '', monitoring_status_label: '', monitoring_reason: '',
              last_received_at: '', last_communication_at: '', last_valid_observation_at: '', published_factor_count: 0,
            })),
            loading: false, error: '', monitoringPublic: false, monitoringEnabled: false,
            scope: resolvedScope, keyword: requestedKeyword, keywordInput: requestedKeyword,
            availableScopes, canViewAll: availableScopes.includes('all'),
            scopeCounts: res.scope_counts || { mine: 0, all: null }, sitesNoPermission: false, pendingScope: null,
          });
        }).catch(fallbackError => {
          if (this._unloaded || this._inactive || this._sitesRequestId !== requestId) return;
          if (scopeGen !== this._scopeGeneration) return;
          this.setData({ loading: false, sitesNoPermission: fallbackError && fallbackError.status === 403, error: fallbackError && fallbackError.status === 403 ? '' : '站点目录加载失败，请重试', pendingScope: null });
        });
      }
      if (scopeGen !== this._scopeGeneration) return;
      this.setData({ loading: false, error: this.data.monitoringPublic ? '站点监测信息加载失败，请重试' : '站点目录加载失败，请重试', pendingScope: null });
    });
  },

  onHide() {
    this._inactive = true;
    this.setMaintenanceSheetTabBarHidden(false);
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
    this._reagentRequestId = (this._reagentRequestId || 0) + 1;
  },

  onUnload() {
    this._unloaded = true;
    this._stationReturnSnapshot = null;
    this.setMaintenanceSheetTabBarHidden(false);
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
    this._reagentRequestId = (this._reagentRequestId || 0) + 1;
  },

  onRetry() {
    const retry = this._retryRequest || { scope: this.data.scope, keyword: this.data.keyword };
    this.loadSites(retry.scope, retry.keyword);
  },

  onScopeMine() {
    if (this.data.activeTab === 'reagents') return this.loadReagents('mine', { restoreScroll: false });
    return this.loadSites('mine', '');
  },

  onScopeAll() {
    if (!this.data.canViewAll) return false;
    if (this.data.activeTab === 'reagents') return this.loadReagents('all', { restoreScroll: false });
    return this.loadSites('all', '');
  },

  // 导航栏范围切换：点击轮转（与计划页交互一致）
  // 采用 pendingScope + 请求代次双重保护：
  //   1. pendingScope 让 UI 立即反馈 + 连续点击正确轮转（基于 pending 而非已提交 scope）
  //   2. _sitesRequestId / _reagentRequestId 确保旧响应不覆盖新结果
  //   3. 失败恢复：pendingScope 清零，保留已提交 scope
  onToggleScope() {
    if (!this.data.canViewAll || this._unloaded || this._inactive) return false;
    const currentOrPending = this.data.pendingScope || this.data.scope;
    const targetScope = currentOrPending === 'all' ? 'mine' : 'all';
    this.setData({ pendingScope: targetScope });
    if (this.data.activeTab === 'reagents') return this.loadReagents(targetScope, { restoreScroll: false });
    return this.loadSites(targetScope, '');
  },

  onKeywordInput(e) { this.setData({ keywordInput: e.detail.value || '' }); },

  onSearch() { if (this.data.scope === 'all') this.loadSites('all', this.data.keywordInput); },

  onClearSearch() {
    this.setData({ keywordInput: '' });
    if (this.data.scope === 'all') this.loadSites('all', '');
  },

  onTabSites() {
    if (this.data.activeTab === 'stations') return;
    // 缓存范围匹配才可直接复用，否则按当前共享范围重载
    const currentScope = this._stationScope
      || (this.data.monitoringPublic ? 'all' : (this.data.pendingScope || this.data.scope));
    const hasCachedSites = this.data.sites.length > 0;
    const scopeMatches = this._loadedScope.stations === currentScope;
    this.setData({ activeTab: 'stations', scope: currentScope, pendingScope: null });
    if (hasCachedSites && scopeMatches) return;
    // 范围不匹配或无缓存：按最新意图加载
    this.loadSites(currentScope, this.data.keyword || '');
  },

  onTabReagent() {
    if (this.data.activeTab === 'reagents') return;
    // 模式切换按最新 pending/已提交范围加载
    const user = getUser() || {};
    const roles = Array.isArray(user.roles) && user.roles.length ? user.roles : [user.role];
    const currentScope = roles.includes('admin')
      ? (this.data.pendingScope || this.data.scope) : 'mine';
    this.setData({ activeTab: 'reagents', scope: currentScope, pendingScope: null });
    // 有缓存且范围匹配则直接复用缓存（不重新请求）
    const hasCachedReagents = this._reagentOverview && Array.isArray(this._reagentOverview.items) && this._reagentOverview.items.length > 0;
    const scopeMatches = this._loadedScope.reagents === currentScope;
    if (hasCachedReagents && scopeMatches) {
      const expansionState = this._reagentExpansionFor(currentScope);
      this.setData({
        siteGroups: projectReagentSiteGroups(this._reagentOverview, this.data.activeFilter, expansionState),
      });
      return;
    }
    this.loadReagents(currentScope, { restoreScroll: false });
  },

  loadReagents(scope, options) {
    const requestedScope = scope === 'all' ? 'all' : (scope === 'mine' ? 'mine' : (this.data.scope || 'mine'));
    const restoreScroll = !options || options.restoreScroll !== false;
    const scrollTop = this._reagentScrollTop || 0;
    if (restoreScroll) {
      const currentExpansion = this._reagentExpansionFor(this.data.scope);
      (this.data.siteGroups || []).forEach(group => {
        if (group && group.site_id) currentExpansion[group.site_id] = group.expanded === true;
      });
    }
    const requestId = (this._reagentRequestId || 0) + 1;
    this._reagentRequestId = requestId;
    // 统一范围代次：跨模式过期请求不得写共享状态
    const scopeGen = ++this._scopeGeneration;
    this.setData({ reagentLoading: true, reagentError: '' });
    return api.reagentOverview({ scope: requestedScope }).then(res => {
      if (this._unloaded || this._inactive || requestId !== this._reagentRequestId) return false;
      if (scopeGen !== this._scopeGeneration) return false;
      this._reagentOverview = res || { items: [] };
      const scopeProjection = projectReagentScope(this._reagentOverview);
      const expansionState = this._reagentExpansionFor(scopeProjection.scope);
      this._loadedScope.reagents = scopeProjection.scope;
      this.setData({
        reagentLoading: false,
        reagentError: '',
        reagentNoViewPermission: false,
        siteGroups: projectReagentSiteGroups(this._reagentOverview, this.data.activeFilter, expansionState),
        scope: scopeProjection.scope,
        pendingScope: null,
        availableScopes: scopeProjection.availableScopes,
        canViewAll: scopeProjection.canViewAll,
        scopeCounts: scopeProjection.scopeCounts,
      });
      if (restoreScroll && scrollTop > 0 && wx.pageScrollTo) {
        setTimeout(() => {
          if (!this._unloaded && requestId === this._reagentRequestId && scopeGen === this._scopeGeneration) wx.pageScrollTo({ scrollTop: scrollTop, duration: 0 });
        }, 0);
      }
      return true;
    }).catch(error => {
      if (this._unloaded || this._inactive || requestId !== this._reagentRequestId) return false;
      if (scopeGen !== this._scopeGeneration) return false;
      if (error && error.status === 403) {
        this._reagentOverview = null;
        this._loadedScope.reagents = null;
        this.setData({ reagentLoading: false, reagentError: '', reagentNoViewPermission: true, siteGroups: [], pendingScope: null });
      } else {
        this.setData({ reagentLoading: false, reagentError: '试剂情况加载失败，请重试', pendingScope: null });
      }
      return false;
    });
  },

  onReagentRetry() { return this.loadReagents(this.data.scope); },

  _reagentExpansionFor(scope) {
    const key = scope === 'all' ? 'all' : 'mine';
    if (!this._reagentExpansion || Array.isArray(this._reagentExpansion)) {
      this._reagentExpansion = { mine: {}, all: {} };
    }
    if (!this._reagentExpansion[key] || typeof this._reagentExpansion[key] !== 'object') {
      this._reagentExpansion[key] = {};
    }
    return this._reagentExpansion[key];
  },

  onReagentFilterTap(event) {
    const filter = String(event.currentTarget.dataset.filter || '');
    if (!['', 'expired', 'expiring', 'low_volume'].includes(filter)) return;
    const expansionState = this._reagentExpansionFor(this.data.scope);
    this.setData({ activeFilter: filter, siteGroups: projectReagentSiteGroups(this._reagentOverview, filter, expansionState) });
  },

  onToggleSiteGroup(event) {
    const siteId = Number(event.currentTarget.dataset.siteId);
    if (!siteId) return;
    const current = (this.data.siteGroups || []).find(group => Number(group.site_id) === siteId);
    if (!current) return;
    const expansionState = this._reagentExpansionFor(this.data.scope);
    expansionState[siteId] = !current.expanded;
    this.setData({ siteGroups: projectReagentSiteGroups(this._reagentOverview, this.data.activeFilter, expansionState) });
  },

  _reagentByIdentity(siteId, reagentId) {
    const items = projectReagentOverview(this._reagentOverview, '');
    return items.find(item => Number(item.site_id) === Number(siteId)
      && Number(item.reagent_id) === Number(reagentId)) || null;
  },

  onOpenReplaceSheet(event) {
    const item = this._reagentByIdentity(
      event.currentTarget.dataset.siteId,
      event.currentTarget.dataset.reagentId
    );
    if (!item || !item.canReplace) return;
    this.setMaintenanceSheetTabBarHidden(true);
    this.setData({
      replaceSheetVisible: true,
      replaceSheet: {
        submitting: false, success: false, serverError: '', errors: {}, scrollIntoView: '', focusField: '', targetId: item.id,
        siteId: item.site_id, reagentId: item.reagent_id, siteName: item.site_name,
        reagentName: item.reagent_name, currentVolume: item.volume, unit: item.unit,
        newVolume: '', replaceTime: localBusinessDate(), estDays: '', batchNo: '', remark: '', idempotencyKey: null,
      },
    });
  },

  onCloseReplaceSheet() {
    if (this.data.replaceSheet.submitting) return;
    this.setData({ replaceSheetVisible: false }, () => {
      this.setMaintenanceSheetTabBarHidden(!!this.data.calibrateSheetVisible);
    });
  },

  _setReplaceField(field, value) {
    const patch = {
      [`replaceSheet.${field}`]: value,
      [`replaceSheet.errors.${field}`]: '',
      'replaceSheet.idempotencyKey': null,
      'replaceSheet.serverError': '',
    };
    if (this.data.replaceSheet.focusField === field) patch['replaceSheet.focusField'] = '';
    this.setData(patch);
  },
  onReplaceVolumeInput(e) { this._setReplaceField('newVolume', e.detail.value); },
  onReplaceTimeChange(e) { this._setReplaceField('replaceTime', e.detail.value); },
  onReplaceEstDaysInput(e) { this._setReplaceField('estDays', e.detail.value); },
  onReplaceBatchNoInput(e) { this._setReplaceField('batchNo', e.detail.value); },
  onReplaceRemarkInput(e) { this._setReplaceField('remark', e.detail.value); },

  onSubmitReplace() {
    const sheet = this.data.replaceSheet;
    if (sheet.submitting) return false;
    const newQty = Number(sheet.newVolume);
    const estDays = Number(sheet.estDays);
    const errors = {};
    if (!(newQty > 0)) errors.newVolume = '请输入大于 0 的新余量';
    if (!sheet.replaceTime) errors.replaceTime = '请选择更换日期';
    if (!Number.isInteger(estDays) || estDays <= 0) errors.estDays = '请输入正整数天数';
    if (Object.keys(errors).length) {
      this._focusFirstSheetError('replaceSheet', errors, ['newVolume', 'replaceTime', 'estDays']);
      return false;
    }
    const key = sheet.idempotencyKey || stableKey('reagent-replace', `${sheet.siteId}-${sheet.reagentId}`);
    this.setData({ 'replaceSheet.submitting': true, 'replaceSheet.serverError': '', 'replaceSheet.errors': {}, 'replaceSheet.idempotencyKey': key });
    return api.replaceReagentInventory({
      site_id: sheet.siteId, reagent_id: sheet.reagentId, old_qty: sheet.currentVolume,
      new_qty: newQty, new_batch_no: String(sheet.batchNo || '').trim(),
      expected_duration_days: estDays, replaced_at: `${sheet.replaceTime} 00:00:00`,
      remark: String(sheet.remark || '').trim(), _idempotency_key: key,
    }).then(() => {
      if (this._unloaded) return;
      this.setData({ 'replaceSheet.submitting': false, 'replaceSheet.success': true });
      return this.loadReagents();
    }).catch(error => {
      if (this._unloaded) return false;
      this.setData({ 'replaceSheet.submitting': false, 'replaceSheet.serverError': String(error && (error.error || error.message) || '更换登记失败，请重试') });
      return false;
    });
  },

  onOpenCalibrateSheet(event) {
    const item = this._reagentByIdentity(
      event.currentTarget.dataset.siteId,
      event.currentTarget.dataset.reagentId
    );
    if (!item || !item.canCalibrate) return;
    this.setMaintenanceSheetTabBarHidden(true);
    this.setData({
      calibrateSheetVisible: true,
      calibrateSheet: {
        submitting: false, success: false, serverError: '', errors: {}, scrollIntoView: '', focusField: '', targetId: item.id,
        siteId: item.site_id, reagentId: item.reagent_id, siteName: item.site_name,
        reagentName: item.reagent_name, replaceTime: item.last_replaced_at || '',
        standardValue: '', measuredValue: '', result: 'pass', followUp: '', remark: '', idempotencyKey: null,
      },
    });
  },

  onCloseCalibrateSheet() {
    if (this.data.calibrateSheet.submitting) return;
    this.setData({ calibrateSheetVisible: false }, () => {
      this.setMaintenanceSheetTabBarHidden(!!this.data.replaceSheetVisible);
    });
  },

  setMaintenanceSheetTabBarHidden(hidden) {
    if (hidden === false && this._maintenanceSheetTabBarHidden !== true) {
      this._maintenanceSheetTabBarHidden = false;
      return;
    }
    if (this._maintenanceSheetTabBarHidden === hidden) return;
    const method = hidden ? 'hideTabBar' : 'showTabBar';
    if (wx && typeof wx[method] === 'function') wx[method]({ animation: false });
    this._maintenanceSheetTabBarHidden = hidden;
  },

  _setCalibrateField(field, value) {
    const patch = {
      [`calibrateSheet.${field}`]: value,
      [`calibrateSheet.errors.${field}`]: '',
      'calibrateSheet.idempotencyKey': null,
      'calibrateSheet.serverError': '',
    };
    if (field === 'result' && value === 'pass') patch['calibrateSheet.errors.followUp'] = '';
    if (this.data.calibrateSheet.focusField === field) patch['calibrateSheet.focusField'] = '';
    this.setData(patch);
  },
  onCalibrateStandardInput(e) { this._setCalibrateField('standardValue', e.detail.value); },
  onCalibrateMeasuredInput(e) { this._setCalibrateField('measuredValue', e.detail.value); },
  onCalibrateResultTap(e) { this._setCalibrateField('result', e.currentTarget.dataset.result); },
  onCalibrateFollowUpTap(e) { this._setCalibrateField('followUp', e.currentTarget.dataset.follow); },
  onCalibrateRemarkInput(e) { this._setCalibrateField('remark', e.detail.value); },

  onSubmitCalibrate() {
    const sheet = this.data.calibrateSheet;
    if (sheet.submitting) return false;
    const standardInput = String(sheet.standardValue == null ? '' : sheet.standardValue).trim();
    const measuredInput = String(sheet.measuredValue == null ? '' : sheet.measuredValue).trim();
    const standard = Number(standardInput);
    const measured = Number(measuredInput);
    const errors = {};
    if (!standardInput || !Number.isFinite(standard)) errors.standardValue = '请输入标样值';
    if (!measuredInput || !Number.isFinite(measured)) errors.measuredValue = '请输入实测值';
    if (!['pass', 'fail'].includes(sheet.result)) errors.result = '请选择标定结果';
    if (sheet.result === 'fail' && !['recalibrate', 'repair'].includes(sheet.followUp)) errors.followUp = '请选择后续动作';
    if (Object.keys(errors).length) {
      this._focusFirstSheetError('calibrateSheet', errors, ['standardValue', 'measuredValue', 'result', 'followUp']);
      return false;
    }
    const key = sheet.idempotencyKey || stableKey('reagent-qc', `${sheet.siteId}-${sheet.reagentId}`);
    this.setData({ 'calibrateSheet.submitting': true, 'calibrateSheet.serverError': '', 'calibrateSheet.errors': {}, 'calibrateSheet.idempotencyKey': key });
    return api.submitReagentQc({
      site_id: sheet.siteId, reagent_id: sheet.reagentId,
      standard_value: standard, measured_value: measured, passed: sheet.result === 'pass',
      fail_action: sheet.result === 'fail' ? (sheet.followUp === 'recalibrate' ? 'calibrate' : 'repair') : '',
      qc_time: localBusinessDateTime(), remark: String(sheet.remark || '').trim(), _idempotency_key: key,
    }).then(() => {
      if (this._unloaded) return;
      this.setData({ 'calibrateSheet.submitting': false, 'calibrateSheet.success': true });
      return this.loadReagents();
    }).catch(error => {
      if (this._unloaded) return false;
      this.setData({ 'calibrateSheet.submitting': false, 'calibrateSheet.serverError': String(error && (error.error || error.message) || '标定提交失败，请重试') });
      return false;
    });
  },

  noop() {},

  _focusFirstSheetError(sheetName, errors, orderedFields) {
    const firstField = orderedFields.find(field => errors[field]);
    if (!firstField) return;
    const prefix = sheetName === 'replaceSheet' ? 'replace' : 'calibrate';
    this.setData({
      [`${sheetName}.errors`]: errors,
      [`${sheetName}.scrollIntoView`]: '',
      [`${sheetName}.focusField`]: '',
    }, () => {
      this.setData({
        [`${sheetName}.scrollIntoView`]: `${prefix}-field-${firstField}`,
        [`${sheetName}.focusField`]: firstField,
      });
    });
  },

  openSite(e) {
    const siteId = Number(e.currentTarget.dataset.id);
    if (!siteId) return;
    this._stationReturnSnapshot = {
      activeTab: this.data.activeTab,
      scope: this.data.scope,
      pendingScope: this.data.pendingScope,
      keywordInput: this.data.keywordInput,
      keyword: this.data.keyword,
      sites: (this.data.sites || []).slice(),
      scrollTop: this._reagentScrollTop || 0,
    };
    app.globalData.selSiteId = siteId;
    wx.navigateTo({ url: '/pages/site/site?site_id=' + siteId + '&source=responsible_sites_monitoring' });
  }
});
