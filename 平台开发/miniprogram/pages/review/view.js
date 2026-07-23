const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');

const app = getApp();

// 从带前缀的 id 取出纯数字（insp_12 / wo_pic_12 / spr_5 / photo_9 ...）
function numId(id) {
  return parseInt(String(id).replace(/[^0-9]/g, ''), 10) || 0;
}

function groupByLabel(list) {
  const map = {};
  list.forEach(it => {
    const k = it.source_label || it.source_type;
    if (!map[k]) map[k] = [];
    map[k].push(it);
  });
  return Object.keys(map).map(k => ({ label: k, items: map[k] }));
}

Page({
  data: {
    loading: true,
    groups: [],
    total: 0,
    rejectShow: false,
    rejectReason: '',
    curId: '',
    curType: '',
    submitting: false
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.load();
  },

  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  load(done) {
    this.setData({ loading: true });
    api.auditPending()
      .then(res => {
        const list = Array.isArray(res) ? res : [];
        this.setData({
          loading: false,
          total: list.length,
          groups: groupByLabel(list)
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loading: false, groups: [], total: 0 });
        if (done) done();
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  // 打开驳回原因弹窗
  onReject(e) {
    const id = e.currentTarget.dataset.id;
    const type = e.currentTarget.dataset.type;
    this.setData({ rejectShow: true, rejectReason: '', curId: id, curType: type });
  },

  onReasonInput(e) { this.setData({ rejectReason: e.detail.value }); },
  noop() {},
  closeReject() { this.setData({ rejectShow: false, rejectReason: '', curId: '', curType: '' }); },

  rejectConfirm() {
    const reason = (this.data.rejectReason || '').trim();
    if (!reason) { wx.showToast({ title: '请填写驳回原因', icon: 'none' }); return; }
    this._dispatch('reject', reason);
  },

  onApprove(e) {
    const id = e.currentTarget.dataset.id;
    const type = e.currentTarget.dataset.type;
    this.setData({ curId: id, curType: type });
    this._dispatch('approve', '');
  },

  _findItem(id) {
    let found = null;
    this.data.groups.forEach(g => g.items.forEach(it => { if (it.id === id) found = it; }));
    return found;
  },

  _dispatch(action, reason) {
    if (this.data.submitting) return;
    const item = this._findItem(this.data.curId);
    if (!item) return;
    const type = item.source_type;
    const nid = numId(item.id);
    this.setData({ submitting: true, rejectShow: false });

    let p;
    switch (type) {
      case 'inspection':
        p = api.reviewInspectionItem(nid, action === 'approve' ? 'approved' : 'rejected', reason);
        break;
      case 'workorder_status':
        p = action === 'approve' ? api.approveWorkorder(item.order_no) : api.rejectWorkorder(item.order_no);
        break;
      case 'workorder_photo':
      case 'photo_review':
        p = api.reviewPhoto(item.attachment_ids || [], action, reason);
        break;
      case 'parts_request':
        p = action === 'approve' ? api.approvePartsRequest(nid) : api.rejectPartsRequest(nid);
        break;
      case 'spare_part_request':
        p = action === 'approve' ? api.approveSparePart(nid) : api.rejectSparePart(nid);
        break;
      case 'vehicle_application':
        if (action !== 'approve') { wx.showToast({ title: '用车仅支持通过', icon: 'none' }); this.setData({ submitting: false }); return; }
        p = api.approveVehicle(nid);
        break;
      default:
        wx.showToast({ title: '未知类型', icon: 'none' });
        this.setData({ submitting: false });
        return;
    }

    p.then(() => {
      wx.showToast({ title: action === 'approve' ? '已通过' : '已驳回', icon: 'success' });
      this.setData({ submitting: false, rejectShow: false, rejectReason: '', curId: '', curType: '' });
      this.load();
    }).catch(err => {
      this.setData({ submitting: false });
      const msg = (err && err.errMsg) ? err.errMsg : '操作失败';
      wx.showModal({ title: '操作失败', content: String(msg).replace('request:fail ', ''), showCancel: false });
    });
  },

  goBack() { wx.navigateBack({ delta: 1 }); }
});
