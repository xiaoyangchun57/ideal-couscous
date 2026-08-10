const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { resolveUploadUrl } = require('../../utils/url.js');
const {
  approveItemIdsForPhotoSelection,
  getRiskyPhotoIds
} = require('../../utils/inspectionReviewDecision.js');
const { findReviewItem } = require('../../utils/notificationTarget.js');

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

function parsePhotoUrls(value) {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) { return []; }
}

function watermarkStatusLabel(status, captureSource) {
  if (captureSource !== 'watermark_album') return '非水印相册来源';
  if (status === 'recognized') return '水印文字已自动识别';
  return '水印自动识别未确认，请结合原图人工查看';
}

function decorateItem(item) {
  const details = Array.isArray(item.attachment_details) ? item.attachment_details : [];
  const paths = details.map(row => row.stored_path).concat(parsePhotoUrls(item.photo_urls));
  const photoUrls = paths.filter((path, index) => path && paths.indexOf(path) === index).map(resolveUploadUrl);
  const itemDetails = (Array.isArray(item.item_details) ? item.item_details : []).map(detail => {
    const detailPaths = parsePhotoUrls(detail.photo_urls);
    return Object.assign({}, detail, {
      photoUrls: detailPaths.filter((path, index) => path && detailPaths.indexOf(path) === index).map(resolveUploadUrl)
    });
  });
  const detailHint = {
    plan_schedule: '查看站点、路线与资源安排',
    inspection_batch: '查看检查项结果与统一影像',
    parts_request: '查看备件清单与履约方式',
    spare_part_request: '查看备件申请明细',
    vehicle_application: '查看车辆、时段与用途',
    workorder_review: '查看工单处置与证据',
    photo_review: '查看影像与风险标记',
    data_review: '查看指标、数值与自动审核结论'
  }[item.source_type] || '查看审批详情';
  const approveLabel = {
    plan_schedule: item.is_change ? '批准变更' : '批准计划',
    inspection_batch: '全部通过',
    parts_request: '批准备件',
    spare_part_request: '批准备件',
    vehicle_application: '批准用车',
    workorder_review: '通过办结',
    photo_review: '通过影像',
    data_review: '核准数据'
  }[item.source_type] || '通过';
  const rejectLabel = {
    plan_schedule: '退回计划',
    inspection_batch: '退回并整改',
    parts_request: '驳回备件',
    spare_part_request: '驳回备件',
    vehicle_application: '驳回用车',
    workorder_review: '退回工单',
    photo_review: '驳回影像',
    data_review: '驳回数据'
  }[item.source_type] || '驳回';
  const reviewPhotos = details.map(detail => Object.assign({}, detail, {
    url: resolveUploadUrl(detail.stored_path),
    itemLabel: detail.item_name || detail.description || '未关联检查项',
    categoryLabel: detail.recognized_category || detail.item_name || '待人工归类',
    classificationLabel: detail.classification_source === 'inspection_item' ? '按检查项自动归类' : '按水印文字自动归类',
    watermarkStatusLabel: watermarkStatusLabel(detail.watermark_status, detail.capture_source),
    selectedForReject: false
  }));
  return Object.assign({}, item, {
    detailOpen: item.source_type === 'photo_review' || item.source_type === 'inspection_batch', photoUrls, itemDetails, reviewPhotos,
    selectedRejectCount: 0, detailHint, approveLabel, rejectLabel
  });
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
    curAction: '',
    submitting: false,
    reviewTarget: null
  },

  onLoad(options) {
    const targetType = String(options && options.target_type || '').trim();
    const targetId = String(options && options.target_id || '').trim();
    const attachmentIds = String(options && options.target_attachment_ids || '')
      .split(',').map(id => id.trim()).filter(Boolean);
    if (targetType && targetId) {
      this.reviewTarget = { kind: 'review', reviewType: targetType, sourceId: targetId, attachmentIds };
      this.setData({ reviewTarget: this.reviewTarget });
    }
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
        const list = (Array.isArray(res) ? res : []).map(decorateItem);
        if (this.reviewTarget && this.reviewTarget.reviewType === 'data_review'
            && !findReviewItem(groupByLabel(list), this.reviewTarget)) {
          return api.dataReviewDetail(this.reviewTarget.sourceId).then(review => {
            list.push(decorateItem({
              source_type: 'data_review',
              source_label: '数据审核',
              id: 'dr_' + review.id,
              title: (review.site_name || '站点') + '数据人工复核',
              site_name: review.site_name || '',
              source_name: review.metric || '',
              submit_time: review.recorded_at || '',
              metric: review.metric || '',
              value: review.value,
              recorded_at: review.recorded_at || '',
              review_status_label: review.status || '',
              auto_reason: review.auto_reason || review.auto_result || '',
              smart_result: review.smart_result || '',
              remark: '请核对原始数值、采集时间和自动审核结论'
            }));
            return list;
          });
        }
        return list;
      })
      .then(list => {
        this.setData({
          loading: false,
          total: list.length,
          groups: groupByLabel(list)
        }, () => this._focusReviewTarget());
        if (done) done();
      })
      .catch(err => {
        this.setData({ loading: false, groups: [], total: 0 });
        if (done) done();
        if (this.reviewTarget) {
          wx.showModal({
            title: '无法打开审核对象',
            content: (err && err.error) || '当前账号无权访问审核列表，或该审核对象已不存在。',
            showCancel: false
          });
          return;
        }
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  // 打开驳回原因弹窗
  onReject(e) {
    const id = e.currentTarget.dataset.id;
    const type = e.currentTarget.dataset.type;
    this.setData({ rejectShow: true, rejectReason: '', curId: id, curType: type, curAction: 'reject' });
  },

  onReasonInput(e) { this.setData({ rejectReason: e.detail.value }); },
  noop() {},
  closeReject() { this.setData({ rejectShow: false, rejectReason: '', curId: '', curType: '', curAction: '' }); },

  rejectConfirm() {
    const reason = (this.data.rejectReason || '').trim();
    if (!reason) { wx.showToast({ title: '请填写驳回原因', icon: 'none' }); return; }
    this._dispatch(this.data.curAction || 'reject', reason);
  },

  onApprove(e) {
    const id = e.currentTarget.dataset.id;
    const type = e.currentTarget.dataset.type;
    this.setData({ curId: id, curType: type });
    this._dispatch('approve', '');
  },

  _focusReviewTarget() {
    if (!this.reviewTarget || this._reviewTargetHandled) return;
    const item = findReviewItem(this.data.groups, this.reviewTarget);
    if (!item) {
      this._reviewTargetHandled = true;
      wx.showModal({
        title: '无法打开审核对象',
        content: '该审核对象不存在、已处理，或当前账号无权访问。',
        showCancel: false
      });
      return;
    }
    const groups = this.data.groups.map(group => Object.assign({}, group, {
      items: group.items.map(row => row.id === item.id
        ? Object.assign({}, row, { targeted: true, detailOpen: true }) : row)
    }));
    this._reviewTargetHandled = true;
    this.setData({ groups }, () => wx.pageScrollTo({ selector: '.review-target', duration: 0 }));
  },

  onTogglePhotoReject(e) {
    const id = e.currentTarget.dataset.id;
    const photoId = Number(e.currentTarget.dataset.photoId);
    const groups = this.data.groups.map(group => Object.assign({}, group, {
      items: group.items.map(item => {
        if (item.id !== id) return item;
        const reviewPhotos = (item.reviewPhotos || []).map(photo => photo.id === photoId
          ? Object.assign({}, photo, { selectedForReject: !photo.selectedForReject }) : photo);
        return Object.assign({}, item, {
          reviewPhotos,
          selectedRejectCount: reviewPhotos.filter(photo => photo.selectedForReject).length
        });
      })
    }));
    this.setData({ groups });
  },

  onSubmitPhotoReview(e) {
    const id = e.currentTarget.dataset.id;
    const item = this._findItem(id);
    if (!item) return;
    this.setData({ curId: id, curType: item.source_type });
    const rejectedIds = (item.reviewPhotos || [])
      .filter(photo => photo.selectedForReject).map(photo => photo.id);
    const unresolvedRiskIds = getRiskyPhotoIds(item.reviewPhotos || [], rejectedIds);
    const continueSubmit = () => {
      if (item.selectedRejectCount > 0) {
        this.setData({ rejectShow: true, rejectReason: '', curAction: 'selective' });
        return;
      }
      this._dispatch('approve', '');
    };
    if (!unresolvedRiskIds.length) {
      continueSubmit();
      return;
    }
    wx.showModal({
      title: '确认风险影像',
      content: `仍有 ${unresolvedRiskIds.length} 张系统标红影像将被通过，请确认已逐张核对。`,
      confirmText: '已核对并继续',
      success: result => { if (result.confirm) continueSubmit(); }
    });
  },

  _findItem(id) {
    let found = null;
    this.data.groups.forEach(g => g.items.forEach(it => { if (it.id === id) found = it; }));
    return found;
  },

  onToggleDetails(e) {
    const id = e.currentTarget.dataset.id;
    const groups = this.data.groups.map(group => Object.assign({}, group, {
      items: group.items.map(item => item.id === id
        ? Object.assign({}, item, { detailOpen: !item.detailOpen }) : item)
    }));
    this.setData({ groups });
  },

  onPreviewPhoto(e) {
    const id = e.currentTarget.dataset.id;
    const item = this._findItem(id);
    if (item && item.photoUrls && item.photoUrls.length) {
      wx.previewImage({ urls: item.photoUrls, current: e.currentTarget.dataset.url });
    }
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
      case 'inspection_batch':
        if ((item.reviewPhotos || []).length) {
          const rejectIds = (item.reviewPhotos || []).filter(photo => photo.selectedForReject).map(photo => photo.id);
          const approveIds = (item.attachment_ids || []).filter(id => rejectIds.indexOf(id) < 0);
          const approveItemIds = approveItemIdsForPhotoSelection(
            item.item_ids || [], item.reviewPhotos || [], rejectIds);
          p = api.reviewInspectionPhotoSelection(approveIds, rejectIds, approveItemIds, reason);
        } else {
          p = api.reviewInspectionBatch(item.item_ids || [], action, reason);
        }
        break;
      case 'workorder_status':
      case 'workorder_review':
        p = action === 'approve' ? api.approveWorkorder(item.order_no) : api.rejectWorkorder(item.order_no, reason);
        break;
      case 'workorder_photo':
        p = api.reviewPhoto(item.attachment_ids || [], action, reason);
        break;
      case 'photo_review': {
        const rejectIds = (item.reviewPhotos || []).filter(photo => photo.selectedForReject).map(photo => photo.id);
        const approveIds = (item.attachment_ids || []).filter(id => rejectIds.indexOf(id) < 0);
        p = action === 'selective'
          ? api.reviewPhotoSelection(approveIds, rejectIds, reason)
          : api.reviewPhoto(item.attachment_ids || [], 'approve', '');
        break;
      }
      case 'parts_request':
        p = action === 'approve' ? api.approvePartsRequest(nid) : api.rejectPartsRequest(nid, reason);
        break;
      case 'spare_part_request':
        p = action === 'approve' ? api.approveSparePart(nid) : api.rejectSparePart(nid, reason);
        break;
      case 'vehicle_application':
        p = api.approveVehicle(nid, action, reason);
        break;
      case 'plan_schedule':
        p = action === 'approve' ? api.approvePlanSchedule(nid) : api.rejectPlanSchedule(nid, reason);
        break;
      case 'data_review':
        p = api.reviewDataReview(nid, action, reason);
        break;
      default:
        wx.showToast({ title: '未知类型', icon: 'none' });
        this.setData({ submitting: false });
        return;
    }

    p.then(() => {
      wx.showToast({ title: action === 'approve' ? '已通过' : '已驳回', icon: 'success' });
      this.setData({ submitting: false, rejectShow: false, rejectReason: '', curId: '', curType: '', curAction: '' });
      this.load();
    }).catch(err => {
      this.setData({ submitting: false });
      if ((type === 'workorder_review' || type === 'workorder_status') && action === 'approve'
          && err && err.code === 'EVIDENCE_ACKNOWLEDGEMENT_REQUIRED') {
        wx.showModal({
          title: '请确认影像风险',
          content: err.error || '影像存在重复或拍摄信息不完整，请查看详情后确认继续通过。',
          confirmText: '确认通过',
          success: result => {
            if (!result.confirm) return;
            this.setData({ submitting: true });
            api.approveWorkorder(item.order_no, { evidence_acknowledged: true })
              .then(() => { wx.showToast({ title: '已通过', icon: 'success' }); this.setData({ submitting: false }); this.load(); })
              .catch(retryErr => { this.setData({ submitting: false }); wx.showModal({ title: '操作失败', content: (retryErr && retryErr.error) || '请稍后重试', showCancel: false }); });
          }
        });
        return;
      }
      const msg = (err && err.errMsg) ? err.errMsg : '操作失败';
      wx.showModal({ title: '操作失败', content: String(msg).replace('request:fail ', ''), showCancel: false });
    });
  },

  goBack() { wx.navigateBack({ delta: 1 }); }
});
