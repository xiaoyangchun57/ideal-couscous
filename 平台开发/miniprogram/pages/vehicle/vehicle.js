const api = require('../../services/api.js');
const { todayStr } = require('../../utils/util.js');
const { normalizePagedList, appendPagedItems } = require('../../utils/pagedList.js');
const { myVehicleQuery, isReturnedUse } = require('../../utils/vehicleScope.js');
const { invalidateUnreadCount } = require('../../utils/notificationCount.js');

const app = getApp();
const CURRENT_PAGE_LIMIT = 100;
const HISTORY_PAGE_LIMIT = 20;

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
  let statusCn = state[0];
  let statusCls = state[1];
  const needsExtension = !!item.needs_extension;
  if (use) {
    statusCn = use.is_returned ? '已归档' : '使用中';
    statusCls = use.is_returned ? 'green' : 'brand';
    if (!use.is_returned && use.needs_extension) {
      statusCn = '使用中 · 已超期';
      statusCls = 'red';
    }
  } else if (item.status === 'approved') {
    if (needsExtension) {
      statusCn = '待延续';
      statusCls = 'red';
    } else if (item.can_checkout) {
      statusCn = '待出车';
      statusCls = 'brand';
    } else {
      statusCn = item.checkout_block_reason || '已安排';
      statusCls = 'gray';
    }
  }
  return Object.assign({}, item, {
    vehicle_label: vehicleLabel(item),
    purpose_label: cleanPurpose(item),
    is_plan_trip: !!item.is_plan_trip,
    trip_end_date: item.trip_end_date || '',
    status_cn: statusCn,
    status_cls: statusCls,
    can_checkout: !!item.can_checkout,
    needs_extension: needsExtension,
    reserves_vehicle: !!item.reserves_vehicle,
    use_expired: !!(item.use_expired || (use && use.use_expired)),
    plan_completed: !!(item.plan_completed || (use && use.plan_completed))
  });
}

