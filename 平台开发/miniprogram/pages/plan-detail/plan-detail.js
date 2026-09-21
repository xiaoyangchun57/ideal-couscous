const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');
const {
  normalizePlanCancelReason,
  startPlanCancellation,
} = require('../../utils/executionState.js');
const { buildScheduleExecutionTarget } = require('../../utils/executionTarget.js');
const { invalidateUnreadCount } = require('../../utils/notificationCount.js');

const app = getApp();
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const EXECUTION_STATUS = {
  active: '执行中', completed: '已完成', rejected: '已驳回', cancelled: '已取消', draft: '待执行'
};
const SITE_TASK_STATUS = {
  pending: { label: '待执行', cls: 'gray' },
  partial: { label: '部分完成', cls: 'orange' },
  completed: { label: '已完成', cls: 'green' },
  change_pending: { label: '变更待审', cls: 'orange' },
  rework: { label: '需整改', cls: 'orange' }
};

const STATUS_CLS_TO_TONE = {
  green: 'success',
  blue: 'info',
  orange: 'warning',
  red: 'error',
  gray: 'default',
};

function planHeaderPresentation(detail) {
  const source = detail || {};
  if (source.status === 'modifying' || source.status === 'change_submitted') {
    return SITE_TASK_STATUS.change_pending;
  }
  if (source.status === 'approved') {
    const key = SITE_TASK_STATUS[source.execution_status]
      ? source.execution_status
      : (source.execution_completed ? 'completed' : 'pending');
    const presentation = SITE_TASK_STATUS[key];
    return {
      label: source.execution_status === key && source.execution_status_cn
        ? source.execution_status_cn
        : presentation.label,
      cls: presentation.cls
    };
  }
  return {
    label: maps.map(maps.PLAN_SCHEDULE_STATUS, source.status, source.status),
    cls: maps.PLAN_SCHEDULE_STATUS_CLS[source.status] || 'gray'
  };
}

function normalizeGeneratedTasks(detail) {
  const source = detail || {};
  if (Array.isArray(source.generated_site_tasks)) {
    return {
      mode: 'site',
      items: source.generated_site_tasks.map((item, index) => {
        const status = SITE_TASK_STATUS[item.status] || {};
        const executionDate = item.execution_date || item.date || '日期未设置';
        const siteName = item.site_name || (item.site_id ? ('站点#' + item.site_id) : '站点未设置');
        const totalItems = item.total_items === null || item.total_items === undefined || item.total_items === ''
          ? null : Number(item.total_items);
        const completedItems = item.completed_items === null || item.completed_items === undefined || item.completed_items === ''
          ? null : Number(item.completed_items);
        const hasItemCounts = Number.isFinite(totalItems) && Number.isFinite(completedItems);
        return Object.assign({}, item, {
          key: ['site', item.plan_id || item.id || 0, executionDate, item.site_id || 0, index].join('-'),
          display_name: executionDate + ' · ' + siteName,
          status_cn: item.status_cn || status.label || '状态未知',
          status_cls: status.cls || 'gray',
          total_items: hasItemCounts ? totalItems : null,
          completed_items: hasItemCounts ? completedItems : null,
          item_counts_text: hasItemCounts ? `${completedItems}/${totalItems} 项` : '检查项未记录',
          legacy: false
        });
      })
    };
  }
  return {
    mode: 'legacy',
    items: (source.generated_plans || []).map((item, index) => Object.assign({}, item, {
      key: ['legacy', item.id || 0, index].join('-'),
      display_name: item.plan_name || ('巡检任务#' + item.id),
      status_cn: EXECUTION_STATUS[item.status] || '状态未知',
      status_cls: item.status === 'completed' ? 'green' : (item.status === 'active' ? 'blue' : 'gray'),
      legacy: true
    }))
  };
}

function summarizeGeneratedTasks(generatedTasks) {
  const summary = { totalSites: 0, completedSites: 0, totalItems: 0, completedItems: 0 };
  if (!generatedTasks || generatedTasks.mode !== 'site') return summary;
  generatedTasks.items.forEach(task => {
    summary.totalSites += 1;
    if (task.status === 'completed') summary.completedSites += 1;
    if (Number.isFinite(task.total_items) && Number.isFinite(task.completed_items)) {
      summary.totalItems += task.total_items;
      summary.completedItems += task.completed_items;
    }
  });
  return summary;
}

