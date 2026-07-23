const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const { chooseAndCompress, fileToBase64 } = require('../../utils/photos.js');
const { resolveUploadUrl } = require('../../utils/url.js');

const app = getApp();

function getGps() {
  return new Promise((resolve) => {
    wx.getLocation({
      type: 'gcj02',
      success(res) { resolve({ lat: res.latitude, lng: res.longitude }); },
      fail() { resolve(null); }
    });
  });
}

// 状态分组的筛选映射（覆盖后端全部 7 种状态，避免漏显）
const TAB_GROUPS = {
  all: null,
  pending: ['pending'],
  accepted: ['accepted', 'dispatched'],
  in_progress: ['in_progress'],
  reviewing: ['reviewing'],
  closed: ['closed', 'resolved']
};

Page({
  data: {
    list: [], all: [], loaded: false,
    tab: 'all',
    tabs: [
      { key: 'all', label: '全部' },
      { key: 'pending', label: '待受理' },
      { key: 'accepted', label: '已受理' },
      { key: 'in_progress', label: '处置中' },
      { key: 'reviewing', label: '审核中' },
      { key: 'closed', label: '已完成' }
    ],
    sheet: { open: false, item: null }, isAdmin: false, canWrite: false, acting: false,
    // 关联下拉选项（可选，不指定则纯文字兜底）
    vehicleOptions: [{ id: 0, label: '不指定（仅填事由）' }],
    partsOptions: [{ id: 0, label: '手动输入（自定义名称）' }],
    // 极简申请弹层（含关联下标）
    vehicleApply: { open: false, reason: '', index: 0 },
    partsApply: { open: false, part_name: '', quantity: 1, reason: '', index: 0 }
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    const u = getUser() || {};
    const role = u.role || '';
    this.setData({
      isAdmin: role === 'admin',
      canWrite: role === 'admin' || role === 'operator'
    });
    this.load();
    this.loadLists();
  },

  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  filter(all, tab) {
    const set = TAB_GROUPS[tab];
    if (!set) return all;
    return all.filter(w => set.indexOf(w.status) >= 0);
  },

  load(one) {
    api.workorders()
      .then(res => {
        const all = (res || []).map(maps.workorderCn);
        this.setData({ all, list: this.filter(all, this.data.tab), loaded: true });
        if (one) one();
      })
      .catch(() => { this.setData({ loaded: true }); if (one) one(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  onTab(e) {
    const tab = e.currentTarget.dataset.t;
    if (tab === this.data.tab) return;
    this.setData({ tab, list: this.filter(this.data.all, tab) });
  },

  onOpen(e) {
    const no = e.currentTarget.dataset.no;
    const item = this.data.list.find(w => w.order_no === no);
    if (item) {
      const stepMap = { pending: 1, accepted: 2, in_progress: 3, reviewing: 4, closed: 5 };
      const step = stepMap[item.status] || 0;
      this.setData({ sheet: { open: true, item: Object.assign({}, item, { step }) } });
    }
  },
  onClose() { this.setData({ 'sheet.open': false }); },
  onCloseVehicle() { this.setData({ 'vehicleApply.open': false }); },
  onCloseParts() { this.setData({ 'partsApply.open': false }); },

  afterAction(tip) {
    this.setData({ acting: false, 'sheet.open': false, vehicleApply: { open: false, reason: '', index: 0 }, partsApply: { open: false, part_name: '', quantity: 1, reason: '', index: 0 } });
    wx.showToast({ title: tip, icon: 'success' });
    this.load();
  },

  doAccept() {
    const no = this.data.sheet.item.order_no;
    this.setData({ acting: true });
    api.updateWorkorderStatus(no, 'accepted')
      .then(() => this.afterAction('已接单'))
      .catch((err) => { this.setData({ acting: false }); wx.showToast({ title: (err && err.error) || '操作失败', icon: 'none' }); });
  },

  // 到场签到：GPS 围栏由后端校验（距站点 ≤500m）
  onCheckIn() {
    const item = this.data.sheet.item;
    if (!item) return;
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      const payload = { order_no: item.order_no, site_id: item.site_id, site_name: item.site_name, check_time: nowStr() };
      if (gps) { payload.lat = gps.lat; payload.lng = gps.lng; }
      api.checkIn(payload)
        .then(() => {
          wx.showToast({ title: '已到场签到', icon: 'success' });
          this.setData({ 'sheet.item.check_in_time': nowStr(), 'sheet.item.checked_in': true });
        })
        .catch((err) => { wx.showToast({ title: (err && err.error) || '签到失败', icon: 'none' }); });
    });
  },

  doStart() {
    const item = this.data.sheet.item;
    if (!item.checked_in) { wx.showToast({ title: '请先到场签到', icon: 'none' }); return; }
    const no = item.order_no;
    this.setData({ acting: true });
    api.updateWorkorderStatus(no, 'in_progress', { client: 'mobile' })
      .then(() => this.afterAction('处置中'))
      .catch((err) => { this.setData({ acting: false }); wx.showToast({ title: (err && err.error) || '操作失败', icon: 'none' }); });
  },

  // 处置影像上传（追加到工单 images）
  onUploadImage() {
    const item = this.data.sheet.item;
    if (!item) return;
    const remain = 6 - (item.images_arr ? item.images_arr.length : 0);
    if (remain <= 0) { wx.showToast({ title: '最多 6 张', icon: 'none' }); return; }
    chooseAndCompress(remain)
      .then(paths => {
        if (!paths.length) return;
        wx.showLoading({ title: '上传中' });
        const tasks = paths.map(p => fileToBase64(p).then(b64 => api.uploadWorkorderImage(item.order_no, b64).then(r => resolveUploadUrl(r.url))));
        Promise.allSettled(tasks).then(results => {
          wx.hideLoading();
          const urls = results.filter(r => r.status === 'fulfilled' && r.value).map(r => r.value);
          if (urls.length) {
            const arr = (this.data.sheet.item.images_arr || []).concat(urls);
            this.setData({ 'sheet.item.images_arr': arr, 'sheet.item.has_images': arr.length > 0, 'sheet.item.images': JSON.stringify(arr) });
          }
          if (results.some(r => r.status === 'rejected')) wx.showToast({ title: '部分上传失败', icon: 'none' });
        }).catch(() => wx.hideLoading());
      }).catch(() => {});
  },

  onPreviewImage(e) {
    const src = e.currentTarget.dataset.src;
    const urls = this.data.sheet.item.images_arr || [];
    wx.previewImage({ urls, current: src });
  },

  doReview() {
    const item = this.data.sheet.item;
    if (!item.has_images) { wx.showToast({ title: '请先上传处置影像', icon: 'none' }); return; }
    const no = item.order_no;
    this.setData({ acting: true });
    api.submitWorkorderReview(no)
      .then(() => this.afterAction('已提交核验'))
      .catch((err) => { this.setData({ acting: false }); wx.showToast({ title: (err && err.error) || '操作失败', icon: 'none' }); });
  },

  doApprove() {
    const no = this.data.sheet.item.order_no;
    this.setData({ acting: true });
    api.approveWorkorder(no)
      .then(() => this.afterAction('已核验通过'))
      .catch((err) => { this.setData({ acting: false }); wx.showToast({ title: (err && err.error) || '操作失败', icon: 'none' }); });
  },

  doReject() {
    const no = this.data.sheet.item.order_no;
    wx.showModal({
      title: '退回修改',
      content: '确认将工单退回给一线重新处置吗？',
      confirmText: '退回',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ acting: true });
        api.rejectWorkorder(no)
          .then(() => this.afterAction('已退回'))
          .catch((err) => { this.setData({ acting: false }); wx.showToast({ title: (err && err.error) || '操作失败', icon: 'none' }); });
      }
    });
  },

  // ---- 关联下拉数据（可选，不指定则纯文字兜底） ----
  loadLists() {
    Promise.all([api.vehicles(), api.partsInventory()])
      .then(([vs, ps]) => {
        const vehicleOptions = [{ id: 0, label: '不指定（仅填事由）' }].concat((vs || []).map(v => ({
          id: v.id,
          label: (v.plate_no || '未上牌') + (v.model ? ' · ' + v.model : '')
        })));
        const partsOptions = [{ id: 0, label: '手动输入（自定义名称）' }].concat((ps || []).map(p => ({
          id: p.id,
          part_name: p.part_name,
          label: p.part_name + (p.part_code ? '（' + p.part_code + '）' : '') + ' 余' + p.quantity
        })));
        this.setData({ vehicleOptions, partsOptions });
      })
      .catch(() => {});
  },

  // ---- 极简申请弹层（可选关联车辆/库存备件） ----
  onApplyVehicle() { this.setData({ 'vehicleApply.open': true, 'vehicleApply.reason': '', 'vehicleApply.index': 0 }); },
  onVehicleReason(e) { this.setData({ 'vehicleApply.reason': e.detail.value }); },
  onVehiclePick(e) { this.setData({ 'vehicleApply.index': parseInt(e.detail.value, 10) }); },
  submitVehicle() {
    const va = this.data.vehicleApply;
    const reason = (va.reason || '').trim();
    if (!reason) { wx.showToast({ title: '请填写用车事由', icon: 'none' }); return; }
    const item = this.data.sheet.item;
    const opt = this.data.vehicleOptions[va.index];
    const vehicle_id = (opt && opt.id) ? opt.id : null;
    wx.showLoading({ title: '提交中' });
    api.applyVehicle({ site_id: item.site_id, work_order_no: item.order_no, reason, vehicle_id })
      .then(() => { wx.hideLoading(); wx.showToast({ title: '用车申请已提交', icon: 'success' }); this.setData({ 'vehicleApply.open': false }); })
      .catch((err) => { wx.hideLoading(); wx.showToast({ title: (err && err.error) || '提交失败', icon: 'none' }); });
  },

  onApplyParts() { this.setData({ 'partsApply.open': true, 'partsApply.part_name': '', 'partsApply.quantity': 1, 'partsApply.reason': '', 'partsApply.index': 0 }); },
  onPartsName(e) { this.setData({ 'partsApply.part_name': e.detail.value }); },
  onPartsQty(e) { this.setData({ 'partsApply.quantity': e.detail.value }); },
  onPartsReason(e) { this.setData({ 'partsApply.reason': e.detail.value }); },
  onPartsPick(e) {
    const idx = parseInt(e.detail.value, 10);
    const opt = this.data.partsOptions[idx];
    const patch = { 'partsApply.index': idx };
    if (opt && opt.id) patch['partsApply.part_name'] = opt.part_name || '';  // 选库存项自动带出名称
    this.setData(patch);
  },
  submitParts() {
    const pa = this.data.partsApply;
    const part_name = (pa.part_name || '').trim();
    const reason = (pa.reason || '').trim();
    if (!part_name) { wx.showToast({ title: '请填写备件名称', icon: 'none' }); return; }
    if (!reason) { wx.showToast({ title: '请填写申请事由', icon: 'none' }); return; }
    const item = this.data.sheet.item;
    const opt = this.data.partsOptions[pa.index];
    const spare_part_id = (opt && opt.id) ? opt.id : null;
    wx.showLoading({ title: '提交中' });
    api.applyParts({ site_id: item.site_id, work_order_no: item.order_no, part_name, quantity: pa.quantity || 1, reason, spare_part_id })
      .then(() => { wx.hideLoading(); wx.showToast({ title: '备件申请已提交', icon: 'success' }); this.setData({ 'partsApply.open': false }); })
      .catch((err) => { wx.hideLoading(); wx.showToast({ title: (err && err.error) || '提交失败', icon: 'none' }); });
  }
});
