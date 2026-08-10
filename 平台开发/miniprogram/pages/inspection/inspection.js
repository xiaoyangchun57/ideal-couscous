const api = require('../../services/api.js');
const { RESULT, INSPECTION_CATEGORY, linkedWorkorderCn, map } = require('../../services/maps.js');
const { getSites, getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const { chooseAndCompress, chooseInspectionPhotos, fileToBase64, persistFile, captureFlushedPhoto } = require('../../utils/photos.js');
const { resolveUploadUrl } = require('../../utils/url.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const localStore = require('../../utils/localStore.js');
const { flushLocalOps } = require('../../utils/sync.js');
const { selectExecutionSite, photoRequirement } = require('../../utils/executionState.js');
const { hasInspectionFieldRecord } = require('../../utils/inspectionSubmissionState.js');
const { requestLocation, locationErrorMessage, shouldOpenLocationSettings } = require('../../utils/location.js');
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

function photoIdempotencyKey(siteId, path, index) {
  // Keep one key when a request times out after the server has committed it.
  const safePath = String(path || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-24);
  return 'photo_' + siteId + '_' + Date.now() + '_' + index + '_' + safePath + '_' + Math.floor(Math.random() * 1e6);
}

function inspectionItemStatus(item, syncPending) {
  if (syncPending) return { label: '待同步', code: 'sync' };
  const reviewStatus = Number(item.review_status || 0);
  if (reviewStatus === 3) return { label: '待整改', code: 'rework' };
  if (item.result && reviewStatus === 1) return { label: '待审核', code: 'review' };
  if (item.result && reviewStatus === 2) return { label: '已通过', code: 'approved' };
  return { label: RESULT[item.result] || '待检', code: item.result || 'pending' };
}

function decoratePackageResources(pkg) {
  if (!pkg) return pkg;
  const resourceParts = (pkg.resource_parts || []).map(part => Object.assign({}, part, {
    planned_quantity: Number(part.planned_quantity || 0),
    issued_quantity: Number(part.issued_quantity || 0),
    remaining_quantity: Number(part.remaining_quantity || 0)
  }));
  const resourceSummary = resourceParts.reduce((summary, part) => {
    summary.planned += part.planned_quantity;
    summary.issued += part.issued_quantity;
    summary.remaining += part.remaining_quantity;
    summary.totalKinds += 1;
    summary.remainingKinds += part.remaining_quantity > 0 ? 1 : 0;
    return summary;
  }, { planned: 0, issued: 0, remaining: 0, totalKinds: 0, remainingKinds: 0 });
  return Object.assign({}, pkg, {
    resource_parts: resourceParts,
    resource_summary: resourceSummary,
    rework_resource: reworkResourcePresentation(pkg),
  });
}

Page({
  data: {
    packages: [],
    responsibleSites: getSites(),
    currentPackage: null,
    selectedPlanId: null,
    sites: [],
    selSite: null,
    selSiteId: null,
    site: null,
    categories: [],
    total: 0, completed: 0, completionPercent: 0, loaded: false, executionError: '',
    abnormalCount: 0,
    tripExpanded: false,
    tripReady: false,
    online: true, syncCount: 0,
    stationStage: null,
    reagents: [],
    reagentAction: '暂无记录',
    photoProgress: { req: 0, taken: 0, missing: 0 },
    reportTypes: REPORT_TYPES,
    reportSheet: { open: false, typeIndex: 0, description: '', photos: [], submitting: false },
    reagentSheet: { open: false, mode: 'replacement', index: 0, newQty: '', duration: '', standardValue: '', measuredValue: '', passed: true, failAction: 'calibrate', submitting: false },
    sheet: { open: false, item: null, result: 'normal', remark: '', calibrator: '', calValues: '', photos: [], localPhotos: [], localPhotoMeta: [], supplementOnly: false, originalPhotoCount: 0 },
    submitting: false,
    checkingOut: false,
    confirmingDeparture: false,
    partsIssueSheet: { open: false, items: [], submitting: false },
    partsOptions: [{ id: 0, label: '手动输入（自定义名称）' }],
    partsFulfillmentOptions: PARTS_FULFILLMENT_OPTIONS,
    partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false },
    vehicleSheet: { open: false, mode: 'dispatch', mileage: '', remarks: '', items: [], submitting: false },
    reworkResourceSheet: { open: false, mode: 'vehicle', vehicles: [], vehicleIndex: 0, exceptionReason: '', submitting: false },
    refuelSheet: { open: false, quantity: '', amount: '', mileage: '', remark: '', label: '加油', unit: 'L', submitting: false },
    vehicleFaultSheet: { open: false, faultType: '车辆故障', mileage: '', description: '', remark: '', submitting: false }
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.refreshSyncState();
    this.loadExecution();
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
    api.todayExecution().then(res => {
      const packages = (res.packages || []).map(decoratePackageResources);
      const preferredSiteId = app.globalData.selSiteId;
      const preferredPlanId = app.globalData.selPlanId || this.data.selectedPlanId;
      const selection = selectExecutionSite(packages, preferredPlanId, preferredSiteId);
      const currentPackage = selection.currentPackage;
      const sites = currentPackage ? currentPackage.sites || [] : [];
      const selected = selection.site;
      const selSiteId = selected ? selected.site_id : null;
      app.globalData.selSiteId = null;
      app.globalData.selPlanId = null;
      const tripReady = this.isTripReady(currentPackage);
      this.setData({ packages, currentPackage, selectedPlanId: currentPackage ? currentPackage.plan_id : null, executionError: '',
        sites: sites.map(s => Object.assign({}, s, { id: s.site_id })), selSiteId, loaded: true,
        tripReady, tripExpanded: currentPackage ? !tripReady : false,
        selSite: currentPackage ? this.data.selSite : null, site: currentPackage ? this.data.site : null,
        categories: currentPackage ? this.data.categories : [], total: currentPackage ? this.data.total : 0,
        completed: currentPackage ? this.data.completed : 0, completionPercent: currentPackage ? this.data.completionPercent : 0,
        abnormalCount: currentPackage ? this.data.abnormalCount : 0,
        photoProgress: currentPackage ? this.data.photoProgress : { req: 0, taken: 0, missing: 0 } });
      if (selSiteId) this.loadTasks(selSiteId, done); else if (done) done();
    }).catch(err => {
      const executionError = (err && err.error) || '巡检任务加载失败，请检查网络后重试';
      this.setData({ loaded: true, executionError, packages: [], currentPackage: null, sites: [], selSite: null, site: null, selSiteId: null, categories: [], total: 0, completed: 0, completionPercent: 0, abnormalCount: 0, photoProgress: { req: 0, taken: 0, missing: 0 }, tripReady: false, tripExpanded: false });
      if (done) done();
    });
  },

  isTripReady(pkg) {
    if (!pkg) return false;
    if (pkg.is_rework) return pkg.resource_state === 'ready';
    if (pkg.is_carryover) return true;
    const confirmation = pkg.departure_confirmation || {};
    const resourcesReady = confirmation.vehicle_confirmed && confirmation.parts_confirmed;
    const vehicleReady = !pkg.vehicle || !!pkg.vehicle_use;
    return !!(resourcesReady && vehicleReady);
  },

  onToggleTrip() {
    if (!this.data.tripReady) return;
    this.setData({ tripExpanded: !this.data.tripExpanded });
  },

  onSelectPackage(e) {
    const planId = e.currentTarget.dataset.id;
    const currentPackage = (this.data.packages || []).find(p => p.plan_id === planId);
    if (!currentPackage) return;
    const sites = (currentPackage.sites || []).map(s => Object.assign({}, s, { id: s.site_id }));
    const selSiteId = sites[0] && sites[0].id;
    const tripReady = this.isTripReady(currentPackage);
    this.setData({ currentPackage, selectedPlanId: planId, sites, selSiteId, categories: [], total: 0, completed: 0,
      completionPercent: 0, abnormalCount: 0, photoProgress: { req: 0, taken: 0, missing: 0 }, stationStage: null,
      tripReady, tripExpanded: !tripReady, reagents: [], reagentAction: '加载中…' });
    if (selSiteId) this.loadTasks(selSiteId);
  },

  onConfirmDeparture() {
    const currentPackage = this.data.currentPackage;
    if (!currentPackage || this.data.confirmingDeparture) return;
    wx.showModal({
      title: '确认出发资源',
      content: '确认已核验本次车辆和备件准备情况？此操作仅留痕，不锁车、不扣库，也不阻断巡检。',
      confirmText: '确认留痕',
      success: (result) => {
        if (!result.confirm) return;
        this.setData({ confirmingDeparture: true });
        api.confirmDepartureResources(currentPackage.plan_id, {
          vehicle_confirmed: true,
          parts_confirmed: true
        }).then(res => {
          const confirmation = res.confirmation || {
            vehicle_confirmed: 1,
            parts_confirmed: 1
          };
          const packages = (this.data.packages || []).map(item =>
            item.plan_id === currentPackage.plan_id
              ? Object.assign({}, item, { departure_confirmation: confirmation })
              : item
          );
          const updatedPackage = packages.find(item => item.plan_id === currentPackage.plan_id);
          const tripReady = this.isTripReady(updatedPackage);
          this.setData({ packages, currentPackage: updatedPackage, confirmingDeparture: false, tripReady, tripExpanded: !tripReady });
          wx.showToast({ title: '已记录资源确认', icon: 'success' });
        }).catch(() => {
          this.setData({ confirmingDeparture: false });
          wx.showToast({ title: '确认记录失败，请重试', icon: 'none' });
        });
      }
    });
  },

  onOpenPartsIssue() {
    const pkg = this.data.currentPackage;
    if (!pkg || !pkg.resource_summary || !pkg.resource_summary.remaining) return;
    const confirmed = pkg.departure_confirmation && pkg.departure_confirmation.parts_confirmed;
    if (!confirmed) {
      wx.showToast({ title: '请先完成出发资源确认', icon: 'none' });
      return;
    }
    const items = (pkg.resource_parts || []).filter(part => part.remaining_quantity > 0).map(part =>
      Object.assign({}, part, { issue_quantity: String(part.remaining_quantity) })
    );
    this.setData({ partsIssueSheet: { open: true, items, submitting: false } });
  },

  onClosePartsIssue() {
    if (!this.data.partsIssueSheet.submitting) this.setData({ 'partsIssueSheet.open': false });
  },

  onPartsIssueQuantity(e) {
    this.setData({ ['partsIssueSheet.items[' + e.currentTarget.dataset.index + '].issue_quantity']: e.detail.value });
  },

  onSubmitPartsIssue() {
    const pkg = this.data.currentPackage;
    const sheet = this.data.partsIssueSheet;
    if (!pkg || sheet.submitting) return;
    const items = [];
    for (const part of sheet.items || []) {
      const quantity = Number(part.issue_quantity || 0);
      if (!Number.isInteger(quantity) || quantity < 0 || quantity > part.remaining_quantity) {
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
        this.setData({ 'partsIssueSheet.submitting': true });
        api.issueExecutionParts(pkg.plan_id, items).then(res => {
          const updated = decoratePackageResources(Object.assign({}, pkg, { resource_parts: res.resource_parts || [] }));
          const packages = (this.data.packages || []).map(item => item.plan_id === pkg.plan_id ? updated : item);
          this.setData({ packages, currentPackage: updated, partsIssueSheet: { open: false, items: [], submitting: false } });
          wx.showToast({ title: '已领用并扣库', icon: 'success' });
        }).catch(err => {
          this.setData({ 'partsIssueSheet.submitting': false });
          wx.showToast({ title: (err && err.message) || '领用失败，请重试', icon: 'none' });
        });
      }
    });
  },

  onOpenVehicleCheckout() {
    const pkg = this.data.currentPackage;
    if (!pkg || !pkg.vehicle) return;
    if (!pkg.vehicle_application_id) { wx.showToast({ title: '未找到本计划获批的用车安排', icon: 'none' }); return; }
    if (pkg.vehicle_needs_extension) {
      wx.showModal({
        title: '用车安排已超期',
        content: '结转巡检尚未完成，请先到“我的车辆”延续本次用车截止日期。',
        confirmText: '立即延续',
        success: result => {
          if (result.confirm) wx.navigateTo({ url: '/pages/vehicle/vehicle' });
        }
      });
      return;
    }
    if (pkg.vehicle_use && pkg.vehicle_use.returned_at) { wx.showToast({ title: '本计划车辆已完成还车', icon: 'none' }); return; }
    if (pkg.vehicle_use && !pkg.vehicle_can_return) {
      wx.showToast({ title: '车辆行程中，到站打卡会自动记录行程节点', icon: 'none' });
      return;
    }
    const mode = pkg.vehicle_use ? 'return' : 'dispatch';
    const mileage = mode === 'return' ? String(pkg.vehicle_use.start_mileage || pkg.vehicle.current_mileage || '') : String(pkg.vehicle.current_mileage || '');
    api.vehicleInspectionTemplate()
      .then(items => this.setData({ vehicleSheet: { open: true, mode, mileage, remarks: '', items: items || [], submitting: false } }))
      .catch(() => {
        const keys = ['驾驶证随车', '保险与年检', '灯光与信号灯', '后视镜', '轮胎及胎压', '车内卫生'];
        this.setData({ vehicleSheet: { open: true, mode, mileage, remarks: '', items: keys.map(key => ({ key, label: key, status: 'normal', remark: '' })), submitting: false } });
      });
  },
  onCloseVehicleCheckout() { this.setData({ 'vehicleSheet.open': false }); },
  onVehicleMileage(e) { this.setData({ 'vehicleSheet.mileage': e.detail.value }); },
  onVehicleRemark(e) { this.setData({ 'vehicleSheet.remarks': e.detail.value }); },
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
    if (!pkg || !pkg.vehicle || !Number.isFinite(mileage) || mileage < 0) { wx.showToast({ title: isReturn ? '请填写还车时里程' : '请填写出车时里程', icon: 'none' }); return; }
    const hasAttention = (sheet.items || []).some(item => item.status === 'attention');
    const hasBlocked = (sheet.items || []).some(item => item.status === 'blocked');
    const overallStatus = hasBlocked ? 'blocked' : (hasAttention ? 'attention' : 'normal');
    if (overallStatus !== 'normal' && !(sheet.remarks || '').trim()) { wx.showToast({ title: '发现异常时请填写现场说明', icon: 'none' }); return; }
    this.setData({ 'vehicleSheet.submitting': true });
    api.submitVehicleInspection({ vehicle_id: pkg.vehicle.id, inspection_type: isReturn ? 'return' : 'dispatch', odometer: mileage, overall_status: overallStatus, items: sheet.items, remarks: sheet.remarks })
      .then(check => {
        if (isReturn) return api.returnVehicle(pkg.vehicle_use.id, { end_mileage: mileage, return_inspection_id: check.id });
        return api.checkOutVehicle({ application_id: pkg.vehicle_application_id, start_mileage: mileage, out_inspection_id: check.id });
      })
      .then(res => {
        this.setData({ 'vehicleSheet.open': false });
        if (res && res.vehicle_status === 'restricted') wx.showModal({ title: '车辆已限制使用', content: '还车检查发现不可继续使用的问题，已转为受限状态，请联系管理员处理。', showCancel: false });
        else wx.showToast({ title: isReturn ? '已完成还车登记' : '已完成出车登记', icon: 'success' });
        this.loadExecution();
      })
      .catch(() => { this.setData({ 'vehicleSheet.submitting': false }); wx.showToast({ title: isReturn ? '还车登记失败，请核对里程和车况' : '出车登记失败，请核对车辆状态', icon: 'none' }); });
  },

  onOpenPartsApply() {
    const site = this.data.selSite;
    if (!site) { wx.showToast({ title: '请先选择站点', icon: 'none' }); return; }
    const openSheet = () => this.setData({ partsApply: {
      open: true, fulfillmentIndex: 0, fulfillment_type: 'stock',
      part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false
    } });
    if (this.data.partsOptions.length > 1) { openSheet(); return; }
    api.partsInventory().then(parts => {
      const partsOptions = [{ id: 0, label: '手动输入（自定义名称）' }].concat((parts || []).map(part => ({
        id: part.id, part_name: part.part_name,
        label: (part.part_name || '备件') + (part.part_code ? '（' + part.part_code + '）' : '') + ' 余 ' + (part.quantity || 0)
      })));
      this.setData({ partsOptions }, openSheet);
    }).catch(openSheet);
  },

  onClosePartsApply() { if (!this.data.partsApply.submitting) this.setData({ 'partsApply.open': false }); },
  onPartsFulfillmentPick(e) {
    const index = parseInt(e.detail.value, 10) || 0;
    const selected = this.data.partsFulfillmentOptions[index] || this.data.partsFulfillmentOptions[0];
    this.setData({ 'partsApply.fulfillmentIndex': index, 'partsApply.fulfillment_type': selected.key,
      'partsApply.index': 0, 'partsApply.part_name': selected.key === 'stock' ? '' : this.data.partsApply.part_name });
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
    const partName = (form.part_name || '').trim();
    const reason = (form.reason || '').trim();
    const option = this.data.partsOptions[form.index];
    const sparePartId = form.fulfillment_type === 'stock' && option && option.id ? option.id : null;
    if (!site || !partName) { wx.showToast({ title: '请填写备件名称', icon: 'none' }); return; }
    if (!reason) { wx.showToast({ title: '请填写申请事由', icon: 'none' }); return; }
    if (form.fulfillment_type === 'stock' && !sparePartId) { wx.showToast({ title: '请选择库存备件', icon: 'none' }); return; }
    this.setData({ 'partsApply.submitting': true });
    api.applyParts({ site_id: site.id || site.site_id, part_name: partName,
      specification: (form.specification || '').trim(), quantity: form.quantity || 1, reason,
      spare_part_id: sparePartId, fulfillment_type: form.fulfillment_type,
      estimated_amount: form.estimated_amount === '' ? null : Number(form.estimated_amount) })
      .then(() => { this.setData({ 'partsApply.open': false, 'partsApply.submitting': false }); wx.showToast({ title: '备件需求已提交', icon: 'success' }); })
      .catch(err => { this.setData({ 'partsApply.submitting': false }); wx.showToast({ title: (err && err.error) || '提交失败', icon: 'none' }); });
  },

  loadTasks(siteId, done) {
    const planId = this.data.selectedPlanId;
    if (!planId) { if (done) done(); return; }
    api.executionSiteTasks(planId, siteId)
      .then(res => {
        const packageSite = ((this.data.currentPackage && this.data.currentPackage.sites) || []).find(s => s.site_id === siteId) || {};
        const selectedSite = Object.assign({}, res.site || {}, {
          linked_workorders: (packageSite.linked_workorders || []).map(linkedWorkorderCn)
        });
        const photosMap = {};
        (res.categories || []).forEach(cat => (cat.items || []).forEach(it => {
          let arr = [];
          try { arr = it.photo_urls ? JSON.parse(it.photo_urls) : []; } catch (e) { arr = []; }
          photosMap[it.item_id] = arr;
        }));
        // 巡检结果枚举集中映射（§6.8：禁止 wxml 硬编码中文枚举）
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
            return Object.assign({}, merged, { result_cn: status.label, status_code: status.code });
          })
        }));
        const localCompleted = decorated.reduce((count, cat) => count + (cat.items || [])
          .filter(item => item.result).length, 0);
        const abnormalCount = decorated.reduce((count, cat) => count + (cat.items || [])
          .filter(item => item.result === 'abnormal').length, 0);
        this.setData({
          site: selectedSite,
          selSite: selectedSite,
          categories: decorated,
          total: res.total || 0,
          completed: localCompleted,
          completionPercent: res.total ? Math.round(localCompleted * 100 / res.total) : 0,
          abnormalCount,
          loaded: true,
          photoProgress: (() => {
            let req = 0, taken = 0;
            (res.categories || []).forEach(cat => (cat.items || []).forEach(it => {
              req += (it.required_photos || 0);
              let arr = []; try { arr = it.photo_urls ? JSON.parse(it.photo_urls) : []; } catch(e) {}
              taken += arr.length;
            }));
            return { req, taken, missing: Math.max(0, req - taken) };
          })()
        });
        this.refreshStationStage(siteId);
        this.loadReagents(siteId);
        if (done) done();
      })
      .catch(() => { this.setData({ loaded: true }); if (done) done(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  loadReagents(siteId) {
    const planId = this.data.selectedPlanId;
    if (!planId || !siteId) return;
    api.executionSiteReagents(planId, siteId)
      .then(res => {
        const reagents = res.items || [];
        const pendingCalibration = reagents.some(item => item.qc_status === 'pending');
        this.setData({ reagents, reagentAction: pendingCalibration ? '开始标定 ›' : (reagents.length ? '登记更换 ›' : '暂无记录') });
      })
      .catch(() => this.setData({ reagents: [], reagentAction: '暂无记录' }));
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
      categories: [], total: 0, completed: 0, completionPercent: 0, abnormalCount: 0,
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
    chooseAndCompress(remaining).then(paths => {
      if (!paths || !paths.length) return [];
      wx.showLoading({ title: '上传中' });
      return Promise.allSettled(paths.map((path, index) => {
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
    }).catch(() => wx.showToast({ title: '照片上传失败', icon: 'none' })).finally(() => wx.hideLoading());
  },
  onPreviewReportPhoto(e) {
    const photos = this.data.reportSheet.photos || [];
    const current = e.currentTarget.dataset.url;
    if (current) wx.previewImage({ current, urls: photos });
  },
  onRemoveReportPhoto(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return;
    api.deletePendingSitePhoto(url).then(() => {
      this.setData({ 'reportSheet.photos': (this.data.reportSheet.photos || []).filter(item => item !== url) });
    }).catch(err => wx.showToast({ title: (err && err.error) || '照片删除失败', icon: 'none' }));
  },
  onSubmitReport() {
    const rs = this.data.reportSheet;
    const reportType = (REPORT_TYPES[rs.typeIndex] || REPORT_TYPES[0]).value;
    if (!rs.description.trim() || !rs.photos.length) { wx.showToast({ title: '请填写说明并拍摄现场照片', icon: 'none' }); return; }
    if (rs.submitting) return;
    this.setData({ 'reportSheet.submitting': true });
    requestLocation().catch(() => null)
      .then(gps => api.submitManualReport({
        site_id: this.data.selSiteId,
        report_type: reportType,
        description: rs.description.trim(),
        photo_urls: rs.photos,
        gps_lat: gps && gps.lat,
        gps_lng: gps && gps.lng,
      }))
      .then(res => {
        this.setData({ 'reportSheet.open': false, 'reportSheet.submitting': false });
        wx.showModal({ title: '异常已上报', content: `已生成工单：${res.order_no || '待分派'}`, showCancel: false });
        this.loadTasks(this.data.selSiteId);
      })
      .catch(err => {
        this.setData({ 'reportSheet.submitting': false });
        wx.showModal({ title: '上报失败', content: (err && err.error) || '提交未完成，请检查网络后重试', showCancel: false });
      });
  },

  hasSiteCheckIn(siteId) {
    if (localStore.getSiteCheckIn(siteId)) return true;
    return (this.data.sites || []).some(s => s.id === siteId && s.checked_in);
  },

  refreshStationStage(siteId) {
    const checkin = localStore.getSiteCheckIn(siteId);
    const site = (this.data.site && this.data.site.id === siteId)
      ? this.data.site : (this.data.sites || []).find(item => item.id === siteId);
    const stationStage = site && site.checked_out
      ? { code: 'checked_out', label: '已离站', cls: 'station-stage-ok' }
      : site && site.rework_checkin_required
        ? { code: 'unvisited', label: '待复到站', cls: 'station-stage-wait' }
      : !checkin && !this.hasSiteCheckIn(siteId)
      ? { code: 'unvisited', label: '待到站', cls: 'station-stage-wait' }
      : checkin && checkin.syncStatus === 'pending'
        ? { code: 'local_pending', label: '已到站，待同步', cls: 'station-stage-pending' }
        : { code: 'checked_in', label: '已到站', cls: 'station-stage-ok' };
    this.setData({ stationStage });
  },

  onOpenReworkResource() {
    const pkg = this.data.currentPackage;
    if (!pkg || !pkg.rework_resource || !pkg.rework_resource.canRequest) return;
    api.vehicles().then(vehicles => {
      this.setData({ reworkResourceSheet: {
        open: true, mode: 'vehicle', vehicles: vehicles || [], vehicleIndex: 0,
        exceptionReason: '', submitting: false
      } });
    }).catch(() => {
      this.setData({ reworkResourceSheet: {
        open: true, mode: 'no_vehicle', vehicles: [], vehicleIndex: 0,
        exceptionReason: '', submitting: false
      } });
    });
  },

  onCloseReworkResource() {
    if (!this.data.reworkResourceSheet.submitting) this.setData({ 'reworkResourceSheet.open': false });
  },

  onReworkResourceMode(e) { this.setData({ 'reworkResourceSheet.mode': e.currentTarget.dataset.mode }); },
  onReworkVehiclePick(e) { this.setData({ 'reworkResourceSheet.vehicleIndex': Number(e.detail.value) || 0 }); },
  onReworkExceptionReason(e) { this.setData({ 'reworkResourceSheet.exceptionReason': e.detail.value }); },

  onSubmitReworkResource() {
    const pkg = this.data.currentPackage;
    const sheet = this.data.reworkResourceSheet;
    if (!pkg || !pkg.rework_resource || sheet.submitting) return;
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
    this.setData({ 'reworkResourceSheet.submitting': true });
    api.requestReworkResource(pkg.plan_id, payload).then(() => {
      this.setData({ 'reworkResourceSheet.open': false, 'reworkResourceSheet.submitting': false });
      wx.showToast({ title: '整改资源申请已提交', icon: 'success' });
      this.loadExecution();
    }).catch(err => {
      this.setData({ 'reworkResourceSheet.submitting': false });
      wx.showToast({ title: (err && err.error) || '资源申请提交失败', icon: 'none' });
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
    if (localStore.getSiteCheckIn(site.id) && localStore.getSiteCheckIn(site.id).syncStatus === 'pending') {
      wx.showToast({ title: '到站打卡尚未同步，请稍候', icon: 'none' });
      return;
    }
    this.setData({ checkingOut: true });
    requestLocation().then(gps => api.checkOutExecutionSite(planId, site.id, {
      lat: gps.lat, lng: gps.lng
    })).then(res => {
      const sites = (this.data.sites || []).map(item => item.id === site.id
        ? Object.assign({}, item, { checked_out: true, check_out_time: res.check_out_time }) : item);
      const currentPackage = this.data.currentPackage ? Object.assign({}, this.data.currentPackage, { sites }) : this.data.currentPackage;
      this.setData({ sites, currentPackage, site: Object.assign({}, site, { checked_out: true }), selSite: Object.assign({}, this.data.selSite, { checked_out: true }), checkingOut: false }, () => {
        this.refreshStationStage(site.id);
      });
      wx.showModal({ title: '离站打卡成功', content: '本站巡检已完成，作业流已闭环。', showCancel: false });
    }).catch(err => {
      this.setData({ checkingOut: false });
      wx.showModal({ title: '离站打卡失败', content: (err && (err.error || err.message)) || '请确认定位有效且仍在站点附近', showCancel: false });
    });
  },

  showCheckoutPrompt() {
    if (!this.data.site || (this.data.site.checked_out) || !this.hasSiteCheckIn(this.data.site.id)) return;
    wx.showModal({ title: '巡检项已完成', content: '请完成离站打卡，闭合本站作业流。', confirmText: '离站打卡', cancelText: '稍后处理', success: result => { if (result.confirm) this.onCheckOut(); } });
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
          this.refreshStationStage(site.id);
          this.setData({ syncCount: pendingSyncCount() });
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
    if (this.data.selSiteId) wx.navigateTo({ url: '/pages/site/site?site_id=' + this.data.selSiteId });
  },

  onOpenItem(e) {
    if (this.data.site && this.data.site.rework_checkin_required) {
      wx.showModal({
        title: '请重新到站打卡',
        content: '该检查项影像已被驳回，必须重新到站打卡成功后才能补拍提交。',
        confirmText: '去打卡',
        cancelText: '稍后处理',
        success: result => { if (result.confirm) this.onCheckIn(); }
      });
      return;
    }
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
    if (target.result && reviewStatus === 2) {
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
    let photos = [];
    try { photos = target.photo_urls ? JSON.parse(target.photo_urls) : []; } catch (e) { photos = []; }
    const requiredPhotos = target.required_photos || 0;
    this.setData({
      sheet: { open: true, item: target, result: target.result || 'normal', remark: target.remark || '', calibrator: target.calibrator || '', calValues: target.calibration_values || '', photos: photos.map(resolveUploadUrl), localPhotos: rejectedLocalPhotos, localPhotoMeta: rejectedPhotoMeta, requiredPhotos, originalPhotoCount: photos.length, supplementOnly: !!(target.result && reviewStatus === 1), photoInfo: photoRequirement(requiredPhotos, photos.length, rejectedLocalPhotos.length) }
    });
  },

  onCloseSheet() { this.setData({ 'sheet.open': false }); },
  onSetResult(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.result': e.currentTarget.dataset.r }); },
  onRemark(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.remark': e.detail.value }); },
  onCalibrator(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.calibrator': e.detail.value }); },
  onCalValues(e) { if (!this.data.sheet.supplementOnly) this.setData({ 'sheet.calValues': e.detail.value }); },

  onAddPhoto(e) {
    const sheet = this.data.sheet;
    if (sheet.photos.length + sheet.localPhotos.length >= 6) { wx.showToast({ title: '最多 6 张', icon: 'none' }); return; }
    const captureSource = e && e.currentTarget.dataset.source === 'camera'
      ? 'camera' : 'watermark_album';
    chooseInspectionPhotos(6 - sheet.photos.length - sheet.localPhotos.length, captureSource)
      .then(paths => {
        if (!paths || !paths.length) return;
        wx.showLoading({ title: '上传中' });
        const siteId = this.data.selSiteId;
        const locationTask = captureSource === 'camera' ? requestLocation().catch(() => null) : Promise.resolve(null);
        return locationTask.then(gps => {
          const metadata = {
            capture_source: captureSource,
            plan_id: sheet.item.plan_id,
            item_id: sheet.item.item_id,
            item_name: sheet.item.item_name,
          };
          if (captureSource === 'camera') {
            metadata.taken_at = nowStr();
            if (gps) { metadata.gps_lat = gps.lat; metadata.gps_lng = gps.lng; }
          }
          // 成功取回 URL；失败（弱网/离线）保留原图与来源，待联网由同步引擎上传。
          const tasks = paths.map((p, index) => {
            const idempotencyKey = photoIdempotencyKey(siteId, p, index);
            const photoMetadata = Object.assign({}, metadata, { _idempotency_key: idempotencyKey });
            return fileToBase64(p)
            .then(b64 => api.uploadSitePhoto(siteId, b64, idempotencyKey, photoMetadata)
              .then(r => ({ url: resolveUploadUrl(r.url), reviewRequired: !!r.review_required })))
            .catch(() => persistFile(p).then(saved => ({ localPath: saved, metadata: photoMetadata })));
          });
          return Promise.allSettled(tasks);
        });
      })
      .then(results => {
        if (!Array.isArray(results)) return;
        wx.hideLoading();
        const urls = [];
        const locals = [];
        const localMeta = [];
        let reviewCount = 0;
        results.forEach(r => {
          if (r.status === 'fulfilled') {
            const v = r.value;
            if (v && v.url) { urls.push(v.url); reviewCount += v.reviewRequired ? 1 : 0; }
            else if (v && v.localPath) { locals.push(v.localPath); localMeta.push(v.metadata || {}); }
          }
        });
        const allRemote = sheet.photos.concat(urls);
        const allLocal = sheet.localPhotos.concat(locals);
        const allLocalMeta = (sheet.localPhotoMeta || []).concat(localMeta);
        this.setData({
          'sheet.photos': allRemote,
          'sheet.localPhotos': allLocal,
          'sheet.localPhotoMeta': allLocalMeta,
          'sheet.photoInfo': photoRequirement(sheet.requiredPhotos, allRemote.length, allLocal.length)
        });
        api.trackEvent('inspection.photo.captured', { site_id: this.data.selSiteId, item_id: sheet.item.item_id, source: captureSource, offline: locals.length > 0 });
        this.setData({ syncCount: pendingSyncCount() });
        if (reviewCount) wx.showToast({ title: '照片已上传，系统已标记复核', icon: 'none', duration: 2600 });
        else if (locals.length && !urls.length) wx.showToast({ title: '照片已本地保存，联网同步', icon: 'none' });
        else if (locals.length) wx.showToast({ title: '部分已本地保存', icon: 'none' });
      })
      .catch(() => {
        wx.hideLoading();
      });
  },

  onDelPhoto(e) {
    const idx = e.currentTarget.dataset.idx;
    if (this.data.sheet.supplementOnly && idx < (this.data.sheet.originalPhotoCount || 0)) {
      wx.showToast({ title: '审核中的原始证据不能删除，只能补充照片', icon: 'none' });
      return;
    }
    const photos = this.data.sheet.photos.slice();
    const item = this.data.sheet.item;
    if (item && item.result) api.deletePhoto(item.item_id, idx); // 已提交则通知后端删除
    photos.splice(idx, 1);
    this.setData({ 'sheet.photos': photos, 'sheet.photoInfo': photoRequirement(this.data.sheet.requiredPhotos, photos.length, this.data.sheet.localPhotos.length) });
  },

  onDelLocalPhoto(e) {
    const idx = e.currentTarget.dataset.idx;
    const localPhotos = this.data.sheet.localPhotos.slice();
    const localPhotoMeta = (this.data.sheet.localPhotoMeta || []).slice();
    localPhotos.splice(idx, 1);
    localPhotoMeta.splice(idx, 1);
    this.setData({ 'sheet.localPhotos': localPhotos, 'sheet.localPhotoMeta': localPhotoMeta, 'sheet.photoInfo': photoRequirement(this.data.sheet.requiredPhotos, this.data.sheet.photos.length, localPhotos.length) });
  },

  onPreview(e) {
    const src = e.currentTarget.dataset.src;
    wx.previewImage({ urls: this.data.sheet.photos.concat(this.data.sheet.localPhotos), current: src });
  },

  updateItemResult(itemId, result, photos, syncPending = false, reviewStatus) {
    const categories = this.data.categories.map(cat => {
      return {
        ...cat,
        items: cat.items.map(it => {
          if (it.item_id !== itemId) return it;
          const merged = Object.assign({}, it, {
            result,
            review_status: reviewStatus === undefined ? it.review_status : reviewStatus,
            sync_pending: syncPending,
          });
          const status = inspectionItemStatus(merged, syncPending);
          return Object.assign({}, merged, { result_cn: status.label, status_code: status.code });
        })
      };
    });
    let completed = 0, total = 0, abnormalCount = 0;
    categories.forEach(cat => cat.items.forEach(it => {
      total++;
      if (it.result) completed++;
      if (it.result === 'abnormal') abnormalCount++;
    }));
    this.setData({ categories, completed, total, abnormalCount,
      completionPercent: total ? Math.round(completed * 100 / total) : 0 }, () => this.refreshStationStage(this.data.selSiteId));
    return { completed, total };
  },

  onSubmitItem() {
    const s = this.data.sheet;
    if (!s.item || this._submittingItem) return;
    if (Number(s.item.review_status || 0) === 2) {
      wx.showToast({ title: '该检查项已通过审核，不能再次提交', icon: 'none' });
      return;
    }
    if (this.data.site && this.data.site.rework_checkin_required) {
      wx.showToast({ title: '请重新到站打卡后再补拍', icon: 'none' });
      return;
    }
    if (s.item.sync_pending || localStore.getPendingSubmit(s.item.item_id, s.item.plan_id)) {
      wx.showToast({ title: '该检查项已本地保存，等待同步完成', icon: 'none' });
      this.setData({ 'sheet.open': false });
      return;
    }
    const photoInfo = photoRequirement(s.requiredPhotos, s.photos.length, s.localPhotos.length);
    if (s.supplementOnly && s.photos.length <= (s.originalPhotoCount || 0) && !s.localPhotos.length) {
      wx.showToast({ title: '请先补充新的现场照片', icon: 'none' });
      return;
    }
    if (!hasInspectionFieldRecord({
      remark: s.remark,
      calibrator: s.calibrator,
      calibrationValues: s.calValues,
      photoCount: photoInfo.captured,
    })) {
      wx.showToast({ title: '请填写现场说明、校准信息或拍摄照片', icon: 'none' });
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
        calibrator: s.calibrator,
        calibration_values: s.calValues,
        // 离线闭环关键：携带站点与本地照片路径，联网后同步引擎先传图再提交
        siteId: this.data.selSiteId,
        localPhotos: localPhotos,
        localPhotoMeta: localPhotoMeta
      };
      if (gps) { payload.gps_lat = gps.lat; payload.gps_lng = gps.lng; }
      // 本地先落库：无论网络成败都先存实体，断网可走完闭环
      const opId = localStore.addOp('submit', payload);
      api.trackEvent('inspection.item.queued', { site_id: this.data.selSiteId, item_id: s.item.item_id, plan_id: s.item.plan_id, operation_id: opId, offline: localPhotos.length > 0 });
      const submitPromise = localPhotos.length
        ? flushLocalOps().then(() => {
            const stillPending = localStore.getPending().some(op => op.id === opId);
            if (stillPending) return Promise.reject(new Error('等待同步'));
            return { success: true };
          })
        : api.submitItem(payload);
      submitPromise
        .then((res) => {
          localStore.markSynced(opId);
          this._afterSubmit(s, false, res);
          if (localPhotos.length && this.data.selSiteId) this.loadTasks(this.data.selSiteId);
          wx.showToast({ title: res && res.order_no ? '异常已转工单' : '已提交', icon: 'success' });
        })
        .catch(() => {
          // 离线/弱网：实体已本地留存，联网后静默同步
          this._afterSubmit(s, true);
          wx.showToast({ title: '已本地保存，联网自动同步', icon: 'none' });
        });
    });
  },

  _afterSubmit(s, syncPending = false, response) {
    this._submittingItem = false;
    this.setData({ submitting: false, 'sheet.open': false });
    if (!syncPending && s && s.item) {
      const oldRejected = localStore.clearRejectedSubmit(s.item.item_id, s.item.plan_id);
      oldRejected.forEach(op => (op.data.localPhotos || []).forEach(filePath => {
        wx.removeSavedFile({ filePath, fail() {} });
      }));
    }
    const progress = this.updateItemResult(
      s.item.item_id, s.result, s.photos.concat(s.localPhotos), syncPending,
      response && response.review_status
    );
    this.setData({ syncCount: pendingSyncCount() });
    if (!syncPending) {
      if (progress.completed >= progress.total && progress.total > 0) this.showCheckoutPrompt();
    }
  },

  onSyncNow() {
    wx.showLoading({ title: '同步中' });
    Promise.all([
      flushQueue(captureFlushedPhoto),
      flushLocalOps().catch(() => ({ synced: 0, remaining: localStore.queueCount(), rejected: [] }))
    ]).then(([requestSummary, localSummary]) => {
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
      wx.hideLoading();
      this.refreshSyncState();
      wx.showToast({ title: '同步失败，请保持网络后重试', icon: 'none' });
    });
  }
});
