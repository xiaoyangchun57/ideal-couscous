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
  return Object.assign({}, pkg, { resource_parts: resourceParts, resource_summary: resourceSummary });
}

function getGps() {
  return new Promise((resolve) => {
    wx.getLocation({
      type: 'gcj02',
      success(res) { resolve({ lat: res.latitude, lng: res.longitude }); },
      fail() { resolve(null); }
    });
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
    sheet: { open: false, item: null, result: 'normal', remark: '', calibrator: '', calValues: '', photos: [], localPhotos: [], localPhotoMeta: [] },
    submitting: false,
    confirmingDeparture: false,
    partsIssueSheet: { open: false, items: [], submitting: false },
    partsOptions: [{ id: 0, label: '手动输入（自定义名称）' }],
    partsFulfillmentOptions: PARTS_FULFILLMENT_OPTIONS,
    partsApply: { open: false, fulfillmentIndex: 0, fulfillment_type: 'stock', part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0, submitting: false },
    vehicleSheet: { open: false, mode: 'dispatch', mileage: '', remarks: '', items: [], submitting: false },
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
            return pendingSubmit ? {
              ...it,
              result: pendingSubmit.data.result,
              result_cn: '待同步',
              sync_pending: true,
            } : {
              ...it,
              result_cn: RESULT[it.result] || '待检',
              sync_pending: false,
            };
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
      return Promise.allSettled(paths.map(path => fileToBase64(path)
        .then(image => api.uploadSitePhoto(this.data.selSiteId, image))));
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
    getGps()
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
    const stationStage = !checkin && !this.hasSiteCheckIn(siteId)
      ? { code: 'unvisited', label: '待到站', cls: 'station-stage-wait' }
      : checkin && checkin.syncStatus === 'pending'
        ? { code: 'local_pending', label: '已到站，待同步', cls: 'station-stage-pending' }
        : { code: 'checked_in', label: '已到站', cls: 'station-stage-ok' };
    this.setData({ stationStage });
  },

  onCheckIn() {
    const site = this.data.site;
    if (!site) return;
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      if (!gps) {
        wx.showModal({ title: '无法获取位置', content: '到站打卡必须获取定位。请打开位置权限并重试。', confirmText: '去设置', success: r => { if (r.confirm) wx.openSetting({}); } });
        return;
      }
      const payload = { site_id: site.id, site_name: site.name, check_time: nowStr() };
      payload.lat = gps.lat; payload.lng = gps.lng;
      // 本地先落库：断网/弱网也留存打卡态，联网后静默同步
      const opId = localStore.addOp('checkin', payload);
      this.refreshStationStage(site.id);
      api.trackEvent('inspection.checkin.queued', { site_id: site.id, operation_id: opId });
      api.checkIn(payload, true)
        .then(() => {
          localStore.markSynced(opId);
          this.refreshStationStage(site.id);
          this.setData({ syncCount: pendingSyncCount() });
          wx.showToast({ title: '打卡成功', icon: 'success' });
        })
        .catch(() => {
          this.setData({ syncCount: pendingSyncCount() });
          wx.showToast({ title: '打卡已本地保存，联网同步', icon: 'none' });
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
    if (target.sync_pending || localStore.getPendingSubmit(target.item_id, target.plan_id)) {
      wx.showToast({ title: '该检查项已本地保存，等待同步完成', icon: 'none' });
      return;
    }
    let photos = [];
    try { photos = target.photo_urls ? JSON.parse(target.photo_urls) : []; } catch (e) { photos = []; }
    const requiredPhotos = target.required_photos || 0;
    this.setData({
      sheet: { open: true, item: target, result: target.result || 'normal', remark: target.remark || '', calibrator: target.calibrator || '', calValues: target.calibration_values || '', photos: photos.map(resolveUploadUrl), localPhotos: [], localPhotoMeta: [], requiredPhotos, photoInfo: photoRequirement(requiredPhotos, photos.length, 0) }
    });
  },

  onCloseSheet() { this.setData({ 'sheet.open': false }); },
  onSetResult(e) { this.setData({ 'sheet.result': e.currentTarget.dataset.r }); },
  onRemark(e) { this.setData({ 'sheet.remark': e.detail.value }); },
  onCalibrator(e) { this.setData({ 'sheet.calibrator': e.detail.value }); },
  onCalValues(e) { this.setData({ 'sheet.calValues': e.detail.value }); },

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
        const locationTask = captureSource === 'camera' ? getGps() : Promise.resolve(null);
        return locationTask.then(gps => {
          const metadata = { capture_source: captureSource };
          if (captureSource === 'camera') {
            metadata.taken_at = nowStr();
            if (gps) { metadata.gps_lat = gps.lat; metadata.gps_lng = gps.lng; }
          }
          // 成功取回 URL；失败（弱网/离线）保留原图与来源，待联网由同步引擎上传。
          const tasks = paths.map(p => fileToBase64(p)
            .then(b64 => api.uploadSitePhoto(siteId, b64, '', metadata)
              .then(r => ({ url: resolveUploadUrl(r.url), reviewRequired: !!r.review_required })))
            .catch(() => persistFile(p).then(saved => ({ localPath: saved, metadata }))));
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

  updateItemResult(itemId, result, photos, syncPending = false) {
    const categories = this.data.categories.map(cat => {
      return {
        ...cat,
        items: cat.items.map(it => it.item_id === itemId ? {
          ...it,
          result,
          result_cn: syncPending ? '待同步' : (RESULT[result] || '待检'),
          sync_pending: syncPending,
        } : it)
      };
    });
    let completed = 0, total = 0, abnormalCount = 0;
    categories.forEach(cat => cat.items.forEach(it => {
      total++;
      if (it.result) completed++;
      if (it.result === 'abnormal') abnormalCount++;
    }));
    this.setData({ categories, completed, total, abnormalCount,
      completionPercent: total ? Math.round(completed * 100 / total) : 0 });
  },

  onSubmitItem() {
    const s = this.data.sheet;
    if (!s.item || this._submittingItem) return;
    if (s.item.sync_pending || localStore.getPendingSubmit(s.item.item_id, s.item.plan_id)) {
      wx.showToast({ title: '该检查项已本地保存，等待同步完成', icon: 'none' });
      this.setData({ 'sheet.open': false });
      return;
    }
    const photoInfo = photoRequirement(s.requiredPhotos, s.photos.length, s.localPhotos.length);
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
    getGps().then(gps => {
      const payload = {
        item_id: s.item.item_id,
        plan_id: s.item.plan_id,
        result: s.result,
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
          this._afterSubmit(s);
          wx.showToast({ title: res && res.order_no ? '异常已转工单' : '已提交', icon: 'success' });
        })
        .catch(() => {
          // 离线/弱网：实体已本地留存，联网后静默同步
          this._afterSubmit(s, true);
          wx.showToast({ title: '已本地保存，联网自动同步', icon: 'none' });
        });
    });
  },

  _afterSubmit(s, syncPending = false) {
    this._submittingItem = false;
    this.setData({ submitting: false, 'sheet.open': false });
    this.updateItemResult(s.item.item_id, s.result, s.photos.concat(s.localPhotos), syncPending);
    this.setData({ syncCount: pendingSyncCount() });
  },

  onSyncNow() {
    wx.showLoading({ title: '同步中' });
    flushQueue(captureFlushedPhoto);
    Promise.resolve(flushLocalOps()).catch(() => {}).then(() => {
      setTimeout(() => {
        wx.hideLoading();
        this.refreshSyncState();
        if (this.data.selSiteId) this.loadTasks(this.data.selSiteId);
        if (this.data.syncCount === 0) wx.showToast({ title: '同步完成', icon: 'success' });
      }, 1000);
    });
  }
});
