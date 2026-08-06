const api = require('../../services/api.js');
const { todayStr } = require('../../utils/util.js');

const app = getApp();

const APPLICATION_STATUS = {
  pending: ['待审批', 'orange'], approved: ['待出车', 'brand'], rejected: ['已驳回', 'red'], cancelled: ['已取消', 'gray']
};

function vehicleLabel(item) {
  return (item.plate_no || '未指定车辆') + (item.model ? ('（车型：' + item.model + '）') : '');
}

function dateOnly(value) {
  return value ? String(value).slice(0, 10) : '';
}

function cleanPurpose(item) {
  const destination = item.destination || '目的地待定';
  const reason = String(item.reason || '').replace(/巡检计划#\d+用车/g, '巡检用车').replace(/计划#\d+/g, '计划').trim();
  return destination + (reason ? (' · ' + reason) : '');
}

function decorateApplication(item, use) {
  const state = APPLICATION_STATUS[item.status] || [item.status || '未知', 'gray'];
  const today = todayStr();
  const useDate = dateOnly(item.start_at);
  const tripEnd = dateOnly(item.end_at);
  const isPlanTrip = String(item.reason || '').indexOf('巡检计划#') >= 0;
  let statusCn = state[0];
  let statusCls = state[1];
  let canCheckout = false;
  if (use) {
    statusCn = use.returned_at ? '已归档' : '使用中';
    statusCls = use.returned_at ? 'green' : 'brand';
  } else if (item.status === 'approved') {
    if (isPlanTrip && useDate <= today && (!tripEnd || today <= tripEnd)) {
      statusCn = '待出车';
      statusCls = 'brand';
      canCheckout = true;
    } else if (useDate && useDate < today) {
      statusCn = '已过期';
      statusCls = 'gray';
    } else if (useDate && useDate > today) {
      statusCn = '已安排';
      statusCls = 'gray';
    } else {
      statusCn = '待出车';
      statusCls = 'brand';
      canCheckout = true;
    }
  }
  return Object.assign({}, item, {
    vehicle_label: vehicleLabel(item),
    purpose_label: cleanPurpose(item),
    is_plan_trip: isPlanTrip,
    trip_end_date: tripEnd,
    status_cn: statusCn,
    status_cls: statusCls,
    can_checkout: canCheckout
  });
}

function groupPlanHistory(uses) {
  const grouped = {};
  const history = [];
  (uses || []).filter(item => item.returned_at).forEach(item => {
    if (!item.is_plan_trip || !item.plan_schedule_id) {
      history.push(item);
      return;
    }
    const key = [item.plan_schedule_id, item.vehicle_id, item.applicant_id || ''].join(':');
    (grouped[key] || (grouped[key] = [])).push(item);
  });
  Object.keys(grouped).forEach(key => {
    const records = grouped[key].sort((a, b) => String(a.checked_out_at || '').localeCompare(String(b.checked_out_at || '')));
    const first = records[0];
    const last = records.slice().sort((a, b) => String(b.returned_at || '').localeCompare(String(a.returned_at || '')))[0];
    history.push(Object.assign({}, first, {
      id: 'plan-trip-' + key,
      checked_out_at: first.checked_out_at,
      returned_at: last.returned_at,
      start_mileage: first.start_mileage,
      end_mileage: last.end_mileage,
      trip_record_count: records.length
    }));
  });
  return history.sort((a, b) => String(b.returned_at || '').localeCompare(String(a.returned_at || '')));
}

function decorateUse(item) {
  const tripEnd = dateOnly(item.end_at);
  const isPlanTrip = String(item.reason || '').indexOf('巡检计划#') >= 0;
  const canReturn = !isPlanTrip || !tripEnd || tripEnd <= todayStr() || item.vehicle_status === 'restricted';
  return Object.assign({}, item, {
    vehicle_label: vehicleLabel(item),
    is_plan_trip: isPlanTrip,
    trip_end_date: tripEnd,
    plan_schedule_id: item.plan_schedule_id || null,
    can_return: canReturn
  });
}

function inspectionItems() {
  return ['驾驶证随车', '保险与年检', '灯光与信号灯', '后视镜', '轮胎及胎压', '车内卫生']
    .map(key => ({ key, label: key, status: 'normal', remark: '' }));
}
function energyMeta(fuelType) { return fuelType === 'electric' ? { label: '充电', unit: 'kWh' } : { label: '加油', unit: 'L' }; }

Page({
  data: {
    loaded: false, activeUse: null, applications: [], history: [], vehicles: [],
    checkoutSheet: { open: false, application: null, mileage: '', remarks: '', items: [], submitting: false },
    returnSheet: { open: false, mileage: '', remarks: '', items: [], submitting: false },
    refuelSheet: { open: false, liters: '', amount: '', mileage: '', remark: '', submitting: false },
    faultSheet: { open: false, faultType: '车辆故障', mileage: '', description: '', remark: '', submitting: false },
    applySheet: { open: false, vehicleIndex: 0, date: '', startTime: '08:00', endTime: '18:00', destination: '', reason: '', submitting: false }
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.load();
  },
  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  load(done) {
    Promise.all([api.vehicleApplications(), api.vehicleUseRecords(), api.vehicles()])
      .then(([applications, uses, vehicles]) => {
        const decoratedUses = (uses || []).map(decorateUse);
        const useByApplication = {};
        decoratedUses.forEach(item => { useByApplication[item.application_id] = item; });
        const decoratedApplications = (applications || []).map(item => {
          const use = useByApplication[item.id];
          return decorateApplication(item, use);
        });
        this.setData({
          loaded: true,
          applications: decoratedApplications.filter(item => !['cancelled', 'archived'].includes(item.status)),
          activeUse: decoratedUses.find(item => !item.returned_at) || null,
          history: groupPlanHistory(decoratedUses).slice(0, 10),
          vehicles: (vehicles || []).filter(item => item.dispatchable)
        });
        if (done) done();
      })
      .catch(() => { this.setData({ loaded: true }); if (done) done(); wx.showToast({ title: '车辆信息加载失败', icon: 'none' }); });
  },

  onOpenCheckout(e) {
    const application = (this.data.applications || []).find(item => String(item.id) === String(e.currentTarget.dataset.id));
    if (!application || !application.vehicle_id) { wx.showToast({ title: '该安排尚未指定车辆', icon: 'none' }); return; }
    if (!application.can_checkout) { wx.showToast({ title: application.status_cn || '当前不可出车', icon: 'none' }); return; }
    const vehicle = (this.data.vehicles || []).find(item => String(item.id) === String(application.vehicle_id));
    api.vehicleInspectionTemplate().then(items => {
      this.setData({ checkoutSheet: { open: true, application, mileage: String((vehicle && vehicle.current_mileage) || ''), remarks: '', items: items || inspectionItems(), submitting: false } });
    }).catch(() => this.setData({ checkoutSheet: { open: true, application, mileage: String((vehicle && vehicle.current_mileage) || ''), remarks: '', items: inspectionItems(), submitting: false } }));
  },
  onCloseCheckout() { this.setData({ 'checkoutSheet.open': false }); },
  onCheckoutMileage(e) { this.setData({ 'checkoutSheet.mileage': e.detail.value }); },
  onCheckoutRemarks(e) { this.setData({ 'checkoutSheet.remarks': e.detail.value }); },
  onCheckoutItem(e) { const { index, status } = e.currentTarget.dataset; this.setData({ ['checkoutSheet.items[' + index + '].status']: status }); },
  onSubmitCheckout() {
    const sheet = this.data.checkoutSheet; const mileage = Number(sheet.mileage);
    if (!sheet.application || !Number.isFinite(mileage) || mileage < 0) { wx.showToast({ title: '请填写出车时里程', icon: 'none' }); return; }
    const blocked = sheet.items.some(item => item.status === 'blocked'); const attention = sheet.items.some(item => item.status === 'attention');
    if ((blocked || attention) && !sheet.remarks.trim()) { wx.showToast({ title: '发现异常时请填写现场说明', icon: 'none' }); return; }
    this.setData({ 'checkoutSheet.submitting': true });
    api.submitVehicleInspection({ vehicle_id: sheet.application.vehicle_id, inspection_type: 'dispatch', odometer: mileage, overall_status: blocked ? 'blocked' : (attention ? 'attention' : 'normal'), items: sheet.items, remarks: sheet.remarks })
      .then(check => api.checkOutVehicle({ application_id: sheet.application.id, start_mileage: mileage, out_inspection_id: check.id }))
      .then(() => { this.setData({ 'checkoutSheet.open': false }); wx.showToast({ title: '已完成出车登记', icon: 'success' }); this.load(); })
      .catch(err => { this.setData({ 'checkoutSheet.submitting': false }); wx.showToast({ title: (err && err.error) || '出车登记失败', icon: 'none' }); });
  },

  onOpenRefuel() { const use = this.data.activeUse; if (!use) return; const energy = energyMeta(use.fuel_type); this.setData({ refuelSheet: { open: true, quantity: '', amount: '', mileage: String(use.start_mileage || ''), remark: '', label: energy.label, unit: energy.unit, submitting: false } }); },
  onCloseRefuel() { this.setData({ 'refuelSheet.open': false }); },
  onRefuelField(e) { this.setData({ ['refuelSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitRefuel() {
    const use = this.data.activeUse; const sheet = this.data.refuelSheet;
    if (!use || !(Number(sheet.quantity) > 0) || !(Number(sheet.mileage) >= Number(use.start_mileage || 0))) { wx.showToast({ title: '请填写有效补给量和当前里程', icon: 'none' }); return; }
    this.setData({ 'refuelSheet.submitting': true });
    api.refuelVehicleUse(use.id, { energy_quantity: Number(sheet.quantity), amount: sheet.amount === '' ? null : Number(sheet.amount), mileage_at: Number(sheet.mileage), remark: sheet.remark })
      .then(() => { this.setData({ 'refuelSheet.open': false }); wx.showToast({ title: sheet.label + '记录已保存', icon: 'success' }); })
      .catch(err => { this.setData({ 'refuelSheet.submitting': false }); wx.showToast({ title: (err && err.error) || (sheet.label + '记录失败'), icon: 'none' }); });
  },

  onOpenFault() { const use = this.data.activeUse; if (!use) return; this.setData({ faultSheet: { open: true, faultType: '车辆故障', mileage: String(use.start_mileage || ''), description: '', remark: '', submitting: false } }); },
  onCloseFault() { this.setData({ 'faultSheet.open': false }); },
  onFaultField(e) { this.setData({ ['faultSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitFault() {
    const use = this.data.activeUse; const sheet = this.data.faultSheet;
    if (!use || !sheet.description.trim()) { wx.showToast({ title: '请填写故障现象', icon: 'none' }); return; }
    this.setData({ 'faultSheet.submitting': true });
    api.reportVehicleFault(use.id, { fault_type: sheet.faultType, mileage_at: sheet.mileage === '' ? null : Number(sheet.mileage), description: sheet.description.trim(), remark: sheet.remark })
      .then(() => { this.setData({ 'faultSheet.open': false }); wx.showModal({ title: '故障已上报', content: '车辆已限制使用，请尽快安全还车并等待维修安排。', showCancel: false }); this.load(); })
      .catch(err => { this.setData({ 'faultSheet.submitting': false }); wx.showToast({ title: (err && err.error) || '故障上报失败', icon: 'none' }); });
  },

  onOpenReturn() {
    const use = this.data.activeUse; if (!use) return;
    if (!use.can_return) { wx.showToast({ title: '计划行程未结束，到站打卡会继续记录行程节点', icon: 'none' }); return; }
    api.vehicleInspectionTemplate().then(items => this.setData({ returnSheet: { open: true, mileage: String(use.start_mileage || ''), remarks: '', items: items || inspectionItems(), submitting: false } }))
      .catch(() => this.setData({ returnSheet: { open: true, mileage: String(use.start_mileage || ''), remarks: '', items: inspectionItems(), submitting: false } }));
  },
  onVehiclePrimaryAction() {
    if (this.data.activeUse && this.data.activeUse.can_return) this.onOpenReturn();
    else this.onOpenPlanChange();
  },
  onOpenPlanChange() {
    const use = this.data.activeUse;
    if (!use || !use.plan_schedule_id) { wx.showToast({ title: '未找到关联巡检计划', icon: 'none' }); return; }
    wx.navigateTo({ url: '/pages/plan-detail/plan-detail?id=' + use.plan_schedule_id });
  },
  onCloseReturn() { this.setData({ 'returnSheet.open': false }); },
  onReturnMileage(e) { this.setData({ 'returnSheet.mileage': e.detail.value }); },
  onReturnRemarks(e) { this.setData({ 'returnSheet.remarks': e.detail.value }); },
  onReturnItem(e) { const { index, status } = e.currentTarget.dataset; this.setData({ ['returnSheet.items[' + index + '].status']: status }); },
  onSubmitReturn() {
    const use = this.data.activeUse; const sheet = this.data.returnSheet; const mileage = Number(sheet.mileage);
    if (use && !use.can_return) { wx.showToast({ title: '计划行程未结束，请先发起计划变更', icon: 'none' }); return; }
    if (!use || !Number.isFinite(mileage) || mileage < Number(use.start_mileage || 0)) { wx.showToast({ title: '结束里程不能小于出车里程', icon: 'none' }); return; }
    const blocked = sheet.items.some(item => item.status === 'blocked'); const attention = sheet.items.some(item => item.status === 'attention');
    if ((blocked || attention) && !sheet.remarks.trim()) { wx.showToast({ title: '发现异常时请填写现场说明', icon: 'none' }); return; }
    this.setData({ 'returnSheet.submitting': true });
    api.submitVehicleInspection({ vehicle_id: use.vehicle_id, inspection_type: 'return', odometer: mileage, overall_status: blocked ? 'blocked' : (attention ? 'attention' : 'normal'), items: sheet.items, remarks: sheet.remarks })
      .then(check => api.returnVehicle(use.id, { end_mileage: mileage, return_inspection_id: check.id }))
      .then(res => { this.setData({ 'returnSheet.open': false }); wx.showModal({ title: '还车已登记', content: res.vehicle_status === 'restricted' ? '车辆已限制使用，等待维修处理。' : '行程已归档，车辆恢复可用。', showCancel: false }); this.load(); })
      .catch(err => { this.setData({ 'returnSheet.submitting': false }); wx.showToast({ title: (err && err.error) || '还车登记失败', icon: 'none' }); });
  },

  onOpenApply() {
    if (!(this.data.vehicles || []).length) { wx.showToast({ title: '当前没有可调度车辆，请联系管理员', icon: 'none' }); return; }
    this.setData({ applySheet: { open: true, vehicleIndex: 0, date: todayStr(), startTime: '08:00', endTime: '18:00', destination: '', reason: '', submitting: false } });
  },
  onCloseApply() { this.setData({ 'applySheet.open': false }); },
  onApplyPick(e) { this.setData({ 'applySheet.vehicleIndex': Number(e.detail.value) || 0 }); },
  onApplyField(e) { this.setData({ ['applySheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitApply() {
    const sheet = this.data.applySheet; const vehicle = this.data.vehicles[sheet.vehicleIndex];
    if (!vehicle || !sheet.date || !sheet.destination.trim() || !sheet.reason.trim()) { wx.showToast({ title: '请选择车辆并填写时间、目的地和事由', icon: 'none' }); return; }
    if (sheet.endTime <= sheet.startTime) { wx.showToast({ title: '结束时间应晚于开始时间', icon: 'none' }); return; }
    this.setData({ 'applySheet.submitting': true });
    api.applyVehicle({ vehicle_id: vehicle.id, start_at: sheet.date + ' ' + sheet.startTime + ':00', end_at: sheet.date + ' ' + sheet.endTime + ':00', destination: sheet.destination.trim(), reason: sheet.reason.trim() })
      .then(() => { this.setData({ 'applySheet.open': false }); wx.showToast({ title: '用车申请已提交', icon: 'success' }); this.load(); })
      .catch(err => { this.setData({ 'applySheet.submitting': false }); wx.showToast({ title: (err && err.error) || '提交申请失败', icon: 'none' }); });
  }
});
