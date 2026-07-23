const api = require('../../services/api.js');
const maps = require('../../services/maps.js');

const app = getApp();
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

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

Page({
  data: {
    loaded: false,
    detail: null,
    days: [],          // [{date, weekday_cn, sites:[{id,name}], vehicle_name}]
    statusCn: '',
    statusCls: '',
    typeCn: '',
    generatedPlans: [],
    resourceDays: [],
    resourceParts: [],
    linkedWorkorders: [],
    canEdit: false
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
    api.planScheduleDetail(this.scheduleId)
      .then(res => {
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
        const reservedParts = Array.isArray(res.resource_parts) && res.resource_parts.length
          ? res.resource_parts.map(p => Object.assign({}, p, {
            status_cn: p.issued_quantity > 0 ? '已领用' : (p.reserved_quantity > 0 ? '已预留' : '待确认'),
            quantity_text: (p.issued_quantity || p.reserved_quantity || p.planned_quantity || 0) + (p.unit || '个')
          }))
          : (Array.isArray(res.spare_parts) ? res.spare_parts.map(p => ({
            part_name: p.part_name || p.name || '备件',
            quantity_text: (p.quantity || 1) + (p.unit || '个'),
            status_cn: '待审批'
          })) : []);

        this.setData({
          loaded: true,
          detail: res,
          days,
          statusCn: maps.map(maps.PLAN_SCHEDULE_STATUS, res.status, res.status),
          statusCls: maps.PLAN_SCHEDULE_STATUS_CLS[res.status] || 'gray',
          typeCn: maps.map(maps.SCHEDULE_TYPE, res.schedule_type, res.schedule_type),
          generatedPlans: res.generated_plans || [],
          resourceDays,
          resourceParts: reservedParts,
          linkedWorkorders: res.linked_workorders || [],
          canEdit: res.status === 'draft' || res.status === 'rejected',
          canChange: res.status === 'approved'
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loaded: true });
        if (done) done();
        wx.showToast({ title: '加载失败', icon: 'none' });
      });
  },

  onEdit() {
    wx.navigateTo({ url: '/pages/plan-edit/plan-edit?id=' + this.scheduleId });
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
        api.submitPlanSchedule(this.scheduleId)
          .then(() => {
            wx.showToast({ title: '已提交审批', icon: 'success' });
            this.load();
          })
          .catch(err => {
            wx.showToast({ title: (err && err.message) || '提交失败', icon: 'none' });
          });
      }
    });
  }
});
