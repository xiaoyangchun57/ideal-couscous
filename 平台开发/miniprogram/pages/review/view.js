const api = require('../../services/api.js');
const { getUser } = require('../../utils/auth.js');
const { resolveUploadUrl } = require('../../utils/url.js');
const { approveItemIdsForPhotoSelection, groupReviewPhotosByItem } = require('../../utils/inspectionReviewDecision.js');
const { findReviewItem } = require('../../utils/notificationTarget.js');
const { reviewExecutionProjection } = require('../../utils/round1Wiring.js');
const {
  getSubmissionGuard,
  getRetryErrorMessage
} = require('../../utils/reviewSubmissionState.js');

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

function projectReviewPresentation(list) {
  const items = Array.isArray(list) ? list : [];
  const detailOpen = items.length === 1;
  const groups = groupByLabel(items.map(item => Object.assign({}, item, { detailOpen })));
  return {
    groups,
    showGroupHeaders: groups.length > 1
  };
}

function parsePhotoUrls(value) {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) { return []; }
}

function reviewItemKey(itemId, fallbackIndex) {
  return itemId !== undefined && itemId !== null && String(itemId).trim() !== ''
    ? 'item:' + String(itemId)
    : 'missing:' + fallbackIndex;
}

function reviewResultLabel(result) {
  if (result === 'normal') return '正常';
  if (result === 'abnormal') return '异常';
  if (result === undefined || result === null || String(result).trim() === '') return '未填写';
  return '状态待确认';
}

function reviewFailureMessage(error) {
  if (error && error.code === 'ATTACHMENT_ITEM_REQUIRED') {
    const photoIds = Array.isArray(error.photo_ids) && error.photo_ids.length
      ? error.photo_ids.join('、') : '未返回';
    return [
      error.error || '照片未完成检查项关联，原照片和本次选择均已保留',
      `附件ID：${photoIds}`,
      `缺少关联：${error.missing_relation || '检查项关联'}`,
      `处理方式：${error.next_action || '返回原检查项重新关联后再审核'}`,
    ].join('\n');
  }
  return getRetryErrorMessage(error);
}

function groupReviewItems(itemDetails, reviewPhotos) {
  const groups = [];
  const groupsByKey = new Map();
  (Array.isArray(itemDetails) ? itemDetails : []).forEach((detail, index) => {
    const source = detail || {};
    const key = reviewItemKey(source.id, index);
    if (groupsByKey.has(key)) return;
    const group = {
      key,
      itemId: source.id !== undefined && source.id !== null ? source.id : null,
      itemLabel: source.item_name || '检查项待确认',
      result: source.result || '',
      result_label: reviewResultLabel(source.result),
      remark: source.remark || '',
      photos: []
    };
    groupsByKey.set(key, group);
    groups.push(group);
  });

  let unknownGroup = null;
  (Array.isArray(reviewPhotos) ? reviewPhotos : []).forEach(photo => {
    const source = photo || {};
    const key = reviewItemKey(source.item_id, 'photo');
    const group = groupsByKey.get(key);
    if (group) {
      group.photos.push(source);
      return;
    }
    if (!unknownGroup) {
      unknownGroup = {
        key: 'unknown', itemId: null, itemLabel: '关联检查项暂不可用',
        result: '', result_label: reviewResultLabel(''), remark: '', photos: []
      };
      groups.push(unknownGroup);
    }
    unknownGroup.photos.push(source);
  });
  return groups;
}

function verifiedRelatedTaskLabel(item) {
  if (item.work_order_no) return '关联工单：' + item.work_order_no;
  if (item.plan_name) return '关联计划：' + item.plan_name;
  if (item.plan_schedule_id) return '关联巡检计划：' + item.plan_schedule_id;
  return '';
}

const REVIEW_BLOCK_REASON = '该审核类型暂不支持处理，请刷新或联系管理员';

