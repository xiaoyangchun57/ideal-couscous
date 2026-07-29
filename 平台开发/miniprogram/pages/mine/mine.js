const { getUser, getSites, clear } = require('../../utils/auth.js');
const maps = require('../../services/maps.js');
const api = require('../../services/api.js');
const { chooseAndCompress, fileToBase64 } = require('../../utils/photos.js');
const { todayStr } = require('../../utils/util.js');

const app = getApp();

Page({
  data: { realName: '', roleCn: '', phone: '', sitesCount: 0, unread: 0, partsRequests: [], partsSheet: { open: false }, partsIssueSheet: { open: false, request: null, quantity: 1, submitting: false }, partsOrderSheet: { open: false, request: null, supplier: '', tracking_no: '', submitting: false }, partsFulfillSheet: { open: false, request: null, supplier: '', actual_amount: '', receipt_no: '', destination: 'direct_use', old_part_disposition: '', evidence_urls: [], uploading: false, submitting: false }, activeVehicleUse: null, vehicleUses: [], vehicleHistoryOpen: false, returnSheet: { open: false, mileage: '', remarks: '', blocked: false, submitting: false } },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    const u = getUser() || {};
    this.setData({
      realName: u.real_name || '运维人员',
      roleCn: maps.map(maps.ROLE, u.role, '运维人员'),
      phone: u.phone || '未绑定',
      sitesCount: (getSites() || []).length
    });
    api.unreadCount()
      .then(r => this.setData({ unread: (r && r.count) || 0 }))
      .catch(() => {});
    api.myPartsRequests().then(rows => {
      const routeLabels = { stock: '库存领用', local_purchase: '附近急购', vendor_order: '厂家订购' };
      const statusLabels = { pending: '待审批', approved: '已批准', ordered: '运输中', issued: '已领用', completed: '已完成', rejected: '已驳回' };
      const partsRequests = (rows || []).map(row => Object.assign({}, row, {
        route_label: routeLabels[row.fulfillment_type] || '备件需求',
        status_label: statusLabels[row.status] || row.status,
        can_issue: row.fulfillment_type === 'stock' && row.status === 'approved' && (row.items || []).some(i => i.remaining_quantity > 0),
        can_order: row.fulfillment_type === 'vendor_order' && row.status === 'approved',
        can_fulfill: row.fulfillment_type !== 'stock' && ['approved', 'ordered'].includes(row.status)
      }));
      this.setData({ partsRequests });
    }).catch(() => {});
    api.vehicleUseRecords().then(rows => {
      const today = todayStr();
      const vehicleUses = (rows || []).map(row => {
        const tripEnd = row.end_at ? String(row.end_at).slice(0, 10) : '';
        const isPlanTrip = String(row.reason || '').indexOf('巡检计划#') >= 0;
        return Object.assign({}, row, {
          vehicle_label: (row.plate_no || '车辆') + (row.model ? (' · ' + row.model) : ''),
          is_plan_trip: isPlanTrip,
          trip_end_date: tripEnd,
          plan_schedule_id: row.plan_schedule_id || null,
          can_return: !isPlanTrip || !tripEnd || tripEnd <= today || row.vehicle_status === 'restricted'
        });
      });
      const activeVehicleUse = vehicleUses.find(row => !row.returned_at) || null;
      this.setData({ activeVehicleUse, vehicleUses });
    }).catch(() => this.setData({ activeVehicleUse: null, vehicleUses: [] }));
  },

  goMessage() { wx.switchTab({ url: '/pages/message/message' }); },
  goReports() { wx.navigateTo({ url: '/pages/reports/reports' }); },
  goReview() { wx.navigateTo({ url: '/pages/review/view' }); },
  goVehicle() { wx.navigateTo({ url: '/pages/vehicle/vehicle' }); },
  goPlanChange() {
    const use = this.data.activeVehicleUse;
    if (!use || !use.plan_schedule_id) { wx.showToast({ title: '未找到关联巡检计划', icon: 'none' }); return; }
    wx.navigateTo({ url: '/pages/plan-detail/plan-detail?id=' + use.plan_schedule_id });
  },
  onOpenParts() { this.setData({ 'partsSheet.open': true }); },
  onCloseParts() { this.setData({ 'partsSheet.open': false }); },
  onOpenPartsIssue(e) {
    const request = this.data.partsRequests.find(row => row.id === Number(e.currentTarget.dataset.id));
    this.setData({ partsIssueSheet: { open: true, request, quantity: 1, submitting: false } });
  },
  onClosePartsIssue() { this.setData({ 'partsIssueSheet.open': false }); },
  onIssueQty(e) { this.setData({ 'partsIssueSheet.quantity': e.detail.value }); },
  onIssueParts() {
    const request = this.data.partsIssueSheet.request;
    const item = request && (request.items || []).find(i => i.remaining_quantity > 0);
    const qty = Number(this.data.partsIssueSheet.quantity || 0);
    if (!request || !item || qty <= 0 || qty > item.remaining_quantity) { wx.showToast({ title: '领用数量不能超过申请余量', icon: 'none' }); return; }
    this.setData({ 'partsIssueSheet.submitting': true });
    api.issuePartsRequest(request.id, [{ part_id: item.part_id, quantity: qty }]).then(() => {
      this.setData({ 'partsIssueSheet.open': false, 'partsIssueSheet.submitting': false });
      this.onShow(); wx.showToast({ title: '已记录现场领用', icon: 'success' });
    }).catch(() => this.setData({ 'partsIssueSheet.submitting': false }));
  },

  onOpenPartsOrder(e) {
    const request = this.data.partsRequests.find(row => row.id === Number(e.currentTarget.dataset.id));
    this.setData({ partsOrderSheet: { open: true, request, supplier: '', tracking_no: '', submitting: false } });
  },
  onClosePartsOrder() { this.setData({ 'partsOrderSheet.open': false }); },
  onPartsOrderSupplier(e) { this.setData({ 'partsOrderSheet.supplier': e.detail.value }); },
  onPartsTracking(e) { this.setData({ 'partsOrderSheet.tracking_no': e.detail.value }); },
  onSubmitPartsOrder() {
    const sheet = this.data.partsOrderSheet;
    if (!(sheet.supplier || '').trim()) { wx.showToast({ title: '请填写供应商', icon: 'none' }); return; }
    this.setData({ 'partsOrderSheet.submitting': true });
    api.orderPartsRequest(sheet.request.id, { supplier: sheet.supplier.trim(), tracking_no: (sheet.tracking_no || '').trim() })
      .then(() => { this.setData({ 'partsOrderSheet.open': false }); this.onShow(); wx.showToast({ title: '已记录下单', icon: 'success' }); })
      .catch(() => this.setData({ 'partsOrderSheet.submitting': false }));
  },

  onOpenPartsFulfill(e) {
    const request = this.data.partsRequests.find(row => row.id === Number(e.currentTarget.dataset.id));
    this.setData({ partsFulfillSheet: { open: true, request, supplier: request.supplier || '', actual_amount: '', receipt_no: '', destination: 'direct_use', old_part_disposition: '', evidence_urls: [], uploading: false, submitting: false } });
  },
  onClosePartsFulfill() { this.setData({ 'partsFulfillSheet.open': false }); },
  onPartsActualAmount(e) { this.setData({ 'partsFulfillSheet.actual_amount': e.detail.value }); },
  onPartsFulfillSupplier(e) { this.setData({ 'partsFulfillSheet.supplier': e.detail.value }); },
  onPartsReceiptNo(e) { this.setData({ 'partsFulfillSheet.receipt_no': e.detail.value }); },
  onPartsDestination(e) { this.setData({ 'partsFulfillSheet.destination': e.detail.value }); },
  onOldPartDisposition(e) { this.setData({ 'partsFulfillSheet.old_part_disposition': e.detail.value }); },
  onUploadPartsEvidence() {
    this.setData({ 'partsFulfillSheet.uploading': true });
    chooseAndCompress(3).then(paths => Promise.all(paths.map(path => fileToBase64(path).then(image => api.uploadPartsEvidence(image)))))
      .then(results => this.setData({ 'partsFulfillSheet.evidence_urls': results.map(row => row.url), 'partsFulfillSheet.uploading': false }))
      .catch(() => { this.setData({ 'partsFulfillSheet.uploading': false }); wx.showToast({ title: '票据上传失败', icon: 'none' }); });
  },
  onSubmitPartsFulfill() {
    const sheet = this.data.partsFulfillSheet;
    if (sheet.actual_amount === '' || Number(sheet.actual_amount) < 0) { wx.showToast({ title: '请填写实际金额', icon: 'none' }); return; }
    if (!sheet.evidence_urls.length && !(sheet.receipt_no || '').trim()) { wx.showToast({ title: '请上传票据或填写票据编号', icon: 'none' }); return; }
    this.setData({ 'partsFulfillSheet.submitting': true });
    api.fulfillPartsRequest(sheet.request.id, {
      supplier: (sheet.supplier || '').trim(), actual_amount: Number(sheet.actual_amount),
      receipt_no: (sheet.receipt_no || '').trim(), destination: sheet.destination,
      old_part_disposition: (sheet.old_part_disposition || '').trim(), evidence_urls: sheet.evidence_urls
    }).then(() => { this.setData({ 'partsFulfillSheet.open': false }); this.onShow(); wx.showToast({ title: '到货台账已生成', icon: 'success' }); })
      .catch(() => this.setData({ 'partsFulfillSheet.submitting': false }));
  },

  onOpenReturnVehicle() {
    if (!this.data.activeVehicleUse) return;
    if (!this.data.activeVehicleUse.can_return) { wx.showToast({ title: '计划行程未结束，到站打卡会继续记录行程节点', icon: 'none' }); return; }
    this.setData({ returnSheet: { open: true, mileage: '', remarks: '', blocked: false, submitting: false } });
  },
  onCloseReturnVehicle() { this.setData({ 'returnSheet.open': false }); },
  onOpenVehicleHistory() { this.setData({ vehicleHistoryOpen: true }); },
  onCloseVehicleHistory() { this.setData({ vehicleHistoryOpen: false }); },
  onReturnMileage(e) { this.setData({ 'returnSheet.mileage': e.detail.value }); },
  onReturnRemarks(e) { this.setData({ 'returnSheet.remarks': e.detail.value }); },
  onReturnBlocked(e) { this.setData({ 'returnSheet.blocked': !!e.detail.value.length }); },
  onSubmitReturnVehicle() {
    const record = this.data.activeVehicleUse; const sheet = this.data.returnSheet;
    if (record && !record.can_return) { wx.showToast({ title: '计划行程未结束，请先发起计划变更', icon: 'none' }); return; }
    const mileage = Number(sheet.mileage);
    if (!record || !mileage || mileage < Number(record.start_mileage || 0)) { wx.showToast({ title: '结束里程不能小于出车里程', icon: 'none' }); return; }
    this.setData({ 'returnSheet.submitting': true });
    const status = sheet.blocked ? 'blocked' : (sheet.remarks.trim() ? 'attention' : 'normal');
    const items = ['灯光及信号灯', '后视镜', '漆面外观', '四轮磨损及胎压', '车内卫生'].map(key => ({ key, status: sheet.blocked ? 'blocked' : 'normal', remark: sheet.remarks }));
    api.submitVehicleInspection({ vehicle_id: record.vehicle_id, inspection_type: 'return', odometer: mileage, overall_status: status, items, remarks: sheet.remarks })
      .then(check => api.returnVehicle(record.id, { end_mileage: mileage, return_inspection_id: check.id }))
      .then(res => { this.setData({ 'returnSheet.open': false, activeVehicleUse: null }); wx.showModal({ title: '还车已登记', content: res.vehicle_status === 'restricted' ? '检查发现影响安全的问题，车辆已限制使用。' : '车辆已归还并恢复可用。', showCancel: false }); })
      .catch(() => { this.setData({ 'returnSheet.submitting': false }); wx.showToast({ title: '还车登记失败，请重试', icon: 'none' }); });
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出当前账号吗？',
      confirmText: '退出',
      success(res) {
        if (res.confirm) {
          clear();
          wx.reLaunch({ url: '/pages/login/login' });
        }
      }
    });
  }
});