function groupPlanHistory(uses) {
  const grouped = {};
  const history = [];
  (uses || []).filter(item => item.is_returned).forEach(item => {
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
  return Object.assign({}, item, {
    vehicle_label: vehicleLabel(item),
    is_plan_trip: !!item.is_plan_trip,
    trip_end_date: item.trip_end_date || '',
    plan_schedule_id: item.plan_schedule_id || null,
    can_return: !!item.can_return,
    is_returned: isReturnedUse(item),
    needs_extension: !!item.needs_extension,
    use_expired: !!item.use_expired,
    plan_completed: !!item.plan_completed
  });
}

function inspectionItems() {
  return ['驾驶证随车', '保险与年检', '灯光与信号灯', '后视镜', '轮胎及胎压', '车内卫生']
    .map(key => ({ key, label: key, status: 'normal', remark: '' }));
}
function energyMeta(fuelType) { return fuelType === 'electric' ? { label: '充电', unit: 'kWh' } : { label: '加油', unit: 'L' }; }
function requiredFiniteNumber(value) {
  const raw = String(value === undefined || value === null ? '' : value).trim();
  if (!raw) return null;
  const number = Number(raw);
  return Number.isFinite(number) ? number : null;
}
function eventOptionIndex(event, optionCount) {
  const dataset = event && event.currentTarget && event.currentTarget.dataset;
  const hasDatasetIndex = dataset && Object.prototype.hasOwnProperty.call(dataset, 'index');
  const raw = hasDatasetIndex ? dataset.index : (event && event.detail && event.detail.value);
  if (typeof raw === 'number') return Number.isInteger(raw) && raw >= 0 && raw < optionCount ? raw : null;
  if (typeof raw !== 'string' || !/^(0|[1-9]\d*)$/.test(raw.trim())) return null;
  const index = Number(raw.trim());
  return index < optionCount ? index : null;
}

function normalizeVehicleTarget(value) {
  const applicationId = Number(value && value.applicationId);
  if (!Number.isInteger(applicationId) || applicationId <= 0) return null;
  const source = String(value.source || '');
  if (!['inspection_departure', 'vehicle_use_expiry', 'approval_result'].includes(source)) return null;
  return {
    applicationId,
    expectedAction: String(value.expectedAction || ''),
    source,
    notificationId: Number.isInteger(Number(value.notificationId)) && Number(value.notificationId) > 0
      ? Number(value.notificationId) : null,
    executionPlanId: value.executionPlanId || null,
    siteId: value.siteId || null,
  };
}

function responseItems(response) {
  return Array.isArray(response) ? response : ((response && response.items) || []);
}

Page({
  data: {
    loaded: false, loadState: 'initial_loading', loadError: '', authorityFresh: false,
    activeUse: null, activeUseConflict: false, activeArrangement: null, hasVehicleExpiry: false,
    applications: [], history: [], historyUses: [], historyPage: 1, historyTotal: 0, historyHasMore: false, historyLoading: false, vehicles: [], sites: [],
    sitesAuthorityFresh: false, sitesLoadError: '',
    checkoutSheet: { open: false, application: null, mileage: '', remarks: '', items: [], submitting: false },
    returnSheet: { open: false, mileage: '', remarks: '', items: [], submitting: false },
    extensionSheet: { open: false, applicationId: null, endDate: '', submitting: false },
    refuelSheet: { open: false, quantity: '', amount: '', mileage: '', remark: '', submitting: false },
    faultSheet: { open: false, faultType: '车辆故障', mileage: '', description: '', remark: '', affectsSafeOperation: null, safetyLabel: '请选择', submitting: false },
    safetyOptions: [{ label: '不影响安全行驶', value: false }, { label: '影响安全行驶', value: true }],
    destinationModes: [{ label: '授权站点', value: 'site' }, { label: '其他地点', value: 'other' }],
    applySheet: { open: false, vehicleIndex: 0, startDate: '', startTime: '08:00', endDate: '', endTime: '18:00', destinationMode: 'site', destinationModeIndex: 0, siteIndex: 0, otherDestination: '', reason: '', submitting: false }
  },

  onLoad(options) {
    this._alive = true;
    this._loadRequestId = 0;
    this._sitesRequestId = 0;
    const applicationId = Number(options && options.application_id);
    const notificationId = Number(options && options.notification_id);
    if (Number.isInteger(applicationId) && applicationId > 0) {
      app.globalData.vehicleTarget = {
        applicationId, expectedAction: 'view_result', source: 'approval_result', notificationId
      };
    }
    this._vehicleTarget = this._readVehicleTarget();
  },
  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this._alive = true;
    if (!this._vehicleTarget) this._vehicleTarget = this._readVehicleTarget();
    this.load();
  },
  onHide() {
    this._alive = false;
    this._loadRequestId = (this._loadRequestId || 0) + 1;
    this._historyRequestId = (this._historyRequestId || 0) + 1;
    this._sheetOpenRequestId = (this._sheetOpenRequestId || 0) + 1;
    this._sitesRequestId = (this._sitesRequestId || 0) + 1;
  },
  onUnload() {
    this.onHide();
    this._finishVehicleTarget(this._vehicleTarget);
  },
  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },
  onReachBottom() {
    if (!this.data.historyLoading && this.data.historyHasMore) this.loadMoreHistory();
  },

  load(done) {
    const user = app.globalData.user || {};
    const requestId = (this._loadRequestId || 0) + 1;
    this._loadRequestId = requestId;
    this._historyRequestId = (this._historyRequestId || 0) + 1;
    this._sheetOpenRequestId = (this._sheetOpenRequestId || 0) + 1;
    const target = this._vehicleTarget;
    const hadData = this.data.loaded;
    this._loadSites();
    this.setData({
      authorityFresh: false,
      loadState: hadData ? 'refreshing' : 'initial_loading',
      loadError: '',
    });
    const targetRequest = target
      ? api.vehicleApplications(myVehicleQuery('all', user, { application_id: target.applicationId }))
      : Promise.resolve([]);
    Promise.all([
      api.vehicleApplications(myVehicleQuery('current', user, { limit: CURRENT_PAGE_LIMIT })),
      api.vehicleUseRecords(myVehicleQuery('current', user, { limit: CURRENT_PAGE_LIMIT })),
      api.vehicleUseRecords(myVehicleQuery('history', user, { page: 1, limit: HISTORY_PAGE_LIMIT })),
      api.vehicles(),
      targetRequest,
    ])
      .then(([applicationsResponse, usesResponse, historyResponse, vehicles, targetResponse]) => {
        if (!this._alive || requestId !== this._loadRequestId || target !== this._vehicleTarget) return;
        const applicationsPage = normalizePagedList(applicationsResponse);
        const usesPage = normalizePagedList(usesResponse);
        const historyPage = normalizePagedList(historyResponse);
        const decoratedUses = usesPage.items.map(decorateUse);
        const historyUses = historyPage.items.map(decorateUse);
        const useByApplication = {};
        decoratedUses.forEach(item => { useByApplication[item.application_id] = item; });
        const applicationRows = applicationsPage.items.slice();
        const exactTargetApplication = responseItems(targetResponse)[0] || null;
        const exactTargetDecorated = exactTargetApplication
          ? decorateApplication(exactTargetApplication, useByApplication[exactTargetApplication.id])
          : null;
        if (exactTargetApplication && (target && target.expectedAction === 'view_result'
          || ['pending', 'approved'].includes(exactTargetApplication.status))
          && !applicationRows.some(item => String(item.id) === String(exactTargetApplication.id))) {
          applicationRows.push(exactTargetApplication);
        }
        const decoratedApplications = applicationRows.map(item => {
          const use = useByApplication[item.id];
          return decorateApplication(item, use);
        });
        const activeUses = decoratedUses.filter(item => !item.is_returned);
        const activeUseConflict = usesPage.total > 1 || activeUses.length > 1;
        const activeUse = activeUses.length === 1 ? activeUses[0] : null;
        const activeArrangements = decoratedApplications.filter(item => item.reserves_vehicle && !item.has_active_use);
        const activeArrangement = activeArrangements.length === 1 ? activeArrangements[0] : null;
        this.setData({
          loaded: true,
          loadState: 'ready',
          loadError: '',
          authorityFresh: true,
          applications: decoratedApplications,
          activeUse,
          activeUseConflict,
          activeArrangement,
          hasVehicleExpiry: decoratedUses.some(item => !item.is_returned && item.needs_extension)
            || decoratedApplications.some(item => item.needs_extension),
          historyUses,
          history: groupPlanHistory(historyUses),
          historyPage: historyPage.page,
          historyTotal: historyPage.total,
          historyHasMore: historyPage.hasMore,
          historyLoading: false,
          vehicles: (vehicles || []).filter(item => item.dispatchable)
        });
        this._consumeVehicleTarget(target, exactTargetApplication, exactTargetDecorated);
        if (!target && this._invalidVehicleTargetMessage) {
          const message = this._invalidVehicleTargetMessage;
          this._invalidVehicleTargetMessage = '';
          wx.showToast({ title: message, icon: 'none' });
        }
        if (done) done();
      })
      .catch(err => {
        if (!this._alive || requestId !== this._loadRequestId || target !== this._vehicleTarget) return;
        const loadError = (err && (err.error || err.message)) || '车辆信息加载失败，请重试';
        this.setData({
          authorityFresh: false,
          loadState: hadData ? 'refresh_error' : 'initial_error',
          loadError,
        });
        if (hadData) wx.showToast({ title: loadError, icon: 'none' });
        if (done) done();
      });
  },

  _loadSites() {
    const requestId = (this._sitesRequestId || 0) + 1;
    this._sitesRequestId = requestId;
    this.setData({ sitesAuthorityFresh: false, sitesLoadError: '' });
    api.sites()
      .then(sites => {
        if (!this._alive || requestId !== this._sitesRequestId) return;
        this.setData({ sites: Array.isArray(sites) ? sites : [], sitesAuthorityFresh: true, sitesLoadError: '' });
      })
      .catch(err => {
        if (!this._alive || requestId !== this._sitesRequestId) return;
        this.setData({
          sitesAuthorityFresh: false,
          sitesLoadError: (err && (err.error || err.message)) || '站点信息加载失败',
        });
      });
  },

  onRetryLoad() { this.load(); },

  _readVehicleTarget() {
    const raw = app.globalData.vehicleTarget;
    if (!raw) return null;
    const target = normalizeVehicleTarget(raw);
    if (target) return target;
    if (app.globalData.vehicleTarget === raw) app.globalData.vehicleTarget = null;
    this._invalidVehicleTargetMessage = '用车目标信息无效，已保留当前页面';
    return null;
  },

  _finishVehicleTarget(target) {
    if (!target) return;
    if (this._vehicleTarget === target) this._vehicleTarget = null;
    const current = app.globalData.vehicleTarget;
    if (current && Number(current.applicationId) === Number(target.applicationId)
      && String(current.expectedAction || '') === String(target.expectedAction || '')
      && String(current.source || '') === String(target.source || '')) {
      app.globalData.vehicleTarget = null;
    }
  },

  _consumeVehicleTarget(target, exactApplication, decoratedExactApplication) {
    if (!target || target !== this._vehicleTarget) return;
    if (target.expectedAction === 'view_result') {
      this._finishVehicleTarget(target);
      if (!exactApplication || !decoratedExactApplication) {
        wx.showToast({ title: '该用车申请不存在或无权查看', icon: 'none' });
      } else if (target.notificationId) {
        api.readNotification(target.notificationId).then(() => invalidateUnreadCount()).catch(() => {});
      }
      return;
    }
    if (target.expectedAction !== 'extend') {
      this._finishVehicleTarget(target);
      wx.showToast({ title: '用车目标动作无效，已保留当前页面', icon: 'none' });
      return;
    }
    const application = decoratedExactApplication;
    if (!exactApplication || !application) {
      this._finishVehicleTarget(target);
      wx.showToast({ title: '该用车申请不存在、已结束或无权查看', icon: 'none' });
      return;
    }
    if (!application.needs_extension) {
      this._finishVehicleTarget(target);
      wx.showToast({ title: '该用车申请当前无需延续，请查看最新状态', icon: 'none' });
      return;
    }
    if (this.data.activeUseConflict) {
      this._finishVehicleTarget(target);
      wx.showToast({ title: '存在多条未归还行程，请联系管理员核对后再延续', icon: 'none' });
      return;
    }
    this._finishVehicleTarget(target);
    this._openExtensionFor(application.id);
  },

  _writeBlockReason() {
    if (!this._alive) return '当前页面已离开，请返回后重试';
    if (!this.data.authorityFresh) return '车辆状态尚未刷新成功，请刷新后重试';
    if (this.data.activeUseConflict) return '检测到多条未归还行程，请刷新或联系管理员核对';
    return '';
  },

  _ensureWritable() {
    const reason = this._writeBlockReason();
    if (reason) wx.showToast({ title: reason, icon: 'none' });
    return !reason;
  },

  loadMoreHistory() {
    const nextPage = Number(this.data.historyPage || 1) + 1;
    const requestId = (this._historyRequestId || 0) + 1;
    this._historyRequestId = requestId;
    this.setData({ historyLoading: true });
    api.vehicleUseRecords(myVehicleQuery('history', app.globalData.user || {}, { page: nextPage, limit: HISTORY_PAGE_LIMIT }))
      .then(response => {
        if (!this._alive || requestId !== this._historyRequestId) return;
        const merged = appendPagedItems(this.data.historyUses, response);
        const historyUses = merged.items.map(decorateUse);
        this.setData({
          historyUses,
          history: groupPlanHistory(historyUses),
          historyPage: merged.page,
          historyTotal: merged.total,
          historyHasMore: merged.hasMore,
          historyLoading: false
        });
      })
      .catch(() => {
        if (!this._alive || requestId !== this._historyRequestId) return;
        this.setData({ historyLoading: false });
        wx.showToast({ title: '行程历史加载失败', icon: 'none' });
      });
  },

  onOpenCheckout(e) {
    if (!this._ensureWritable()) return;
    const application = (this.data.applications || []).find(item => String(item.id) === String(e.currentTarget.dataset.id));
    if (!application || !application.vehicle_id) { wx.showToast({ title: '该安排尚未指定车辆', icon: 'none' }); return; }
    if (!application.can_checkout) { wx.showToast({ title: application.checkout_block_reason || '当前不可出车', icon: 'none' }); return; }
    const vehicle = (this.data.vehicles || []).find(item => String(item.id) === String(application.vehicle_id));
    const requestId = (this._sheetOpenRequestId || 0) + 1;
    this._sheetOpenRequestId = requestId;
    const authorityRequestId = this._loadRequestId;
    api.vehicleInspectionTemplate().then(items => {
      if (!this._alive || !this.data.authorityFresh || requestId !== this._sheetOpenRequestId
        || authorityRequestId !== this._loadRequestId) return;
      if (!Array.isArray(items) || !items.length) throw new Error('检查模板不可用');
      this.setData({ checkoutSheet: { open: true, application, mileage: String((vehicle && vehicle.current_mileage) || ''), remarks: '', items, submitting: false,
        inspectionKey: 'vehicle_dispatch_inspection_' + application.id + '_' + Date.now(), recordKey: 'vehicle_checkout_' + application.id + '_' + Date.now() } });
    }).catch(err => {
      if (!this._alive || requestId !== this._sheetOpenRequestId) return;
      wx.showToast({ title: (err && err.error) || '出车检查模板加载失败，请重试', icon: 'none' });
    });
  },
  onCloseCheckout() {
    if (this.data.checkoutSheet.submitting) { wx.showToast({ title: '出车登记中，请稍候', icon: 'none' }); return; }
    this.setData({ 'checkoutSheet.open': false });
  },
  onCheckoutMileage(e) { this.setData({ 'checkoutSheet.mileage': e.detail.value }); },
  onCheckoutRemarks(e) { this.setData({ 'checkoutSheet.remarks': e.detail.value }); },
  onCheckoutItem(e) { const { index, status } = e.currentTarget.dataset; this.setData({ ['checkoutSheet.items[' + index + '].status']: status }); },
  onSubmitCheckout() {
    if (!this._ensureWritable()) return;
    const sheet = this.data.checkoutSheet; const mileage = Number(sheet.mileage);
    if (!sheet.application || !Number.isFinite(mileage) || mileage < 0) { wx.showToast({ title: '请填写出车时里程', icon: 'none' }); return; }
    const blocked = sheet.items.some(item => item.status === 'blocked'); const attention = sheet.items.some(item => item.status === 'attention');
    if ((blocked || attention) && !sheet.remarks.trim()) { wx.showToast({ title: '发现异常时请填写现场说明', icon: 'none' }); return; }
    this.setData({ 'checkoutSheet.submitting': true });
    api.submitVehicleInspection({ vehicle_id: sheet.application.vehicle_id, inspection_type: 'dispatch', odometer: mileage, overall_status: blocked ? 'blocked' : (attention ? 'attention' : 'normal'), items: sheet.items, remarks: sheet.remarks, _idempotency_key: sheet.inspectionKey })
      .then(check => api.checkOutVehicle({ application_id: sheet.application.id, start_mileage: mileage, out_inspection_id: check.id, _idempotency_key: sheet.recordKey }))
      .then(() => { this.setData({ 'checkoutSheet.open': false }); wx.showToast({ title: '已完成出车登记', icon: 'success' }); this.load(); })
      .catch(err => { this.setData({ 'checkoutSheet.submitting': false }); wx.showToast({ title: (err && err.error) || '出车登记失败', icon: 'none' }); });
  },

  onOpenRefuel() { if (!this._ensureWritable()) return; const use = this.data.activeUse; if (!use) { wx.showToast({ title: '未找到唯一进行中的行程', icon: 'none' }); return; } const energy = energyMeta(use.fuel_type); this.setData({ refuelSheet: { open: true, quantity: '', amount: '', mileage: String(use.start_mileage || ''), remark: '', label: energy.label, unit: energy.unit, requestKey: 'vehicle_refuel_' + use.id + '_' + Date.now(), submitting: false } }); },
  onCloseRefuel() {
    if (this.data.refuelSheet.submitting) { wx.showToast({ title: '正在处理中，请稍候', icon: 'none' }); return; }
    this.setData({ 'refuelSheet.open': false });
  },
  onRefuelField(e) { this.setData({ ['refuelSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitRefuel() {
    if (!this._ensureWritable()) return;
    const use = this.data.activeUse; const sheet = this.data.refuelSheet;
    if (sheet.submitting) return;
    const quantity = requiredFiniteNumber(sheet.quantity);
    const amount = requiredFiniteNumber(sheet.amount);
    const mileage = requiredFiniteNumber(sheet.mileage);
    if (!use || !(quantity > 0) || !(amount > 0) || mileage === null || mileage < Number(use.start_mileage || 0)) { wx.showToast({ title: '请填写有效补给量、金额和当前里程', icon: 'none' }); return; }
    this.setData({ 'refuelSheet.submitting': true });
    api.refuelVehicleUse(use.id, { energy_quantity: quantity, amount, mileage_at: mileage, remark: sheet.remark, _idempotency_key: sheet.requestKey })
      .then(() => { this.setData({ 'refuelSheet.open': false }); wx.showToast({ title: sheet.label + '记录已保存', icon: 'success' }); })
      .catch(err => { this.setData({ 'refuelSheet.submitting': false }); wx.showToast({ title: (err && err.error) || (sheet.label + '记录失败'), icon: 'none' }); });
  },

  onOpenFault() { if (!this._ensureWritable()) return; const use = this.data.activeUse; if (!use) { wx.showToast({ title: '未找到唯一进行中的行程', icon: 'none' }); return; } this.setData({ faultSheet: { open: true, faultType: '车辆故障', mileage: String(use.start_mileage || ''), description: '', remark: '', affectsSafeOperation: null, safetyLabel: '请选择', requestKey: 'vehicle_fault_' + use.id + '_' + Date.now(), submitting: false } }); },
  onCloseFault() {
    if (this.data.faultSheet.submitting) { wx.showToast({ title: '正在处理中，请稍候', icon: 'none' }); return; }
    this.setData({ 'faultSheet.open': false });
  },
  onFaultField(e) { this.setData({ ['faultSheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onFaultSafetyPick(e) {
    const index = eventOptionIndex(e, this.data.safetyOptions.length);
    if (index === null) { wx.showToast({ title: '安全影响选项无效，请重试', icon: 'none' }); return; }
    const option = this.data.safetyOptions[index];
    this.setData({ 'faultSheet.affectsSafeOperation': option.value, 'faultSheet.safetyLabel': option.label });
  },
  onSubmitFault() {
    if (!this._ensureWritable()) return;
    const use = this.data.activeUse; const sheet = this.data.faultSheet;
    if (sheet.submitting) return;
    if (!use || !sheet.description.trim()) { wx.showToast({ title: '请填写故障现象', icon: 'none' }); return; }
    const mileage = requiredFiniteNumber(sheet.mileage);
    if (mileage === null || mileage < Number(use.start_mileage || 0)) { wx.showToast({ title: '请填写有效的故障里程', icon: 'none' }); return; }
    if (typeof sheet.affectsSafeOperation !== 'boolean') { wx.showToast({ title: '请选择是否影响安全行驶', icon: 'none' }); return; }
    this.setData({ 'faultSheet.submitting': true });
    api.reportVehicleFault(use.id, { fault_type: sheet.faultType, mileage_at: mileage, description: sheet.description.trim(), remark: sheet.remark, affects_safe_operation: sheet.affectsSafeOperation, _idempotency_key: sheet.requestKey })
      .then(result => {
        this.setData({ 'faultSheet.open': false });
        let content = '故障已记录，车辆状态未被本次上报限制。';
        if (sheet.affectsSafeOperation) content = '车辆已限制使用，请尽快安全还车并等待维修安排。';
        else if (result && result.vehicle_status === 'restricted') content = '故障已记录；车辆此前已受限，仍保持限制使用。';
        wx.showModal({ title: '故障已上报', content, showCancel: false });
        this.load();
      })
      .catch(err => { this.setData({ 'faultSheet.submitting': false }); wx.showToast({ title: (err && err.error) || '故障上报失败', icon: 'none' }); });
  },

  onOpenReturn() {
    if (!this._ensureWritable()) return;
    const use = this.data.activeUse; if (!use) { wx.showToast({ title: '未找到唯一进行中的行程', icon: 'none' }); return; }
    if (!use.can_return) { wx.showToast({ title: '计划行程未结束，到站打卡会继续记录行程节点', icon: 'none' }); return; }
    const requestId = (this._sheetOpenRequestId || 0) + 1;
    this._sheetOpenRequestId = requestId;
    const authorityRequestId = this._loadRequestId;
    api.vehicleInspectionTemplate().then(items => {
      if (!this._alive || !this.data.authorityFresh || requestId !== this._sheetOpenRequestId
        || authorityRequestId !== this._loadRequestId) return;
      if (!Array.isArray(items) || !items.length) throw new Error('检查模板不可用');
      this.setData({ returnSheet: { open: true, mileage: String(use.start_mileage || ''), remarks: '', items, submitting: false,
        inspectionKey: 'vehicle_return_inspection_' + use.id + '_' + Date.now(), recordKey: 'vehicle_return_' + use.id + '_' + Date.now() } });
    }).catch(err => {
      if (!this._alive || requestId !== this._sheetOpenRequestId) return;
      wx.showToast({ title: (err && err.error) || '还车检查模板加载失败，请重试', icon: 'none' });
    });
  },
  onVehiclePrimaryAction() {
    if (this.data.activeUse && this.data.activeUse.needs_extension) this.onOpenExtension();
    else if (this.data.activeUse && this.data.activeUse.can_return) this.onOpenReturn();
    else this.onOpenPlanChange();
  },
  onOpenExtension(e) {
    if (!this._ensureWritable()) return;
    const eventId = e && e.currentTarget && e.currentTarget.dataset.id;
    let applicationId = eventId;
    if (!applicationId && this.data.activeUse && this.data.activeUse.needs_extension) {
      applicationId = this.data.activeUse.application_id;
    }
    if (!applicationId && this.data.activeArrangement && this.data.activeArrangement.needs_extension) {
      applicationId = this.data.activeArrangement.id;
    }
    this._openExtensionFor(applicationId);
  },
  _openExtensionFor(applicationId) {
    let application = (this.data.applications || []).find(
      item => String(item.id) === String(applicationId),
    );
    if (!application && this.data.activeUse
      && String(this.data.activeUse.application_id) === String(applicationId)
      && this.data.activeUse.needs_extension) {
      application = { id: this.data.activeUse.application_id, needs_extension: true };
    }
    if (!application || !application.needs_extension) {
      wx.showToast({ title: '该用车安排当前无需延续，请刷新后查看', icon: 'none' });
      return;
    }
    this._extensionIdempotencyKey = `vehicle-extension-${application.id}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.setData({ extensionSheet: { open: true, applicationId: application.id, endDate: todayStr(), submitting: false } });
  },
  onCloseExtension() {
    if (this.data.extensionSheet.submitting) { wx.showToast({ title: '正在处理中，请稍候', icon: 'none' }); return; }
    this.setData({ 'extensionSheet.open': false });
  },
  onExtensionDate(e) { this.setData({ 'extensionSheet.endDate': e.detail.value }); },
  onSubmitExtension() {
    return this._submitExtension(false);
  },
  _submitExtension(confirmConflicts) {
    if (!this._ensureWritable()) return;
    const sheet = this.data.extensionSheet;
    if (!sheet.applicationId || !sheet.endDate) { wx.showToast({ title: '请选择延续截止日期', icon: 'none' }); return; }
    this.setData({ 'extensionSheet.submitting': true });
    api.extendVehicleApplication(sheet.applicationId, sheet.endDate, {
      confirmConflicts: !!confirmConflicts,
      idempotencyKey: this._extensionIdempotencyKey,
    })
      .then(() => {
        this.setData({ 'extensionSheet.open': false, 'extensionSheet.submitting': false });
        this._extensionIdempotencyKey = '';
        wx.showToast({ title: '用车时间已延续', icon: 'success' });
        this.load();
      })
      .catch(err => {
        this.setData({ 'extensionSheet.submitting': false });
        if (err && err.code === 'VEHICLE_EXTENSION_CONFIRM_REQUIRED') {
          const conflicts = Array.isArray(err.conflicts) ? err.conflicts : [];
          const names = [...new Set(conflicts.map(item => item.responsible_name).filter(Boolean))];
          wx.showModal({
            title: '后续计划车辆将冲突',
            content: `${conflicts.length} 项后续安排需要更换车辆${names.length ? `，负责人：${names.join('、')}` : ''}。确认延期并通知相关人员？`,
            confirmText: '确认延期',
            success: result => { if (result.confirm) this._submitExtension(true); },
          });
          return;
        }
        wx.showToast({ title: (err && (err.error || err.message)) || '延续失败，请重试', icon: 'none' });
      });
  },
  onOpenPlanChange() {
    const source = this.data.activeUse || this.data.activeArrangement;
    if (!source || !source.plan_schedule_id) { wx.showToast({ title: '未找到关联巡检计划', icon: 'none' }); return; }
    wx.navigateTo({ url: '/pages/plan-detail/plan-detail?id=' + source.plan_schedule_id });
  },
  onCloseReturn() {
    if (this.data.returnSheet.submitting) { wx.showToast({ title: '还车登记中，请稍候', icon: 'none' }); return; }
    this.setData({ 'returnSheet.open': false });
  },
  onReturnMileage(e) { this.setData({ 'returnSheet.mileage': e.detail.value }); },
  onReturnRemarks(e) { this.setData({ 'returnSheet.remarks': e.detail.value }); },
  onReturnItem(e) { const { index, status } = e.currentTarget.dataset; this.setData({ ['returnSheet.items[' + index + '].status']: status }); },
  onSubmitReturn() {
    if (!this._ensureWritable()) return;
    const use = this.data.activeUse; const sheet = this.data.returnSheet; const mileage = Number(sheet.mileage);
    if (use && !use.can_return) { wx.showToast({ title: '计划行程未结束，请先发起计划变更', icon: 'none' }); return; }
    if (!use || !Number.isFinite(mileage) || mileage < Number(use.start_mileage || 0)) { wx.showToast({ title: '结束里程不能小于出车里程', icon: 'none' }); return; }
    const blocked = sheet.items.some(item => item.status === 'blocked'); const attention = sheet.items.some(item => item.status === 'attention');
    if ((blocked || attention) && !sheet.remarks.trim()) { wx.showToast({ title: '发现异常时请填写现场说明', icon: 'none' }); return; }
    this.setData({ 'returnSheet.submitting': true });
    api.submitVehicleInspection({ vehicle_id: use.vehicle_id, inspection_type: 'return', odometer: mileage, overall_status: blocked ? 'blocked' : (attention ? 'attention' : 'normal'), items: sheet.items, remarks: sheet.remarks, _idempotency_key: sheet.inspectionKey })
      .then(check => api.returnVehicle(use.id, { end_mileage: mileage, return_inspection_id: check.id, _idempotency_key: sheet.recordKey }))
      .then(res => { this.setData({ 'returnSheet.open': false }); wx.showModal({ title: '还车已登记', content: res.vehicle_status === 'restricted' ? '车辆已限制使用，等待维修处理。' : '行程已归档，车辆恢复可用。', showCancel: false }); this.load(); })
      .catch(err => { this.setData({ 'returnSheet.submitting': false }); wx.showToast({ title: (err && err.error) || '还车登记失败', icon: 'none' }); });
  },

  onOpenApply() {
    if (!this._ensureWritable()) return;
    if (!this.data.sitesAuthorityFresh) {
      wx.showToast({ title: this.data.sitesLoadError ? '站点信息加载失败，请刷新后重试' : '站点信息加载中，请稍候', icon: 'none' });
      return;
    }
    if (!(this.data.vehicles || []).length) { wx.showToast({ title: '当前没有可调度车辆，请联系管理员', icon: 'none' }); return; }
    const hasSites = (this.data.sites || []).length > 0;
    this.setData({ applySheet: { open: true, vehicleIndex: 0, startDate: todayStr(), startTime: '08:00', endDate: todayStr(), endTime: '18:00', destinationMode: hasSites ? 'site' : 'other', destinationModeIndex: hasSites ? 0 : 1, siteIndex: 0, otherDestination: '', reason: '', requestKey: 'vehicle_application_' + Date.now(), submitting: false } });
  },
  onCloseApply() {
    if (this.data.applySheet.submitting) { wx.showToast({ title: '正在处理中，请稍候', icon: 'none' }); return; }
    this.setData({ 'applySheet.open': false });
  },
  onApplyPick(e) { this.setData({ 'applySheet.vehicleIndex': Number(e.detail.value) || 0 }); },
  onApplyDestinationMode(e) {
    const index = eventOptionIndex(e, this.data.destinationModes.length);
    if (index === null) { wx.showToast({ title: '目的地选项无效，请重试', icon: 'none' }); return; }
    const option = this.data.destinationModes[index];
    this.setData({ 'applySheet.destinationModeIndex': index, 'applySheet.destinationMode': option.value });
  },
  onApplySitePick(e) { this.setData({ 'applySheet.siteIndex': Number(e.detail.value) || 0 }); },
  onApplyField(e) { this.setData({ ['applySheet.' + e.currentTarget.dataset.field]: e.detail.value }); },
  onSubmitApply() {
    if (!this._ensureWritable()) return;
    const sheet = this.data.applySheet; const vehicle = this.data.vehicles[sheet.vehicleIndex];
    if (sheet.submitting) return;
    const site = this.data.sites[sheet.siteIndex];
    const otherDestination = String(sheet.otherDestination || '').trim();
    if (!vehicle || !sheet.startDate || !sheet.endDate || !sheet.reason.trim()) { wx.showToast({ title: '请选择车辆并填写完整时间和事由', icon: 'none' }); return; }
    if (sheet.destinationMode === 'site' && !site) { wx.showToast({ title: '请选择授权站点', icon: 'none' }); return; }
    if (sheet.destinationMode === 'other' && (!otherDestination || otherDestination.length > 100)) { wx.showToast({ title: otherDestination.length > 100 ? '其他地点不能超过100字' : '请填写其他地点', icon: 'none' }); return; }
    const startAt = sheet.startDate + ' ' + sheet.startTime + ':00';
    const endAt = sheet.endDate + ' ' + sheet.endTime + ':00';
    if (endAt <= startAt) { wx.showToast({ title: '结束时间应晚于开始时间', icon: 'none' }); return; }
    this.setData({ 'applySheet.submitting': true });
    api.applyVehicle({ vehicle_id: vehicle.id, start_at: startAt, end_at: endAt, destination_mode: sheet.destinationMode, site_id: sheet.destinationMode === 'site' ? site.id : null, destination: sheet.destinationMode === 'other' ? otherDestination : '', reason: sheet.reason.trim(), _idempotency_key: sheet.requestKey })
      .then(() => { this.setData({ 'applySheet.open': false }); wx.showToast({ title: '用车申请已提交', icon: 'success' }); this.load(); })
      .catch(err => { this.setData({ 'applySheet.submitting': false }); wx.showToast({ title: (err && err.error) || '提交申请失败', icon: 'none' }); });
  }
});
