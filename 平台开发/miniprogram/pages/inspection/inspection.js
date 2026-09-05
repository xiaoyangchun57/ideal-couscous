const api = require('../../services/api.js');
const { RESULT, INSPECTION_CATEGORY, linkedWorkorderCn, map } = require('../../services/maps.js');
const { getSites, getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const {
  chooseAndCompress, chooseInspectionPhotos, fileToBase64, persistFile, captureFlushedPhoto,
  isPhotoSelectionCancelled, photoCaptureErrorMessage, shouldOpenCameraSettings,
  requestCaptureSessionWithLocation, captureSourceNeedsLocationSession,
  collectInspectionPhotoUploadResults, processPhotoUploadIssues,
  setInspectionPhotoIssueMessage,
  runPhotoActionOnce,
  validateReportPhotoPaths, handlePhotoActionFailure,
  deletePendingReportPhotoOnce,
  deletePendingPhotoOnce, inspectionUploadTaskResult,
} = require('../../utils/photos.js');
const { resolveUploadUrl, uploadStoragePath, prepareReportPhotoStoragePaths } = require('../../utils/url.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const localStore = require('../../utils/localStore.js');
const { flushLocalOps } = require('../../utils/sync.js');
const {
  photoRequirement, inspectionPhotoProgress, projectInspectionFieldProgress,
  inspectionItemPhotoState, inspectionItemPhotoRequirement,
  applyRejectedInspectionPhotoPurge, applyInspectionSubmission,
  rejectedInspectionEvidence, addPendingInspectionPhotos, removePendingInspectionPhoto,
} = require('../../utils/executionState.js');
const executionTarget = require('../../utils/executionTarget.js');
const { buildDeparturePreparation, quantityOrNull } = require('../../utils/departurePreparation.js');
const { hasInspectionFieldRecord, resolveLocalSubmitFlush } = require('../../utils/inspectionSubmissionState.js');
const { requestLocation, locationErrorMessage, shouldOpenLocationSettings } = require('../../utils/location.js');
const { inventoryOptions, inventoryErrorMessage, buildPartsPayload } = require('../../utils/partsApplication.js');
const { buildCheckinPayload, reworkResourcePresentation } = require('../../utils/reworkFlow.js');

const app = getApp();

const REPORT_TYPES = [
  { value: 'sensory', label: '感官异常' },
  { value: 'equipment', label: '设备异常' },
  { value: 'environment', label: '环境异常' },
  { value: 'violation', label: '违规操作' },
  { value: 'pollution', label: '污染事件' },
];

const PARTS_FULFILLMENT_OPTIONS = [
  { key: 'stock', label: '库存领用' },
  { key: 'local_purchase', label: '附近急购' },
  { key: 'vendor_order', label: '厂家订购' }
];

function pendingSyncCount() {
  return queueCount() + localStore.queueCount();
}

function isTransientSyncError(error) {
  return !error || error.code === -1 || error.status >= 500;
}

function withTimeout(promise, timeoutMs, message) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject({ code: 'REQUEST_TIMEOUT', error: message }), timeoutMs);
    Promise.resolve(promise).then(value => {
      clearTimeout(timer);
      resolve(value);
    }, error => {
      clearTimeout(timer);
      reject(error);
    });
  });
}

function photoIdempotencyKey(siteId, path, index) {
  // Keep one key when a request times out after the server has committed it.
  const safePath = String(path || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-24);
  return 'photo_' + siteId + '_' + Date.now() + '_' + index + '_' + safePath + '_' + Math.floor(Math.random() * 1e6);
}

function inspectionItemStatus(item, syncPending) {
  if (item.evidence_status === 'supplement_required') return { label: '需补拍', code: 'supplement' };
  if (item.evidence_status === 'replacement_submitted') return { label: '待审核', code: 'supplement_review' };
  if (syncPending) return { label: '待同步', code: 'sync' };
  const reviewStatus = Number(item.review_status || 0);
  if (reviewStatus === 3) return { label: '待整改', code: 'rework' };
  if (item.result && reviewStatus === 1) return { label: '待审核', code: 'review' };
  if (item.result && reviewStatus === 2) return { label: '已通过', code: 'approved' };
  return { label: RESULT[item.result] || '待检', code: item.result || 'pending' };
}

function inspectionSheetPhotoRequirement(sheet, photos, pendingPhotos, localPhotos) {
  if (sheet && sheet.replacementPhotoStatus === 'pending_review') {
    return {
      required: 0, captured: 0, missing: 0, ready: false, blocked: true,
      blockReason: sheet.replacementBlockReason || '原照片待审核，审核完成后才能补拍',
    };
  }
  const remote = Array.isArray(photos) ? photos : [];
  const pending = Array.isArray(pendingPhotos) ? pendingPhotos : [];
  const local = Array.isArray(localPhotos) ? localPhotos : [];
  return sheet && sheet.supplementOnly
    ? photoRequirement(sheet.replacementRequiredPhotos, pending.length, local.length)
    : photoRequirement(sheet && sheet.requiredPhotos, remote.length, local.length);
}

function decoratePackageResources(pkg) {
  if (!pkg) return pkg;
  const resourceParts = (pkg.resource_parts || []).map(part => Object.assign({}, part, {
    planned_quantity: quantityOrNull(part.planned_quantity),
    issued_quantity: quantityOrNull(part.issued_quantity),
    remaining_quantity: quantityOrNull(part.remaining_quantity)
  }));
  const presentation = reworkResourcePresentation(pkg);
  return Object.assign({}, pkg, {
    plan_display_name: pkg.package_label || pkg.plan_name
      || (Number(pkg.schedule_id) > 0 ? ('计划#' + Number(pkg.schedule_id)) : '计划名称未记录'),
    resource_parts: resourceParts,
    rework_resource: Object.assign({}, presentation, pkg.rework_resource || {}),
  });
}