function weekdayCn(dateStr) {
  return WEEKDAYS[new Date(dateStr.replace(/-/g, '/')).getDay()];
}
function addDays(dateStr, n) {
  const d = new Date(dateStr.replace(/-/g, '/'));
  d.setDate(d.getDate() + n);
  const y = d.getFullYear();
  const m = ('0' + (d.getMonth() + 1)).slice(-2);
  const dd = ('0' + d.getDate()).slice(-2);
  return y + '-' + m + '-' + dd;
}
function todayString() {
  const d = new Date();
  const y = d.getFullYear();
  const m = ('0' + (d.getMonth() + 1)).slice(-2);
  const day = ('0' + d.getDate()).slice(-2);
  return y + '-' + m + '-' + day;
}

Page({
  data: {
    loaded: false,
    detail: null,
    days: [],          // [{date, weekday_cn, sites:[{id,name}], vehicle_name}]
    statusCn: '',
    statusCls: '',
    statusTone: 'default',
    typeCn: '',
    generatedPlans: [],
    generatedTaskMode: 'site',
    resourceDays: [],
    resourceParts: [],
    linkedWorkorders: [],
    canEdit: false,
    canExecute: false,
    canContinueRework: false,
    canContinueExecution: false,
    executionActionText: '进入现场作业',
    executionTarget: null,
    reworkBlockReason: '',
    executionBlockReason: '',
    vehicleAdjustmentRequired: false,
    canFavorite: false,
    favoriting: false,
    favorite: null,
    favoriteSheet: { open: false, periodStart: '', submitting: false },
    canCancel: false,
    cancelBlockReason: '',
    cancelSheet: { open: false, reason: '', submitting: false, error: '' }
  },

  onLoad(opts) {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this.scheduleId = opts.id;
    const notificationId = Number(opts && opts.notification_id);
    this._resultNotificationId = Number.isInteger(notificationId) && notificationId > 0 ? notificationId : null;
    this._alive = true;
    this._executionRequestId = 0;
  },

  onShow() {
    this._alive = true;
    this._executionNavigating = false;
    if (this.scheduleId) this.load();
  },

  onHide() {
    this._alive = false;
    this._executionRequestId = (this._executionRequestId || 0) + 1;
    this._executionNavigating = false;
  },

  onUnload() {
    this._alive = false;
    this._executionRequestId = (this._executionRequestId || 0) + 1;
    this._executionNavigating = false;
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh());
  },

  load(done) {
    const user = getUser() || {};
    const roles = user.roles || [user.role || ''];
    const canUseFavorites = roles.includes('operator');
    Promise.all([
      api.planScheduleDetail(this.scheduleId),
      canUseFavorites ? api.planScheduleFavorites().catch(() => []) : Promise.resolve([])
    ])
      .then(([res, favorites]) => {
        const planData = res.plan_data || {};
        const vehicleDays = res.vehicle_days || {};
        const vehicleMap = res.vehicle_map || {};
        const siteMap = res.site_map || {};
        const start = res.period_start;
        const end = res.period_end;

        // 构建逐日视图
        const days = [];
        let cur = start;
        while (cur <= end) {
          const dp = planData[cur] || {};
          const siteIds = dp.sites || [];
          days.push({
            date: cur,
            weekday_cn: weekdayCn(cur),
            sites: siteIds.map(id => ({ id, name: (siteMap[id] && siteMap[id].name) || ('站点#' + id) })),
            vehicle_id: vehicleDays[cur] || null,
            vehicle_name: vehicleMap[vehicleDays[cur]] ? (vehicleMap[vehicleDays[cur]].plate_no || vehicleMap[vehicleDays[cur]].model || '已安排车辆') : ''
          });
          cur = addDays(cur, 1);
        }

        const resourceDays = days.filter(d => d.vehicle_id || d.sites.length).map(d => ({
          date: d.date,
          date_short: d.date.slice(5),
          weekday_cn: d.weekday_cn,
          vehicle_name: d.vehicle_name || (d.sites.length ? '未安排用车' : '')
        })).filter(d => d.vehicle_name);
        const plannedParts = Array.isArray(res.resource_parts) && res.resource_parts.length
          ? res.resource_parts.map(p => Object.assign({}, p, {
            status_cn: p.issued_quantity > 0
              ? `已领${p.issued_quantity}${p.unit || '个'}`
              : '待现场领用',
            quantity_text: `计划${p.planned_quantity || 0}${p.unit || '个'}`
          }))
          : (Array.isArray(res.spare_parts) ? res.spare_parts.map(p => ({
            part_name: p.part_name || p.name || '备件',
            quantity_text: (p.quantity || 1) + (p.unit || '个'),
            status_cn: '待审批'
          })) : []);

        const favorite = (favorites || []).find(item => Number(item.source_schedule_id) === Number(this.scheduleId)) || null;
        const executionCompleted = !!res.execution_completed;
        const generatedTasks = normalizeGeneratedTasks(res);
        const headerStatus = planHeaderPresentation(res);
        const statusTone = STATUS_CLS_TO_TONE[headerStatus.cls] || 'default';
        const { totalSites, completedSites, totalItems, completedItems } = summarizeGeneratedTasks(generatedTasks);
        const progressPercent = totalSites > 0 ? Math.round((completedSites / totalSites) * 100) : 0;
        const progressFillStyle = `width:${progressPercent}%`;
        const actionTarget = buildScheduleExecutionTarget(res, this.scheduleId);
        const isRework = res.execution_status === 'rework';
        const canContinueRework = isRework && res.can_continue_rework === true
          && !!(actionTarget.executionPlanId && actionTarget.siteId);
        const canContinueExecution = !isRework && res.can_continue_execution === true
          && !!actionTarget.scheduleId;
        const executionActionText = canContinueRework
          ? '继续整改'
          : (canContinueExecution && actionTarget.workDate === todayString()
              && res.execution_status === 'pending'
            ? '进入现场作业' : '继续执行');
        this.setData({
          loaded: true,
          detail: res,
          days,
          statusCn: headerStatus.label,
          statusCls: headerStatus.cls,
          statusTone,
          typeCn: maps.map(maps.SCHEDULE_TYPE, res.schedule_type, res.schedule_type),
          generatedPlans: generatedTasks.items,
          generatedTaskMode: generatedTasks.mode,
          resourceDays,
          resourceParts: plannedParts,
          linkedWorkorders: res.linked_workorders || [],
          canEdit: ['draft', 'rejected', 'modifying'].includes(res.status),
          canChange: res.status === 'approved' && !executionCompleted,
          canExecute: canContinueRework || canContinueExecution,
          canContinueRework,
          canContinueExecution,
          executionActionText,
          executionTarget: actionTarget,
          reworkBlockReason: res.rework_block_reason || '',
          executionBlockReason: res.execution_block_reason || '',
          vehicleAdjustmentRequired: res.vehicle_adjustment_required === true,
          canFavorite: canUseFavorites && Number(res.user_id) === Number(user.id) && days.some(day => day.sites.length > 0),
          canCancel: res.can_cancel === true,
          cancelBlockReason: res.can_cancel === true
            ? '' : (res.cancel_block_reason || '当前计划不可取消'),
          favorite,
          totalSites,
          completedSites,
          totalItems,
          completedItems,
          progressPercent,
          progressFillStyle
        });
        this._markResultNotificationRead();
        if (done) done();
      })
      .catch(err => {
        this.setData({ loaded: true });
        if (done) done();
        wx.showModal({
          title: '无法打开计划',
          content: (err && err.error) || '该计划不存在或当前账号无权访问。',
          showCancel: false
        });
      });
  },

  _markResultNotificationRead() {
    const notificationId = this._resultNotificationId;
    if (!notificationId || this._resultNotificationRead) return;
    this._resultNotificationRead = true;
    api.readNotification(notificationId).then(() => invalidateUnreadCount())
      .catch(() => { this._resultNotificationRead = false; });
  },

  onEdit() {
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + this.scheduleId });
  },

  onGoExecution() {
    if (this._executionNavigating) {
      wx.showToast({ title: '正在进入现场，请稍候', icon: 'none' });
      return;
    }
    if (!this.data.canExecute) {
      wx.showToast({
        title: this.data.reworkBlockReason || this.data.executionBlockReason
          || '当前任务不可执行，请刷新后重试',
        icon: 'none'
      });
      if (this.data.detail) this.load();
      return;
    }
    if (this.data.detail && this.data.detail.execution_status === 'rework') {
      this._preflightReworkExecution();
      return;
    }
    this._preflightFieldExecution();
  },

  _preflightReworkExecution() {
    this._executionNavigating = true;
    const requestId = (this._executionRequestId || 0) + 1;
    this._executionRequestId = requestId;
    api.planScheduleDetail(this.scheduleId).then(detail => {
      if (!this._alive || requestId !== this._executionRequestId) return;
      const target = buildScheduleExecutionTarget(detail, this.scheduleId);
      const targetIsExact = !!(target.executionPlanId && target.siteId);
      if (detail.execution_status !== 'rework'
          || detail.can_continue_rework !== true || !targetIsExact) {
        this._executionNavigating = false;
        const reason = detail.rework_block_reason
          || '整改任务已闭环或执行权限已变化，请刷新计划详情';
        this.setData({
          detail,
          canExecute: false,
          canContinueRework: false,
          executionTarget: null,
          reworkBlockReason: reason,
        });
        wx.showToast({ title: reason, icon: 'none' });
        this.load();
        return;
      }
      this._navigateToExecution(target);
    }).catch(err => {
      if (!this._alive || requestId !== this._executionRequestId) return;
      this._executionNavigating = false;
      wx.showToast({
        title: (err && (err.error || err.message)) || '整改任务校验失败，请刷新后重试',
        icon: 'none'
      });
    });
  },

  _preflightFieldExecution() {
    this._executionNavigating = true;
    const requestId = (this._executionRequestId || 0) + 1;
    this._executionRequestId = requestId;
    api.planScheduleDetail(this.scheduleId).then(detail => {
      if (!this._alive || requestId !== this._executionRequestId) return;
      const target = buildScheduleExecutionTarget(detail, this.scheduleId);
      const isOrdinaryExecution = detail.execution_status === 'pending'
        || detail.execution_status === 'partial';
      if (!isOrdinaryExecution || detail.can_continue_execution !== true
          || !target.scheduleId) {
        this._executionNavigating = false;
        const reason = detail.execution_block_reason
          || '执行任务已闭环或执行权限已变化，请刷新计划详情';
        this.setData({
          detail,
          canExecute: false,
          canContinueExecution: false,
          executionTarget: null,
          executionBlockReason: reason,
        });
        wx.showToast({ title: reason, icon: 'none' });
        this.load();
        return;
      }
      this._navigateToExecution(target);
    }).catch(err => {
      if (!this._alive || requestId !== this._executionRequestId) return;
      this._executionNavigating = false;
      wx.showToast({
        title: (err && (err.error || err.message)) || '执行任务校验失败，请刷新后重试',
        icon: 'none'
      });
    });
  },

  _navigateToExecution(target) {
    app.globalData.executionTarget = target;
    try {
      wx.navigateTo({
        url: '/pages/inspection/inspection',
        fail: err => {
          if (app.globalData.executionTarget === target) app.globalData.executionTarget = null;
          this._executionNavigating = false;
          wx.showToast({
            title: (err && (err.errMsg || err.message)) || '进入现场失败，请重试',
            icon: 'none'
          });
        }
      });
    } catch (err) {
      if (app.globalData.executionTarget === target) app.globalData.executionTarget = null;
      this._executionNavigating = false;
      wx.showToast({ title: '进入现场失败，请重试', icon: 'none' });
    }
  },

  onFavorite() {
    if (!this.data.canFavorite || this.data.favoriting) return;
    wx.showModal({
      title: '收藏为常用计划',
      editable: true,
      placeholderText: '可填写名称，如：周二周四固定路线',
      content: '',
      confirmText: '收藏',
      success: result => {
        if (!result.confirm) return;
        this.setData({ favoriting: true });
        api.addPlanScheduleFavorite(this.scheduleId, (result.content || '').trim())
          .then(() => {
            wx.showToast({ title: '已加入常用计划', icon: 'success' });
            this.load();
          })
          .catch(err => wx.showToast({ title: (err && (err.error || err.message)) || '收藏失败', icon: 'none' }))
          .finally(() => this.setData({ favoriting: false }));
      }
    });
  },

  onOpenCancel() {
    if (!this.data.canCancel) {
      wx.showToast({
        title: this.data.cancelBlockReason || '计划状态或权限已变化，请刷新后重试',
        icon: 'none'
      });
      return;
    }
    if (this.data.cancelSheet.submitting) {
      wx.showToast({ title: '取消请求正在处理中，请稍候', icon: 'none' });
      return;
    }
    this.setData({
      'cancelSheet.open': true,
      'cancelSheet.error': ''
    });
  },

  onCancelReasonInput(e) {
    this.setData({
      'cancelSheet.reason': e.detail.value,
      'cancelSheet.error': ''
    });
  },

  onCloseCancel() {
    if (this.data.cancelSheet.submitting) {
      wx.showToast({ title: '取消请求正在处理中，请稍候', icon: 'none' });
      return;
    }
    this.setData({ 'cancelSheet.open': false });
  },

  onConfirmCancel() {
    if (!this.data.canCancel) {
      const error = this.data.cancelBlockReason || '计划状态或权限已变化，请刷新后重试';
      this.setData({ 'cancelSheet.error': error });
      wx.showToast({ title: error, icon: 'none' });
      return;
    }
    const normalized = normalizePlanCancelReason(this.data.cancelSheet.reason);
    if (normalized.error) {
      this.setData({ 'cancelSheet.error': normalized.error });
      return;
    }
    const detail = this.data.detail || {};
    const action = startPlanCancellation(this, () => api.cancelPlanSchedule(
      this.scheduleId, normalized.value, detail.version
    ));
    if (!action.started) {
      wx.showToast({ title: '取消请求正在处理中，请稍候', icon: 'none' });
      return;
    }
    this.setData({
      'cancelSheet.reason': normalized.value,
      'cancelSheet.submitting': true,
      'cancelSheet.error': ''
    });
    action.promise
      .then(() => {
        this.setData({
          'cancelSheet.open': false,
          'cancelSheet.reason': '',
          'cancelSheet.error': ''
        });
        wx.showToast({ title: '计划已取消', icon: 'success' });
        this.load();
      })
      .catch(err => {
        this.setData({
          'cancelSheet.error': (err && (err.error || err.message)) || '取消失败，请稍后重试'
        });
      })
      .finally(() => this.setData({ 'cancelSheet.submitting': false }));
  },

  onFavoriteAction() {
    if (this.data.favorite) this.onUseFavorite();
    else this.onFavorite();
  },

  onUseFavorite() {
    const favorite = this.data.favorite;
    if (!favorite) return;
    this.setData({ favoriteSheet: {
      open: true, periodStart: favorite.suggested_period_start || todayString(), submitting: false
    } });
  },

  onFavoriteDate(e) {
    this.setData({ 'favoriteSheet.periodStart': e.detail.value });
  },

  onCloseFavoriteSheet() {
    if (!this.data.favoriteSheet.submitting) this.setData({ 'favoriteSheet.open': false });
  },

  onCreateFavoriteDraft() {
    const favorite = this.data.favorite;
    const sheet = this.data.favoriteSheet;
    if (!favorite || !sheet.periodStart || sheet.submitting) return;
    this.setData({ 'favoriteSheet.submitting': true });
    api.createDraftFromPlanScheduleFavorite(favorite.id, sheet.periodStart)
      .then(res => {
        const scheduleId = res && res.schedule && res.schedule.id;
        if (!scheduleId) throw new Error('草稿创建结果无效');
        this.setData({ 'favoriteSheet.open': false, 'favoriteSheet.submitting': false });
        wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + scheduleId });
      })
      .catch(err => {
        this.setData({ 'favoriteSheet.submitting': false });
        wx.showToast({ title: (err && (err.error || err.message)) || '生成草稿失败', icon: 'none' });
      });
  },

  // 开始修改只进入编辑态；保存生效仍由编辑页完成。
  onChangeRequest() {
    wx.showModal({
      title: '开始修改计划',
      editable: true,
      content: '此步只进入编辑。完成修改并确认后，新计划将直接生效。',
      placeholderText: '请填写修改原因（如日期或站点顺序错误）',
      success: (r) => {
        if (!r.confirm) return;
        const reason = (r.content || '').trim();
        if (!reason) {
          wx.showToast({ title: '请填写变更原因', icon: 'none' });
          return;
        }
        api.requestPlanScheduleChange(this.scheduleId, reason)
          .then(() => {
            wx.showToast({ title: '请完成修改并确认生效', icon: 'none' });
            wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + this.scheduleId });
          })
          .catch(err => {
            wx.showToast({ title: (err && err.message) || '发起变更失败', icon: 'none' });
          });
      }
    });
  },

  onSubmit() {
    wx.showModal({
      title: '确认提交',
      content: '提交后将进入审批流程，确认提交？',
      success: (r) => {
        if (!r.confirm) return;
        api.submitPlanSchedule(this.scheduleId, this.data.detail && this.data.detail.version)
          .then(() => {
            wx.showToast({ title: '已提交审批', icon: 'success' });
            this.load();
          })
          .catch(err => {
            wx.showToast({ title: (err && (err.error || err.message)) || '提交失败', icon: 'none' });
          });
      }
    });
  }
});

module.exports = {
  normalizeGeneratedTasks,
  planHeaderPresentation,
  summarizeGeneratedTasks,
  buildScheduleExecutionTarget,
};
