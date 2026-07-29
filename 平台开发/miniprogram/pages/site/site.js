const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const { queueCount, flushQueue } = require('../../utils/request.js');

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

const PARTS_FULFILLMENT_OPTIONS = [
  { key: 'stock', label: '库存领用' },
  { key: 'local_purchase', label: '附近急购' },
  { key: 'vendor_order', label: '厂家订购' }
];

Page({
  data: {
    siteId: null, site: null, checkingIn: false, online: true, syncCount: 0,
    partsOptions: [{ id: 0, label: '手动输入（自定义名称）' }],
    partsFulfillmentOptions: PARTS_FULFILLMENT_OPTIONS,
    partsApply: {
      open: false, fulfillmentIndex: 0, fulfillment_type: 'stock',
      part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0,
      submitting: false
    }
  },

  onLoad(options) {
    const id = options.site_id || app.globalData.selSiteId;
    this.setData({ siteId: id });
    if (id) this.loadSite(id);
  },

  onShow() {
    this.refreshSyncState();
  },

  refreshSyncState(done) {
    wx.getNetworkType({
      success: (res) => {
        this.setData({ online: res.networkType !== 'none', syncCount: queueCount() });
        if (done) done();
      },
      fail: () => {
        this.setData({ syncCount: queueCount() });
        if (done) done();
      }
    });
  },

  onSyncNow() {
    if (!this.data.syncCount) return;
    wx.showLoading({ title: '同步中' });
    flushQueue();
    setTimeout(() => {
      wx.hideLoading();
      this.refreshSyncState(() => wx.showToast({
        title: this.data.syncCount ? '仍有操作待同步' : '同步完成',
        icon: this.data.syncCount ? 'none' : 'success'
      }));
    }, 1000);
  },

  loadSite(id) {
    Promise.all([api.siteTasks(id), api.partsInventory().catch(() => [])])
      .then(([res, parts]) => {
        const partsOptions = [{ id: 0, label: '手动输入（自定义名称）' }].concat((parts || []).map(part => ({
          id: part.id,
          part_name: part.part_name,
          label: (part.part_name || '备件') + (part.part_code ? '（' + part.part_code + '）' : '') + ' 余' + (part.quantity || 0)
        })));
        this.setData({ site: res.site || null, partsOptions });
      })
      .catch(() => wx.showToast({ title: '加载失败', icon: 'none' }));
  },

  onNavigate() {
    const s = this.data.site;
    if (!s || s.lat == null || s.lng == null) { wx.showToast({ title: '无坐标信息', icon: 'none' }); return; }
    wx.openLocation({
      latitude: s.lat, longitude: s.lng, name: s.name, address: s.code,
      fail() { wx.showToast({ title: '打开地图失败', icon: 'none' }); }
    });
  },

  onCheckIn() {
    const s = this.data.site;
    if (!s || this.data.checkingIn) return;
    this.setData({ checkingIn: true });
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      const payload = { site_id: s.id, site_name: s.name, check_time: nowStr() };
      if (gps) { payload.lat = gps.lat; payload.lng = gps.lng; }
      api.checkIn(payload)
        .then(() => wx.showToast({ title: '打卡成功', icon: 'success' }))
        .catch((err) => {
          this.setData({ syncCount: queueCount() });
          wx.showToast({ title: err && err.queued ? '已离线保存，联网后自动同步' : '打卡失败', icon: 'none' });
        })
        .finally(() => this.setData({ checkingIn: false }));
    });
  },

  onCalibrate() {
    const s = this.data.site;
    if (!s) return;
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      if (!gps) { wx.showToast({ title: '定位失败', icon: 'none' }); return; }
      api.calibrate(s.id, gps.lat, gps.lng)
        .then(res => {
          const d = (res && res.distance_m != null) ? res.distance_m : 0;
          wx.showToast({ title: '已校准 偏移' + d + 'm', icon: 'none' });
          this.loadSite(s.id);
        })
      .catch(() => wx.showToast({ title: '校准失败', icon: 'none' }));
    });
  },

  onOpenPartsApply() {
    this.setData({
      partsApply: {
        open: true, fulfillmentIndex: 0, fulfillment_type: 'stock',
        part_name: '', specification: '', estimated_amount: '', quantity: 1, reason: '', index: 0,
        submitting: false
      }
    });
  },

  onClosePartsApply() {
    if (!this.data.partsApply.submitting) this.setData({ 'partsApply.open': false });
  },

  onPartsFulfillmentPick(e) {
    const index = parseInt(e.detail.value, 10) || 0;
    const selected = this.data.partsFulfillmentOptions[index] || this.data.partsFulfillmentOptions[0];
    this.setData({
      'partsApply.fulfillmentIndex': index,
      'partsApply.fulfillment_type': selected.key,
      'partsApply.index': 0,
      'partsApply.part_name': selected.key === 'stock' ? '' : this.data.partsApply.part_name
    });
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
    const site = this.data.site;
    const form = this.data.partsApply;
    const partName = (form.part_name || '').trim();
    const reason = (form.reason || '').trim();
    const option = this.data.partsOptions[form.index];
    const sparePartId = form.fulfillment_type === 'stock' && option && option.id ? option.id : null;
    if (!site) return;
    if (!partName) { wx.showToast({ title: '请填写备件名称', icon: 'none' }); return; }
    if (!reason) { wx.showToast({ title: '请填写申请事由', icon: 'none' }); return; }
    if (form.fulfillment_type === 'stock' && !sparePartId) { wx.showToast({ title: '请选择库存备件', icon: 'none' }); return; }
    this.setData({ 'partsApply.submitting': true });
    api.applyParts({
      site_id: site.id,
      part_name: partName,
      specification: (form.specification || '').trim(),
      quantity: form.quantity || 1,
      reason,
      spare_part_id: sparePartId,
      fulfillment_type: form.fulfillment_type,
      estimated_amount: form.estimated_amount === '' ? null : Number(form.estimated_amount)
    }).then(() => {
      this.setData({ 'partsApply.open': false, 'partsApply.submitting': false });
      wx.showToast({ title: '备件需求已提交', icon: 'success' });
    }).catch(err => {
      this.setData({ 'partsApply.submitting': false });
      wx.showToast({ title: (err && err.error) || '提交失败', icon: 'none' });
    });
  }
});
