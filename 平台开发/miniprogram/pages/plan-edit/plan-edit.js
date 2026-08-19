const api = require('../../services/api.js');
const maps = require('../../services/maps.js');
const { getUser, getSites } = require('../../utils/auth.js');

const app = getApp();
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const MAX_PERIOD_DAYS = 366;

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

function periodDates(start, end) {
  const parse = value => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    if (!match) return null;
    const parts = match.slice(1).map(Number);
    const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    return date.getUTCFullYear() === parts[0]
      && date.getUTCMonth() === parts[1] - 1
      && date.getUTCDate() === parts[2] ? date : null;
  };
  const startDate = parse(start);
  const endDate = parse(end);
  if (!startDate || !endDate || startDate > endDate) {
    throw new Error('周期日期无效，请检查开始和结束日期');
  }
  const count = Math.floor((endDate - startDate) / 86400000) + 1;
  if (count > MAX_PERIOD_DAYS) throw new Error('周期最多支持366天，请缩短日期范围');
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(startDate.getTime() + index * 86400000);
    return [date.getUTCFullYear(), ('0' + (date.getUTCMonth() + 1)).slice(-2),
      ('0' + date.getUTCDate()).slice(-2)].join('-');
  });
}

function reconcilePeriodDays(start, end, existing, fillRange) {
  const dates = periodDates(start, end);
  const allowed = new Set(dates);
  const byDate = new Map();
  (existing || []).forEach(day => {
    if (!day || !allowed.has(day.date)) return;
    const inspectionItems = {};
    Object.keys(day.inspection_items || {}).forEach(key => {
      const value = day.inspection_items[key];
      inspectionItems[key] = Array.isArray(value) ? value.slice() : value;
    });
    byDate.set(day.date, Object.assign({}, day, {
      date: day.date,
      weekday_cn: weekdayCn(day.date),
      sites: Array.isArray(day.sites) ? day.sites.slice() : [],
      notes: day.notes || '',
      vehicle_id: day.vehicle_id || null,
      inspection_items: inspectionItems
    }));
  });
  const targetDates = fillRange ? dates : [...byDate.keys()].sort();
  return targetDates.map(date => byDate.get(date) || {
    date, weekday_cn: weekdayCn(date), sites: [], vehicle_id: null,
    notes: '', inspection_items: {}
  });
}

function initializeInspectionItemSelections(days, options) {
  return (days || []).map(day => {
    const selected = Object.assign({}, day.inspection_items || {});
    (day.sites || []).forEach(siteId => {
      const key = String(siteId);
      if (!(key in selected)) {
        selected[key] = (options && options[siteId] || []).map(item => Number(item.id));
      }
    });
    return Object.assign({}, day, { inspection_items: selected });
  });
}

function dayHasBusinessContent(day) {
  const inspectionItems = day && day.inspection_items;
  return !!(day && (
    (Array.isArray(day.sites) && day.sites.length)
    || String(day.notes || '').trim()
    || day.vehicle_id
    || (inspectionItems && typeof inspectionItems === 'object'
        && Object.keys(inspectionItems).some(key => Array.isArray(inspectionItems[key])
          ? inspectionItems[key].length > 0 : !!inspectionItems[key]))
  ));
}

