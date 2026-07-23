const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser, getSites } = require('../../utils/auth.js');

const app = getApp();
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

// 计算下周一日期（YYYY-MM-DD）
function nextMonday() {
  const d = new Date();
  const day = d.getDay(); // 0=Sun
  const diff = day === 0 ? 1 : (8 - day);
  d.setDate(d.getDate() + diff);
  return fmt(d);
}
function fmt(d) {
  const y = d.getFullYear();
  const m = ('0' + (d.getMonth() + 1)).slice(-2);
  const dd = ('0' + d.getDate()).slice(-2);
  return y + '-' + m + '-' + dd;
}
function addDays(dateStr, n) {
  const d = new Date(dateStr.replace(/-/g, '/'));
  d.setDate(d.getDate() + n);
  return fmt(d);
}
function weekdayCn(dateStr) {
  return WEEKDAYS[new Date(dateStr.replace(/-/g, '/')).getDay()];
}
function lastDayOfMonth(y, m) { return new Date(y, m, 0).getDate(); } // m: 1-12

// 各频次周期计算：返回 [periodStart, periodEnd]
function periodRange(type) {
  const now = new Date();
  if (type === 'monthly') {
    // 下个月 1 号 ~ 月末
    const y = now.getMonth() === 11 ? now.getFullYear() + 1 : now.getFullYear();
    const m = (now.getMonth() + 1) % 12 + 1; // 下个月(1-12)
    return [fmt(new Date(y, m - 1, 1)), fmt(new Date(y, m - 1, lastDayOfMonth(y, m)))];
  }
  if (type === 'quarterly') {
    // 下个季度首月1号 ~ 末月月末
    const q = Math.floor(now.getMonth() / 3); // 当前季度 0-3
    const nq = (q + 1) % 4;                   // 下个季度 0-3
    const y = q === 3 ? now.getFullYear() + 1 : now.getFullYear();
    const startM = nq * 3 + 1;                // 季度首月(1-12)
    const endM = startM + 2;
    return [fmt(new Date(y, startM - 1, 1)), fmt(new Date(y, endM - 1, lastDayOfMonth(y, endM)))];
  }
  if (type === 'yearly') {
    // 明年 1/1 ~ 12/31
    const y = now.getFullYear() + 1;
    return [fmt(new Date(y, 0, 1)), fmt(new Date(y, 11, 31))];
  }
  // weekly：下周一 ~ 周日
  const start = nextMonday();
  return [start, addDays(start, 6)];
}