const REVIEW_CAPABILITIES = {
  inspection: {
    dispatch(item, action, reason) {
      return api.reviewInspectionItem(numId(item.id), action === 'approve' ? 'approved' : 'rejected', reason);
    }
  },
  inspection_batch: {
    dispatch(item, action, reason) {
      if ((item.reviewPhotos || []).length) {
        const rejectIds = (item.reviewPhotos || []).filter(photo => photo.selectedForReject).map(photo => photo.id);
        const approveIds = (item.attachment_ids || []).filter(id => rejectIds.indexOf(id) < 0);
        const approveItemIds = approveItemIdsForPhotoSelection(
          item.item_ids || [], item.reviewPhotos || [], rejectIds);
        return api.reviewInspectionPhotoSelection(approveIds, rejectIds, approveItemIds, reason);
      }
      return api.reviewInspectionBatch(item.item_ids || [], action, reason);
    }
  },
  workorder_status: {
    dispatch(item, action, reason) {
      return action === 'approve' ? api.approveWorkorder(item.order_no) : api.rejectWorkorder(item.order_no, reason);
    }
  },
  workorder_review: {
    dispatch(item, action, reason) {
      return action === 'approve' ? api.approveWorkorder(item.order_no) : api.rejectWorkorder(item.order_no, reason);
    }
  },
  workorder_photo: {
    dispatch(item, action, reason) {
      return api.reviewPhoto(item.attachment_ids || [], action, reason);
    }
  },
  photo_review: {
    dispatch(item, action, reason) {
      const rejectIds = (item.reviewPhotos || []).filter(photo => photo.selectedForReject).map(photo => photo.id);
      const approveIds = (item.attachment_ids || []).filter(id => rejectIds.indexOf(id) < 0);
      return action === 'selective'
        ? api.reviewPhotoSelection(approveIds, rejectIds, reason)
        : api.reviewPhoto(item.attachment_ids || [], 'approve', '');
    }
  },
  parts_request: {
    dispatch(item, action, reason) {
      return action === 'approve' ? api.approvePartsRequest(numId(item.id)) : api.rejectPartsRequest(numId(item.id), reason);
    }
  },
  spare_part_request: {
    dispatch(item, action, reason) {
      return action === 'approve' ? api.approveSparePart(numId(item.id)) : api.rejectSparePart(numId(item.id), reason);
    }
  },
  vehicle_application: {
    dispatch(item, action, reason) {
      return api.approveVehicle(numId(item.id), action, reason);
    }
  },
  plan_schedule: {
    dispatch(item, action, reason) {
      return action === 'approve' ? api.approvePlanSchedule(numId(item.id)) : api.rejectPlanSchedule(numId(item.id), reason);
    }
  },
  data_review: {
    dispatch(item, action, reason) {
      return api.reviewDataReview(numId(item.id), action, reason);
    }
  }
};

function reviewCapability(sourceType) {
  return REVIEW_CAPABILITIES[String(sourceType || '').trim()] || null;
}

function decorateItem(item) {
  const capability = reviewCapability(item.source_type);
  const details = Array.isArray(item.attachment_details) ? item.attachment_details : [];
  const paths = details.map(row => row.display_url).concat(parsePhotoUrls(item.photo_urls));
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
  }[item.source_type] || REVIEW_BLOCK_REASON;
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
  const reviewPhotos = details.map((detail, index) => {
    const sourceUrl = detail.display_url ? resolveUploadUrl(detail.display_url) : '';
    return Object.assign({}, detail, {
      mediaKey: detail.id !== undefined && detail.id !== null
        ? 'attachment:' + String(detail.id) : 'attachment-index:' + index,
      sourceUrl,
      localPath: '',
      mediaStatus: sourceUrl ? 'loading' : 'error',
      mediaError: sourceUrl ? '' : '照片加载失败，点击重试',
      itemLabel: detail.item_name || '',
      archiveName: detail.archive_name || '',
      originalFilename: detail.original_filename || detail.filename || '',
      primaryStatusLabel: detail.review_status_label || '待审核',
      takenAt: detail.taken_at || '',
      selectedForReject: false
    });
  });
  const requesterName = item.requester_name || item.applicant_name || '';
  const executorName = item.executor_name || item.assignee || '';
  return Object.assign({}, item, reviewExecutionProjection(item), {
    detailOpen: false, photoUrls, itemDetails, reviewPhotos,
    reviewPhotoGroups: groupReviewPhotosByItem(reviewPhotos),
    reviewItemGroups: groupReviewItems(itemDetails, reviewPhotos),
    selectedRejectCount: 0, detailHint, approveLabel, rejectLabel,
    requester_name: requesterName,
    executor_name: executorName,
    verifiedRelatedTaskLabel: verifiedRelatedTaskLabel(item),
    canReview: !!capability,
    reviewBlockReason: capability ? '' : REVIEW_BLOCK_REASON
  });
}