function planSubmitFingerprint(payload) {
  const businessPayload = Object.assign({}, payload);
  delete businessPayload.version;
  return JSON.stringify(businessPayload);
}

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
    version: null,
    scheduleType: 'weekly',
    scheduleTypeOptions: ['周检', '月检', '季检', '年检'],
    typeKeys: ['weekly', 'monthly', 'quarterly', 'yearly'],
    periodStart: '',
    periodEnd: '',
    days: [],           // [{date, weekday_cn, sites:[], vehicle_id:null}]
    mySites: [],        // [{id, name}]
    vehicles: [],       // [{id, name/plate_number}]
    partsInventory: [], // 可用于本次出车的备件库存
    partsInventoryState: 'loading', // loading | ready | empty | unavailable
    selectedParts: [],  // [{part_id, part_name, quantity}]
    linkedWorkOrderIds: [],
    suggestions: [],    // [{type, site_id, site_name, text, level}]
    siteScores: {},     // {site_id: score}
    templateContext: [],
    inspectionItemOptions: {},
    inspectionItemsState: 'idle', // idle | loading | ready | unavailable
    inspectionItemsError: '',
    coverageWarning: '',
    remarks: '',
    coverageExceptionReason: '',
    noVehicleRequired: false,
    planVehicleId: null,
    vehicleExceptionReason: '',
    submitting: false,
    submitError: '',
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

  // 周检自动展开周期日期；月、季、年只保留用户选择的实际执行日期。
  initPeriod(type) {
    const [start, end] = periodRange(type);
    try {
      const days = reconcilePeriodDays(start, end, [], type === 'weekly');
      this.setData({ scheduleType: type, periodStart: start, periodEnd: end, days, loaded: true }, () => this.loadInspectionItems());
    } catch (err) {
      wx.showToast({ title: err.message || '周期日期无效', icon: 'none' });
      this.setData({ loaded: true });
    }
  },

  // 切换频次（仅新建时可切换；编辑已有排程锁定频次）
  onTypeChange(e) {
    if (this.data.editId) return;
    const idx = parseInt(e.detail.value);
    const type = this.data.typeKeys[idx];
    this.initPeriod(type);
    this.loadSuggestions(type);
  },

  // 月、季、年选择实际执行日期；周检日期由周期自动生成。
  onAddDay(e) {
    if (this.data.scheduleType === 'weekly') return;
    const date = e.detail.value;
    if (!date) return;
    if (date < this.data.periodStart || date > this.data.periodEnd) {
      wx.showToast({ title: '日期需在周期范围内', icon: 'none' });
      return;
    }
    if (this.data.days.some(d => d.date === date)) {
      wx.showToast({ title: '该日期已选择', icon: 'none' });
      return;
    }
    try {
      const days = reconcilePeriodDays(this.data.periodStart, this.data.periodEnd,
        this.data.days.concat([{ date, sites: [], notes: '', inspection_items: {} }]), false);
      this.setData({ days }, () => this.refreshValidation());
    } catch (err) {
      wx.showToast({ title: err.message || '日期无效', icon: 'none' });
    }
  },

  // 长周期可移除实际执行日；周检日期行始终跟随周期。
  onRemoveDay(e) {
    if (this.data.scheduleType === 'weekly') return;
    const date = e.currentTarget.dataset.date;
    this.setData({ days: this.data.days.filter(d => d.date !== date) }, () => this.refreshValidation());
  },

  updatePeriod(field, value) {
    const start = field === 'periodStart' ? value : this.data.periodStart;
    const end = field === 'periodEnd' ? value : this.data.periodEnd;
    try {
      periodDates(start, end);
    } catch (err) {
      wx.showToast({ title: err.message || '周期日期无效', icon: 'none' });
      return;
    }
    const outside = (this.data.days || []).filter(day =>
      (day.date < start || day.date > end) && dayHasBusinessContent(day));
    if (outside.length) {
      wx.showModal({
        title: '先调整已有执行日期',
        content: `${outside.map(day => day.date).join('、')} 超出新周期。请先移除或改回周期，系统不会静默删除安排。`,
        showCancel: false
      });
      return;
    }
    try {
      const days = reconcilePeriodDays(start, end, this.data.days, this.data.scheduleType === 'weekly');
      this.setData({ [field]: value, days }, () => this.refreshValidation());
    } catch (err) {
      wx.showToast({ title: err.message || '周期日期无效', icon: 'none' });
    }
  },

  onPeriodStart(e) { this.updatePeriod('periodStart', e.detail.value); },
  onPeriodEnd(e) { this.updatePeriod('periodEnd', e.detail.value); },

  // 加载已有排程
  loadExisting(id) {
    api.planScheduleDetail(id)
      .then(res => {
        const planData = res.plan_data || {};
        const vehicleDays = res.vehicle_days || {};
        const legacyVehicleIds = [...new Set(Object.values(vehicleDays).filter(Boolean).map(Number))];
        const start = res.period_start;
        const end = res.period_end;
        const type = res.schedule_type || 'weekly';
        const existingDays = [];
        Object.keys(planData).sort().forEach(date => {
          const dayPlan = planData[date] || {};
          const day = {
            date, weekday_cn: weekdayCn(date),
            sites: dayPlan.sites || [], vehicle_id: vehicleDays[date] || null, notes: dayPlan.notes || '',
            inspection_items: dayPlan.inspection_items || {}
          };
          if (dayHasBusinessContent(day)) existingDays.push(day);
        });
        const days = reconcilePeriodDays(start, end, existingDays, type === 'weekly');
        this.setData({
          loaded: true,
          editId: res.id,
          version: Number(res.version || 1),
          scheduleType: res.schedule_type || 'weekly',
          periodStart: start,
          periodEnd: end,
          days,
          selectedParts: Array.isArray(res.spare_parts) ? res.spare_parts : [],
          linkedWorkOrderIds: Array.isArray(res.work_order_ids) ? res.work_order_ids : [],
          templateContext: Array.isArray(res.template_context) ? res.template_context : [],
          remarks: res.remarks || '',
          coverageExceptionReason: res.coverage_exception_reason || '',
          noVehicleRequired: !!res.vehicle_exception_reason,
          planVehicleId: res.vehicle_id || (legacyVehicleIds.length === 1 ? legacyVehicleIds[0] : null),
          vehicleExceptionReason: res.vehicle_exception_reason || '',
          isChange: res.status === 'modifying',
          changeReason: res.change_reason || ''
        }, () => { this.loadVehicles(); this.loadInspectionItems(); });
      })
      .catch(err => {
        wx.showToast({ title: (err && (err.error || err.message)) || '加载失败', icon: 'none' });
        this.setData({ loaded: true });
      });
  },

  loadVehicles() {
    api.vehicles()
      .then(res => {
        const selectedIds = new Set((this.data.days || [])
          .map(day => Number(day.vehicle_id || 0)).filter(Boolean));
        const vehicles = (Array.isArray(res) ? res : []).map(v => ({
          id: v.id,
          name: v.plate_no || v.plate_number || v.name || ('车辆#' + v.id),
          disabled: !v.dispatchable
        })).filter(v => !v.disabled || selectedIds.has(Number(v.id)));
        this.setData({ vehicles });
      })
      .catch(() => {});
  },

  loadPartsInventory() {
    this.setData({ partsInventoryState: 'loading' });
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
        this.setData({ partsInventory, partsInventoryState: partsInventory.length ? 'ready' : 'empty' });
      })
      .catch(() => this.setData({ partsInventory: [], partsInventoryState: 'unavailable' }));
  },

  loadSuggestions(scheduleType) {
    const u = getUser();
    if (!u || !u.id) return;
    api.planSuggestions(u.id, scheduleType || this.data.scheduleType)
      .then(res => {
        this.setData({
          suggestions: res.suggestions || [],
          siteScores: res.site_scores || {},
          templateContext: Array.isArray(res.template_context) ? res.template_context : []
        });
      })
      .catch(() => {});
  },

  // 切换某天的站点选中
  onToggleSite(e) {
    const { dayIdx, siteId } = e.currentTarget.dataset;
    const key = 'days[' + dayIdx + '].sites';
    let sites = this.data.days[dayIdx].sites.slice();
    const inspectionItems = Object.assign({}, this.data.days[dayIdx].inspection_items || {});
    const pos = sites.indexOf(siteId);
    if (pos > -1) {
      sites.splice(pos, 1);
      delete inspectionItems[String(siteId)];
    } else {
      sites.push(siteId);
    }
    this.setData({
      [key]: sites,
      ['days[' + dayIdx + '].inspection_items']: inspectionItems
    }, () => { this.loadInspectionItems(); this.refreshValidation(); });
  },

  // 一键全选/清空当天
  onToggleAll(e) {
    const dayIdx = e.currentTarget.dataset.dayIdx;
    const key = 'days[' + dayIdx + '].sites';
    const cur = this.data.days[dayIdx].sites;
    const allIds = this.data.mySites.map(s => s.id);
    // 如果已全选则清空，否则全选
    const allSelected = allIds.every(id => cur.indexOf(id) > -1);
    const updates = { [key]: allSelected ? [] : allIds.slice() };
    if (allSelected) updates['days[' + dayIdx + '].inspection_items'] = {};
    this.setData(updates, () => { this.loadInspectionItems(); this.refreshValidation(); });
  },

  onDayNotes(e) {
    const dayIdx = e.currentTarget.dataset.dayIdx;
    if (!this.data.days[dayIdx]) return;
    this.setData({ ['days[' + dayIdx + '].notes']: e.detail.value }, () => this.refreshValidation());
  },

  loadInspectionItems() {
    const requestId = (this._inspectionItemsRequest || 0) + 1;
    this._inspectionItemsRequest = requestId;
    const siteIds = [...new Set((this.data.days || []).flatMap(day => day.sites || []).map(Number).filter(Boolean))];
    if (!siteIds.length) {
      this.setData({ inspectionItemOptions: {}, inspectionItemsState: 'ready', inspectionItemsError: '' });
      return;
    }
    const pendingOptions = {};
    siteIds.forEach(siteId => { pendingOptions[siteId] = this.data.inspectionItemOptions[siteId] || []; });
    this.setData({ inspectionItemOptions: pendingOptions, inspectionItemsState: 'loading', inspectionItemsError: '' });
    const scheduleType = this.data.scheduleType;
    Promise.all(siteIds.map(siteId => api.inspectionConfigMatches(siteId, scheduleType)
      .then(result => [siteId, Array.isArray(result && result.items) ? result.items : []])))
      .then(entries => {
        if (requestId !== this._inspectionItemsRequest) return;
        const inspectionItemOptions = {};
        entries.forEach(([siteId, items]) => { inspectionItemOptions[siteId] = items; });
        this.setData({
          inspectionItemOptions,
          days: initializeInspectionItemSelections(this.data.days, inspectionItemOptions),
          inspectionItemsState: 'ready',
          inspectionItemsError: ''
        });
      })
      .catch(err => {
        if (requestId !== this._inspectionItemsRequest) return;
        this.setData({ inspectionItemOptions: {}, inspectionItemsState: 'unavailable',
          inspectionItemsError: (err && (err.error || err.message)) || '检查项加载失败，请重试后再保存' });
      });
  },

  onToggleInspectionItem(e) {
    const { dayIdx, siteId, itemId } = e.currentTarget.dataset;
    const day = this.data.days[dayIdx];
    if (!day) return;
    const selected = Object.assign({}, day.inspection_items || {});
    const key = String(siteId);
    const ids = (selected[key] || []).map(Number);
    const position = ids.indexOf(Number(itemId));
    if (position >= 0) ids.splice(position, 1); else ids.push(Number(itemId));
    selected[key] = ids;
    this.setData({ ['days[' + dayIdx + '].inspection_items']: selected }, () => this.refreshValidation());
  },

  onPlanVehicleChange(e) {
    const idx = parseInt(e.detail.value);
    const vehicleId = idx >= 0 && this.data.vehicles[idx] ? this.data.vehicles[idx].id : null;
    this.setData({ planVehicleId: vehicleId }, () => this.refreshValidation());
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

  onNoVehicleRequired(e) {
    const enabled = !!e.detail.value;
    const updates = { noVehicleRequired: enabled };
    if (enabled) {
      updates.days = this.data.days.map(day => Object.assign({}, day, { vehicle_id: null }));
      updates.planVehicleId = null;
    } else {
      updates.vehicleExceptionReason = '';
    }
    this.setData(updates, () => this.refreshValidation());
  },

  onVehicleExceptionReason(e) {
    this.setData({ vehicleExceptionReason: e.detail.value }, () => this.refreshValidation());
  },

  // 构建请求体
  buildPayload(submit) {
    const { scheduleType, periodStart, periodEnd, days, remarks, coverageExceptionReason,
      noVehicleRequired, vehicleExceptionReason, selectedParts, suggestions, version, planVehicleId } = this.data;
    const planData = {};
    const vehicleDays = {};
    days.forEach(d => {
      if (d.sites.length) {
        const inspectionItems = {};
        d.sites.forEach(siteId => {
          const ids = d.inspection_items && d.inspection_items[String(siteId)];
          if (Array.isArray(ids)) inspectionItems[String(siteId)] = [...new Set(ids.map(Number).filter(Number.isInteger))];
        });
        planData[d.date] = { sites: d.sites, notes: d.notes || '', inspection_items: inspectionItems };
        if (!noVehicleRequired && planVehicleId) {
          vehicleDays[d.date] = planVehicleId;
        }
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
      vehicle_id: noVehicleRequired ? null : (planVehicleId || null),
      spare_parts: selectedParts,
      work_order_ids: Array.from(new Set(linkedWorkOrderIds)),
      remarks: remarks,
      coverage_exception_reason: coverageExceptionReason,
      vehicle_exception_reason: noVehicleRequired ? vehicleExceptionReason.trim() : '',
      submit: !!submit,
      ...(this.data.editId ? { version } : {})
    };
  },

  // 将后端结构化风险直接挂到对应日期，避免用户只看到文字后再手工查找站点。
  applyValidation(vr) {
    const details = vr.warning_details || [];
    const byDate = {};
    let coverageWarning = '';
    details.forEach(w => {
      if (w.type === 'coverage_missing') coverageWarning = w.text;
      if (w.date) (byDate[w.date] || (byDate[w.date] = [])).push(w.text);
    });
    const days = this.data.days.map(d => Object.assign({}, d, { warning_text: (byDate[d.date] || []).join('\n') }));
    this.setData({ days, coverageWarning });
  },

  refreshValidation() {
    if (this._validationTimer) clearTimeout(this._validationTimer);
    this._validationTimer = setTimeout(() => {
      const payload = this.buildPayload(false);
      api.validatePlanSchedule(Object.assign({ user_id: (getUser() || {}).id }, payload))
        .then(vr => this.applyValidation(vr || {}))
        .catch(() => {});
    }, 250);
  },

  // 提交审批
  onSubmit() {
    if (this.data.submitting) return;
    if (this.data.inspectionItemsState !== 'ready') {
      wx.showToast({ title: this.data.inspectionItemsError || '检查项尚未加载完成', icon: 'none' });
      return;
    }
    // 前端基本校验
    const hasSites = this.data.days.some(d => d.sites.length > 0);
    if (!hasSites) {
      wx.showToast({ title: '请至少安排一天的巡检站点', icon: 'none' });
      return;
    }
    this.setData({ submitting: true, submitError: '' });
    const payload = this.buildPayload(true);
    const payloadFingerprint = planSubmitFingerprint(payload);
    const pendingSubmit = this._pendingFormalSubmit;
    const retrySavedSubmit = !!(this.data.editId && pendingSubmit
      && pendingSubmit.version === this.data.version
      && pendingSubmit.fingerprint === payloadFingerprint);

    const submission = retrySavedSubmit
      ? api.submitPlanSchedule(this.data.editId, pendingSubmit.version)
      : api.validatePlanSchedule(Object.assign({ user_id: (getUser() || {}).id }, payload))
      .then(vr => {
        this.applyValidation(vr || {});
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
          return api.updatePlanSchedule(this.data.editId, payload, { queue: false })
            .then(saved => {
              this.setData({ version: saved.version });
              this._pendingFormalSubmit = {
                version: saved.version,
                fingerprint: payloadFingerprint,
              };
              return api.submitPlanSchedule(this.data.editId, saved.version);
            });
        }
        return api.createPlanSchedule(payload);
      });

    submission
      .then(submitted => {
        if (this.data.isChange && (!submitted || submitted.status !== 'change_submitted')) {
          throw { error: '服务端未确认计划变更已进入待审核，请直接重试' };
        }
        this._pendingFormalSubmit = null;
        wx.showToast({ title: '已提交审批', icon: 'success' });
        setTimeout(() => wx.navigateBack(), 1200);
      })
      .catch(err => {
        if (err === 'blocked' || err === 'cancel') return;
        const message = (err && (err.error || err.message)) || '提交失败，请重试';
        const nextAction = err && err.code === 'PLAN_VERSION_CONFLICT'
          ? '计划已被其他操作更新，请返回计划详情刷新后重新编辑。'
          : '修改内容已保留，请确认后直接重试。';
        const failureText = message + '\n' + nextAction;
        this.setData({ submitError: failureText });
        wx.showModal({
          title: '提交未完成',
          content: failureText,
          showCancel: false
        });
      })
      .finally(() => this.setData({ submitting: false }));
  },

  // 存草稿
  onSaveDraft() {
    if (this.data.submitting) return;
    if (this.data.inspectionItemsState !== 'ready') {
      wx.showToast({ title: this.data.inspectionItemsError || '检查项尚未加载完成', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    const payload = this.buildPayload(false);
    const p = this.data.editId
      ? api.updatePlanSchedule(this.data.editId, payload)
      : api.createPlanSchedule(payload);
    p.then(saved => {
      if (this.data.editId && saved && saved.version) this.setData({ version: saved.version });
      const issueCount = Number(saved && saved.draft_issue_count || 0);
      wx.showToast({ title: issueCount ? `已保存，${issueCount}项待完善` : '已保存草稿', icon: 'none' });
      setTimeout(() => wx.navigateBack(), 1000);
    })
    .catch(err => {
      wx.showToast({ title: (err && (err.error || err.message)) || '保存失败', icon: 'none' });
    })
    .finally(() => this.setData({ submitting: false }));
  }
});

module.exports = {
  initializeInspectionItemSelections, dayHasBusinessContent, planSubmitFingerprint,
  periodDates, reconcilePeriodDays
};