Page({
  data: {
    editId: null,
    scheduleType: 'weekly',
    scheduleTypeOptions: ['周检', '月检', '季检', '年检'],
    typeKeys: ['weekly', 'monthly', 'quarterly', 'yearly'],
    periodStart: '',
    periodEnd: '',
    days: [],           // [{date, weekday_cn, sites:[], vehicle_id:null}]
    mySites: [],        // [{id, name}]
    vehicles: [],       // [{id, name/plate_number}]
    partsInventory: [], // 可用于本次出车的备件库存
    selectedParts: [],  // [{part_id, part_name, quantity}]
    linkedWorkOrderIds: [],
    suggestions: [],    // [{type, site_id, site_name, text, level}]
    siteScores: {},     // {site_id: score}
    remarks: '',
    coverageExceptionReason: '',
    submitting: false,
    loaded: false,
    isChange: false,      // 是否为变更编辑（modifying 状态）
    changeReason: ''
  },

  onLoad(opts) {
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    const mySites = (getSites() || []).map(s => ({ id: s.id, name: s.name }));
    this.setData({ mySites });

    if (opts.id) {
      // 编辑已有排程
      this.setData({ editId: parseInt(opts.id) });
      this.loadExisting(opts.id);
    } else {
      this.initPeriod('weekly');
    }
    this.loadVehicles();
    this.loadPartsInventory();
    this.loadSuggestions();
  },

  // 新建：按频次初始化周期。周检预填7天（要求全覆盖）；月/季/年检周期长，
  // 由运维通过"添加日期"挑选具体巡检日，不预铺全部日期。
  initPeriod(type) {
    const [start, end] = periodRange(type);
    let days = [];
    if (type === 'weekly') {
      let cur = start;
      while (cur <= end) {
        days.push({ date: cur, weekday_cn: weekdayCn(cur), sites: [], vehicle_id: null });
        cur = addDays(cur, 1);
      }
    }
    this.setData({ scheduleType: type, periodStart: start, periodEnd: end, days, loaded: true });
  },

  // 切换频次（仅新建时可切换；编辑已有排程锁定频次）
  onTypeChange(e) {
    if (this.data.editId) return;
    const idx = parseInt(e.detail.value);
    this.initPeriod(this.data.typeKeys[idx]);
  },

  // 添加巡检日期（月/季/年检用，限制在周期范围内）
  onAddDay(e) {
    const date = e.detail.value;
    if (!date) return;
    if (date < this.data.periodStart || date > this.data.periodEnd) {
      wx.showToast({ title: '日期需在周期范围内', icon: 'none' });
      return;
    }
    if (this.data.days.some(d => d.date === date)) {
      wx.showToast({ title: '该日期已添加', icon: 'none' });
      return;
    }
    const days = this.data.days.concat([{ date, weekday_cn: weekdayCn(date), sites: [], vehicle_id: null }]);
    days.sort((a, b) => a.date < b.date ? -1 : 1);
    this.setData({ days });
  },

  // 删除某天
  onRemoveDay(e) {
    const date = e.currentTarget.dataset.date;
    this.setData({ days: this.data.days.filter(d => d.date !== date) });
  },

  // 加载已有排程
  loadExisting(id) {
    api.planScheduleDetail(id)
      .then(res => {
        const planData = res.plan_data || {};
        const vehicleDays = res.vehicle_days || {};
        const start = res.period_start;
        const end = res.period_end;
        const type = res.schedule_type || 'weekly';
        const days = [];
        if (type === 'weekly') {
          // 周检：铺满周期内每一天（空草稿也要有7天可选）
          let cur = start;
          while (cur <= end) {
            const dayPlan = planData[cur] || {};
            days.push({
              date: cur, weekday_cn: weekdayCn(cur),
              sites: dayPlan.sites || [], vehicle_id: vehicleDays[cur] || null, notes: dayPlan.notes || ''
            });
            cur = addDays(cur, 1);
          }
        } else {
          // 月/季/年检：只载入已有安排的日期
          Object.keys(planData).sort().forEach(date => {
            const dayPlan = planData[date] || {};
            days.push({
              date, weekday_cn: weekdayCn(date),
              sites: dayPlan.sites || [], vehicle_id: vehicleDays[date] || null, notes: dayPlan.notes || ''
            });
          });
        }
        this.setData({
          loaded: true,
          editId: res.id,
          scheduleType: res.schedule_type || 'weekly',
          periodStart: start,
          periodEnd: end,
          days,
          selectedParts: Array.isArray(res.spare_parts) ? res.spare_parts : [],
          linkedWorkOrderIds: Array.isArray(res.work_order_ids) ? res.work_order_ids : [],
          remarks: res.remarks || '',
          coverageExceptionReason: res.coverage_exception_reason || '',
          isChange: res.status === 'modifying',
          changeReason: res.change_reason || ''
        });
      })
      .catch(() => {
        wx.showToast({ title: '加载失败', icon: 'none' });
        this.setData({ loaded: true });
      });
  },

  loadVehicles() {
    api.vehicles()
      .then(res => {
        const vehicles = (Array.isArray(res) ? res : []).map(v => ({
          id: v.id,
          name: v.plate_number || v.name || ('车辆#' + v.id)
        }));
        this.setData({ vehicles });
      })
      .catch(() => {});
  },

  loadPartsInventory() {
    api.partsInventory()
      .then(res => {
        const partsInventory = (Array.isArray(res) ? res : [])
          .filter(p => Number(p.quantity) > 0)
          .map(p => ({
            id: p.id,
            name: p.part_name || p.part_code || ('备件#' + p.id),
            quantity: Number(p.quantity) || 0,
            unit: p.unit || '件'
          }));
        this.setData({ partsInventory });
      })
      .catch(() => {});
  },

  loadSuggestions() {
    const u = getUser();
    if (!u || !u.id) return;
    api.planSuggestions(u.id)
      .then(res => {
        this.setData({
          suggestions: res.suggestions || [],
          siteScores: res.site_scores || {}
        });
      })
      .catch(() => {});
  },

  // 切换某天的站点选中
  onToggleSite(e) {
    const { dayIdx, siteId } = e.currentTarget.dataset;
    const key = 'days[' + dayIdx + '].sites';
    let sites = this.data.days[dayIdx].sites.slice();
    const pos = sites.indexOf(siteId);
    if (pos > -1) {
      sites.splice(pos, 1);
    } else {
      sites.push(siteId);
    }
    this.setData({ [key]: sites });
  },

  // 一键全选/清空当天
  onToggleAll(e) {
    const dayIdx = e.currentTarget.dataset.dayIdx;
    const key = 'days[' + dayIdx + '].sites';
    const cur = this.data.days[dayIdx].sites;
    const allIds = this.data.mySites.map(s => s.id);
    // 如果已全选则清空，否则全选
    const allSelected = allIds.every(id => cur.indexOf(id) > -1);
    this.setData({ [key]: allSelected ? [] : allIds.slice() });
  },

  // 选择车辆
  onVehicleChange(e) {
    const dayIdx = e.currentTarget.dataset.dayIdx;
    const idx = parseInt(e.detail.value);
    const vId = idx >= 0 ? this.data.vehicles[idx].id : null;
    this.setData({ ['days[' + dayIdx + '].vehicle_id']: vId });
  },

  onTogglePart(e) {
    const partId = Number(e.currentTarget.dataset.partId);
    const part = this.data.partsInventory.find(p => p.id === partId);
    if (!part) return;
    const selected = this.data.selectedParts.slice();
    const idx = selected.findIndex(p => Number(p.part_id) === partId);
    if (idx >= 0) {
      selected.splice(idx, 1);
    } else {
      selected.push({ part_id: part.id, part_name: part.name, quantity: 1 });
    }
    this.setData({ selectedParts: selected });
  },

  onRemarks(e) {
    this.setData({ remarks: e.detail.value });
  },

  onCoverageExceptionReason(e) {
    this.setData({ coverageExceptionReason: e.detail.value });
  },

  // 构建请求体
  buildPayload(submit) {
    const { scheduleType, periodStart, periodEnd, days, remarks, coverageExceptionReason, selectedParts, suggestions } = this.data;
    const planData = {};
    const vehicleDays = {};
    days.forEach(d => {
      if (d.sites.length) {
        planData[d.date] = { sites: d.sites, notes: d.notes || '' };
      }
      if (d.vehicle_id) {
        vehicleDays[d.date] = d.vehicle_id;
      }
    });
    const selectedSiteIds = new Set();
    days.forEach(d => d.sites.forEach(siteId => selectedSiteIds.add(Number(siteId))));
    const linkedWorkOrderIds = (suggestions || [])
      .filter(s => s.type === 'work_order' && selectedSiteIds.has(Number(s.site_id)) && s.ref_id)
      .map(s => s.ref_id);
    return {
      schedule_type: scheduleType,
      period_start: periodStart,
      period_end: periodEnd,
      plan_data: planData,
      vehicle_days: vehicleDays,
      spare_parts: selectedParts,
      work_order_ids: Array.from(new Set(linkedWorkOrderIds)),
      remarks: remarks,
      coverage_exception_reason: coverageExceptionReason,
      submit: !!submit
    };
  },

  // 提交审批
  onSubmit() {
    if (this.data.submitting) return;
    // 前端基本校验
    const hasSites = this.data.days.some(d => d.sites.length > 0);
    if (!hasSites) {
      wx.showToast({ title: '请至少安排一天的巡检站点', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    const payload = this.buildPayload(true);

    // 先校验
    api.validatePlanSchedule(Object.assign({ user_id: (getUser() || {}).id }, payload))
      .then(vr => {
        if (vr.errors && vr.errors.length) {
          this.setData({ submitting: false });
          wx.showModal({
            title: '校验不通过',
            content: vr.errors.join('\n'),
            showCancel: false
          });
          return Promise.reject('blocked');
        }
        // 有警告时提示但允许继续
        if (vr.warnings && vr.warnings.length) {
          return new Promise((resolve, reject) => {
            wx.showModal({
              title: '提示',
              content: vr.warnings.join('\n') + '\n\n仍要提交吗？',
              success(r) { r.confirm ? resolve() : reject('cancel'); }
            });
          });
        }
      })
      .then(() => {
        // 创建或更新
        if (this.data.editId) {
          return api.updatePlanSchedule(this.data.editId, payload)
            .then(() => api.submitPlanSchedule(this.data.editId));
        }
        return api.createPlanSchedule(payload);
      })
      .then(() => {
        wx.showToast({ title: '已提交审批', icon: 'success' });
        setTimeout(() => wx.navigateBack(), 1200);
      })
      .catch(err => {
        if (err === 'blocked' || err === 'cancel') return;
        wx.showToast({ title: (err && err.message) || '提交失败', icon: 'none' });
      })
      .finally(() => this.setData({ submitting: false }));
  },

  // 存草稿
  onSaveDraft() {
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    const payload = this.buildPayload(false);
    const p = this.data.editId
      ? api.updatePlanSchedule(this.data.editId, payload)
      : api.createPlanSchedule(payload);
    p.then(() => {
      wx.showToast({ title: '已保存草稿', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 1000);
    })
    .catch(err => {
      wx.showToast({ title: (err && err.message) || '保存失败', icon: 'none' });
    })
    .finally(() => this.setData({ submitting: false }));
  }
});