Page({
  data: {
    // ===== 6.5.1 执行包与站点选择阶段 =====
    viewPhase: 'entry',       // entry | departure | inspection
    entryState: 'initial_loading',
    entryPackages: [],
    selectedEntryPackageId: null,
    selectedEntrySiteId: null,
    entryTargetMessage: null,
    entryCanEnter: false,
    entryHasAmbiguity: false,
    entrySource: 'generic',
    entryError: '',
    entryRefreshError: '',
    entryEntering: false,
    entrySites: [],           // 当前选中包的站点列表（用于选择阶段展示）
    entryCompletedPackages: [],
    entryBrowseClosed: false, // 只读浏览已闭环站点结果
    // ===== 6.5.2 出发资源与车辆准备阶段 =====
    departureVm: null,        // DeparturePreparationViewModel
    departureState: 'initial_loading', // initial_loading | ready | refresh_error | blocking_error | target_unavailable
    departureRefreshError: '',
    departureSubmitting: {    // 各动作独立提交状态
      vehicleAck: false,
      partsAck: false,
      partsIssue: false,
      vehicleCheckout: false,
      reworkRequest: false,
      extend: false,
    },
    departureTarget: null,    // 当前锁定的 ExecutionTarget 快照
    departureEnteringSite: false,
    departureVehicleTemplateLoading: false,
    // ===== 现场阶段（原有） =====
    packages: [],
    responsibleSites: getSites(),
    currentPackage: null,
    selectedPlanId: null,
    focusedItemId: null,
    sites: [],
    selSite: null,
    selSiteId: null,
    site: null,
    categories: [],
    total: 0, completed: 0, completionPercent: 0, progressFillStyle: 'width:0%', loaded: false, executionError: '',
    abnormalCount: 0,
    online: true, syncCount: 0,
    stationStage: null,
    stationSyncPending: false,
    stationPendingCount: 0,
    stationOutcome: null,
    reagents: [],
    reagentAction: '暂无记录',
    photoProgress: { req: 0, taken: 0, missing: 0 },
    reportTypes: REPORT_TYPES,
    reportSheet: { open: false, typeIndex: 0, description: '', photos: [], submitting: false },
    reagentSheet: { open: false, mode: 'replacement', index: 0, newQty: '', duration: '', standardValue: '', measuredValue: '', passed: true, failAction: 'calibrate', submitting: false },
    sheet: { open: false, item: null, result: 'normal', remark: '', calibrator: '', calValues: '', photos: [], localPhotos: [], localPhotoMeta: [], supplementOnly: false, originalPhotoCount: 0, photoResultTitle: '', photoResultMessage: '' },
    submitting: false,
    checkingOut: false,
    partsIssueSheet: { open: false, items: [], submitting: false },
    partsOptions: [],
    partsInventoryStatus: 'idle', partsInventoryError: '',
    partsFulfillmentOptions: PARTS_FULFILLMENT_OPTIONS,
    partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false },
    vehicleSheet: { open: false, mode: 'dispatch', mileage: '', remarks: '', items: [], submitting: false },
    reworkResourceSheet: { open: false, mode: 'vehicle', vehicles: [], vehicleIndex: 0, exceptionReason: '', submitting: false },
    refuelSheet: { open: false, quantity: '', amount: '', mileage: '', remark: '', label: '加油', unit: 'L', submitting: false },
    vehicleFaultSheet: { open: false, faultType: '车辆故障', mileage: '', description: '', remark: '', submitting: false }
  },

  onShow() {
    this._alive = true;
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.refreshSyncState();
    if (this.data.viewPhase === 'departure' && this.data.departureTarget) {
      this._departureActionIds = {};
      this._departureRefreshing = false;
      this.setData({
        departureSubmitting: {
          vehicleAck: false, partsAck: false, partsIssue: false,
          vehicleCheckout: false, reworkRequest: false, extend: false,
        },
        departureEnteringSite: false,
        departureVehicleTemplateLoading: false,
        'partsIssueSheet.submitting': false,
        'vehicleSheet.submitting': false,
        'reworkResourceSheet.submitting': false,
      });
      this.onDepartureRefresh();
      return;
    }
    this.loadExecution();
  },

  onLoad() {
    this._alive = true;
  },

  onUnload() {
    this.onHide();
  },

  onHide() {
    this._alive = false;
    this._executionRequestId = (this._executionRequestId || 0) + 1;
    this._entryRequestId = (this._entryRequestId || 0) + 1;
    this._tasksRequestId = (this._tasksRequestId || 0) + 1;
    this._reagentsRequestId = (this._reagentsRequestId || 0) + 1;
    this._departureRefreshId = (this._departureRefreshId || 0) + 1;
    this._departureArrivalRequestId = (this._departureArrivalRequestId || 0) + 1;
    this._departureOpenRequestId = (this._departureOpenRequestId || 0) + 1;
    this._departureEpoch = (this._departureEpoch || 0) + 1;
    this._departureRefreshing = false;
  },

  onPageScroll(event) {
    if (!this.data.sheet.open) this._inspectionParentScrollTop = Number(event && event.scrollTop) || 0;
  },

  refreshSyncState(done) {
    wx.getNetworkType({
      success: (res) => {
        this.setData({ online: res.networkType !== 'none', syncCount: pendingSyncCount() });
        if (done) done();
      },
      fail: () => {
        this.setData({ syncCount: pendingSyncCount() });
        if (done) done();
      }
    });
  },

  onPullDownRefresh() {
    this.loadExecution(() => wx.stopPullDownRefresh());
  },

  loadExecution(done) {
    if (this._entryTarget === undefined) {
      this._entryTarget = executionTarget.normalizeExecutionTarget(this._readNavTarget());
      this._clearNavTarget();
    }
    const requestId = (this._executionRequestId || 0) + 1;
    this._executionRequestId = requestId;
    api.todayExecution().then(res => {
      if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._executionRequestId)) { if (done) done(); return; }
      const packages = (res.packages || []).map(decoratePackageResources);
      this._rawExecutionPackages = packages;
      const entryPackages = packages.map(executionTarget.buildPackageOption);
      const resolved = executionTarget.resolveExecutionTarget(entryPackages, this._entryTarget);
      this._allEntryPackages = entryPackages;
      // Entry selection contains only currently executable candidates. Closed
      // results remain available from the dedicated read-only branch.
      const entryCompletedPackages = [];

      const selectedPkg = resolved.selectedPackageId
        ? resolved.packages.find(p => p.executionPlanId === resolved.selectedPackageId)
        : null;
      const entrySites = selectedPkg ? selectedPkg.sites : [];

      if (this.data.viewPhase === 'inspection') {
        this._applyInspectionData(packages, done);
        return;
      }

      // 闭环结果只读浏览模式：刷新后仍保持浏览视图
      if (this.data.entryBrowseClosed) {
        this._applyBrowseClosedView(entryPackages, done);
        return;
      }

      let autoEnter = resolved.state === 'ready_to_enter'
        && this._entryTarget && this._entryTarget.executionPlanId && this._entryTarget.siteId;
      // 单包单站且无歧义：不进入选择页，直接进入出发准备
      const singlePackage = resolved.packages.length === 1;
      const singleSite = entrySites.length === 1;
      const noAmbiguity = !resolved.hasAmbiguity && singlePackage && singleSite;
      if (noAmbiguity && resolved.state === 'ready_to_enter' && !autoEnter) {
        this._entryTarget = this._entryTarget || executionTarget.normalizeExecutionTarget({
          executionPlanId: resolved.selectedPackageId,
          siteId: resolved.selectedSiteId,
          source: 'auto_select',
        });
        autoEnter = true;
      }
      if (!noAmbiguity) {
        wx.setNavigationBarTitle({ title: '选择执行任务' });
      }
      this.setData({
        // Keep a precise auto-entry in the existing loading state so the
        // package chooser never flashes before server target verification.
        entryState: autoEnter ? 'initial_loading' : resolved.state,
        entryPackages: resolved.packages,
        selectedEntryPackageId: resolved.selectedPackageId,
        selectedEntrySiteId: resolved.selectedSiteId,
        entryTargetMessage: resolved.targetMessage,
        entryCanEnter: resolved.canEnter,
        entryHasAmbiguity: resolved.hasAmbiguity,
        entrySites,
        entryCompletedPackages,
        entryError: '',
        entryRefreshError: '',
        loaded: true,
      }, () => {
        if (autoEnter && executionTarget.shouldApplyAsyncResult(
          this._alive, requestId, this._executionRequestId,
          Number(this.data.selectedEntryPackageId) === Number(resolved.selectedPackageId)
            && Number(this.data.selectedEntrySiteId) === Number(resolved.selectedSiteId)
        )) this.onEnterSite();
      });
      if (done) done();
    }).catch(err => {
      if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._executionRequestId)) { if (done) done(); return; }
      const executionError = (err && (err.error || err.message)) || '巡检任务加载失败，请检查网络后重试';
      if (this.data.viewPhase === 'inspection') {
        this.setData({ executionError, loaded: true });
      } else {
        const hasPrevious = (this.data.entryPackages || []).length > 0;
        this.setData({
          entryState: hasPrevious ? this.data.entryState : 'blocking_error',
          entryError: executionError,
          entryRefreshError: hasPrevious ? executionError : '',
          loaded: true,
        });
      }
      if (done) done();
    });
  },

  _readNavTarget() {
    return app.globalData.executionTarget || {};
  },

  _clearNavTarget() {
    app.globalData.executionTarget = null;
  },

  _applyInspectionData(packages, done) {
    const preferredSiteId = this.data.selSiteId;
    const preferredPlanId = this.data.selectedPlanId;
    const currentPackage = (packages || []).find(pkg => Number(pkg.plan_id) === Number(preferredPlanId));
    if (!currentPackage) {
      this.setData({ executionError: '当前执行包已不可用，请返回重新选择', loaded: true });
      if (done) done();
      return;
    }
    const sites = currentPackage ? currentPackage.sites || [] : [];
    const selected = sites.find(site => Number(site.site_id) === Number(preferredSiteId));
    if (!selected) {
      this.setData({ executionError: '当前站点已不可用，请返回重新选择', loaded: true });
      if (done) done();
      return;
    }
    const selSiteId = selected ? selected.site_id : null;
    this.setData({ packages, currentPackage, selectedPlanId: currentPackage ? currentPackage.plan_id : null, executionError: '',
      sites: sites.map(s => Object.assign({}, s, { id: s.site_id })), selSiteId, loaded: true,
      selSite: currentPackage ? this.data.selSite : null, site: currentPackage ? this.data.site : null,
      categories: currentPackage ? this.data.categories : [], total: currentPackage ? this.data.total : 0,
      completed: currentPackage ? this.data.completed : 0, completionPercent: currentPackage ? this.data.completionPercent : 0,
      progressFillStyle: currentPackage ? this.data.progressFillStyle : 'width:0%',
      abnormalCount: currentPackage ? this.data.abnormalCount : 0,
      photoProgress: currentPackage ? this.data.photoProgress : { req: 0, taken: 0, missing: 0 } });
    if (selSiteId) this.loadTasks(selSiteId, done); else if (done) done();
  },

  onEntrySelectPackage(e) {
    const pkgId = Number(e.currentTarget.dataset.id);
    if (this._entryTarget && this._entryTarget.executionPlanId
      && Number(this._entryTarget.executionPlanId) !== pkgId) return;
    const pkg = this.data.entryPackages.find(p => p.executionPlanId === pkgId);
    if (!pkg) return;
    this._invalidateEntryRequest();
    const browseClosed = this.data.entryBrowseClosed;
    const selectableSites = browseClosed
      ? pkg.sites.filter(s => s.phase === 'closed')
      : pkg.sites.filter(s => s.phase !== 'closed');
    const canAutoSelectSite = selectableSites.length === 1;
    this.setData({
      selectedEntryPackageId: pkgId,
      selectedEntrySiteId: canAutoSelectSite ? selectableSites[0].siteId : null,
      entrySites: pkg.sites,
      entryCanEnter: canAutoSelectSite,
      entryHasAmbiguity: selectableSites.length > 1 || this.data.entryPackages.length > 1,
    });
  },

  onEntrySelectSite(e) {
    const siteId = Number(e.currentTarget.dataset.id);
    if (this._entryTarget && this._entryTarget.siteId
      && Number(this._entryTarget.siteId) !== siteId) return;
    const pkg = this.data.entryPackages.find(p => p.executionPlanId === this.data.selectedEntryPackageId);
    const site = pkg && pkg.sites.find(s => s.siteId === siteId);
    if (!site || (this.data.entryBrowseClosed && site.phase !== 'closed')) return;
    this._invalidateEntryRequest();
    this.setData({ selectedEntrySiteId: siteId, entryCanEnter: true });
  },

  _invalidateEntryRequest() {
    this._entryRequestId = (this._entryRequestId || 0) + 1;
    if (this.data.entryEntering) this.setData({ entryEntering: false });
  },

  onEnterSite() {
    if (!this.data.entryCanEnter || this.data.entryEntering) return;
    const pkgId = this.data.selectedEntryPackageId;
    const siteId = this.data.selectedEntrySiteId;
    if (!pkgId || !siteId) return;
    const packageTarget = (this._allEntryPackages || this.data.entryPackages || [])
      .find(pkg => Number(pkg.executionPlanId) === Number(pkgId));
    const siteTarget = packageTarget && (packageTarget.sites || []).find(site => Number(site.siteId) === Number(siteId));
    if (this.data.entryBrowseClosed && siteTarget && siteTarget.phase !== 'closed') {
      this.setData({ entryTargetMessage: '仅可查看已闭环站点结果', entryCanEnter: false });
      return;
    }
    if (!packageTarget || !siteTarget || !executionTarget.matchesExecutionTarget(packageTarget, siteTarget, this._entryTarget)) {
      this.setData({ entryTargetMessage: '目标已变化，请重新选择', entryCanEnter: false });
      return;
    }
    this.setData({ entryEntering: true });
    const requestId = (this._entryRequestId || 0) + 1;
    this._entryRequestId = requestId;
    api.executionSiteTasks(pkgId, siteId, {
      reworkOnly: !!(this._entryTarget && this._entryTarget.reworkOnly),
    }).then(res => {
      const currentPackageId = this.data.selectedEntryPackageId;
      const currentSiteId = this.data.selectedEntrySiteId;
      if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._entryRequestId,
        Number(currentPackageId) === Number(pkgId) && Number(currentSiteId) === Number(siteId))) return;
      let focusedReworkItemId = this._entryTarget && this._entryTarget.itemId;
      if (!executionTarget.itemBelongsToCategories(res && res.categories, focusedReworkItemId)
          && this._entryTarget && this._entryTarget.reworkOnly) {
        const firstCategory = (res.categories || []).find(category => (category.items || []).length);
        const firstItem = firstCategory && firstCategory.items[0];
        focusedReworkItemId = firstItem && Number(firstItem.item_id) > 0 ? Number(firstItem.item_id) : null;
        if (focusedReworkItemId) {
          this._entryTarget = Object.assign({}, this._entryTarget, { itemId: focusedReworkItemId });
        }
      }
      if (!focusedReworkItemId && this._entryTarget && this._entryTarget.reworkOnly) {
        this.setData({
          entryState: this.data.entryState === 'initial_loading' ? 'target_unavailable' : this.data.entryState,
          entryEntering: false,
          entryTargetMessage: '整改任务状态已更新，请刷新首页后重试',
        });
        return;
      }
      if (!executionTarget.itemBelongsToCategories(res && res.categories, focusedReworkItemId)) {
        this.setData({
          entryState: this.data.entryState === 'initial_loading' ? 'target_unavailable' : this.data.entryState,
          entryEntering: false,
          entryTargetMessage: '目标检查项已不可用，请重新选择',
        });
        wx.showToast({ title: '目标检查项已不可用', icon: 'none' });
        return;
      }
      const rawPkg = (this._rawExecutionPackages || []).find(pkg => Number(pkg.plan_id) === Number(pkgId));
      if (!rawPkg) {
        this.setData({
          entryState: this.data.entryState === 'initial_loading' ? 'target_unavailable' : this.data.entryState,
          entryEntering: false,
          entryTargetMessage: '目标执行包已不可用，请重新选择',
        });
        return;
      }
      const verifiedPkg = Object.assign({}, rawPkg, {
        arrival_gate: (res && res.arrival_gate) || rawPkg.arrival_gate,
      });
      // 构建出发准备 ViewModel 并切换阶段
      const target = executionTarget.normalizeExecutionTarget({
        scheduleId: verifiedPkg.schedule_id,
        executionPlanId: pkgId,
        workDate: verifiedPkg.work_date,
        siteId,
        source: this._entryTarget ? this._entryTarget.source : 'generic',
        itemId: focusedReworkItemId,
        reworkOnly: !!(this._entryTarget && this._entryTarget.reworkOnly),
      });
      const vm = buildDeparturePreparation(verifiedPkg, target);
      const sites = (verifiedPkg.sites || []).map(site => Object.assign({}, site, { id: site.site_id }));
      this._rawExecutionPackages = (this._rawExecutionPackages || []).map(pkg =>
        Number(pkg.plan_id) === Number(pkgId) ? verifiedPkg : pkg
      );
      if (this.data.entryBrowseClosed) {
        this.setData({
          viewPhase: 'inspection',
          entryEntering: false,
          selectedPlanId: pkgId,
          selSiteId: siteId,
          focusedItemId: (this._entryTarget && this._entryTarget.itemId) || null,
          packages: this._rawExecutionPackages,
          currentPackage: verifiedPkg,
          sites,
        });
        this.loadTasks(siteId, null, res);
        return;
      }
      this._departureEpoch = (this._departureEpoch || 0) + 1;
      this._departureOpenRequestId = (this._departureOpenRequestId || 0) + 1;
      this._departureActionIds = {};
      if (vm.preparationCompleted) {
        wx.setNavigationBarTitle({ title: '现场作业' });
        this.setData({
          viewPhase: 'inspection',
          entryEntering: false,
          selectedPlanId: pkgId,
          selSiteId: siteId,
          focusedItemId: (this._entryTarget && this._entryTarget.itemId) || null,
          packages: this._rawExecutionPackages,
          currentPackage: verifiedPkg,
          sites,
          departureVm: vm,
          departureState: 'ready',
          departureTarget: target,
          departureRefreshError: '',
          departureEnteringSite: false,
          departureVehicleTemplateLoading: false,
        });
        this.loadTasks(siteId, null, res);
        return;
      }
      wx.setNavigationBarTitle({ title: '出发准备' });
      this.setData({
        viewPhase: 'departure',
        entryEntering: false,
        selectedPlanId: pkgId,
        selSiteId: siteId,
        focusedItemId: (this._entryTarget && this._entryTarget.itemId) || null,
        packages: this._rawExecutionPackages,
        currentPackage: verifiedPkg,
        sites,
        departureVm: vm,
        departureState: 'ready',
        departureTarget: target,
        departureRefreshError: '',
        departureVehicleTemplateLoading: false,
      });
    }).catch(err => {
      if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._entryRequestId,
        Number(this.data.selectedEntryPackageId) === Number(pkgId) && Number(this.data.selectedEntrySiteId) === Number(siteId))) return;
      const msg = (err && err.error) || '站点详情加载失败，请重试';
      this.setData({
        entryState: this._entryTarget && this._entryTarget.reworkOnly ? 'target_unavailable'
          : (this.data.entryState === 'initial_loading' ? 'ready_to_enter' : this.data.entryState),
        entryCanEnter: this._entryTarget && this._entryTarget.reworkOnly ? false : this.data.entryCanEnter,
        entryEntering: false,
        entryTargetMessage: msg,
        entryError: msg,
      });
    });
  },

  // ===== 6.5.2 出发资源与车辆准备阶段动作 =====

  onDepartureBack() {
    this._departureRefreshId = (this._departureRefreshId || 0) + 1;
    this._departureArrivalRequestId = (this._departureArrivalRequestId || 0) + 1;
    this._departureOpenRequestId = (this._departureOpenRequestId || 0) + 1;
    this._departureEpoch = (this._departureEpoch || 0) + 1;
    this._departureRefreshing = false;
    this.setData({
      viewPhase: 'entry',
      departureVm: null,
      departureState: 'initial_loading',
      departureTarget: null,
      departureEnteringSite: false,
      departureVehicleTemplateLoading: false,
    });
  },

  onDepartureRefresh() {
    if (this._departureRefreshing || this._departureHasPendingActions()) return;
    const target = this._departureTargetSnapshot();
    if (!target || !target.executionPlanId) return;
    const hasPrevious = !!this.data.departureVm;
    const requestId = (this._departureRefreshId || 0) + 1;
    this._departureRefreshId = requestId;
    const epoch = this._departureEpoch || 0;
    this._departureRefreshing = true;
    this.setData({ departureState: hasPrevious ? 'action_pending' : 'initial_loading' });
    api.todayExecution().then(res => {
      if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureRefreshId)) return;
      this._departureRefreshing = false;
      const rawPackages = (res.packages || []).map(decoratePackageResources);
      const rawPkg = rawPackages.find(p => Number(p.plan_id) === Number(target.executionPlanId));
      const targetSite = rawPkg && (rawPkg.sites || [])
        .find(site => Number(site.site_id) === Number(target.siteId));
      const targetMatches = rawPkg
        && (!target.scheduleId || Number(rawPkg.schedule_id) === Number(target.scheduleId))
        && (!target.workDate || String(rawPkg.work_date || '') === String(target.workDate));
      if (!targetMatches || !targetSite) {
        this.setData({
          departureState: 'target_unavailable',
          departureRefreshError: '目标执行包或站点已不可用',
        });
        return;
      }
      const vm = buildDeparturePreparation(rawPkg, target);
      this._rawExecutionPackages = rawPackages;
      this.setData({
        packages: rawPackages,
        currentPackage: rawPkg,
        departureVm: vm,
        departureState: 'ready',
        departureRefreshError: '',
      });
    }).catch(err => {
      if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureRefreshId)) return;
      this._departureRefreshing = false;
      const msg = (err && (err.error || err.message)) || '刷新失败，请检查网络后重试';
      if (hasPrevious) {
        const vm = Object.assign({}, this.data.departureVm, { refreshError: msg });
        if (vm) {
          this.setData({ departureVm: vm, departureState: 'refresh_error', departureRefreshError: msg });
        }
      } else {
        this.setData({ departureState: 'blocking_error', departureRefreshError: msg });
      }
    });
  },

  _departureTargetSnapshot() {
    const target = this.data.departureTarget;
    if (!target) return null;
    return {
      scheduleId: Number(target.scheduleId) || null,
      executionPlanId: Number(target.executionPlanId) || null,
      siteId: Number(target.siteId) || null,
      workDate: target.workDate || null,
      source: target.source || 'generic',
      itemId: Number(target.itemId) || null,
      reworkOnly: !!target.reworkOnly,
    };
  },

  _departureTargetMatches(target) {
    const current = this.data.departureTarget;
    return Boolean(target && current
      && Number(target.scheduleId || 0) === Number(current.scheduleId || 0)
      && Number(target.executionPlanId) === Number(current.executionPlanId)
      && Number(target.siteId) === Number(current.siteId)
      && String(target.workDate || '') === String(current.workDate || '')
      && String(target.source || 'generic') === String(current.source || 'generic')
      && !!target.reworkOnly === !!current.reworkOnly);
  },

  _departureAsyncCurrent(target, epoch, requestId, currentRequestId) {
    return Boolean(this._alive && this.data.viewPhase === 'departure'
      && this._departureTargetMatches(target)
      && epoch === (this._departureEpoch || 0)
      && requestId === currentRequestId);
  },

  _departureHasPendingActions() {
    return Object.values(this.data.departureSubmitting || {}).some(Boolean)
      || this.data.departureEnteringSite || this.data.departureVehicleTemplateLoading
      || this._departureRefreshing;
  },

  _departureCanWrite(actionKey) {
    return !this._departureWriteBlockReason(actionKey);
  },

  _departureWriteBlockReason(actionKey) {
    const target = this.data.departureTarget;
    const pkg = this.data.currentPackage;
    const targetSite = pkg && (pkg.sites || [])
      .some(site => Number(site.site_id) === Number(target && target.siteId));
    if (!this._alive || this.data.viewPhase !== 'departure') {
      return '当前页面状态已变化，请返回出发准备后重试';
    }
    if (this.data.departureState !== 'ready') {
      return '出发准备状态正在更新，请刷新后重试';
    }
    if (!target || !pkg || Number(pkg.plan_id) !== Number(target.executionPlanId) || !targetSite) {
      return '当前执行目标已变化，请刷新后重试';
    }
    if (actionKey === 'vehicleCheckout' && this.data.departureVehicleTemplateLoading) {
      return '出车前检查正在加载，请稍候';
    }
    if ((this.data.departureSubmitting || {})[actionKey]) {
      return '当前操作正在处理中，请稍候';
    }
    return '';
  },

  _showDepartureActionBlocked(actionKey) {
    const reason = this._departureWriteBlockReason(actionKey);
    if (reason) wx.showToast({ title: reason, icon: 'none' });
    return Boolean(reason);
  },

  _beginDepartureAction(actionKey) {
    if (!this._departureCanWrite(actionKey)) return null;
    this._departureActionIds = this._departureActionIds || {};
    const actionId = (this._departureActionIds[actionKey] || 0) + 1;
    this._departureActionIds[actionKey] = actionId;
    const snapshot = {
      actionKey,
      actionId,
      epoch: this._departureEpoch || 0,
      target: this._departureTargetSnapshot(),
    };
    this.setData({ ['departureSubmitting.' + actionKey]: true });
    return snapshot;
  },

  _departureActionCurrent(snapshot) {
    return Boolean(snapshot && this._alive && this.data.viewPhase === 'departure'
      && snapshot.epoch === (this._departureEpoch || 0)
      && this._departureTargetMatches(snapshot.target)
      && this._departureActionIds
      && this._departureActionIds[snapshot.actionKey] === snapshot.actionId);
  },

  _finishDepartureAction(snapshot) {
    if (!this._departureActionCurrent(snapshot)) return false;
    this.setData({ ['departureSubmitting.' + snapshot.actionKey]: false });
    return true;
  },

  onDepartureConfirmVehicle() {
    this._confirmDepartureAcknowledgement('vehicleAck');
  },

  onDepartureConfirmParts() {
    this._confirmDepartureAcknowledgement('partsAck');
  },

  onDepartureToggleAck(e) {
    const type = e && e.currentTarget && e.currentTarget.dataset.type;
    if (type === 'vehicle') this.onDepartureConfirmVehicle();
    else if (type === 'parts') this.onDepartureConfirmParts();
  },

  _confirmDepartureAcknowledgement(actionKey) {
    const vm = this.data.departureVm;
    const isVehicle = actionKey === 'vehicleAck';
    const acknowledgement = isVehicle ? vm && vm.vehicleAcknowledgement : vm && vm.partsAcknowledgement;
    if (!this._departureCanWrite(actionKey) || !vm || vm.mode === 'carryover' || !acknowledgement
        || acknowledgement.status !== 'unconfirmed') return;
    wx.showModal({
      title: isVehicle ? '核对车辆安排' : '核对备件计划',
      content: isVehicle
        ? '确认已查看本次车辆安排？此操作只记录核对结果，不代表车辆已出车。'
        : '确认已查看本次备件计划？此操作只记录核对结果，不会扣减库存。',
      confirmText: '确认核对',
      success: result => {
        if (!result.confirm) return;
        const snapshot = this._beginDepartureAction(actionKey);
        if (!snapshot) return;
        const payload = isVehicle ? { vehicle_confirmed: true } : { parts_confirmed: true };
        api.confirmDepartureResources(snapshot.target.executionPlanId, payload).then(res => {
          if (!this._departureActionCurrent(snapshot)) return;
          const confirmation = res.confirmation;
          if (!confirmation) throw new Error('核对结果缺失，请重试');
          const updated = decoratePackageResources(Object.assign({}, this.data.currentPackage, {
            departure_confirmation: confirmation,
          }));
          const updatedVm = buildDeparturePreparation(updated, snapshot.target);
          const packages = (this.data.packages || []).map(item =>
            Number(item.plan_id) === Number(snapshot.target.executionPlanId) ? updated : item
          );
          this.setData({
            packages,
            currentPackage: updated,
            departureVm: updatedVm,
            ['departureSubmitting.' + actionKey]: false,
          });
          wx.showToast({ title: isVehicle ? '车辆安排已核对' : '备件计划已核对', icon: 'success' });
        }).catch(err => {
          if (!this._finishDepartureAction(snapshot)) return;
          wx.showToast({
            title: (err && (err.error || err.message)) || '核对记录失败，请重试',
            icon: 'none',
          });
        });
      },
    });
  },

  onDepartureVehicleAction(e) {
    const action = (e && e.currentTarget && e.currentTarget.dataset.action)
      || (this.data.departureVm && this.data.departureVm.vehicle
        && this.data.departureVm.vehicle.primaryAction);
    if (action === 'retry') {
      this.onDepartureRefresh();
      return;
    }
    const actionKey = {
      request_resource: 'reworkRequest',
      checkout: 'vehicleCheckout',
      extend: 'extend',
    }[action];
    if (!actionKey) {
      wx.showToast({ title: '车辆状态已变化，请刷新后重试', icon: 'none' });
      return;
    }
    if (this._showDepartureActionBlocked(actionKey)) return;
    if (action === 'request_resource') {
      this.onOpenReworkResource();
    } else if (action === 'checkout') {
      this.onOpenVehicleCheckout();
    } else if (action === 'extend') {
      this.onOpenVehicleCheckout();
    }
  },

  onDepartureTodoTap(e) {
    const action = e && e.currentTarget && e.currentTarget.dataset.action;
    if (!action || action === 'none') return;
    if (action === 'checkout' || action === 'extend') {
      this.onDepartureVehicleAction({ currentTarget: { dataset: { action } } });
    } else if (action === 'request_resource') {
      this.onOpenReworkResource();
    } else if (action === 'issue') {
      this.onDepartureIssueParts();
    } else if (action === 'ack') {
      this.onDepartureToggleAck({ currentTarget: { dataset: { type: 'parts' } } });
    } else if (action === 'verify') {
      // 已出车状态下的查看：打开车辆详情或直接提示
      wx.showToast({ title: '车辆已出车，可在车辆记录中查看', icon: 'none' });
    }
  },

  onDepartureIssueParts() {
    if (!this._departureCanWrite('partsIssue')) return;
    this.onOpenPartsIssue();
  },

  onDepartureContinueToArrival() {
    const vm = this.data.departureVm;
    if (this.data.departureState !== 'ready' || !vm || !vm.canContinueToArrival
        || this.data.departureEnteringSite || this._departureHasPendingActions()) return;
    const target = this._departureTargetSnapshot();
    if (!target || !target.executionPlanId || !target.siteId) return;
    const pkgId = target.executionPlanId;
    const siteId = target.siteId;
    const requestId = (this._departureArrivalRequestId || 0) + 1;
    this._departureArrivalRequestId = requestId;
    const epoch = this._departureEpoch || 0;
    this.setData({ departureEnteringSite: true });
    let freshPackages = null;
    let freshPkg = null;
    api.todayExecution().then(res => {
      if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureArrivalRequestId)) return null;
      freshPackages = (res.packages || []).map(decoratePackageResources);
      freshPkg = freshPackages.find(item => Number(item.plan_id) === Number(pkgId));
      const targetSite = freshPkg && (freshPkg.sites || [])
        .find(site => Number(site.site_id) === Number(siteId));
      const targetMatches = freshPkg
        && (!target.scheduleId || Number(freshPkg.schedule_id) === Number(target.scheduleId))
        && (!target.workDate || String(freshPkg.work_date || '') === String(target.workDate));
      if (!targetMatches || !targetSite) {
        this.setData({
          departureState: 'target_unavailable',
          departureRefreshError: '目标执行包或站点已不可用',
          departureEnteringSite: false,
        });
        return null;
      }
      const freshVm = buildDeparturePreparation(freshPkg, target);
      if (!freshVm.canContinueToArrival) {
        this._rawExecutionPackages = freshPackages;
        this.setData({
          packages: freshPackages,
          currentPackage: freshPkg,
          departureVm: freshVm,
          departureState: 'ready',
          departureRefreshError: '',
          departureEnteringSite: false,
        });
        wx.showToast({ title: freshVm.gateMessage || '当前尚不能进入到站阶段', icon: 'none' });
        return null;
      }
      return api.executionSiteTasks(pkgId, siteId, { reworkOnly: !!target.reworkOnly });
    }).then(res => {
      if (!res || !this._departureAsyncCurrent(
        target, epoch, requestId, this._departureArrivalRequestId
      )) return;
      const exactGate = res.arrival_gate;
      if (!exactGate || exactGate.allowed !== true) {
        freshPkg = Object.assign({}, freshPkg, {
          arrival_gate: exactGate || {
            allowed: false,
            code: 'ARRIVAL_RESOURCE_STATE_UNAVAILABLE',
            message: '车辆履约状态暂不可用，请刷新后重试',
          },
        });
        freshPackages = freshPackages.map(item =>
          Number(item.plan_id) === Number(pkgId) ? freshPkg : item
        );
        const blockedVm = buildDeparturePreparation(freshPkg, target);
        this._rawExecutionPackages = freshPackages;
        this.setData({
          packages: freshPackages,
          currentPackage: freshPkg,
          departureVm: blockedVm,
          departureState: 'ready',
          departureRefreshError: '',
          departureEnteringSite: false,
        });
        wx.showToast({ title: blockedVm.gateMessage || '当前尚不能进入到站阶段', icon: 'none' });
        return;
      }
      const sites = (freshPkg.sites || []).map(site => Object.assign({}, site, { id: site.site_id }));
      if (!executionTarget.itemBelongsToCategories(res.categories, target.itemId)) {
        this.setData({
          departureState: 'target_unavailable',
          departureRefreshError: '目标检查项已不可用',
          departureEnteringSite: false,
        });
        return;
      }
      this._rawExecutionPackages = freshPackages;
      this.setData({ departureEnteringSite: false });
      this.setData({
        viewPhase: 'inspection',
        selectedPlanId: pkgId,
        selSiteId: siteId,
        packages: freshPackages,
        currentPackage: freshPkg,
        sites,
      });
      this.loadTasks(siteId, null, res);
    }).catch(err => {
      if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureArrivalRequestId)) return;
      const msg = (err && (err.error || err.message)) || '站点详情加载失败，请重试';
      const preservedVm = Object.assign({}, this.data.departureVm, { refreshError: msg });
      if (preservedVm) {
        this.setData({
          departureVm: preservedVm,
          departureState: 'refresh_error',
          departureRefreshError: msg,
          departureEnteringSite: false,
        });
      }
    });
  },

  onEntryRetry() {
    const hasPrevious = (this.data.entryPackages || []).length > 0;
    const browseClosed = !!this.data.entryBrowseClosed;
    this.setData(hasPrevious
      ? { entryError: '', entryRefreshError: '', entryBrowseClosed: browseClosed }
      : { entryState: 'initial_loading', entryError: '', entryRefreshError: '', entryBrowseClosed: browseClosed });
    this.loadExecution();
  },

  onViewClosedResults() {
    const packages = this._allEntryPackages || this.data.entryPackages || [];
    if (!packages.length) return;
    this._invalidateEntryRequest();
    this._applyBrowseClosedView(packages);
  },

  onEntryViewCompletedPackage(e) {
    const packageId = Number(e && e.currentTarget && e.currentTarget.dataset.id);
    const pkg = (this.data.entryCompletedPackages || []).find(item => Number(item.executionPlanId) === packageId);
    if (!pkg) {
      wx.showToast({ title: '已闭环执行包已不可用，请刷新后重试', icon: 'none' });
      return;
    }
    this._entryTarget = executionTarget.normalizeExecutionTarget({
      executionPlanId: pkg.executionPlanId,
      scheduleId: pkg.scheduleId,
      workDate: pkg.workDate,
      source: 'completed_result',
    });
    this._invalidateEntryRequest();
    this._applyBrowseClosedView(this._allEntryPackages || this.data.entryPackages || []);
  },

  _applyBrowseClosedView(packages, done) {
    const list = executionTarget.closedResultPackages(packages, this._entryTarget);
    if (!list.length) {
      this.setData({
        entryState: executionTarget.hasExactTarget(this._entryTarget) ? 'target_unavailable' : 'closed_only',
        entryPackages: [],
        selectedEntryPackageId: null,
        selectedEntrySiteId: null,
        entrySites: [],
        entryCanEnter: false,
        entryTargetMessage: executionTarget.hasExactTarget(this._entryTarget)
          ? '目标闭环结果已不可用，请重新选择'
          : '当前没有可查看的闭环结果',
        entryBrowseClosed: true,
        entryRefreshError: '',
        loaded: true,
      });
      if (done) done();
      return;
    }
    if (list.length === 1) {
      const pkg = list[0];
      const closedSites = pkg.sites || [];
      this.setData({
        entryState: 'selecting',
        entryPackages: list,
        selectedEntryPackageId: pkg.executionPlanId,
        selectedEntrySiteId: closedSites.length === 1 ? closedSites[0].siteId : null,
        entrySites: pkg.sites,
        entryCanEnter: closedSites.length === 1,
        entryTargetMessage: '正在查看已闭环站点结果',
        entryBrowseClosed: true,
        entryRefreshError: '',
        loaded: true,
      });
    } else {
      this.setData({
        entryState: 'selecting',
        entryPackages: list,
        selectedEntryPackageId: null,
        selectedEntrySiteId: null,
        entrySites: [],
        entryCanEnter: false,
        entryTargetMessage: '请选择要查看的执行包',
        entryBrowseClosed: true,
        entryRefreshError: '',
        loaded: true,
      });
    }
    if (done) done();
  },

  onEntryReselect() {
    this._invalidateEntryRequest();
    if (this._entryTarget && this._entryTarget.reworkOnly) {
      wx.switchTab({
        url: '/pages/index/index',
        fail: () => this.setData({ entryTargetMessage: '首页打开失败，请重试' }),
      });
      return;
    }
    this._entryTarget = executionTarget.normalizeExecutionTarget({ source: 'manual_reselect' });
    const resolved = executionTarget.resolveExecutionTarget(this._allEntryPackages || [], this._entryTarget);
    const selectedPkg = resolved.selectedPackageId
      ? resolved.packages.find(pkg => Number(pkg.executionPlanId) === Number(resolved.selectedPackageId))
      : null;
    this.setData({
      entryState: resolved.state,
      entryPackages: resolved.packages,
      selectedEntryPackageId: resolved.selectedPackageId,
      selectedEntrySiteId: resolved.selectedSiteId,
      entryCanEnter: resolved.canEnter,
      entryTargetMessage: resolved.targetMessage,
      entrySites: selectedPkg ? selectedPkg.sites : [],
      entryBrowseClosed: false,
    });
  },

  onEntryBack() {
    wx.switchTab({ url: '/pages/plan/plan' });
  },

  onSelectPackage(e) {
    const planId = e.currentTarget.dataset.id;
    const currentPackage = (this.data.packages || []).find(p => p.plan_id === planId);
    if (!currentPackage) return;
    const sites = (currentPackage.sites || []).map(s => Object.assign({}, s, { id: s.site_id }));
    const selSiteId = sites[0] && sites[0].id;
    this.setData({ currentPackage, selectedPlanId: planId, sites, selSiteId, categories: [], total: 0, completed: 0,
      completionPercent: 0, progressFillStyle: 'width:0%', abnormalCount: 0, photoProgress: { req: 0, taken: 0, missing: 0 }, stationStage: null,
      reagents: [], reagentAction: '加载中…' });
    if (selSiteId) this.loadTasks(selSiteId);
  },

  onOpenPartsIssue() {
    const pkg = this.data.currentPackage;
    const vm = this.data.departureVm;
    if (!this._departureCanWrite('partsIssue') || !pkg || !vm || vm.mode === 'carryover'
        || !vm.partsSummary.canIssue) return;
    if (vm.partsAcknowledgement.status !== 'confirmed') {
      wx.showToast({ title: '请先核对备件计划', icon: 'none' });
      return;
    }
    const items = (pkg.resource_parts || []).filter(part =>
      Number.isFinite(part.remaining_quantity) && part.remaining_quantity > 0
    ).map(part =>
      Object.assign({}, part, { issue_quantity: String(part.remaining_quantity) })
    );
    this._partsIssueDepartureTarget = this._departureTargetSnapshot();
    this.setData({ partsIssueSheet: { open: true, items, submitting: false } });
  },

  onClosePartsIssue() {
    if (!this.data.partsIssueSheet.submitting) {
      this._partsIssueDepartureTarget = null;
      this.setData({ 'partsIssueSheet.open': false });
    }
  },

  onPartsIssueQuantity(e) {
    this.setData({ ['partsIssueSheet.items[' + e.currentTarget.dataset.index + '].issue_quantity']: e.detail.value });
  },

  onSubmitPartsIssue() {
    const pkg = this.data.currentPackage;
    const sheet = this.data.partsIssueSheet;
    if (!pkg || sheet.submitting || !this._departureCanWrite('partsIssue')
        || !this._departureTargetMatches(this._partsIssueDepartureTarget)) return;
    const items = [];
    for (const part of sheet.items || []) {
      const quantity = Number(part.issue_quantity || 0);
      if (!Number.isInteger(quantity) || quantity < 0
          || !Number.isFinite(part.remaining_quantity) || quantity > part.remaining_quantity) {
        wx.showToast({ title: part.part_name + '领用数量不正确', icon: 'none' });
        return;
      }
      if (quantity > 0) {
        items.push({ part_id: part.part_id, quantity });
      }
    }
    if (!items.length) {
      wx.showToast({ title: '请填写本次实际领用数量', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '确认现场领用',
      content: '本次确认 ' + items.length + ' 项备件，提交后将立即扣减库存且不能在此撤销。',
      confirmText: '确认扣库',
      success: result => {
        if (!result.confirm) return;
        if (!this._departureTargetMatches(this._partsIssueDepartureTarget)) return;
        const snapshot = this._beginDepartureAction('partsIssue');
        if (!snapshot) return;
        this.setData({ 'partsIssueSheet.submitting': true });
        api.issueExecutionParts(snapshot.target.executionPlanId, items).then(res => {
          if (!this._departureActionCurrent(snapshot)) return;
          const updated = decoratePackageResources(Object.assign({}, this.data.currentPackage, {
            resource_parts: res.resource_parts || [],
          }));
          const packages = (this.data.packages || []).map(item =>
            Number(item.plan_id) === Number(snapshot.target.executionPlanId) ? updated : item
          );
          this._partsIssueDepartureTarget = null;
          this.setData({
            packages,
            currentPackage: updated,
            departureVm: buildDeparturePreparation(updated, snapshot.target),
            'departureSubmitting.partsIssue': false,
            partsIssueSheet: { open: false, items: [], submitting: false },
          });
          wx.showToast({ title: '已领用并扣库', icon: 'success' });
        }).catch(err => {
          if (!this._finishDepartureAction(snapshot)) return;
          this.setData({ 'partsIssueSheet.submitting': false });
          wx.showToast({ title: (err && (err.error || err.message)) || '领用失败，请重试', icon: 'none' });
        });
      }
    });
  },

  onOpenVehicleCheckout() {
    const pkg = this.data.currentPackage;
    const vm = this.data.departureVm;
    if (!pkg || !pkg.vehicle || !vm) {
      wx.showToast({ title: '车辆安排尚未加载完成，请刷新后重试', icon: 'none' });
      return;
    }
    if (!pkg.vehicle_application_id) { wx.showToast({ title: '未找到本计划获批的用车安排', icon: 'none' }); return; }
    if (pkg.vehicle_needs_extension) {
      if (this._showDepartureActionBlocked('extend')) return;
      wx.showModal({
        title: '用车安排已超期',
        content: '结转巡检尚未完成，请先到“我的用车”延续本次用车截止日期。',
        confirmText: '立即延续',
        success: result => {
          if (!result.confirm) return;
          const snapshot = this._beginDepartureAction('extend');
          if (!snapshot) return;
          const applicationId = Number(pkg.vehicle_application_id);
          if (!Number.isInteger(applicationId) || applicationId <= 0) {
            this._finishDepartureAction(snapshot);
            wx.showToast({ title: '用车申请编号无效，请刷新后重试', icon: 'none' });
            return;
          }
          const vehicleTarget = {
            applicationId,
            expectedAction: 'extend',
            source: 'inspection_departure',
            executionPlanId: Number(snapshot.target.executionPlanId) || null,
            siteId: Number(snapshot.target.siteId) || null,
          };
          app.globalData.vehicleTarget = vehicleTarget;
          wx.navigateTo({
            url: '/pages/vehicle/vehicle',
            fail: () => {
              if (app.globalData.vehicleTarget === vehicleTarget) app.globalData.vehicleTarget = null;
              if (!this._finishDepartureAction(snapshot)) return;
              wx.showToast({ title: '车辆页面打开失败，请重试', icon: 'none' });
            },
          });
        }
      });
      return;
    }
    if (this._showDepartureActionBlocked('vehicleCheckout')) return;
    if (pkg.vehicle_use && pkg.vehicle_use.returned_at) { wx.showToast({ title: '本计划车辆已完成还车', icon: 'none' }); return; }
    if (pkg.vehicle_use) {
      wx.showToast({ title: '车辆已完成出车登记', icon: 'none' });
      return;
    }
    const target = this._departureTargetSnapshot();
    const epoch = this._departureEpoch || 0;
    const requestId = (this._departureOpenRequestId || 0) + 1;
    this._departureOpenRequestId = requestId;
    const mileage = String(pkg.vehicle.current_mileage || '');
    this.setData({ departureVehicleTemplateLoading: true });
    const openSheet = items => {
      if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureOpenRequestId)) return;
      this._vehicleSheetDepartureTarget = target;
      this.setData({
        departureVehicleTemplateLoading: false,
        vehicleSheet: {
          open: true, mode: 'dispatch', mileage, remarks: '', items, submitting: false,
        },
      });
    };
    const timeoutMs = Number(this._vehicleTemplateTimeoutMs) > 0
      ? Number(this._vehicleTemplateTimeoutMs) : 15000;
    withTimeout(
      Promise.resolve().then(() => api.vehicleInspectionTemplate()),
      timeoutMs,
      '车辆检查项加载超时，请重试'
    )
      .then(items => openSheet(items || []))
      .catch(err => {
        if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureOpenRequestId)) return;
        const reason = (err && (err.error || err.message)) || '车辆检查项加载失败，请重试';
        this.setData({ departureVehicleTemplateLoading: false });
        wx.showModal({
          title: '车辆检查加载失败',
          content: reason,
          confirmText: '重试',
          cancelText: '暂不处理',
          success: result => {
            if (result.confirm) this.onOpenVehicleCheckout();
          },
        });
      });
  },
  onCloseVehicleCheckout() {
    if (this.data.vehicleSheet.submitting) return;
    this._vehicleSheetDepartureTarget = null;
    this.setData({ 'vehicleSheet.open': false });
  },
  onVehicleMaskTap() {
    const sheet = this.data.vehicleSheet;
    if (!sheet || sheet.submitting) return;
    const hasInput = (sheet.mileage && sheet.mileage !== '')
      || (sheet.remarks && sheet.remarks.trim() !== '')
      || (sheet.items || []).some(item => item.status && item.status !== 'normal');
    if (hasInput) {
      wx.showModal({
        title: '返回出发准备？',
        content: '已填写的检查内容将不会保存。',
        confirmText: '返回',
        cancelText: '继续检查',
        success: (res) => {
          if (res.confirm) this.onCloseVehicleCheckout();
        },
      });
    } else {
      this.onCloseVehicleCheckout();
    }
  },
  onVehicleMileage(e) {
    this.setData({
      'vehicleSheet.mileage': e.detail.value,
      'vehicleSheet.mileageError': '',
    });
  },
  onVehicleRemark(e) {
    this.setData({
      'vehicleSheet.remarks': e.detail.value,
      'vehicleSheet.remarksError': '',
    });
  },
  onVehicleItemStatus(e) {
    const { index, status } = e.currentTarget.dataset;
    this.setData({ ['vehicleSheet.items[' + index + '].status']: status });
  },
  onOpenRefuel() {
    const pkg = this.data.currentPackage;
    if (!pkg || !pkg.vehicle_use || pkg.vehicle_use.returned_at) return;
    const electric = pkg.vehicle && pkg.vehicle.fuel_type === 'electric';
    this.setData({ refuelSheet: { open: true, quantity: '', amount: '', mileage: String(pkg.vehicle_use.start_mileage || ''), remark: '', label: electric ? '充电' : '加油', unit: electric ? 'kWh' : 'L', submitting: false } });
  },
  onCloseRefuel() { this.setData({ 'refuelSheet.open': false }); },
  onRefuelField(e) { this.setData({ ['refuelSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitRefuel() {
    const pkg = this.data.currentPackage; const sheet = this.data.refuelSheet;
    if (!pkg || !pkg.vehicle_use || !(Number(sheet.quantity) > 0) || !(Number(sheet.mileage) >= 0)) { wx.showToast({ title: '请填写补给量和当前里程', icon: 'none' }); return; }
    this.setData({ 'refuelSheet.submitting': true });
    api.refuelVehicleUse(pkg.vehicle_use.id, { energy_quantity: Number(sheet.quantity), amount: sheet.amount === '' ? null : Number(sheet.amount), mileage_at: Number(sheet.mileage), remark: sheet.remark })
      .then(() => { this.setData({ 'refuelSheet.open': false }); wx.showToast({ title: sheet.label + '记录已保存', icon: 'success' }); })
      .catch(() => { this.setData({ 'refuelSheet.submitting': false }); wx.showToast({ title: sheet.label + '记录保存失败', icon: 'none' }); });
  },
  onOpenVehicleFault() {
    const pkg = this.data.currentPackage;
    if (!pkg || !pkg.vehicle_use || pkg.vehicle_use.returned_at) return;
    this.setData({ vehicleFaultSheet: { open: true, faultType: '车辆故障', mileage: String(pkg.vehicle_use.start_mileage || ''), description: '', remark: '', submitting: false } });
  },
  onCloseVehicleFault() { this.setData({ 'vehicleFaultSheet.open': false }); },
  onVehicleFaultField(e) { this.setData({ ['vehicleFaultSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitVehicleFault() {
    const pkg = this.data.currentPackage; const sheet = this.data.vehicleFaultSheet;
    if (!pkg || !pkg.vehicle_use || !sheet.description.trim()) { wx.showToast({ title: '请填写故障现象', icon: 'none' }); return; }
    this.setData({ 'vehicleFaultSheet.submitting': true });
    api.reportVehicleFault(pkg.vehicle_use.id, { fault_type: sheet.faultType, mileage_at: sheet.mileage === '' ? null : Number(sheet.mileage), description: sheet.description.trim(), remark: sheet.remark })
      .then(() => { this.setData({ 'vehicleFaultSheet.open': false }); wx.showModal({ title: '故障已上报', content: '车辆已限制使用并进入待维修，请尽快安全还车。', showCancel: false }); this.loadExecution(); })
      .catch(() => { this.setData({ 'vehicleFaultSheet.submitting': false }); wx.showToast({ title: '故障上报失败', icon: 'none' }); });
  },
  onSubmitVehicleCheckout() {
    const pkg = this.data.currentPackage; const sheet = this.data.vehicleSheet;
    const mileage = Number(sheet.mileage);
    const isReturn = sheet.mode === 'return';
    if (!pkg || !pkg.vehicle || sheet.submitting || isReturn
        || !this._departureCanWrite('vehicleCheckout')
        || !this._departureTargetMatches(this._vehicleSheetDepartureTarget)) return;

    let mileageError = '';
    let remarksError = '';
    if (!Number.isFinite(mileage) || mileage < 0) {
      mileageError = '请填写出车时里程（公里数）';
    }
    const hasAttention = (sheet.items || []).some(item => item.status === 'attention');
    const hasBlocked = (sheet.items || []).some(item => item.status === 'blocked');
    const overallStatus = hasBlocked ? 'blocked' : (hasAttention ? 'attention' : 'normal');
    if (overallStatus !== 'normal' && !(sheet.remarks || '').trim()) {
      remarksError = '发现异常时请填写现场说明';
    }
    if (mileageError || remarksError) {
      this.setData({
        'vehicleSheet.mileageError': mileageError,
        'vehicleSheet.remarksError': remarksError,
      });
      if (mileageError) wx.showToast({ title: mileageError, icon: 'none' });
      else if (remarksError) wx.showToast({ title: remarksError, icon: 'none' });
      return;
    }
    // 清除错误状态
    if (sheet.mileageError || sheet.remarksError) {
      this.setData({
        'vehicleSheet.mileageError': '',
        'vehicleSheet.remarksError': '',
      });
    }
    const snapshot = this._beginDepartureAction('vehicleCheckout');
    if (!snapshot) return;
    const vehicleId = pkg.vehicle.id;
    const applicationId = pkg.vehicle_application_id;
    this.setData({ 'vehicleSheet.submitting': true });
    api.submitVehicleInspection({ vehicle_id: vehicleId, inspection_type: 'dispatch', odometer: mileage, overall_status: overallStatus, items: sheet.items, remarks: sheet.remarks })
      .then(check => {
        return api.checkOutVehicle({ application_id: applicationId, start_mileage: mileage, out_inspection_id: check.id });
      })
      .then(() => {
        if (!this._departureActionCurrent(snapshot)) return;
        this._vehicleSheetDepartureTarget = null;
        this.setData({
          'vehicleSheet.open': false,
          'vehicleSheet.submitting': false,
          'departureSubmitting.vehicleCheckout': false,
        });
        wx.showToast({ title: '已完成出车登记', icon: 'success' });
        this.onDepartureRefresh();
      })
      .catch(err => {
        if (!this._finishDepartureAction(snapshot)) return;
        this.setData({ 'vehicleSheet.submitting': false });
        wx.showToast({
          title: (err && (err.error || err.message)) || '出车登记失败，请核对车辆状态',
          icon: 'none',
        });
      });
  },

  onOpenPartsApply() {
    const site = this.data.selSite;
    if (!site) { wx.showToast({ title: '请先选择站点', icon: 'none' }); return; }
    const openSheet = () => this.setData({ partsApply: {
      open: true, fulfillmentIndex: 0, fulfillment_type: 'stock',
      part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false,
      requestKey: 'parts_inspection_' + (site.id || site.site_id) + '_' + Date.now()
    } });
    openSheet();
    if (this.data.partsInventoryStatus === 'idle' || this.data.partsInventoryStatus === 'error') this.loadPartsInventory();
  },

  loadPartsInventory() {
    if (this.data.partsInventoryStatus === 'loading') return;
    const requestId = (this._partsInventoryRequest || 0) + 1;
    this._partsInventoryRequest = requestId;
    this.setData({ partsInventoryStatus: 'loading', partsInventoryError: '' });
    api.partsInventory().then(parts => {
      if (this._partsInventoryRequest !== requestId) return;
      const partsOptions = inventoryOptions(parts);
      this.setData({ partsOptions, partsInventoryStatus: partsOptions.length ? 'ready' : 'empty' });
    }).catch(error => {
      if (this._partsInventoryRequest !== requestId) return;
      this.setData({ partsInventoryStatus: 'error', partsInventoryError: inventoryErrorMessage(error) });
    });
  },

  onRetryPartsInventory() { this.loadPartsInventory(); },

  onClosePartsApply() {
    if (this.data.partsApply.submitting) { wx.showToast({ title: '提交中，请稍候', icon: 'none' }); return; }
    this.setData({ 'partsApply.open': false });
  },
  onPartsFulfillmentPick(e) {
    if (this.data.partsApply.submitting) return;
    const index = parseInt(e.detail.value, 10) || 0;
    const selected = this.data.partsFulfillmentOptions[index] || this.data.partsFulfillmentOptions[0];
    const previousType = this.data.partsApply.fulfillment_type;
    const resetManualFields = selected.key === 'stock' || previousType === 'stock';
    this.setData({ 'partsApply.fulfillmentIndex': index, 'partsApply.fulfillment_type': selected.key,
      'partsApply.index': 0,
      'partsApply.part_name': resetManualFields ? '' : this.data.partsApply.part_name,
      'partsApply.specification': resetManualFields ? '' : this.data.partsApply.specification,
      'partsApply.estimated_amount': resetManualFields ? '' : this.data.partsApply.estimated_amount });
  },
  onPartsFulfillmentSelect(e) {
    this.onPartsFulfillmentPick({ detail: { value: e.currentTarget.dataset.index } });
  },
  onPartsPick(e) {
    const index = parseInt(e.detail.value, 10) || 0;
    const option = this.data.partsOptions[index];
    const patch = { 'partsApply.index': index };
    if (option && option.id) patch['partsApply.part_name'] = option.part_name || '';
    this.setData(patch);
  },
  onPartsName(e) { this.setData({ 'partsApply.part_name': e.detail.value }); },
  onPartsSpecification(e) { this.setData({ 'partsApply.specification': e.detail.value }); },
  onPartsEstimatedAmount(e) { this.setData({ 'partsApply.estimated_amount': e.detail.value }); },
  onPartsQty(e) { this.setData({ 'partsApply.quantity': e.detail.value }); },
  onPartsReason(e) { this.setData({ 'partsApply.reason': e.detail.value }); },
  onSubmitPartsApply() {
    const site = this.data.selSite;
    const form = this.data.partsApply;
    if (form.submitting) return;
    if (!site) return;
    const result = buildPartsPayload(form, this.data.partsOptions, this.data.partsInventoryStatus);
    if (result.error) { wx.showToast({ title: result.error, icon: 'none' }); return; }
    this.setData({ 'partsApply.submitting': true });
    api.applyParts(Object.assign({ site_id: site.id || site.site_id, _idempotency_key: form.requestKey }, result.payload))
      .then(() => { this.setData({ 'partsApply.open': false, 'partsApply.submitting': false }); wx.showToast({ title: '已提交审批', icon: 'success' }); })
      .catch(err => { this.setData({ 'partsApply.submitting': false }); wx.showToast({ title: (err && err.error) || '提交失败', icon: 'none' }); });
  },

  loadTasks(siteId, done, verifiedResponse) {
    const planId = this.data.selectedPlanId;
    if (!planId) { if (done) done(); return; }
    const requestId = (this._tasksRequestId || 0) + 1;
    this._tasksRequestId = requestId;
    const request = verifiedResponse ? Promise.resolve(verifiedResponse) : api.executionSiteTasks(planId, siteId, {
      reworkOnly: !!(this.data.departureTarget && this.data.departureTarget.reworkOnly),
    });
    request
      .then(res => {
        if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._tasksRequestId,
          Number(this.data.selectedPlanId) === Number(planId) && Number(this.data.selSiteId) === Number(siteId))) { if (done) done(); return; }
        const packageSite = ((this.data.currentPackage && this.data.currentPackage.sites) || []).find(s => s.site_id === siteId) || {};
        const selectedSite = Object.assign({}, packageSite, res.site || {}, {
          linked_workorders: (packageSite.linked_workorders || []).map(linkedWorkorderCn)
        });
        const photosMap = {};
        (res.categories || []).forEach(cat => (cat.items || []).forEach(it => {
          let arr = [];
          try { arr = it.photo_urls ? JSON.parse(it.photo_urls) : []; } catch (e) { arr = []; }
          photosMap[it.item_id] = arr;
        }));
        // 巡检结果枚举集中映射（§6.8：禁止 wxml 硬编码中文枚举）
        const focusedItemId = (this._entryTarget && this._entryTarget.itemId) || this.data.focusedItemId;
        const decorated = (res.categories || []).map(cat => ({
          ...cat,
          // 接口给出的业务展示名优先；旧接口或新增分类则保留原有名称，不能笼统显示“未分类”。
          category_cn: cat.category_cn || map(INSPECTION_CATEGORY, cat.category, cat.category || '其他检查'),
          items: (cat.items || []).map(it => {
            const pendingSubmit = localStore.getPendingSubmit(it.item_id, it.plan_id);
            const merged = pendingSubmit ? Object.assign({}, it, {
              result: pendingSubmit.data.result,
              sync_pending: true,
            }) : Object.assign({}, it, { sync_pending: false });
            const status = inspectionItemStatus(merged, merged.sync_pending);
            return Object.assign({}, merged, { result_cn: status.label, status_code: status.code, focused: Number(merged.item_id) === Number(focusedItemId) });
          })
        }));
        const fieldProgress = projectInspectionFieldProgress(decorated);
        if (!executionTarget.itemBelongsToCategories(decorated, focusedItemId)) {
          this._entryTarget = Object.assign({}, this._entryTarget, { itemId: null });
          wx.showToast({ title: '目标检查项已不可用', icon: 'none' });
        }
        const abnormalCount = decorated.reduce((count, cat) => count + (cat.items || [])
          .filter(item => item.result === 'abnormal').length, 0);
        this.setData({
          site: selectedSite,
          selSite: selectedSite,
          categories: fieldProgress.categories,
          focusedItemId: executionTarget.itemBelongsToCategories(decorated, focusedItemId) ? (focusedItemId || null) : null,
          total: fieldProgress.total,
          completed: fieldProgress.completed,
          completionPercent: fieldProgress.percent,
          progressFillStyle: `width:${fieldProgress.percent}%`,
          abnormalCount,
          loaded: true,
          photoProgress: inspectionPhotoProgress(res.categories)
        });
        this.refreshStationStage(siteId);
        this.loadReagents(siteId);
        if (done) done();
      })
      .catch(() => {
        if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._tasksRequestId,
          Number(this.data.selectedPlanId) === Number(planId) && Number(this.data.selSiteId) === Number(siteId))) { if (done) done(); return; }
        if (verifiedResponse) {
          this.setData({
            viewPhase: 'entry',
            entryEntering: false,
            entryCanEnter: true,
            entryTargetMessage: '站点详情处理失败，请重试',
            entryError: '站点详情处理失败，请重试',
          });
          if (done) done();
          return;
        }
        this.setData({ loaded: true }); if (done) done(); wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  loadReagents(siteId) {
    const planId = this.data.selectedPlanId;
    if (!planId || !siteId) return;
    const requestId = (this._reagentsRequestId || 0) + 1;
    this._reagentsRequestId = requestId;
    api.executionSiteReagents(planId, siteId)
      .then(res => {
        if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._reagentsRequestId,
          Number(this.data.selectedPlanId) === Number(planId) && Number(this.data.selSiteId) === Number(siteId))) return;
        const reagents = res.items || [];
        const pendingCalibration = reagents.some(item => item.qc_status === 'pending');
        this.setData({ reagents, reagentAction: pendingCalibration ? '开始标定 ›' : (reagents.length ? '登记更换 ›' : '暂无记录') });
      })
      .catch(() => {
        if (!executionTarget.shouldApplyAsyncResult(this._alive, requestId, this._reagentsRequestId,
          Number(this.data.selectedPlanId) === Number(planId) && Number(this.data.selSiteId) === Number(siteId))) return;
        this.setData({ reagents: [], reagentAction: '暂无记录' });
      });
  },

  onOpenReagentSheet() {
    if (!(this.data.reagents || []).length) {
      wx.showToast({ title: '本站暂无试剂库存记录', icon: 'none' });
      return;
    }
    const first = this.data.reagents[0];
    this.setData({ reagentSheet: {
      open: true, mode: first.qc_status === 'pending' ? 'qc' : 'replacement', index: 0,
      newQty: '', duration: first.expected_duration_days || '', standardValue: '', measuredValue: '',
      passed: true, failAction: 'calibrate', submitting: false
    } });
  },

  onCloseReagentSheet() { this.setData({ 'reagentSheet.open': false }); },
  onReagentPick(e) {
    const index = Number(e.detail.value) || 0;
    const item = this.data.reagents[index] || {};
    this.setData({ 'reagentSheet.index': index, 'reagentSheet.duration': item.expected_duration_days || '' });
  },
  onReagentMode(e) { this.setData({ 'reagentSheet.mode': e.currentTarget.dataset.mode }); },
  onReagentField(e) { this.setData({ ['reagentSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onReagentPass(e) { this.setData({ 'reagentSheet.passed': e.currentTarget.dataset.passed === 'true' }); },
  onReagentFailAction(e) { this.setData({ 'reagentSheet.failAction': e.currentTarget.dataset.action }); },

  onSubmitReagent() {
    const sheet = this.data.reagentSheet;
    const reagent = (this.data.reagents || [])[sheet.index];
    if (!reagent || sheet.submitting) return;
    const planId = this.data.selectedPlanId;
    const siteId = this.data.selSiteId;
    let request;
    if (sheet.mode === 'replacement') {
      if (sheet.newQty === '') { wx.showToast({ title: '请填写更换后余量', icon: 'none' }); return; }
      request = api.replaceExecutionReagent(planId, siteId, {
        reagent_id: reagent.reagent_id, new_qty: Number(sheet.newQty),
        expected_duration_days: sheet.duration
      });
    } else {
      if (sheet.standardValue === '' || sheet.measuredValue === '') { wx.showToast({ title: '请填写标样值和实测值', icon: 'none' }); return; }
      request = api.submitExecutionReagentQc(planId, siteId, {
        reagent_id: reagent.reagent_id, standard_value: Number(sheet.standardValue),
        measured_value: Number(sheet.measuredValue), passed: sheet.passed,
        fail_action: sheet.passed ? '' : sheet.failAction
      });
    }
    this.setData({ 'reagentSheet.submitting': true });
    request.then(res => {
      this.setData({ 'reagentSheet.open': false, 'reagentSheet.submitting': false });
      this.loadReagents(siteId);
      wx.showToast({ title: sheet.mode === 'replacement' ? '已更换，待标定' : (res.qc_status === 'passed' ? '标定通过' : '标定未通过'), icon: 'success' });
    }).catch(() => {
      this.setData({ 'reagentSheet.submitting': false });
      wx.showToast({ title: '提交失败，请重试', icon: 'none' });
    });
  },

  onSelectSite(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ selSiteId: id, stationStage: null, reagents: [], reagentAction: '加载中…',
      categories: [], total: 0, completed: 0, completionPercent: 0, progressFillStyle: 'width:0%', abnormalCount: 0,
      photoProgress: { req: 0, taken: 0, missing: 0 } });
    this.loadTasks(id);
  },

  onOpenReport() {
    if (!this.data.selSiteId) return;
    this.setData({ reportSheet: { open: true, typeIndex: 0, description: '', photos: [], submitting: false } });
  },
  onCloseReport() { this.setData({ 'reportSheet.open': false }); },
  onReportType(e) { this.setData({ 'reportSheet.typeIndex': Number(e.detail.value) || 0 }); },
  onReportDescription(e) { this.setData({ 'reportSheet.description': e.detail.value }); },
  onAddReportPhoto() {
    const current = this.data.reportSheet.photos || [];
    const remaining = 6 - current.length;
    if (remaining <= 0) { wx.showToast({ title: '最多上传 6 张照片', icon: 'none' }); return; }
    return runPhotoActionOnce(this, 'media_picker', () => chooseAndCompress(remaining)
      .then(paths => {
        const selectedPaths = validateReportPhotoPaths(paths);
        wx.showLoading({ title: '上传中', mask: true });
        return Promise.allSettled(selectedPaths.map((path, index) => {
          const idempotencyKey = photoIdempotencyKey(this.data.selSiteId, path, index);
          return fileToBase64(path).then(image => api.uploadSitePhoto(
            this.data.selSiteId, image, idempotencyKey, { _idempotency_key: idempotencyKey }
          ));
        }));
      }).then(results => {
        if (!Array.isArray(results)) return;
        const uploaded = results.filter(row => row.status === 'fulfilled' && row.value && row.value.url)
          .map(row => resolveUploadUrl(row.value.url));
        const photos = current.concat(uploaded.filter(url => current.indexOf(url) === -1));
        this.setData({ 'reportSheet.photos': photos });
        if (uploaded.length !== results.length) wx.showToast({ title: uploaded.length ? '部分照片上传失败' : '照片上传失败', icon: 'none' });
      }).catch(error => handlePhotoActionFailure(error, message => new Promise(resolve => {
        wx.hideLoading();
        wx.showModal({
          title: '照片处理未完成', content: message, showCancel: false,
          success: resolve, fail: resolve,
        });
      }))), title => wx.showLoading({ title, mask: true }), () => wx.hideLoading());
  },
  onPreviewReportPhoto(e) {
    const photos = this.data.reportSheet.photos || [];
    const current = e.currentTarget.dataset.url;
    if (current) wx.previewImage({ current, urls: photos });
  },
  onRemoveReportPhoto(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return;
    const storagePath = uploadStoragePath(url);
    if (!storagePath) {
      wx.showToast({ title: '照片地址无效，请重新上传', icon: 'none' });
      return;
    }
    return deletePendingReportPhotoOnce(
      this, url, storagePath,
      path => api.deletePendingSitePhoto(path),
      () => this.data.reportSheet.photos || []
    ).then(photos => {
      this.setData({ 'reportSheet.photos': photos });
      return { success: true };
    }).catch(err => {
      wx.showToast({ title: (err && err.error) || '照片删除失败，请重试', icon: 'none' });
      return { success: false, error: err };
    });
  },
  onSubmitReport() {
    const rs = this.data.reportSheet;
    const reportType = (REPORT_TYPES[rs.typeIndex] || REPORT_TYPES[0]).value;
    if (!rs.description.trim() || !rs.photos.length) { wx.showToast({ title: '请填写说明并拍摄现场照片', icon: 'none' }); return; }
    if (rs.submitting) return;
    const preparedPhotos = prepareReportPhotoStoragePaths(rs.photos);
    if (!preparedPhotos.ok) {
      const title = preparedPhotos.reason === 'invalid_count'
        ? '请上传 1 至 6 张现场照片'
        : '照片地址无效，请重新上传';
      wx.showToast({ title, icon: 'none' });
      return;
    }
    this.setData({ 'reportSheet.submitting': true });
    requestLocation().catch(() => null)
      .then(gps => api.submitManualReport({
        site_id: this.data.selSiteId,
        report_type: reportType,
        description: rs.description.trim(),
        photo_urls: preparedPhotos.paths,
        gps_lat: gps && gps.lat,
        gps_lng: gps && gps.lng,
      }))
      .then(res => {
        const orderNo = String(res && res.order_no || '').trim();
        if (!orderNo) throw { error: '工单编号未返回，请重试' };
        this.setData({ 'reportSheet.open': false, 'reportSheet.submitting': false });
        wx.showModal({ title: '异常已上报', content: `已生成工单：${orderNo}`, showCancel: false });
        this.loadTasks(this.data.selSiteId);
      })
      .catch(err => {
        this.setData({ 'reportSheet.submitting': false });
        wx.showModal({ title: '上报失败', content: (err && err.error) || '提交未完成，请检查网络后重试', showCancel: false });
      });
  },

  hasSiteCheckIn(siteId) {
    if (localStore.getSiteCheckIn(siteId)) return true;
    if (this.data.site && this.data.site.id === siteId && this.data.site.checked_in) return true;
    return (this.data.sites || []).some(s => s.id === siteId && s.checked_in);
  },

  refreshStationStage(siteId) {
    const checkin = localStore.getSiteCheckIn(siteId);
    const site = (this.data.site && this.data.site.id === siteId)
      ? this.data.site : (this.data.sites || []).find(item => item.id === siteId);
    const pendingItems = (this.data.categories || []).reduce((count, category) => count
      + (category.items || []).filter(item => item.sync_pending
        || localStore.getPendingSubmit(item.item_id, item.plan_id)).length, 0);
    const localCheckinPending = !!(checkin && checkin.syncStatus === 'pending');
    const stationSyncPending = localCheckinPending || pendingItems > 0;
    const stationStage = site && site.checked_out
      ? { code: 'checked_out', label: '已离站', cls: 'station-stage-ok' }
      : !checkin && !this.hasSiteCheckIn(siteId)
      ? { code: 'unvisited', label: '待到站', cls: 'station-stage-wait' }
      : stationSyncPending
        ? { code: 'local_pending', label: '已到站，待同步', cls: 'station-stage-pending' }
        : { code: 'checked_in', label: '已到站', cls: 'station-stage-ok' };
    this.setData({ stationStage, stationSyncPending, stationPendingCount: pendingItems + (localCheckinPending ? 1 : 0) });
  },

  onOpenReworkResource() {
    const pkg = this.data.currentPackage;
    if (!this._departureCanWrite('reworkRequest') || !pkg || !pkg.rework_resource
        || !pkg.rework_resource.canRequest) return;
    const target = this._departureTargetSnapshot();
    const epoch = this._departureEpoch || 0;
    const requestId = (this._departureOpenRequestId || 0) + 1;
    this._departureOpenRequestId = requestId;
    const openSheet = (mode, vehicles) => {
      if (!this._departureAsyncCurrent(target, epoch, requestId, this._departureOpenRequestId)) return;
      this._reworkSheetDepartureTarget = target;
      this.setData({ reworkResourceSheet: {
        open: true, mode, vehicles, vehicleIndex: 0,
        exceptionReason: '', submitting: false,
      } });
    };
    api.vehicles().then(vehicles => {
      openSheet('vehicle', vehicles || []);
    }).catch(() => {
      openSheet('no_vehicle', []);
    });
  },

  onCloseReworkResource() {
    if (!this.data.reworkResourceSheet.submitting) {
      this._reworkSheetDepartureTarget = null;
      this.setData({ 'reworkResourceSheet.open': false });
    }
  },

  onReworkResourceMode(e) { this.setData({ 'reworkResourceSheet.mode': e.currentTarget.dataset.mode }); },
  onReworkVehiclePick(e) { this.setData({ 'reworkResourceSheet.vehicleIndex': Number(e.detail.value) || 0 }); },
  onReworkExceptionReason(e) { this.setData({ 'reworkResourceSheet.exceptionReason': e.detail.value }); },

  onSubmitReworkResource() {
    const pkg = this.data.currentPackage;
    const sheet = this.data.reworkResourceSheet;
    if (!pkg || !pkg.rework_resource || sheet.submitting
        || !this._departureCanWrite('reworkRequest')
        || !this._departureTargetMatches(this._reworkSheetDepartureTarget)) return;
    const payload = sheet.mode === 'vehicle'
      ? { vehicle_id: (sheet.vehicles[sheet.vehicleIndex] || {}).id }
      : { no_vehicle_required: true, vehicle_exception_reason: (sheet.exceptionReason || '').trim() };
    if (sheet.mode === 'vehicle' && !payload.vehicle_id) {
      wx.showToast({ title: '请选择可用车辆', icon: 'none' });
      return;
    }
    if (sheet.mode === 'no_vehicle' && !payload.vehicle_exception_reason) {
      wx.showToast({ title: '请填写无车例外原因', icon: 'none' });
      return;
    }
    const snapshot = this._beginDepartureAction('reworkRequest');
    if (!snapshot) return;
    this.setData({ 'reworkResourceSheet.submitting': true });
    api.requestReworkResource(snapshot.target.executionPlanId, payload).then(() => {
      if (!this._departureActionCurrent(snapshot)) return;
      this._reworkSheetDepartureTarget = null;
      this.setData({
        'reworkResourceSheet.open': false,
        'reworkResourceSheet.submitting': false,
        'departureSubmitting.reworkRequest': false,
      });
      wx.showToast({ title: '整改资源申请已提交', icon: 'success' });
      this.onDepartureRefresh();
    }).catch(err => {
      if (!this._finishDepartureAction(snapshot)) return;
      this.setData({ 'reworkResourceSheet.submitting': false });
      wx.showToast({ title: (err && (err.error || err.message)) || '资源申请提交失败', icon: 'none' });
    });
  },

  onCheckOut() {
    const site = this.data.site;
    const planId = this.data.selectedPlanId;
    if (!site || !planId || this.data.checkingOut) return;
    if (this.data.completed < this.data.total) {
      wx.showToast({ title: '请先完成本站全部检查项', icon: 'none' });
      return;
    }
    if (this.data.stationSyncPending) {
      wx.showToast({ title: '本站仍有待同步记录，请先完成同步', icon: 'none' });
      return;
    }
    this.setData({ checkingOut: true });
    requestLocation().then(gps => api.checkOutExecutionSite(planId, site.id, {
      lat: gps.lat, lng: gps.lng
    })).then(res => {
      const sites = (this.data.sites || []).map(item => item.id === site.id
        ? Object.assign({}, item, { checked_out: true, check_out_time: res.check_out_time }) : item);
      const currentPackage = this.data.currentPackage ? Object.assign({}, this.data.currentPackage, { sites }) : this.data.currentPackage;
      const orderedIds = (currentPackage && Array.isArray(currentPackage.site_order) ? currentPackage.site_order : [])
        .map(Number).filter(Boolean);
      const routeSites = orderedIds.length
        ? orderedIds.map(id => sites.find(item => Number(item.id) === id || Number(item.site_id) === id)).filter(Boolean)
        : sites;
      const currentIdx = routeSites.findIndex(item => Number(item.id) === Number(site.id));
      const nextSite = routeSites.find((item, index) => index > currentIdx && !item.checked_out && item.phase !== 'closed');
      const stationOutcome = nextSite ? {
        type: 'next_site',
        siteId: nextSite.id || nextSite.site_id,
        siteName: nextSite.name || nextSite.site_name || '下一站',
      } : { type: 'return_plans' };
      this.setData({ sites, currentPackage, site: Object.assign({}, site, { checked_out: true }), selSite: Object.assign({}, this.data.selSite, { checked_out: true }), checkingOut: false }, () => {
        this.refreshStationStage(site.id);
      });
      this.setData({ stationOutcome });
      // Refresh server facts without leaving the stable checkout result view.
      this.loadExecution();
    }).catch(err => {
      this.setData({ checkingOut: false });
      wx.showModal({ title: '离站打卡失败', content: (err && (err.error || err.message)) || '请确认定位有效且仍在站点附近', showCancel: false });
    });
  },

  onGoToNextSite() {
    const outcome = this.data.stationOutcome;
    const nextSiteId = Number(outcome && outcome.siteId);
    if (!nextSiteId || !this.data.currentPackage) {
      wx.showToast({ title: '下一站信息已变化，请刷新后重试', icon: 'none' });
      return;
    }
    const nextSite = (this.data.sites || []).find(item => Number(item.id) === nextSiteId || Number(item.site_id) === nextSiteId);
    if (!nextSite || nextSite.checked_out || nextSite.phase === 'closed') {
      wx.showToast({ title: '下一站已变化，请刷新后重试', icon: 'none' });
      return;
    }
    this._entryTarget = executionTarget.normalizeExecutionTarget({
      scheduleId: this.data.currentPackage.schedule_id,
      executionPlanId: this.data.currentPackage.plan_id,
      workDate: this.data.currentPackage.work_date,
      siteId: nextSiteId,
      source: 'next_site',
    });
    this.setData({ selSiteId: nextSiteId, selSite: nextSite, site: nextSite,
      stationOutcome: null, stationStage: null, categories: [], total: 0, completed: 0,
      completionPercent: 0, progressFillStyle: 'width:0%', abnormalCount: 0,
      photoProgress: { req: 0, taken: 0, missing: 0 } });
    this.loadTasks(nextSiteId);
  },

  onBack() {
    if (this.data.sheet && this.data.sheet.open) {
      this.onCloseSheet();
      return;
    }
    if (this._backNavigating) {
      wx.showToast({ title: '正在返回计划，请稍候', icon: 'none' });
      return;
    }
    this._backNavigating = true;
    wx.switchTab({
      url: '/pages/plan/plan',
      success: () => { this._backNavigating = false; },
      fail: () => {
        this._backNavigating = false;
        wx.showToast({ title: '返回计划失败，请重试', icon: 'none' });
      },
    });
  },

  onCheckIn() {
    const site = this.data.site;
    const planId = this.data.selectedPlanId;
    if (!site || !planId) return;
    wx.showLoading({ title: '定位中' });
    requestLocation().then(gps => {
      wx.hideLoading();
      const payload = buildCheckinPayload(site, planId, gps, nowStr());
      // 本地先落库：断网/弱网也留存打卡态，联网后静默同步
      const opId = localStore.addOp('checkin', payload);
      if (site.rework_checkin_required) {
        this.setData({ site: Object.assign({}, site, { rework_checkin_required: false }),
          selSite: Object.assign({}, this.data.selSite, { rework_checkin_required: false }) });
      }
      this.refreshStationStage(site.id);
      api.trackEvent('inspection.checkin.queued', { site_id: site.id, operation_id: opId });
      api.checkIn(payload, true)
        .then(() => {
          localStore.markSynced(opId);
          const sites = (this.data.sites || []).map(item => item.id === site.id
            ? Object.assign({}, item, { checked_in: true }) : item);
          const checkedSite = Object.assign({}, this.data.site, { checked_in: true });
          this.setData({ sites, site: checkedSite, selSite: checkedSite, syncCount: pendingSyncCount() },
            () => this.refreshStationStage(site.id));
          this.loadTasks(site.id);
          wx.showToast({ title: '打卡成功', icon: 'success' });
        })
        .catch((error) => {
          if (!isTransientSyncError(error)) {
            localStore.removeOp(opId);
            if (site.rework_checkin_required) {
              this.setData({ site: Object.assign({}, site, { rework_checkin_required: true }),
                selSite: Object.assign({}, this.data.selSite, { rework_checkin_required: true }) });
            }
            this.refreshStationStage(site.id);
            this.setData({ syncCount: pendingSyncCount() });
            wx.showModal({ title: '打卡未完成', content: error.error || '服务器拒绝了本次打卡，请按提示处理', showCancel: false });
            return;
          }
          this.setData({ syncCount: pendingSyncCount() });
          wx.showToast({ title: '打卡已本地保存，联网同步', icon: 'none' });
        });
    }).catch(error => {
      wx.hideLoading();
      const openSettings = shouldOpenLocationSettings(error);
      wx.showModal({
        title: '无法获取位置', content: locationErrorMessage(error), showCancel: false,
        confirmText: openSettings ? '去设置' : '知道了',
        success: () => { if (openSettings) wx.openSetting({}); }
      });
    });
  },

  onLinkedWorkorder(e) {
    const orderNo = e.currentTarget.dataset.orderNo;
    if (!orderNo) return;
    app.globalData.selWorkorderNo = orderNo;
    wx.navigateTo({ url: '/pages/workorder/workorder' });
  },

  goSite() {
    if (this.data.selSiteId) wx.navigateTo({ url: '/pages/site/site?site_id=' + this.data.selSiteId + '&source=inspection_readonly' });
  },

  onOpenItem(e) {
    if (!this.hasSiteCheckIn(this.data.selSiteId)) {
      wx.showModal({
        title: '请先到站打卡',
        content: '完成到站打卡后才能填写检查项。现在去打卡？',
        confirmText: '去打卡',
        success: result => { if (result.confirm) this.onCheckIn(); },
      });
      return;
    }
    const id = e.currentTarget.dataset.id;
    let target = null;
    (this.data.categories || []).forEach(cat => (cat.items || []).forEach(it => { if (it.item_id === id) target = it; }));
    if (!target) return;
    const reviewStatus = Number(target.review_status || 0);
    const supplementRequired = target.evidence_status === 'supplement_required';
    if (target.result && reviewStatus === 2 && !supplementRequired) {
      wx.showToast({ title: '该检查项已通过审核，不能再次上传或提交', icon: 'none' });
      return;
    }
    if (target.sync_pending || localStore.getPendingSubmit(target.item_id, target.plan_id)) {
      wx.showToast({ title: '该检查项已本地保存，等待同步完成', icon: 'none' });
      return;
    }
    const rejectedSubmit = localStore.getRejectedSubmit(target.item_id, target.plan_id);
    const rejectedLocalPhotos = rejectedSubmit && rejectedSubmit.data && Array.isArray(rejectedSubmit.data.localPhotos)
      ? rejectedSubmit.data.localPhotos : [];
    const rejectedPhotoMeta = rejectedSubmit && rejectedSubmit.data && Array.isArray(rejectedSubmit.data.localPhotoMeta)
      ? rejectedSubmit.data.localPhotoMeta : [];
    const photoState = inspectionItemPhotoState(target);
    const photos = photoState.photos;
    const requiredPhotos = target.required_photos || 0;
    const photoInfo = inspectionItemPhotoRequirement(target, rejectedLocalPhotos.length);
    if (target.replacement_submission_allowed === false) {
      photoInfo.blocked = true;
      photoInfo.ready = false;
      photoInfo.blockReason = target.replacement_block_reason || '补拍照片待审核，审核完成后才能继续处理';
    }
    const currentUser = getUser() || {};
    const rejectedEvidence = rejectedInspectionEvidence(
      target.evidence_attachments, currentUser.id, resolveUploadUrl);
    this._inspectionSheetScrollTop = Number(this._inspectionParentScrollTop) || 0;
    this.setData({
      sheet: { open: true, item: target, result: target.result || 'normal', remark: target.remark || '', calibrator: target.calibrator || '', calValues: target.calibration_values || '', requiresCalibration: target.requiresCalibration === true, photos: photos.map(resolveUploadUrl), pendingPhotos: photoState.pendingPhotos.map(resolveUploadUrl), localPhotos: rejectedLocalPhotos, localPhotoMeta: rejectedPhotoMeta, requiredPhotos, replacementRequiredPhotos: photoInfo.required, replacementPhotoStatus: target.replacement_photo_status || 'not_applicable', replacementBlockReason: target.replacement_block_reason || '', originalPhotoCount: photoState.formalPhotos.length, supplementOnly: !!(target.result && reviewStatus === 1) || supplementRequired, voidedEvidenceCount: (target.voided_evidence || []).length, rejectedEvidence, purgeTarget: null, purgeCount: 0, purgeError: '', purgeLoading: false, photoResultTitle: '', photoResultMessage: '', photoInfo }
    });
  },

  onOpenRejectedPurge(e) {
    if (this.data.sheet.purgeLoading) return;
    const attachmentId = Number(e.currentTarget.dataset.id);
    const target = (this.data.sheet.rejectedEvidence || []).find(row => Number(row.id) === attachmentId);
    if (!target) return;
    this.setData({ 'sheet.purgeTarget': target, 'sheet.purgeCount': 0,
      'sheet.purgeError': '', 'sheet.purgeLoading': true });
    api.getRejectedInspectionPurgeBatch(target.id).then(preview => {
      this.setData({ 'sheet.purgeCount': Number(preview.count) || 0, 'sheet.purgeLoading': false });
    }).catch(error => {
      this.setData({ 'sheet.purgeLoading': false,
        'sheet.purgeError': (error && (error.error || error.message)) || '整改包范围读取失败，请重试' });
    });
  },

  onCancelRejectedPurge() {
    if (!this.data.sheet.purgeLoading) this.setData({
      'sheet.purgeTarget': null, 'sheet.purgeCount': 0, 'sheet.purgeError': '' });
  },

  onConfirmRejectedPurge() {
    const sheet = this.data.sheet;
    if (!sheet.purgeTarget || !sheet.purgeCount || sheet.purgeLoading) return;
    this.setData({ 'sheet.purgeLoading': true, 'sheet.purgeError': '' });
    api.purgeRejectedInspectionPhotoBatch(sheet.purgeTarget.id).then(result => {
      const deletedIds = new Set((result.attachment_ids || []).map(Number));
      const itemResults = new Map((result.items || []).map(row => [Number(row.item_id), row]));
      const deletedPaths = new Set();
      (this.data.categories || []).forEach(category => (category.items || []).forEach(item =>
        (item.evidence_attachments || []).forEach(attachment => {
          if (deletedIds.has(Number(attachment.id))) deletedPaths.add(uploadStoragePath(attachment.stored_path));
        })));
      let updatedItem = sheet.item;
      const categories = (this.data.categories || []).map(category => Object.assign({}, category, {
        items: (category.items || []).map(item => {
          const itemResult = itemResults.get(Number(item.item_id));
          if (!itemResult) return item;
          const next = Object.assign({}, item, {
            photo_urls: JSON.stringify(itemResult.photo_urls || []),
            actual_photos: Math.max(0, Number(itemResult.actual_photos) || 0),
            pending_photo_urls: (item.pending_photo_urls || []).filter(
              path => !deletedPaths.has(uploadStoragePath(path))),
            evidence_attachments: (item.evidence_attachments || []).filter(
              attachment => !deletedIds.has(Number(attachment.id))),
          });
          if (Number(item.item_id) === Number(sheet.item.item_id)) updatedItem = next;
          return next;
        }),
      }));
      const photoState = inspectionItemPhotoState(updatedItem || sheet.item);
      const currentUser = getUser() || {};
      const rejectedEvidence = rejectedInspectionEvidence(
        updatedItem && updatedItem.evidence_attachments, currentUser.id, resolveUploadUrl);
      const photos = photoState.photos.map(resolveUploadUrl);
      const pendingPhotos = photoState.pendingPhotos.map(resolveUploadUrl);
      const fieldProgress = projectInspectionFieldProgress(categories);
      this.setData({ categories: fieldProgress.categories, 'sheet.item': updatedItem || sheet.item,
        'sheet.photos': photos, 'sheet.pendingPhotos': pendingPhotos,
        'sheet.originalPhotoCount': photoState.formalPhotos.length,
        'sheet.photoInfo': inspectionSheetPhotoRequirement(
          sheet, photos, pendingPhotos, sheet.localPhotos),
        'sheet.rejectedEvidence': rejectedEvidence,
        completed: fieldProgress.completed, total: fieldProgress.total,
        completionPercent: fieldProgress.percent,
        progressFillStyle: `width:${fieldProgress.percent}%`,
        'sheet.purgeTarget': null, 'sheet.purgeCount': 0, 'sheet.purgeLoading': false });
      wx.showToast({ title: '整改照片已清理', icon: 'success' });
    }).catch(error => {
      this.setData({ 'sheet.purgeLoading': false,
        'sheet.purgeError': (error && (error.error || error.message)) || '彻底删除失败，请重试' });
    });
  },

  _restoreInspectionScroll() {
    const scrollTop = Number(this._inspectionSheetScrollTop);
    if (Number.isFinite(scrollTop) && wx.pageScrollTo) wx.pageScrollTo({ scrollTop, duration: 0 });
  },
  onCloseSheet() {
    this.setData({ 'sheet.open': false }, () => this._restoreInspectionScroll());
  },
  onSetResult(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.result': e.currentTarget.dataset.r }); },
  onRemark(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.remark': e.detail.value }); },
  onCalibrator(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.calibrator': e.detail.value }); },
  onCalValues(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.calValues': e.detail.value }); },

  onAddPhoto(e) {
    const sheet = this.data.sheet;
    if (sheet.replacementPhotoStatus === 'pending_review') {
      wx.showToast({ title: sheet.replacementBlockReason || '原照片待审核，审核完成后才能补拍', icon: 'none' });
      return;
    }
    if (sheet.photos.length + sheet.localPhotos.length >= 6) { wx.showToast({ title: '最多 6 张', icon: 'none' }); return; }
    const captureSource = e && e.currentTarget.dataset.source === 'camera'
      ? 'camera' : 'watermark_album';
    return runPhotoActionOnce(this, captureSource, () => {
      let verifiedLocation = null;
      const sessionTask = captureSourceNeedsLocationSession(captureSource)
        ? requestCaptureSessionWithLocation(requestLocation, gps => {
            verifiedLocation = gps;
            return api.createPhotoCaptureSession({
              site_id: this.data.selSiteId,
              plan_id: sheet.item.plan_id,
              item_id: sheet.item.item_id,
              capture_source: captureSource,
              gps_lat: gps.lat,
              gps_lng: gps.lng,
            });
          })
        : Promise.resolve(null);
      return sessionTask.then(session => chooseInspectionPhotos(
      captureSource === 'camera' ? 1 : 6 - sheet.photos.length - sheet.localPhotos.length,
      captureSource
    ).then(paths => ({ paths, session })))
      .then(({ paths, session }) => {
        if (!paths || !paths.length) return;
        wx.showLoading({ title: '上传中' });
        const siteId = this.data.selSiteId;
        const metadata = {
          capture_source: captureSource,
          plan_id: sheet.item.plan_id,
          item_id: sheet.item.item_id,
          item_name: sheet.item.item_name,
        };
        if (captureSource === 'camera') {
          metadata.capture_session = session && session.capture_session;
          metadata.taken_at = nowStr();
          metadata.gps_lat = verifiedLocation.lat;
          metadata.gps_lng = verifiedLocation.lng;
        }
        // 网络失败保留本地文件；来源校验失败由服务端返回业务结果，不加入检查项。
        const tasks = paths.map((p, index) => {
          const idempotencyKey = photoIdempotencyKey(siteId, p, index);
          const photoMetadata = Object.assign({}, metadata, { _idempotency_key: idempotencyKey });
          return fileToBase64(p)
            .then(b64 => api.uploadSitePhoto(siteId, b64, idempotencyKey, photoMetadata)
              .then(result => inspectionUploadTaskResult(result, {
                path: p, image: b64, metadata: photoMetadata, idempotencyKey,
              }, resolveUploadUrl)))
            .catch(error => {
              if (!isTransientSyncError(error)) throw error;
              return persistFile(p).then(saved => ({ localPath: saved, metadata: photoMetadata }));
            });
        });
        return Promise.allSettled(tasks);
      })
      .then(results => {
        if (!Array.isArray(results)) return;
        wx.hideLoading();
        const uploadResult = collectInspectionPhotoUploadResults(results);
        const urls = uploadResult.urls;
        const locals = uploadResult.localPaths;
        const localMeta = uploadResult.localMetadata;
        const allRemote = Array.from(new Set(sheet.photos.concat(urls)));
        const allPending = Array.from(new Set((sheet.pendingPhotos || []).concat(urls)));
        const categories = addPendingInspectionPhotos(
          this.data.categories, sheet.item.item_id, urls);
        const allLocal = sheet.localPhotos.concat(locals);
        const allLocalMeta = (sheet.localPhotoMeta || []).concat(localMeta);
        this.setData({
          'sheet.photos': allRemote,
          'sheet.pendingPhotos': allPending,
          categories,
          'sheet.localPhotos': allLocal,
          'sheet.localPhotoMeta': allLocalMeta,
          'sheet.photoInfo': inspectionSheetPhotoRequirement(
            sheet, allRemote, allPending, allLocal)
        });
        api.trackEvent('inspection.photo.captured', { site_id: this.data.selSiteId, item_id: sheet.item.item_id, source: captureSource, offline: locals.length > 0 });
        this.setData({ syncCount: pendingSyncCount() });
        const promptIssue = issue => new Promise(resolve => {
          const fixedMessage = setInspectionPhotoIssueMessage(this, issue);
          if (issue.kind === 'failed') {
            wx.showModal({
              title: `第${issue.index + 1}张照片未上传`,
              content: fixedMessage,
              showCancel: false,
              success: () => resolve('acknowledged'),
              fail: () => resolve('acknowledged'),
            });
            return;
          }
          const failedPhoto = issue.value;
          const canKeep = !!failedPhoto.rejected.can_keep_as_supplement;
          wx.showModal({
            title: captureSource === 'camera' ? '现场照片未通过校验' : '无法作为必需现场照片',
            content: fixedMessage,
            confirmText: captureSource === 'camera' ? '重新拍摄' : '重新选择',
            showCancel: canKeep,
            cancelText: '保留为附件',
            success: modalResult => resolve(modalResult.cancel && canKeep ? 'supplement' : 'retry'),
            fail: () => resolve('retry'),
          });
        });
        const retainSupplement = failedPhoto => {
          const keepKey = `${failedPhoto.idempotencyKey}:supplement`;
          return api.uploadSitePhoto(this.data.selSiteId, failedPhoto.image, keepKey,
            Object.assign({}, failedPhoto.metadata, {
              _idempotency_key: keepKey,
              keep_as_supplement: true,
            })).then(() => wx.showToast({ title: '已保留为补充附件', icon: 'none' }))
            .catch(() => wx.showToast({ title: '附件保留失败，请重试', icon: 'none' }));
        };
        if (!uploadResult.issues.length) {
          this.setData({ 'sheet.photoResultTitle': '', 'sheet.photoResultMessage': '' });
        }
        return processPhotoUploadIssues(uploadResult.issues, promptIssue, retainSupplement)
          .then(() => {
            if (locals.length && !urls.length) wx.showToast({ title: '照片已本地保存，联网同步', icon: 'none' });
            else if (locals.length) wx.showToast({ title: '部分已本地保存', icon: 'none' });
          });
      })
      .catch(err => {
        wx.hideLoading();
        if (isPhotoSelectionCancelled(err)) return;
        const locationFailure = err && err.capturePhase === 'location';
        if (locationFailure) {
          this.setData({
            'sheet.photoResultTitle': '无法获取位置',
            'sheet.photoResultMessage': locationErrorMessage(err),
          });
        } else {
          setInspectionPhotoIssueMessage(this, { kind: 'failed', error: err });
        }
        const openSettings = locationFailure
          ? shouldOpenLocationSettings(err) : shouldOpenCameraSettings(err);
        wx.showModal({
          title: locationFailure ? '无法获取位置'
            : (captureSource === 'camera' ? '现场拍摄未启动' : '照片选择未完成'),
          content: locationFailure ? locationErrorMessage(err) : photoCaptureErrorMessage(err),
          showCancel: false,
          confirmText: openSettings ? '去设置' : '知道了',
          success: () => { if (openSettings) wx.openSetting({}); },
        });
      });
    }, title => wx.showLoading({ title, mask: true }), () => wx.hideLoading());
  },

  onDelPhoto(e) {
    const idx = e.currentTarget.dataset.idx;
    if (this.data.sheet.supplementOnly && idx < (this.data.sheet.originalPhotoCount || 0)) {
      wx.showToast({ title: '审核中的原始证据不能删除，只能补充照片', icon: 'none' });
      return;
    }
    const photos = this.data.sheet.photos.slice();
    const item = this.data.sheet.item;
    const url = photos[idx];
    if ((this.data.sheet.pendingPhotos || []).indexOf(url) !== -1) {
      const storagePath = uploadStoragePath(url);
      return deletePendingPhotoOnce(
        this, url, storagePath, path => api.deletePendingSitePhoto(path),
        () => this.data.sheet.photos || []
      ).then(nextPhotos => {
        const pendingPhotos = (this.data.sheet.pendingPhotos || []).filter(path => path !== url);
        const categories = removePendingInspectionPhoto(
          this.data.categories, item && item.item_id, storagePath);
        this.setData({
          'sheet.photos': nextPhotos,
          'sheet.pendingPhotos': pendingPhotos,
          categories,
          'sheet.photoInfo': inspectionSheetPhotoRequirement(
            this.data.sheet, nextPhotos, pendingPhotos, this.data.sheet.localPhotos),
        });
      }).catch(error => {
        wx.showToast({ title: (error && error.error) || '照片删除失败，请重试', icon: 'none' });
      });
    }
    if (item && item.result) api.deletePhoto(item.item_id, idx); // 已提交则通知后端删除
    photos.splice(idx, 1);
    this.setData({ 'sheet.photos': photos, 'sheet.photoInfo': inspectionSheetPhotoRequirement(
      this.data.sheet, photos, this.data.sheet.pendingPhotos, this.data.sheet.localPhotos) });
  },

  onDelLocalPhoto(e) {
    const idx = e.currentTarget.dataset.idx;
    const localPhotos = this.data.sheet.localPhotos.slice();
    const localPhotoMeta = (this.data.sheet.localPhotoMeta || []).slice();
    localPhotos.splice(idx, 1);
    localPhotoMeta.splice(idx, 1);
    this.setData({ 'sheet.localPhotos': localPhotos, 'sheet.localPhotoMeta': localPhotoMeta,
      'sheet.photoInfo': inspectionSheetPhotoRequirement(
        this.data.sheet, this.data.sheet.photos, this.data.sheet.pendingPhotos, localPhotos) });
  },

  onPreview(e) {
    const src = e.currentTarget.dataset.src;
    wx.previewImage({ urls: this.data.sheet.photos.concat(this.data.sheet.localPhotos), current: src });
  },

  updateItemResult(itemId, result, photos, syncPending = false, reviewStatus, evidenceStatus) {
    const categories = applyInspectionSubmission(
      this.data.categories, itemId, result, photos, syncPending, reviewStatus, evidenceStatus
    ).map(cat => {
      return {
        ...cat,
        items: cat.items.map(it => {
          if (it.item_id !== itemId) return it;
          const status = inspectionItemStatus(it, syncPending);
          return Object.assign({}, it, { result_cn: status.label, status_code: status.code });
        })
      };
    });
    let abnormalCount = 0;
    categories.forEach(cat => cat.items.forEach(it => {
      if (it.result === 'abnormal') abnormalCount++;
    }));
    const fieldProgress = projectInspectionFieldProgress(categories);
    this.setData({ categories: fieldProgress.categories,
      completed: fieldProgress.completed, total: fieldProgress.total,
      abnormalCount, completionPercent: fieldProgress.percent,
      progressFillStyle: `width:${fieldProgress.percent}%`,
      photoProgress: inspectionPhotoProgress(categories),
    }, () => this.refreshStationStage(this.data.selSiteId));
    return { completed: fieldProgress.completed, total: fieldProgress.total };
  },

  onSubmitItem() {
    const s = this.data.sheet;
    if (!s.item || this._submittingItem) return;
    if (s.replacementPhotoStatus === 'pending_review') {
      wx.showToast({ title: s.replacementBlockReason || '原照片待审核，审核完成后才能补拍', icon: 'none' });
      return;
    }
    if (Number(s.item.review_status || 0) === 2 && s.item.evidence_status !== 'supplement_required') {
      wx.showToast({ title: '该检查项已通过审核，不能再次提交', icon: 'none' });
      return;
    }
    if (s.item.sync_pending || localStore.getPendingSubmit(s.item.item_id, s.item.plan_id)) {
      wx.showToast({ title: '该检查项已本地保存，等待同步完成', icon: 'none' });
      this.setData({ 'sheet.open': false });
      return;
    }
    const photoInfo = inspectionSheetPhotoRequirement(
      s, s.photos, s.pendingPhotos, s.localPhotos);
    if (s.supplementOnly && s.photos.length <= (s.originalPhotoCount || 0) && !s.localPhotos.length) {
      wx.showToast({ title: '请先补充新的现场照片', icon: 'none' });
      return;
    }
    if (!hasInspectionFieldRecord({
      remark: s.remark,
      calibrator: s.requiresCalibration ? s.calibrator : '',
      calibrationValues: s.requiresCalibration ? s.calValues : '',
      photoCount: photoInfo.captured,
    })) {
      wx.showToast({
        title: s.requiresCalibration ? '请填写现场说明、校准信息或拍摄照片' : '请填写现场说明或拍摄照片',
        icon: 'none',
      });
      return;
    }
    if (s.result === 'normal' && !photoInfo.ready) {
      wx.showToast({ title: '请按要求补齐现场照片', icon: 'none' });
      return;
    }
    if (s.result === 'abnormal' && photoInfo.captured === 0) {
      wx.showToast({ title: '异常项必须拍照', icon: 'none' });
      return;
    }
    this._submittingItem = true;
    this.setData({ submitting: true });
    const photoUrls = JSON.stringify(s.photos);
    const localPhotos = s.localPhotos.slice();
    const localPhotoMeta = (s.localPhotoMeta || []).slice();
    requestLocation().catch(() => null).then(gps => {
      const payload = {
        item_id: s.item.item_id,
        plan_id: s.item.plan_id,
        result: s.result,
        supplement: !!s.supplementOnly,
        remark: s.remark,
        photo_urls: photoUrls,
        // 离线闭环关键：携带站点与本地照片路径，联网后同步引擎先传图再提交
        siteId: this.data.selSiteId,
        localPhotos: localPhotos,
        localPhotoMeta: localPhotoMeta
      };
      if (s.requiresCalibration) {
        payload.calibrator = s.calibrator;
        payload.calibration_values = s.calValues;
      }
      if (gps) { payload.gps_lat = gps.lat; payload.gps_lng = gps.lng; }
      // 本地先落库：无论网络成败都先存实体，断网可走完闭环
      const opId = localStore.addOp('submit', payload);
      api.trackEvent('inspection.item.queued', { site_id: this.data.selSiteId, item_id: s.item.item_id, plan_id: s.item.plan_id, operation_id: opId, offline: localPhotos.length > 0 });
      const submitPromise = localPhotos.length
        ? flushLocalOps().then((summary) => {
            const stillPending = localStore.getPending().some(op => op.id === opId);
            const outcome = resolveLocalSubmitFlush(summary, opId, stillPending);
            if (outcome.status === 'rejected') {
              return Promise.reject(Object.assign(new Error(outcome.error), { submissionRejected: true }));
            }
            if (outcome.status === 'pending') return Promise.reject(new Error('等待同步'));
            return outcome.response;
          })
        : api.submitItem(payload);
      submitPromise
        .then((res) => {
          localStore.markSynced(opId);
          this._afterSubmit(s, false, res);
          if (localPhotos.length && this.data.selSiteId) this.loadTasks(this.data.selSiteId);
          wx.showToast({ title: res && res.order_no ? '异常已转工单' : '已提交', icon: 'success' });
        })
        .catch((error) => {
          if (error && error.submissionRejected) {
            this._submittingItem = false;
            this.setData({ submitting: false });
            wx.showModal({
              title: '提交未完成',
              content: error.message || '服务器拒绝了本次提交，请按提示修正后重试',
              showCancel: false
            });
            return;
          }
          // 离线/弱网：实体已本地留存，联网后静默同步
          this._afterSubmit(s, true);
          wx.showToast({ title: '已本地保存，联网自动同步', icon: 'none' });
        });
    });
  },

  _afterSubmit(s, syncPending = false, response) {
    this._submittingItem = false;
    this.setData({ submitting: false, 'sheet.open': false }, () => this._restoreInspectionScroll());
    if (!syncPending && s && s.item) {
      const oldRejected = localStore.clearRejectedSubmit(s.item.item_id, s.item.plan_id);
      oldRejected.forEach(op => (op.data.localPhotos || []).forEach(filePath => {
        wx.removeSavedFile({ filePath, fail() {} });
      }));
    }
    this.updateItemResult(
      s.item.item_id, s.result,
      (!syncPending && response && Array.isArray(response.photo_urls))
        ? response.photo_urls.map(resolveUploadUrl) : s.photos,
      syncPending,
      response && response.review_status, response && response.evidence_status
    );
    this.setData({ syncCount: pendingSyncCount() });
    if (!syncPending && this.data.selSiteId) this.loadTasks(this.data.selSiteId);
  },

  onSyncNow() {
    if (this._syncingNow) {
      wx.showToast({ title: '正在同步，请稍候', icon: 'none' });
      return;
    }
    this._syncingNow = true;
    wx.showLoading({ title: '同步中' });
    Promise.all([
      flushQueue(captureFlushedPhoto),
      flushLocalOps().catch(() => ({ synced: 0, remaining: localStore.queueCount(), rejected: [] }))
    ]).then(([requestSummary, localSummary]) => {
      this._syncingNow = false;
      wx.hideLoading();
      this.refreshSyncState();
      if (this.data.selSiteId) this.loadTasks(this.data.selSiteId);
      const rejected = (requestSummary.rejected || []).length + (localSummary.rejected || []).length;
      if (rejected) {
        const details = (requestSummary.rejected || []).concat(localSummary.rejected || [])
          .map(item => item.error).filter(Boolean).slice(0, 2).join('；');
        wx.showModal({ title: '同步被服务器拒绝', content: details || `${rejected} 项操作被服务器拒绝，请按提示重新操作。`, showCancel: false });
        return;
      }
      if (rejected) wx.showModal({ title: '部分操作未同步', content: `${rejected} 项被服务器拒绝，请按提示重新操作。`, showCancel: false });
      else if (this.data.syncCount === 0) wx.showToast({ title: '同步完成', icon: 'success' });
    }).catch(() => {
      this._syncingNow = false;
      wx.hideLoading();
      this.refreshSyncState();
      wx.showToast({ title: '同步失败，请保持网络后重试', icon: 'none' });
    });
  }
});