Page({
  data: {
    loading: true,
    loadState: 'loading',
    loadError: '',
    groups: [],
    showGroupHeaders: false,
    total: 0,
    rejectShow: false,
    rejectReason: '',
    curId: '',
    curType: '',
    curAction: '',
    submittingId: '',
    reviewError: '',
    reviewTarget: null
  },

  onLoad(options) {
    this._alive = true;
    this._reviewMediaEpoch = 0;
    this._reviewMediaTasks = {};
    this._reviewMediaTempFiles = {};
    this._reviewMediaRefreshUsed = {};
    const targetType = String(options && options.target_type || '').trim();
    const targetId = String(options && options.target_id || '').trim();
    const attachmentIds = String(options && options.target_attachment_ids || '')
      .split(',').map(id => id.trim()).filter(Boolean);
    const cycleKey = String(options && options.cycle_key || '').trim();
    if (targetType && targetId) {
      this.reviewTarget = { kind: 'review', reviewType: targetType, sourceId: targetId, attachmentIds, cycleKey };
      this.setData({ reviewTarget: this.reviewTarget });
    }
  },

  onShow() {
    this._alive = true;
    this._syncSubmissionView();
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.load();
  },

  onHide() {
    this._alive = false;
    this._disposeProtectedReviewMedia(false);
  },

  onUnload() {
    this._alive = false;
    this._disposeProtectedReviewMedia(false);
  },

  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()); },

  load(done) {
    this._disposeProtectedReviewMedia(false);
    this._reviewMediaRefreshUsed = {};
    const requestId = (this._loadRequestId || 0) + 1;
    this._loadRequestId = requestId;
    const hasRetainedList = this.data.groups.length > 0;
    const retainedGroups = hasRetainedList ? this._resetReviewPhotoMedia(this.data.groups) : this.data.groups;
    this.setData({
      loading: true,
      loadState: hasRetainedList ? 'data' : 'loading',
      loadError: '',
      groups: retainedGroups
    });
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
        if (!this._isCurrentLoad(requestId)) return;
        const presentation = projectReviewPresentation(list);
        const groups = this.reviewTarget && this.reviewTarget.cycleKey
          ? presentation.groups.map(group => Object.assign({}, group, {
            items: group.items.map(item => Object.assign({}, item, { detailOpen: false, targeted: false }))
          }))
          : presentation.groups;
        this.setData({
          loading: false,
          total: list.length,
          groups,
          showGroupHeaders: presentation.showGroupHeaders,
          loadState: list.length ? 'data' : 'empty',
          loadError: ''
        }, () => {
          this._focusReviewTarget();
          this._restartProtectedReviewMedia(this.data.groups, true);
        });
        if (done) done();
      })
      .catch(err => {
        if (!this._isCurrentLoad(requestId)) return;
        if (err && err.status === 403) {
          this.setData({
            loading: false, loadState: 'no_permission', loadError: '',
            groups: [], total: 0, showGroupHeaders: false,
          });
          if (done) done();
          return;
        }
        const hasRetainedList = this.data.groups.length > 0;
        this.setData({
          loading: false,
          loadState: hasRetainedList ? 'stale' : 'error',
          loadError: hasRetainedList ? '当前审核可能不是最新，请重试' : '审核列表加载失败，请重试'
        });
        if (hasRetainedList) this._markAllProtectedReviewMediaFailed();
        if (done) done();
      });
  },

  onRetry() { this.load(); },

  _disposeProtectedReviewMedia(clearState) {
    this._reviewMediaEpoch = (this._reviewMediaEpoch || 0) + 1;
    const tasks = this._reviewMediaTasks || {};
    Object.keys(tasks).forEach(key => {
      const task = tasks[key];
      if (task && typeof task.abort === 'function') task.abort();
    });
    this._reviewMediaTasks = {};
    const files = this._reviewMediaTempFiles || {};
    Object.keys(files).forEach(filePath => this._removeProtectedReviewTempFile(filePath));
    this._reviewMediaTempFiles = {};
    if (clearState && this._alive !== false) this.setData({ groups: [] });
  },

  _removeProtectedReviewTempFile(filePath) {
    if (!filePath) return;
    const manager = wx.getFileSystemManager && wx.getFileSystemManager();
    if (manager && typeof manager.unlink === 'function') {
      manager.unlink({ filePath, fail() {} });
    } else if (wx.removeSavedFile) {
      wx.removeSavedFile({ filePath, fail() {} });
    }
  },

  _resetReviewPhotoMedia(groups) {
    return (groups || []).map(group => Object.assign({}, group, {
      items: (group.items || []).map(item => {
        const currentPhotos = item.reviewPhotos || [];
        if (!currentPhotos.some(photo => Object.prototype.hasOwnProperty.call(photo, 'sourceUrl'))) return item;
        const reviewPhotos = currentPhotos.map(photo => Object.assign({}, photo, {
          localPath: '',
          mediaStatus: photo.sourceUrl ? 'loading' : 'error',
          mediaError: photo.sourceUrl ? '' : '照片加载失败，点击重试'
        }));
        return Object.assign({}, item, {
          reviewPhotos,
          reviewPhotoGroups: groupReviewPhotosByItem(reviewPhotos),
          reviewItemGroups: groupReviewItems(item.itemDetails, reviewPhotos)
        });
      })
    }));
  },

  _restartProtectedReviewMedia(groups, includeCollapsed) {
    this._disposeProtectedReviewMedia(false);
    const epoch = this._reviewMediaEpoch;
    const nextGroups = this._resetReviewPhotoMedia(groups || this.data.groups);
    this.setData({ groups: nextGroups }, () => {
      nextGroups.forEach(group => (group.items || []).forEach(item => {
        if (!includeCollapsed && !item.detailOpen) return;
        (item.reviewPhotos || []).forEach(photo => {
          if (photo.mediaStatus === 'loading') {
            this._downloadProtectedReviewPhoto(item.id, photo.mediaKey, photo.sourceUrl, epoch);
          }
        });
      }));
    });
  },

  _setProtectedReviewPhoto(itemId, mediaKey, sourceUrl, epoch, patch) {
    if (this._alive === false || epoch !== this._reviewMediaEpoch) return false;
    let changed = false;
    const groups = this.data.groups.map(group => Object.assign({}, group, {
      items: group.items.map(item => {
        if (String(item.id) !== String(itemId)) return item;
        const reviewPhotos = (item.reviewPhotos || []).map(photo => {
          if (photo.mediaKey !== mediaKey || photo.sourceUrl !== sourceUrl) return photo;
          changed = true;
          return Object.assign({}, photo, patch);
        });
        if (!changed) return item;
        return Object.assign({}, item, {
          reviewPhotos,
          reviewPhotoGroups: groupReviewPhotosByItem(reviewPhotos),
          reviewItemGroups: groupReviewItems(item.itemDetails, reviewPhotos)
        });
      })
    }));
    if (changed) this.setData({ groups });
    return changed;
  },

  _downloadProtectedReviewPhoto(itemId, mediaKey, sourceUrl, epoch) {
    if (!sourceUrl || !/^https?:\/\//i.test(sourceUrl) || typeof wx.downloadFile !== 'function') {
      this._setProtectedReviewPhoto(itemId, mediaKey, sourceUrl, epoch, {
        mediaStatus: 'error', localPath: '', mediaError: '照片加载失败，点击重试'
      });
      return;
    }
    this._setProtectedReviewPhoto(itemId, mediaKey, sourceUrl, epoch, {
      mediaStatus: 'loading', localPath: '', mediaError: ''
    });
    const taskKey = [epoch, itemId, mediaKey].join(':');
    const finish = () => { delete (this._reviewMediaTasks || {})[taskKey]; };
    const markFailed = () => {
      this._setProtectedReviewPhoto(itemId, mediaKey, sourceUrl, epoch, {
        mediaStatus: 'error', localPath: '', mediaError: '照片加载失败，点击重试'
      });
    };
    const refreshExpiredTicket = () => {
      const refreshKey = String(itemId);
      if (!this._reviewMediaRefreshUsed[refreshKey]) {
        this._reviewMediaRefreshUsed[refreshKey] = true;
        this._refreshProtectedReviewTickets(itemId, epoch);
        return true;
      }
      return false;
    };
    const fail = error => {
      finish();
      if (this._alive === false || epoch !== this._reviewMediaEpoch) return;
      const statusCode = Number(error && error.statusCode);
      if ((statusCode === 401 || statusCode === 403) && refreshExpiredTicket()) return;
      markFailed();
    };
    const verifyLocalImage = (localPath, attempt) => {
      if (typeof wx.getImageInfo !== 'function') return Promise.resolve(localPath);
      return new Promise((resolve, reject) => wx.getImageInfo({
        src: localPath,
        success: () => resolve(localPath),
        fail: reject,
      })).catch(error => {
        if (attempt >= 1) return Promise.reject(error);
        return new Promise(resolve => setTimeout(resolve, 80))
          .then(() => verifyLocalImage(localPath, attempt + 1));
      });
    };
    const task = wx.downloadFile({
      url: sourceUrl,
      success: response => {
        finish();
        const statusCode = Number(response && response.statusCode);
        const localPath = response && response.tempFilePath;
        if (statusCode < 200 || statusCode >= 300 || !localPath) {
          fail({ statusCode });
          return;
        }
        verifyLocalImage(localPath, 0).then(() => {
          if (this._setProtectedReviewPhoto(itemId, mediaKey, sourceUrl, epoch, {
            mediaStatus: 'ready', localPath, mediaError: ''
          })) {
            this._reviewMediaTempFiles[localPath] = true;
          } else this._removeProtectedReviewTempFile(localPath);
        }).catch(() => {
          this._removeProtectedReviewTempFile(localPath);
          if (this._alive !== false && epoch === this._reviewMediaEpoch) markFailed();
        });
      },
      fail
    });
    this._reviewMediaTasks[taskKey] = task;
  },

  _markProtectedReviewItemFailed(itemId, epoch) {
    if (this._alive === false || epoch !== this._reviewMediaEpoch) return;
    const item = this._findItem(itemId);
    (item && item.reviewPhotos || []).forEach(photo => {
      if (photo.mediaStatus !== 'ready') {
        this._setProtectedReviewPhoto(itemId, photo.mediaKey, photo.sourceUrl, epoch, {
          mediaStatus: 'error', localPath: '', mediaError: '照片加载失败，点击重试'
        });
      }
    });
  },

  _markAllProtectedReviewMediaFailed() {
    const groups = this.data.groups.map(group => Object.assign({}, group, {
      items: group.items.map(item => {
        if (!(item.reviewPhotos || []).some(photo => Object.prototype.hasOwnProperty.call(photo, 'sourceUrl'))) {
          return item;
        }
        const reviewPhotos = item.reviewPhotos.map(photo => Object.assign({}, photo, {
          mediaStatus: 'error', localPath: '', mediaError: '照片加载失败，点击重试'
        }));
        return Object.assign({}, item, {
          reviewPhotos,
          reviewPhotoGroups: groupReviewPhotosByItem(reviewPhotos),
          reviewItemGroups: groupReviewItems(item.itemDetails, reviewPhotos)
        });
      })
    }));
    this.setData({ groups });
  },

  _refreshProtectedReviewTickets(itemId, epoch) {
    api.auditPending().then(rows => {
      if (this._alive === false || epoch !== this._reviewMediaEpoch) return;
      const current = this._findItem(itemId);
      const raw = (Array.isArray(rows) ? rows : []).find(row => String(row.id) === String(itemId)
        && (!current || row.source_type === current.source_type));
      if (!raw) throw new Error('review item unavailable');
      const refreshed = decorateItem(raw);
      if (!current) throw new Error('review item unavailable');
      const selected = {};
      (current.reviewPhotos || []).forEach(photo => { selected[String(photo.id)] = !!photo.selectedForReject; });
      const reviewPhotos = refreshed.reviewPhotos.map(photo => Object.assign({}, photo, {
        selectedForReject: !!selected[String(photo.id)]
      }));
      const replacement = Object.assign({}, current, refreshed, {
        detailOpen: current.detailOpen,
        targeted: current.targeted,
        reviewPhotos,
        reviewPhotoGroups: groupReviewPhotosByItem(reviewPhotos),
        reviewItemGroups: groupReviewItems(refreshed.itemDetails, reviewPhotos),
        selectedRejectCount: reviewPhotos.filter(photo => photo.selectedForReject).length
      });
      const groups = this.data.groups.map(group => Object.assign({}, group, {
        items: group.items.map(item => String(item.id) === String(itemId) ? replacement : item)
      }));
      this._restartProtectedReviewMedia(groups, false);
    }).catch(() => this._markProtectedReviewItemFailed(itemId, epoch));
  },

  onRetryProtectedReviewPhoto(e) {
    const item = this._findItem(e.currentTarget.dataset.id);
    const mediaKey = e.currentTarget.dataset.photoKey;
    const photo = item && (item.reviewPhotos || []).find(row => row.mediaKey === mediaKey);
    if (!photo) return;
    this._downloadProtectedReviewPhoto(item.id, mediaKey, photo.sourceUrl, this._reviewMediaEpoch);
  },

  onProtectedReviewPhotoRenderError(e) {
    const item = this._findItem(e.currentTarget.dataset.id);
    const mediaKey = e.currentTarget.dataset.photoKey;
    const photo = item && (item.reviewPhotos || []).find(row => row.mediaKey === mediaKey);
    if (!photo) return;
    if (photo.localPath) {
      this._removeProtectedReviewTempFile(photo.localPath);
      delete this._reviewMediaTempFiles[photo.localPath];
    }
    this._setProtectedReviewPhoto(item.id, mediaKey, photo.sourceUrl, this._reviewMediaEpoch, {
      mediaStatus: 'error', localPath: '', mediaError: '照片加载失败，点击重试'
    });
  },

  onViewSiteInspection(event) {
    const item = this._findItem(event.currentTarget.dataset.id);
    if (!item || !item.canViewInspection || this._navigationInFlight) return false;
    const target = {
      executionPlanId: item.executionPlanId,
      siteId: item.executionSiteId,
      source: 'review_execution_record',
      viewMode: 'review',
      readOnly: true,
    };
    this._navigationInFlight = true;
    app.globalData.executionTarget = target;
    const release = () => { this._navigationInFlight = false; };
    const fail = () => {
      if (app.globalData.executionTarget === target) app.globalData.executionTarget = null;
      release();
      wx.showToast({ title: '打开现场记录失败，请重试', icon: 'none' });
    };
    wx.navigateTo({ url: '/pages/inspection/inspection?view_mode=review', fail, complete: release });
    return true;
  },

  _isCurrentLoad(requestId) {
    return this._alive !== false && requestId === this._loadRequestId;
  },

  // 打开驳回原因弹窗
  onReject(e) {
    const id = e.currentTarget.dataset.id;
    const type = e.currentTarget.dataset.type;
    const item = this._findItem(id);
    if (!this._canWriteReview(item)) return;
    if (!this._guardSubmission(id)) return;
    this.setData({ rejectShow: true, rejectReason: '', curId: id, curType: type, curAction: 'reject' });
  },

  onReasonInput(e) { this.setData({ rejectReason: e.detail.value }); },
  noop() {},
  closeReject() {
    if (this._submissionPhase === 'risk-reject' && this._isSubmissionFor(this.data.curId)) {
      this._releaseSubmission(this.data.curId);
      return;
    }
    this.setData({ rejectShow: false, rejectReason: '', curId: '', curType: '', curAction: '' });
  },

  rejectConfirm() {
    const reason = (this.data.rejectReason || '').trim();
    if (!reason) { wx.showToast({ title: '请填写驳回原因', icon: 'none' }); return; }
    const riskReasonLock = this._submissionPhase === 'risk-reject'
      && this._isSubmissionFor(this.data.curId);
    this._dispatch(this.data.curAction || 'reject', reason, undefined, {
      lockAlreadyHeld: riskReasonLock
    });
  },

  onApprove(e) {
    const id = e.currentTarget.dataset.id;
    const type = e.currentTarget.dataset.type;
    const item = this._findItem(id);
    if (!this._canWriteReview(item)) return;
    if (!this._guardSubmission(id)) return;
    this.setData({ curId: id, curType: type });
    this._dispatch('approve', '', id);
  },

  _focusReviewTarget() {
    if (this._alive === false || !this.reviewTarget || this._reviewTargetHandled) return;
    if (this.reviewTarget.cycleKey) {
      api.auditTargetStatus(this.reviewTarget.reviewType, this.reviewTarget.sourceId, this.reviewTarget.cycleKey)
        .then(status => {
          if (this._alive === false || this._reviewTargetHandled) return;
          if (status && status.state === 'processed') {
            this._reviewTargetHandled = true;
            const detail = status.result_detail ? '；' + status.result_detail : '';
            wx.showModal({ title: '事项已处理',
              content: '该事项已由其他审核人处理。当前结果：' + (status.result_label || '已处理') + detail + '（只读）',
              showCancel: false });
            return;
          }
          if (!status || status.state !== 'pending') {
            wx.showModal({ title: '无法打开审核对象', content: '该审核事项当前不可打开，请刷新后重试。', showCancel: false });
            return;
          }
          const item = findReviewItem(this.data.groups, this.reviewTarget);
          if (!item) {
            wx.showModal({ title: '无法打开审核对象', content: '该审核事项已刷新，请返回审核列表后重试。', showCancel: false });
            return;
          }
          const groups = this.data.groups.map(group => Object.assign({}, group, {
            items: group.items.map(row => row.id === item.id
              ? Object.assign({}, row, { targeted: true, detailOpen: true }) : row)
          }));
          this._reviewTargetHandled = true;
          this.setData({ groups }, () => wx.pageScrollTo({ selector: '.review-target', duration: 0 }));
        })
        .catch(err => {
          if (this._alive === false || this._reviewTargetHandled) return;
          const content = err && err.error === '当前账号无权查看该审批事项'
            ? '当前账号无权访问该审核事项。'
            : err && err.error === '该审核事项不存在' ? '该审核事项不存在。' : '该审核事项当前不可打开，请刷新后重试。';
          wx.showModal({ title: '无法打开审核对象', content, showCancel: false });
        });
      return;
    }
    const item = findReviewItem(this.data.groups, this.reviewTarget);
    if (!item) {
      api.auditTargetStatus(this.reviewTarget.reviewType, this.reviewTarget.sourceId, this.reviewTarget.cycleKey)
        .then(status => {
          if (this._alive === false || this._reviewTargetHandled) return;
          this._reviewTargetHandled = true;
          if (status && status.state === 'processed') {
            const detail = status.result_detail ? '；' + status.result_detail : '';
            wx.showModal({ title: '事项已处理',
              content: '该事项已由其他审核人处理。当前结果：' + (status.result_label || '已处理') + detail + '（只读）',
              showCancel: false });
            return;
          }
          wx.showModal({ title: '无法打开审核对象', content: '该审核事项当前不可打开，请刷新后重试。', showCancel: false });
        })
        .catch(err => {
          if (this._alive === false || this._reviewTargetHandled) return;
          this._reviewTargetHandled = true;
          const content = err && err.error === '当前账号无权查看该审批事项'
            ? '当前账号无权访问该审核事项。'
            : err && err.error === '该审核事项不存在' ? '该审核事项不存在。' : '该审核事项当前不可打开，请刷新后重试。';
          wx.showModal({ title: '无法打开审核对象', content, showCancel: false });
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
    if (!this._guardSubmission(id)) return;
    const groups = this.data.groups.map(group => Object.assign({}, group, {
      items: group.items.map(item => {
        if (item.id !== id) return item;
        const reviewPhotos = (item.reviewPhotos || []).map(photo => photo.id === photoId
          ? Object.assign({}, photo, { selectedForReject: !photo.selectedForReject }) : photo);
        return Object.assign({}, item, {
          reviewPhotos,
          reviewPhotoGroups: groupReviewPhotosByItem(reviewPhotos),
          reviewItemGroups: groupReviewItems(item.itemDetails, reviewPhotos),
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
    if (!this._canWriteReview(item)) return;
    if (!this._guardSubmission(id)) return;
    this.setData({ curId: id, curType: item.source_type });
    if (item.selectedRejectCount > 0) {
      this.setData({ rejectShow: true, rejectReason: '', curAction: 'selective' });
      return;
    }
    this._dispatch('approve', '', id);
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
    this._restartProtectedReviewMedia(groups, false);
  },

  onPreviewPhoto(e) {
    const id = e.currentTarget.dataset.id;
    const item = this._findItem(id);
    const mediaKey = e.currentTarget.dataset.photoKey;
    if (!mediaKey && item && item.photoUrls && item.photoUrls.length) {
      wx.previewImage({ urls: item.photoUrls, current: e.currentTarget.dataset.url });
      return;
    }
    const photo = item && (item.reviewPhotos || []).find(row => row.mediaKey === mediaKey);
    const readyPhotos = item ? (item.reviewPhotos || []).filter(row => row.mediaStatus === 'ready' && row.localPath) : [];
    if (!photo || photo.mediaStatus !== 'ready' || !photo.localPath) {
      wx.showToast({ title: '照片尚未加载完成，请稍后重试', icon: 'none' });
      return;
    }
    wx.previewImage({ urls: readyPhotos.map(row => row.localPath), current: photo.localPath });
  },

  _guardSubmission(id, allowSameItem) {
    const guard = getSubmissionGuard(this._submittingId || this.data.submittingId, id,
      allowSameItem ? { allowSameItem: true } : undefined);
    if (!guard.allowed) {
      if (guard.message) wx.showToast({ title: guard.message, icon: 'none' });
      return false;
    }
    return true;
  },

  _canWriteReview(item) {
    if (this.data.loading || this.data.loadState === 'stale') {
      wx.showToast({ title: '请先刷新后再操作', icon: 'none' });
      return false;
    }
    if (!item || !reviewCapability(item.source_type)) {
      wx.showToast({ title: REVIEW_BLOCK_REASON, icon: 'none' });
      return false;
    }
    return true;
  },

  _isSubmissionFor(id) {
    const activeId = this._submittingId || this.data.submittingId;
    return !!activeId && String(activeId) === String(id);
  },

  _lockSubmission(id, phase, extra) {
    this._submissionPhase = phase || 'request';
    this._setSubmittingId(id, extra);
  },

  _releaseSubmission(id, extra) {
    if (id !== undefined && id !== null && id !== '' && !this._isSubmissionFor(id)) return false;
    this._setSubmittingId('', Object.assign({
      rejectShow: false,
      rejectReason: '',
      curId: '',
      curType: '',
      curAction: ''
    }, extra || {}));
    return true;
  },

  _setSubmittingId(id, extra) {
    const hasId = id !== undefined && id !== null && id !== '';
    if (!hasId) this._submissionPhase = '';
    else if (!this._submissionPhase) this._submissionPhase = 'request';
    this._submittingId = hasId ? String(id) : '';
    if (this._alive !== false) this.setData(Object.assign({ submittingId: hasId ? id : '' }, extra || {}));
  },

  _syncSubmissionView() {
    if (this._submittingId) {
      this.setData({ submittingId: this._submittingId });
      return;
    }
    this.setData(this._submissionRecovery || {
      submittingId: '',
      rejectShow: false,
      rejectReason: '',
      curId: '',
      curType: '',
      curAction: ''
    });
    this._submissionRecovery = null;
  },

  _dispatch(action, reason, itemId, options) {
    const id = itemId || this.data.curId;
    const lockAlreadyHeld = !!(options && options.lockAlreadyHeld);
    const item = this._findItem(id);
    if (!this._canWriteReview(item)) {
      if (lockAlreadyHeld) this._releaseSubmission(id);
      return;
    }
    if (lockAlreadyHeld) {
      if (!this._isSubmissionFor(id) || this._submissionPhase === 'request') return;
    } else if (!this._guardSubmission(id)) {
      return;
    }
    this._lockSubmission(item.id, 'request', { rejectShow: false, reviewError: '' });

    const p = reviewCapability(item.source_type).dispatch(item, action, reason);

    p.then(() => {
      this._releaseSubmission(item.id);
      if (this._alive === false) {
        this._submissionRecovery = {
          submittingId: '', rejectShow: false, rejectReason: '', curId: '', curType: '', curAction: ''
        };
        return;
      }
      wx.showToast({ title: action === 'approve' ? '已通过' : '已驳回', icon: 'success' });
      this.load();
    }).catch(err => {
      const retryState = {
        submittingId: '',
        rejectShow: action === 'reject' || action === 'selective',
        rejectReason: action === 'reject' || action === 'selective' ? reason : '',
        curId: action === 'reject' || action === 'selective' ? item.id : '',
        curType: action === 'reject' || action === 'selective' ? item.source_type : '',
        curAction: action === 'reject' || action === 'selective' ? action : ''
      };
      this._releaseSubmission(item.id, retryState);
      if (this._alive === false) {
        this._submissionRecovery = retryState;
        return;
      }
      const retryMessage = reviewFailureMessage(err);
      this.setData({ reviewError: retryMessage });
      wx.showModal({ title: '操作失败', content: retryMessage, showCancel: false });
    });
  },

  goBack() { wx.navigateBack({ delta: 1 }); }
});
