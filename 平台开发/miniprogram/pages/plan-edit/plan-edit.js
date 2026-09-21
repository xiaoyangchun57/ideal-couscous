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

function initializeInspectionItemSelections(days, options, scheduleType = 'weekly') {
  return (days || []).map(day => {
    const selected = Object.assign({}, day.inspection_items || {});
    (day.sites || []).forEach(siteId => {
      const key = String(siteId);
      if (!(key in selected) && options && Object.prototype.hasOwnProperty.call(options, siteId)) {
        selected[key] = (options && options[siteId] || [])
          .filter(item => !item.frequency || item.frequency === scheduleType)
          .map(item => Number(item.id));
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

function inspectionGroupsForDays(days, options, scheduleType) {
  return (days || []).map(day => {
    const groups = {};
    (day.sites || []).forEach(siteId => {
      const selected = new Set((day.inspection_items && day.inspection_items[siteId] || []).map(Number));
      groups[siteId] = (scheduleType === 'weekly' ? ['weekly', 'monthly', 'quarterly'] : [scheduleType]).map(frequency => {
        const items = (options[siteId] || []).filter(item => (item.frequency || scheduleType) === frequency);
        const overdue = items.filter(item => item.due_status === 'overdue').length;
        const soon = items.filter(item => item.due_status === 'due_soon').length;
        return {
          frequency, label: SCHEDULE_LABELS[frequency], optional: frequency !== scheduleType,
          expanded: frequency === scheduleType || !!(day.expandedInspectionGroups && day.expandedInspectionGroups[siteId + ':' + frequency]),
          count: items.length, selectedCount: items.filter(item => selected.has(Number(item.id))).length,
          dueLabels: frequency === 'weekly' ? '' : [overdue ? overdue + '项已到期' : '',
            soon ? soon + '项临近周期' : ''].filter(Boolean).join('；'), items
        };
      }).filter(group => group.count > 0);
    });
    return Object.assign({}, day, { inspectionGroups: groups });
  });
}

function planSubmitFingerprint(payload) {
  const businessPayload = Object.assign({}, payload);
  delete businessPayload.version;
  return JSON.stringify(businessPayload);
}

const SCHEDULE_LABELS = { weekly: '周检', monthly: '月检', quarterly: '季检', yearly: '年检' };
const REFERENCE_LABELS = {
  work_order: '工单', alert: '告警', manual_report: '人工上报'
};

function availableSiteIds(mySites) {
  const seen = new Set();
  return (Array.isArray(mySites) ? mySites : []).reduce((ids, site) => {
    if (!site || site.id === undefined || site.id === null) return ids;
    const key = String(site.id);
    if (!seen.has(key)) {
      seen.add(key);
      ids.push(site.id);
    }
    return ids;
  }, []);
}

function hasAllAvailableSites(selectedSiteIds, mySites) {
  const allIds = availableSiteIds(mySites);
  if (!allIds.length) return false;
  const selected = new Set((Array.isArray(selectedSiteIds) ? selectedSiteIds : []).map(String));
  return allIds.every(id => selected.has(String(id)));
}

function withDaySiteSelectionState(days, mySites) {
  return (Array.isArray(days) ? days : []).map(day => Object.assign({}, day, {
    allSitesSelected: hasAllAvailableSites(day && day.sites, mySites)
  }));
}

function summarizeTemplateContext(context, scheduleType) {
  const grouped = new Map();
  (Array.isArray(context) ? context : []).forEach(item => {
    if (!item || !item.template_name) return;
    const itemCount = Number(item.item_count) || 0;
    const key = JSON.stringify([item.template_name, itemCount]);
    if (!grouped.has(key)) grouped.set(key, {
      key, template_name: item.template_name, item_count: itemCount, siteIds: new Set()
    });
    grouped.get(key).siteIds.add(String(item.site_id || item.site_name || ''));
  });
  return [...grouped.values()].map(item => {
    const siteCount = item.siteIds.size;
    return {
      key: item.key, template_name: item.template_name, item_count: item.item_count,
      site_count: siteCount,
      label: `${SCHEDULE_LABELS[scheduleType] || '本周期'}可选检查项：${item.item_count}项 · 适用${siteCount}个负责站点`
    };
  });
}

function schedulingReferencePresentation(suggestions) {
  const source = Array.isArray(suggestions) ? suggestions : [];
  const seen = new Set();
  const items = [];
  source.forEach(item => {
    if (!item || item.type === 'priority' || item.type === 'reagent') return;
    const key = [item.type, item.site_id, item.ref_id, item.text].join('|');
    if (seen.has(key)) return;
    seen.add(key);
    items.push(item);
  });
  const counts = {};
  items.forEach(item => { counts[item.type] = (counts[item.type] || 0) + 1; });
  const summary = Object.keys(REFERENCE_LABELS)
    .filter(type => counts[type])
    .map(type => REFERENCE_LABELS[type] + counts[type])
    .join(' · ');
  return { items, total: items.length, summary };
}

function projectSiteNameMap(siteMap, mySites) {
  const names = {};
  (Array.isArray(mySites) ? mySites : []).forEach(site => {
    if (!site || site.id === undefined || !String(site.name || '').trim()) return;
    names[String(site.id)] = String(site.name).trim();
  });
  Object.keys(siteMap && typeof siteMap === 'object' ? siteMap : {}).forEach(key => {
    const site = siteMap[key];
    if (!site || !String(site.name || '').trim()) return;
    const id = site.id === undefined || site.id === null ? key : site.id;
    names[String(id)] = String(site.name).trim();
  });
  return names;
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
    displaySuggestions: [],
    referenceSummary: '',
    referenceExpanded: false,
    partsExpanded: false,
    remarkExpanded: false,
    siteScores: {},     // {site_id: score}
    templateContext: [],
    templateSummaries: [],
    inspectionItemOptions: {},
    inspectionItemsState: 'idle', // idle | loading | ready | unavailable
    inspectionItemsError: '',
    inspectionSiteStates: {},
    coverageWarning: '',
    remarks: '',
    coverageExceptionReason: '',
    noVehicleRequired: false,
    planVehicleId: null,
    vehicleExceptionReason: '',
    vehicleAdjustmentRequired: false,
    submitting: false,
    submitError: '',
    draftSaveError: '',
    loaded: false,
    detailState: 'loading', // loading | ready | error
    detailLoadError: '',
    rejectReason: '',
    siteNameById: {},
    isChange: false,      // 是否为变更编辑（modifying 状态）
    changeReason: ''
  },

  onLoad(opts) {
    this._pageAlive = true;
    this._pageActive = true;
    this._detailLoadRequestId = 0;
    this._detailLoadPending = false;
    this.setPageTitle(opts.id ? '编辑计划' : '新建计划');
    if (!app.globalData.token) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    const mySites = (getSites() || []).map(s => ({ id: s.id, name: s.name }));
    this.setData({ mySites, siteNameById: projectSiteNameMap(null, mySites) });

    if (opts.id) {
      // 编辑已有排程
      this.setData({ editId: parseInt(opts.id) });
      this.loadExisting(opts.id);
    } else {
      this.setData({ detailState: 'ready', detailLoadError: '' });
      this.initPeriod('weekly');
    }
    this.loadVehicles();
    this.loadPartsInventory();
    this.loadSuggestions();
  },

  setPageTitle(title) {
    if (wx.setNavigationBarTitle) wx.setNavigationBarTitle({ title });
  },

  onShow() {
    this._pageActive = true;
    if (this._inspectionReloadNeeded && this.data.detailState === 'ready') {
      this._inspectionReloadNeeded = false;
      this.loadInspectionItems();
    }
    if (this.data.editId && this.data.detailState === 'loading' && !this._detailLoadPending) {
      this.loadExisting(this.data.editId);
    }
  },

  onHide() {
    this._pageActive = false;
    this._validationSerial = (this._validationSerial || 0) + 1;
    if (this._validationTimer) clearTimeout(this._validationTimer);
    this._inspectionReloadNeeded = Object.values(this.data.inspectionSiteStates).some(state => state.state === 'loading');
    this._inspectionItemsRequest = (this._inspectionItemsRequest || 0) + 1;
    this._detailLoadRequestId = (this._detailLoadRequestId || 0) + 1;
    this._detailLoadPending = false;
  },

  onUnload() {
    this._pageAlive = false;
    this._validationSerial = (this._validationSerial || 0) + 1;
    this._pageActive = false;
    this._inspectionItemsRequest = (this._inspectionItemsRequest || 0) + 1;
    this._detailLoadRequestId = (this._detailLoadRequestId || 0) + 1;
    this._detailLoadPending = false;
    if (this._validationTimer) clearTimeout(this._validationTimer);
  },

  // 周检自动展开周期日期；月、季、年只保留用户选择的实际执行日期。
  initPeriod(type) {
    const [start, end] = periodRange(type);
    try {
      const days = withDaySiteSelectionState(
        reconcilePeriodDays(start, end, [], type === 'weekly'), this.data.mySites);
      this.setData({
        scheduleType: type, periodStart: start, periodEnd: end, days,
        loaded: true, detailState: 'ready', detailLoadError: ''
      }, () => this.loadInspectionItems());
    } catch (err) {
      wx.showToast({ title: err.message || '周期日期无效', icon: 'none' });
      this.setData({ loaded: true, detailState: 'ready', detailLoadError: '' });
    }
  },

  // 切换频次（仅新建时可切换；编辑已有排程锁定频次）
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
      const days = withDaySiteSelectionState(reconcilePeriodDays(this.data.periodStart, this.data.periodEnd,
        this.data.days.concat([{ date, sites: [], notes: '', inspection_items: {} }]), false), this.data.mySites);
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
      const days = withDaySiteSelectionState(reconcilePeriodDays(
        start, end, this.data.days, this.data.scheduleType === 'weekly'), this.data.mySites);
      this.setData({ [field]: value, days }, () => this.refreshValidation());
    } catch (err) {
      wx.showToast({ title: err.message || '周期日期无效', icon: 'none' });
    }
  },

  onPeriodStart(e) { this.updatePeriod('periodStart', e.detail.value); },
  onPeriodEnd(e) { this.updatePeriod('periodEnd', e.detail.value); },

  // 加载已有排程
  loadExisting(id) {
    const editId = Number(id || this.data.editId);
    if (!editId) return;
    if (this._detailLoadPending && Number(this.data.editId) === editId) {
      wx.showToast({ title: '计划详情正在加载，请稍候', icon: 'none' });
      return;
    }
    const requestId = (this._detailLoadRequestId || 0) + 1;
    this._detailLoadRequestId = requestId;
    this._detailLoadPending = true;
    this.setData({
      editId, loaded: false, detailState: 'loading', detailLoadError: '',
      draftSaveError: '', submitError: ''
    });
    const isCurrent = () => this._pageAlive !== false && this._pageActive !== false
      && this._detailLoadRequestId === requestId && Number(this.data.editId) === editId;
    api.planScheduleDetail(editId)
      .then(res => {
        if (!isCurrent()) return;
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
        const days = withDaySiteSelectionState(
          reconcilePeriodDays(start, end, existingDays, type === 'weekly'), this.data.mySites);
        const resolvedVehicleId = res.vehicle_id || (legacyVehicleIds.length === 1 ? legacyVehicleIds[0] : null);
        this.setData({
          loaded: true,
          detailState: 'ready',
          detailLoadError: '',
          editId: res.id,
          version: Number(res.version || 1),
          scheduleType: res.schedule_type || 'weekly',
          periodStart: start,
          periodEnd: end,
          days,
          selectedParts: Array.isArray(res.spare_parts) ? res.spare_parts : [],
          linkedWorkOrderIds: Array.isArray(res.work_order_ids) ? res.work_order_ids : [],
          templateContext: Array.isArray(res.template_context) ? res.template_context : [],
          rejectReason: res.reject_reason || '',
          siteNameById: projectSiteNameMap(res.site_map, this.data.mySites),
          remarks: res.remarks || '',
          coverageExceptionReason: res.coverage_exception_reason || '',
          noVehicleRequired: !!res.no_vehicle_required,
          planVehicleId: resolvedVehicleId,
          vehicleExceptionReason: res.vehicle_exception_reason || '',
          vehicleAdjustmentRequired: res.vehicle_adjustment_required === true,
          isChange: res.status === 'modifying',
          changeReason: res.change_reason || ''
        }, () => {
          if (!isCurrent()) return;
          this.setPageTitle(res.status === 'modifying' ? '计划变更' : '编辑计划');
          this.loadVehicles();
          this.loadInspectionItems();
        });
      })
      .catch(err => {
        if (!isCurrent()) return;
        const reason = (err && (err.error || err.message)) || '计划详情加载失败';
        this.setData({ loaded: false, detailState: 'error', detailLoadError: reason });
      })
      .finally(() => {
        if (this._detailLoadRequestId === requestId) this._detailLoadPending = false;
      });
  },

  onRetryLoadExisting() {
    if (this._detailLoadPending) {
      wx.showToast({ title: '计划详情正在加载，请稍候', icon: 'none' });
      return;
    }
    this.loadExisting(this.data.editId);
  },

  loadVehicles() {
    api.vehicles()
      .then(res => {
        const selectedIds = new Set((this.data.days || [])
          .map(day => Number(day.vehicle_id || 0)).filter(Boolean));
        const vehicles = (Array.isArray(res) ? res : []).map(v => ({
          id: v.id,
          name: v.plate_no || v.plate_number || v.name || ('车辆#' + v.id),
          disabled: v.schedulable === false
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
        const suggestions = Array.isArray(res.suggestions) ? res.suggestions : [];
        const templateContext = Array.isArray(res.template_context) ? res.template_context : [];
        const reference = schedulingReferencePresentation(suggestions);
        this.setData({
          suggestions,
          displaySuggestions: reference.items,
          referenceSummary: reference.summary,
          referenceExpanded: false,
          siteScores: res.site_scores || {},
          templateContext,
          templateSummaries: summarizeTemplateContext(templateContext,
            scheduleType || this.data.scheduleType)
        });
      })
      .catch(() => this.setData({ suggestions: [], displaySuggestions: [], referenceSummary: '',
        referenceExpanded: false, templateContext: [], templateSummaries: [] }));
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
      ['days[' + dayIdx + '].inspection_items']: inspectionItems,
      ['days[' + dayIdx + '].allSitesSelected']: hasAllAvailableSites(sites, this.data.mySites)
    }, () => { this.loadInspectionItems(); this.refreshValidation(); });
  },

  // 一键全选/清空当天
  onToggleAll(e) {
    const dayIdx = e.currentTarget.dataset.dayIdx;
    const key = 'days[' + dayIdx + '].sites';
    const cur = this.data.days[dayIdx].sites;
    const allIds = availableSiteIds(this.data.mySites);
    // 如果已全选则清空，否则全选
    const allSelected = hasAllAvailableSites(cur, this.data.mySites);
    const updates = {
      [key]: allSelected ? [] : allIds.slice(),
      ['days[' + dayIdx + '].allSitesSelected']: !allSelected && allIds.length > 0
    };
    if (allSelected) updates['days[' + dayIdx + '].inspection_items'] = {};
    this.setData(updates, () => { this.loadInspectionItems(); this.refreshValidation(); });
  },

  onDayNotes(e) {
    const dayIdx = e.currentTarget.dataset.dayIdx;
    if (!this.data.days[dayIdx]) return;
    this.setData({ ['days[' + dayIdx + '].notes']: e.detail.value }, () => this.refreshValidation());
  },

  projectInspectionState() {
    const ids = [...new Set(this.data.days.flatMap(day => day.sites || []).map(Number))];
    const states = ids.map(id => (this.data.inspectionSiteStates[id] || {}).state || 'loading');
    const confirmed = {};
    ids.forEach(id => {
      const state = this.data.inspectionSiteStates[id];
      if (state && state.state === 'ready' && state.frequency === this.data.scheduleType) confirmed[id] = this.data.inspectionItemOptions[id] || [];
    });
    this.setData({ inspectionItemsState: states.includes('unavailable') ? 'unavailable'
      : states.includes('loading') ? 'loading' : 'ready',
      inspectionItemsError: states.includes('unavailable') ? '部分站点检查项加载失败，请重试该站点' : '',
      days: inspectionGroupsForDays(initializeInspectionItemSelections(this.data.days, confirmed, this.data.scheduleType),
        this.data.inspectionItemOptions, this.data.scheduleType) });
  },

  loadInspectionItems(event) {
    this.updateFieldGuidance();
    const requestId = this._inspectionItemsRequest || 0;
    const siteIds = [...new Set((this.data.days || []).flatMap(day => day.sites || []).map(Number).filter(Boolean))];
    const scheduleType = this.data.scheduleType;
    const retryId = Number(event && event.currentTarget && event.currentTarget.dataset.siteId);
    this._inspectionSiteSerials = this._inspectionSiteSerials || {};
    const requests = siteIds.filter(siteId => retryId ? siteId === retryId
      : !this.data.inspectionSiteStates[siteId] || this.data.inspectionSiteStates[siteId].frequency !== scheduleType
        || this.data.inspectionSiteStates[siteId].state !== 'ready');
    return Promise.all(requests.map(siteId => {
      const serial = (this._inspectionSiteSerials[siteId] || 0) + 1;
      this._inspectionSiteSerials[siteId] = serial;
      this.setData({ inspectionSiteStates: Object.assign({}, this.data.inspectionSiteStates,
        { [siteId]: { state: 'loading', error: '', frequency: scheduleType } }) });
      const current = () => this._pageAlive !== false && requestId === (this._inspectionItemsRequest || 0)
        && serial === this._inspectionSiteSerials[siteId] && scheduleType === this.data.scheduleType
        && this.data.days.some(day => (day.sites || []).map(Number).includes(siteId));
      this.projectInspectionState();
      return Promise.resolve().then(() => api.inspectionConfigMatches(siteId, scheduleType)).then(result => {
        if (!current()) return;
        if (!result || !Array.isArray(result.items)) throw { error: '站点检查项响应不完整，请重试' };
        const inspectionItemOptions = Object.assign({}, this.data.inspectionItemOptions, { [siteId]: result.items });
        this.setData({ inspectionItemOptions,
          days: initializeInspectionItemSelections(this.data.days, { [siteId]: result.items }, scheduleType),
          inspectionSiteStates: Object.assign({}, this.data.inspectionSiteStates,
            { [siteId]: { state: 'ready', error: '', frequency: scheduleType } }) });
        this.projectInspectionState();
      }).catch(error => {
        if (!current()) return;
        this.setData({ inspectionSiteStates: Object.assign({}, this.data.inspectionSiteStates,
          { [siteId]: { state: 'unavailable', frequency: scheduleType,
            error: (error && (error.error || error.message)) || '检查项加载失败，请重试' } }) });
        this.projectInspectionState();
      });
    })).then(() => { if (this._pageAlive !== false && requestId === (this._inspectionItemsRequest || 0)) this.projectInspectionState(); });
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
    const days = this.data.days.slice();
    days[dayIdx] = Object.assign({}, day, { inspection_items: selected });
    this.setData({ days: inspectionGroupsForDays(days, this.data.inspectionItemOptions, this.data.scheduleType) }, () => this.refreshValidation());
  },

  onToggleInspectionGroup(e) {
    const { dayIdx, siteId, frequency } = e.currentTarget.dataset;
    const days = this.data.days.slice();
    const day = days[dayIdx];
    if (!day) return;
    const expanded = Object.assign({}, day.expandedInspectionGroups || {});
    expanded[siteId + ':' + frequency] = !expanded[siteId + ':' + frequency];
    days[dayIdx] = Object.assign({}, day, { expandedInspectionGroups: expanded });
    this.setData({ days: inspectionGroupsForDays(days, this.data.inspectionItemOptions, this.data.scheduleType) });
  },

  onPlanVehicleChange(e) {
    const idx = parseInt(e.detail.value);
    const vehicleId = idx >= 0 && this.data.vehicles[idx] ? this.data.vehicles[idx].id : null;
    const updates = { planVehicleId: vehicleId };
    if (vehicleId) updates.vehicleExceptionReason = '';
    this.setData(updates, () => this.refreshValidation());
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
    this.setData({ coverageExceptionReason: e.detail.value }, () => this.refreshValidation());
  },

  onVehicleModeSelect(e) {
    const enabled = e.currentTarget.dataset.mode === 'none';
    const updates = { noVehicleRequired: enabled };
    if (enabled) {
      updates.days = this.data.days.map(day => Object.assign({}, day, { vehicle_id: null }));
      updates.planVehicleId = null;
    }
    this.setData(updates, () => this.refreshValidation());
  },

  onToggleSchedulingReference() {
    this.setData({ referenceExpanded: !this.data.referenceExpanded });
  },

  onToggleParts() { this.setData({ partsExpanded: !this.data.partsExpanded }); },
  onToggleRemark() { this.setData({ remarkExpanded: !this.data.remarkExpanded }); },

  onNavBack() {
    if (this._navBackPending) {
      wx.showToast({ title: '正在返回，请稍候', icon: 'none' });
      return;
    }
    this._navBackPending = true;
    let settled = false;
    const finish = failed => {
      if (settled) return;
      settled = true;
      this._navBackPending = false;
      if (failed) wx.showToast({ title: '暂时无法返回，请重试', icon: 'none' });
    };
    try {
      wx.navigateBack({ delta: 1, success: () => finish(false), fail: () => finish(true) });
    } catch (err) {
      finish(true);
    }
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
      schedule_type: this.data.editId ? scheduleType : 'weekly',
      period_start: periodStart,
      period_end: periodEnd,
      plan_data: planData,
      vehicle_days: vehicleDays,
      vehicle_id: noVehicleRequired ? null : (planVehicleId || null),
      no_vehicle_required: !!noVehicleRequired,
      spare_parts: selectedParts,
      work_order_ids: Array.from(new Set(linkedWorkOrderIds)),
      remarks: remarks,
      coverage_exception_reason: coverageExceptionReason,
      vehicle_exception_reason: planVehicleId ? '' : vehicleExceptionReason.trim(),
      submit: !!submit,
      ...(this.data.editId ? { version } : {})
    };
  },

  // 将后端结构化风险直接挂到对应日期，避免用户只看到文字后再手工查找站点。
  applyValidation(vr) {
    const details = (vr.error_details || []).concat(vr.warning_details || []);
    const byDate = {};
    const routesByDate = {};
    details.forEach(w => {
      if (w.date && w.type === 'route_backtrack' && Array.isArray(w.suggested_site_names)) {
        (routesByDate[w.date] || (routesByDate[w.date] = [])).push({
          orderText: '建议顺序：' + w.suggested_site_names.join(' → '),
          metaText: Number.isFinite(Number(w.estimated_distance_saved_km))
            ? '预计少绕行约 ' + Number(w.estimated_distance_saved_km).toFixed(1) + ' km · 按站点位置估算'
            : '按站点位置估算',
        });
      } else if (w.date && !['vehicle_id', 'vehicle_days'].includes(w.field)) {
        (byDate[w.date] || (byDate[w.date] = [])).push(w.text);
      }
    });
    const days = this.data.days.map(d => Object.assign({}, d, {
      warning_text: (byDate[d.date] || []).join('\n'),
      route_suggestions: routesByDate[d.date] || [],
    }));
    this.setData({ days });
  },

  refreshValidation() {
    this.updateFieldGuidance();
    const validationSerial = (this._validationSerial || 0) + 1;
    this._validationSerial = validationSerial;
    if (this._validationTimer) clearTimeout(this._validationTimer);
    this._validationTimer = setTimeout(() => {
      const payload = this.buildPayload(false);
      api.validatePlanSchedule(Object.assign({ user_id: (getUser() || {}).id }, payload))
        .then(vr => {
          if (validationSerial === this._validationSerial) this.applyValidation(vr || {});
        })
        .catch(() => {});
    }, 250);
  },

  // 提交审批
  createNewPlan(payload) {
    const actorId = (getUser() || {}).id;
    const fingerprint = JSON.stringify([actorId, payload]);
    if (!this._newCreationIntent || this._newCreationIntent.fingerprint !== fingerprint) {
      this._newCreationIntent = { fingerprint,
        key: 'plan-create-' + Date.now() + '-' + Math.random().toString(36).slice(2) + '-' + Math.random().toString(36).slice(2) };
    }
    return api.createPlanSchedule(Object.assign({}, payload, { _idempotency_key: this._newCreationIntent.key }));
  },

  isCurrentCreation(payload) {
    return this._pageAlive !== false && JSON.stringify(payload) === JSON.stringify(this.buildPayload(payload.submit));
  },

  updateFieldGuidance() {
    const selected = new Set(this.data.days.flatMap(day => day.sites || []).map(Number));
    const missing = (this.data.mySites || []).filter(site => !selected.has(Number(site.id)));
    const coverageRequired = this.data.scheduleType === 'weekly' && missing.length > 0;
    const fieldErrors = {};
    if (!selected.size) fieldErrors.sites = '请至少安排一个巡检站点';
    if (!this.data.noVehicleRequired && !this.data.planVehicleId) fieldErrors.planVehicleId = '请选择计划车辆';
    if (coverageRequired && !String(this.data.coverageExceptionReason || '').trim()) {
      fieldErrors.coverage_exception_reason = '请说明未覆盖站点的原因';
    }
    const missingSiteList = coverageRequired ? missing.map(site => site.name) : [];
    this.setData({ coverageRequired, missingSiteNames: missingSiteList.join('、'), missingSiteList,
      missingCount: missingSiteList.length, coverageWarning: coverageRequired ? '本周存在未覆盖站点' : '', fieldErrors });
    return fieldErrors;
  },

  scrollToErrorAnchor(selector) {
    if (!selector || !wx.pageScrollTo) return;
    if (!wx.createSelectorQuery) {
      wx.pageScrollTo({ selector, duration: 200 });
      return;
    }
    const query = wx.createSelectorQuery();
    query.select('.pe-nav-wrap').boundingClientRect();
    query.select(selector).boundingClientRect();
    query.selectViewport().scrollOffset();
    query.exec(results => {
      const nav = results && results[0];
      const target = results && results[1];
      const viewport = results && results[2];
      if (!nav || !target || !viewport || !Number.isFinite(target.top)
          || !Number.isFinite(nav.bottom) || !Number.isFinite(viewport.scrollTop)) {
        wx.pageScrollTo({ selector, duration: 200 });
        return;
      }
      const safetyGap = 12;
      wx.pageScrollTo({
        scrollTop: Math.max(0, viewport.scrollTop + target.top - nav.bottom - safetyGap),
        duration: 200,
      });
    });
  },

  locateSubmitError(error) {
    const errors = this.updateFieldGuidance();
    const structured = error && (error.validation || error) || {};
    const dates = structured.dates || [];
    const details = structured.error_details || structured.warning_details || [];
    const detail = details.find(item => item.date || item.field || (item.site_ids && item.site_ids.length));
    let field = structured.field || (detail && detail.field) || (!error && Object.keys(errors)[0]);
    if (field === 'vehicle_id' || field === 'vehicle_days') field = 'planVehicleId';
    if (field === 'vehicle_exception_reason' && !this.data.noVehicleRequired) field = 'planVehicleId';
    const date = dates[0] || (detail && detail.date);
    const siteIds = structured.site_ids || (detail && detail.site_ids) || [];
    const index = field === 'planVehicleId' ? -1 : this.data.days.findIndex(day => date ? day.date === date
      : siteIds.some(id => (day.sites || []).map(Number).includes(Number(id))));
    let selector = index >= 0 ? '#plan-day-' + index : '';
    if (field === 'vehicle_exception_reason' && this.data.noVehicleRequired) selector = '#plan-vehicle-reason';
    if (field === 'coverage_exception_reason') selector = '#plan-coverage-reason';
    if (field === 'planVehicleId') selector = '#plan-vehicle-row';
    if (field === 'period_start' || field === 'period_end') selector = '#plan-period-section';
    if (!selector && field) selector = ({ sites: '#plan-sites', site_ids: '#plan-sites',
      vehicle_exception_reason: '#plan-vehicle-reason', coverage_exception_reason: '#plan-coverage-reason',
      period_start: '#plan-period-section', period_end: '#plan-period-section', plan_data: '#plan-sites' })[field] || '';
    if (field && structured.error) errors[field] = structured.error;
    if (field && detail && detail.text) errors[field] = detail.text;
    if (index >= 0) {
      const days = this.data.days.slice();
      days[index] = Object.assign({}, days[index], { warning_text: structured.error || (detail && detail.text)
        || (structured.errors || []).join('\n') || '请检查本日站点安排' });
      this.setData({ days });
    }
    const reveal = () => this.setData({ fieldErrors: errors, focusedErrorField: '' }, () => {
      this.scrollToErrorAnchor(selector);
    });
    if (this.data.focusedErrorField) this.setData({ focusedErrorField: '' }, reveal);
    else reveal();
    return !!selector;
  },

  onSubmit() {
    if (this.data.submitting) return;
    if (this.data.detailState !== 'ready') {
      wx.showToast({
        title: this.data.detailState === 'error' ? '请先重试加载计划详情' : '计划详情正在加载，请稍候',
        icon: 'none'
      });
      return;
    }
    if (this.data.inspectionItemsState !== 'ready') {
      wx.showToast({ title: this.data.inspectionItemsError || '检查项尚未加载完成', icon: 'none' });
      return;
    }
    // 前端基本校验
    const fieldErrors = this.updateFieldGuidance();
    if (Object.keys(fieldErrors).length) {
      this.setData({ submitError: '' });
      this.locateSubmitError();
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
          if (!this.locateSubmitError(vr)) this.setData({ submitError: vr.errors.join('\n') });
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
        if (this._pageAlive === false) return Promise.reject('cancel');
        return this.createNewPlan(payload);
      });

    submission
      .then(submitted => {
        if (!this.data.editId && !this.isCurrentCreation(payload)) return;
        if (this.data.isChange && (!submitted || submitted.status !== 'approved' || submitted.direct_applied !== true)) {
          throw { error: '服务端未确认计划变更已生效，请直接重试' };
        }
        this._pendingFormalSubmit = null;
        this.setData({ submitError: '' });
        wx.showToast({ title: this.data.isChange ? '计划变更已生效' : (this.data.editId || (submitted && submitted.status === 'submitted') ? '已提交审批' : '计划已恢复，请查看当前状态'), icon: 'success' });
        setTimeout(() => {
          if (this._pageAlive !== false && (this.data.editId || this.isCurrentCreation(payload))) wx.navigateBack();
        }, 1200);
      })
      .catch(err => {
        if (!this.data.editId && !this.isCurrentCreation(payload)) return;
        if (err === 'blocked' || err === 'cancel') return;
        const message = (err && (err.error || err.message)) || '提交失败，请重试';
        if (!(err && (err.network || err.code === 'PLAN_VERSION_CONFLICT')) && this.locateSubmitError(err)) {
          this.setData({ submitError: '' });
          return;
        }
        const nextAction = err && err.code === 'PLAN_VERSION_CONFLICT'
          ? '计划已被其他操作更新，请返回计划详情刷新后重新编辑。'
          : '修改内容已保留，请确认后直接重试。';
        const failureText = message + '\n' + nextAction;
        this.setData({ submitError: failureText });
      })
      .finally(() => { if (this._pageAlive !== false) this.setData({ submitting: false }); });
  },

  // 存草稿
  onSaveDraft() {
    if (this.data.submitting) return;
    if (this.data.detailState !== 'ready') {
      wx.showToast({
        title: this.data.detailState === 'error' ? '请先重试加载计划详情' : '计划详情正在加载，请稍候',
        icon: 'none'
      });
      return;
    }
    if (this.data.inspectionItemsState !== 'ready') {
      wx.showToast({ title: this.data.inspectionItemsError || '检查项尚未加载完成', icon: 'none' });
      return;
    }
    this.setData({ submitting: true, draftSaveError: '' });
    const payload = this.buildPayload(false);
    const p = this.data.editId
      ? api.updatePlanSchedule(this.data.editId, payload)
      : this.createNewPlan(payload);
    p.then(saved => {
      if (!this.data.editId && !this.isCurrentCreation(payload)) return;
      const successPatch = { draftSaveError: '' };
      if (this.data.editId && saved && saved.version) successPatch.version = saved.version;
      this.setData(successPatch);
      const issueCount = Number(saved && saved.draft_issue_count || 0);
      wx.showToast({ title: !this.data.editId && saved && saved.status !== 'draft' ? '计划已恢复，请查看当前状态'
        : (issueCount ? `已保存，${issueCount}项待完善` : '已保存草稿'), icon: 'none' });
      setTimeout(() => {
        if (this._pageAlive !== false && (this.data.editId || this.isCurrentCreation(payload))) wx.navigateBack();
      }, 1000);
    })
    .catch(err => {
      if (!this.data.editId && !this.isCurrentCreation(payload)) return;
      const reason = (err && (err.error || err.message)) || '保存失败';
      if (!(err && (err.network || err.code === 'PLAN_VERSION_CONFLICT')) && this.locateSubmitError(err)) {
        this.setData({ draftSaveError: '' });
        return;
      }
      const failureText = reason + (err && err.code === 'PLAN_VERSION_CONFLICT'
        ? '\n计划已更新，请返回详情刷新后重新编辑。' : '\n内容已保留，可重试');
      this.setData({ draftSaveError: failureText });
    })
    .finally(() => { if (this._pageAlive !== false) this.setData({ submitting: false }); });
  }
});

module.exports = {
  initializeInspectionItemSelections, dayHasBusinessContent, planSubmitFingerprint,
  periodDates, reconcilePeriodDays, summarizeTemplateContext, schedulingReferencePresentation,
  projectSiteNameMap, availableSiteIds, hasAllAvailableSites, withDaySiteSelectionState, inspectionGroupsForDays
};
