const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser } = require('../../utils/auth.js');

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
        return Object.assign({}, item, {
          key: ['site', item.plan_id || item.id || 0, executionDate, item.site_id || 0, index].join('-'),
          display_name: executionDate + ' · ' + siteName,
          status_cn: item.status_cn || status.label || '状态未知',
          status_cls: status.cls || 'gray',
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
    typeCn: '',
    generatedPlans: [],
    generatedTaskMode: 'site',
    resourceDays: [],
    resourceParts: [],
    linkedWorkorders: [],
    canEdit: false,
    canExecute: false,
    canFavorite: false,
    favoriting: false,
    favorite: null,
    favoriteSheet: { open: false, periodStart: '', submitting: false }
  },

  onLoad(opts) {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this.scheduleId = opts.id;
  },

  onShow() {
    if (this.scheduleId) this.load();
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
        this.setData({
          loaded: true,
          detail: res,
          days,
          statusCn: headerStatus.label,
          statusCls: headerStatus.cls,
          typeCn: maps.map(maps.SCHEDULE_TYPE, res.schedule_type, res.schedule_type),
          generatedPlans: generatedTasks.items,
          generatedTaskMode: generatedTasks.mode,
          resourceDays,
          resourceParts: plannedParts,
          linkedWorkorders: res.linked_workorders || [],
          canEdit: res.status === 'draft' || res.status === 'rejected',
          canChange: res.status === 'approved' && !executionCompleted,
          canExecute: res.status === 'approved' && !executionCompleted && days.some(day => day.date === todayString() && day.sites.length > 0),
          canFavorite: canUseFavorites && Number(res.user_id) === Number(user.id) && days.some(day => day.sites.length > 0),
          favorite
        });
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

  onEdit() {
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + this.scheduleId });
  },

  onGoExecution() {
    app.globalData.selPlanId = this.scheduleId;
    wx.switchTab({ url: '/pages/inspection/inspection' });
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

  // 发起变更：已通过的计划 → modifying，随后进入编辑页修改
  onChangeRequest() {
    wx.showModal({
      title: '发起变更',
      editable: true,
      placeholderText: '请填写变更原因（如车辆故障、突发任务）',
      success: (r) => {
        if (!r.confirm) return;
        const reason = (r.content || '').trim();
        if (!reason) {
          wx.showToast({ title: '请填写变更原因', icon: 'none' });
          return;
        }
        api.requestPlanScheduleChange(this.scheduleId, reason)
          .then(() => {
            wx.showToast({ title: '已发起变更', icon: 'success' });
            // 进入编辑页修改计划
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

module.exports = { normalizeGeneratedTasks, planHeaderPresentation };
