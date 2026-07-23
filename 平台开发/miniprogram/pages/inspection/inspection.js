const api = require('../../services/api.js');
const { RESULT, INSPECTION_CATEGORY, map } = require('../../services/maps.js');
const { getSites, getUser } = require('../../utils/auth.js');
const { nowStr } = require('../../utils/util.js');
const { chooseAndCompress, fileToBase64, persistFile, captureFlushedPhoto } = require('../../utils/photos.js');
const { resolveUploadUrl } = require('../../utils/url.js');
const { queueCount, flushQueue } = require('../../utils/request.js');
const localStore = require('../../utils/localStore.js');
const { flushLocalOps } = require('../../utils/sync.js');
const { selectExecutionSite, photoRequirement } = require('../../utils/executionState.js');

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

Page({
  data: {
    packages: [],
    currentPackage: null,
    selectedPlanId: null,
    sites: [],
    selSite: null,
    selSiteId: null,
    site: null,
    categories: [],
    total: 0, completed: 0, loaded: false,
    syncCount: 0,
    stationStage: null,
    sheet: { open: false, item: null, result: 'normal', remark: '', calibrator: '', calValues: '', photos: [], localPhotos: [] },
    submitting: false
  },

  onShow() {
    if (!app.globalData.token) { wx.reLaunch({ url: '/pages/login/login' }); return; }
    this.setData({ syncCount: queueCount() });
    this.loadExecution();
  },

  onPullDownRefresh() {
    this.loadExecution(() => wx.stopPullDownRefresh());
  },

  loadExecution(done) {
    api.todayExecution().then(res => {
      const packages = res.packages || [];
      const preferredSiteId = app.globalData.selSiteId;
      const selection = selectExecutionSite(packages, this.data.selectedPlanId, preferredSiteId);
      const currentPackage = selection.currentPackage;
      const sites = currentPackage ? currentPackage.sites || [] : [];
      const selected = selection.site;
      const selSiteId = selected ? selected.site_id : null;
      app.globalData.selSiteId = null;
      this.setData({ packages, currentPackage, selectedPlanId: currentPackage ? currentPackage.plan_id : null,
        sites: sites.map(s => Object.assign({}, s, { id: s.site_id })), selSiteId, loaded: !!currentPackage,
        selSite: currentPackage ? this.data.selSite : null, site: currentPackage ? this.data.site : null,
        categories: currentPackage ? this.data.categories : [], total: currentPackage ? this.data.total : 0,
        completed: currentPackage ? this.data.completed : 0 });
      if (selSiteId) this.loadTasks(selSiteId, done); else if (done) done();
    }).catch(() => { this.setData({ loaded: true, packages: [], currentPackage: null, sites: [], selSite: null, site: null, selSiteId: null, categories: [], total: 0, completed: 0 }); if (done) done(); });
  },

  onSelectPackage(e) {
    const planId = e.currentTarget.dataset.id;
    const currentPackage = (this.data.packages || []).find(p => p.plan_id === planId);
    if (!currentPackage) return;
    const sites = (currentPackage.sites || []).map(s => Object.assign({}, s, { id: s.site_id }));
    const selSiteId = sites[0] && sites[0].id;
    this.setData({ currentPackage, selectedPlanId: planId, sites, selSiteId, categories: [], total: 0, completed: 0 });
    if (selSiteId) this.loadTasks(selSiteId);
  },

  loadTasks(siteId, done) {
    const planId = this.data.selectedPlanId;
    if (!planId) { if (done) done(); return; }
    api.executionSiteTasks(planId, siteId)
      .then(res => {
        const photosMap = {};
        (res.categories || []).forEach(cat => (cat.items || []).forEach(it => {
          let arr = [];
          try { arr = it.photo_urls ? JSON.parse(it.photo_urls) : []; } catch (e) { arr = []; }
          photosMap[it.item_id] = arr;
        }));
        // 巡检结果枚举集中映射（§6.8：禁止 wxml 硬编码中文枚举）
        const decorated = (res.categories || []).map(cat => ({
          ...cat,
          category_cn: map(INSPECTION_CATEGORY, cat.category, '未分类'),
          items: (cat.items || []).map(it => ({
            ...it,
            result_cn: RESULT[it.result] || '待检'
          }))
        }));
        this.setData({
          site: res.site || null,
          selSite: res.site || null,
          categories: decorated,
          total: res.total || 0,
          completed: res.completed || 0,
          loaded: true,
          photoProgress: (() => {
            let req = 0, taken = 0;
            (res.categories || []).forEach(cat => (cat.items || []).forEach(it => {
              req += (it.required_photos || 0);
              let arr = []; try { arr = it.photo_urls ? JSON.parse(it.photo_urls) : []; } catch(e) {}
              taken += arr.length;
            }));
            return { req, taken, missing: Math.max(0, req - taken) };
          })()
        });
        this.refreshStationStage(siteId);
        if (done) done();
      })
      .catch(() => { this.setData({ loaded: true }); if (done) done(); wx.showToast({ title: '加载失败', icon: 'none' }); });
  },

  onSelectSite(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ selSiteId: id });
    this.loadTasks(id);
  },

  refreshStationStage(siteId) {
    const checkin = localStore.getSiteCheckIn(siteId);
    const stationStage = !checkin
      ? { code: 'unvisited', label: '待到站', cls: 'station-stage-wait' }
      : checkin.syncStatus === 'pending'
        ? { code: 'local_pending', label: '已到站，待同步', cls: 'station-stage-pending' }
        : { code: 'checked_in', label: '已到站', cls: 'station-stage-ok' };
    this.setData({ stationStage });
  },

  onCheckIn() {
    const site = this.data.site;
    if (!site) return;
    wx.showLoading({ title: '定位中' });
    getGps().then(gps => {
      wx.hideLoading();
      const payload = { site_id: site.id, site_name: site.name, check_time: nowStr() };
      if (gps) { payload.lat = gps.lat; payload.lng = gps.lng; }
      // 本地先落库：断网/弱网也留存打卡态，联网后静默同步
      const opId = localStore.addOp('checkin', payload);
      this.refreshStationStage(site.id);
      api.trackEvent('inspection.checkin.queued', { site_id: site.id, operation_id: opId });
      api.checkIn(payload, true)
        .then(() => { localStore.markSynced(opId); wx.showToast({ title: '打卡成功', icon: 'success' }); })
        .catch(() => { wx.showToast({ title: '打卡已本地保存，联网同步', icon: 'none' }); });
    });
  },

  goSite() {
    if (this.data.selSiteId) wx.navigateTo({ url: '/pages/site/site?site_id=' + this.data.selSiteId });
  },

  onOpenItem(e) {
    const id = e.currentTarget.dataset.id;
    let target = null;
    (this.data.categories || []).forEach(cat => (cat.items || []).forEach(it => { if (it.item_id === id) target = it; }));
    if (!target) return;
    let photos = [];
    try { photos = target.photo_urls ? JSON.parse(target.photo_urls) : []; } catch (e) { photos = []; }
    const requiredPhotos = target.required_photos || 0;
    this.setData({
      sheet: { open: true, item: target, result: target.result || 'normal', remark: target.remark || '', calibrator: target.calibrator || '', calValues: target.calibration_values || '', photos: photos.map(resolveUploadUrl), localPhotos: [], requiredPhotos, photoInfo: photoRequirement(requiredPhotos, photos.length, 0) }
    });
  },

  onCloseSheet() { this.setData({ 'sheet.open': false }); },
  onSetResult(e) { this.setData({ 'sheet.result': e.currentTarget.dataset.r }); },
  onRemark(e) { this.setData({ 'sheet.remark': e.detail.value }); },
  onCalibrator(e) { this.setData({ 'sheet.calibrator': e.detail.value }); },
  onCalValues(e) { this.setData({ 'sheet.calValues': e.detail.value }); },

  onAddPhoto() {
    const sheet = this.data.sheet;
    if (sheet.photos.length + sheet.localPhotos.length >= 6) { wx.showToast({ title: '最多 6 张', icon: 'none' }); return; }
    chooseAndCompress(6 - sheet.photos.length - sheet.localPhotos.length)
      .then(paths => {
        if (!paths || !paths.length) return;
        wx.showLoading({ title: '上传中' });
        const siteId = this.data.selSiteId;
        // 成功取回 URL；失败（弱网/离线）保留本地路径，待联网由同步引擎上传
        const tasks = paths.map(p => fileToBase64(p)
          .then(b64 => api.uploadSitePhoto(siteId, b64).then(r => ({ url: resolveUploadUrl(r.url) })))
          .catch(() => persistFile(p).then(saved => ({ localPath: saved }))));
        Promise.allSettled(tasks)
          .then(results => {
            wx.hideLoading();
            const urls = [];
            const locals = [];
            results.forEach(r => {
              if (r.status === 'fulfilled') {
                const v = r.value;
                if (v && v.url) urls.push(v.url);
                else if (v && v.localPath) locals.push(v.localPath);
              }
            });
            const allRemote = sheet.photos.concat(urls);
            const allLocal = sheet.localPhotos.concat(locals);
            this.setData({
              'sheet.photos': allRemote,
              'sheet.localPhotos': allLocal,
              'sheet.photoInfo': photoRequirement(sheet.requiredPhotos, allRemote.length, allLocal.length)
            });
            api.trackEvent('inspection.photo.captured', { site_id: siteId, item_id: sheet.item.item_id, offline: locals.length > 0 });
            this.setData({ syncCount: queueCount() });
            if (locals.length && !urls.length) wx.showToast({ title: '照片已本地保存，联网同步', icon: 'none' });
            else if (locals.length) wx.showToast({ title: '部分已本地保存', icon: 'none' });
          })
          .catch(() => { wx.hideLoading(); });
      })
      .catch(() => {});
  },

  onDelPhoto(e) {
    const idx = e.currentTarget.dataset.idx;
    const photos = this.data.sheet.photos.slice();
    const item = this.data.sheet.item;
    if (item && item.result) api.deletePhoto(item.item_id, idx); // 已提交则通知后端删除
    photos.splice(idx, 1);
    this.setData({ 'sheet.photos': photos, 'sheet.photoInfo': photoRequirement(this.data.sheet.requiredPhotos, photos.length, this.data.sheet.localPhotos.length) });
  },

  onDelLocalPhoto(e) {
    const idx = e.currentTarget.dataset.idx;
    const localPhotos = this.data.sheet.localPhotos.slice();
    localPhotos.splice(idx, 1);
    this.setData({ 'sheet.localPhotos': localPhotos, 'sheet.photoInfo': photoRequirement(this.data.sheet.requiredPhotos, this.data.sheet.photos.length, localPhotos.length) });
  },

  onPreview(e) {
    const src = e.currentTarget.dataset.src;
    wx.previewImage({ urls: this.data.sheet.photos.concat(this.data.sheet.localPhotos), current: src });
  },

  updateItemResult(itemId, result, photos) {
    const categories = this.data.categories.map(cat => {
      return {
        ...cat,
        items: cat.items.map(it => it.item_id === itemId ? { ...it, result, result_cn: RESULT[result] || '待检' } : it)
      };
    });
    let completed = 0, total = 0;
    categories.forEach(cat => cat.items.forEach(it => { total++; if (it.result) completed++; }));
    this.setData({ categories, completed, total });
  },

  onSubmitItem() {
    const s = this.data.sheet;
    if (!s.item) return;
    const photoInfo = photoRequirement(s.requiredPhotos, s.photos.length, s.localPhotos.length);
    if (s.result === 'normal' && !photoInfo.ready) {
      wx.showToast({ title: '请按要求补齐现场照片', icon: 'none' });
      return;
    }
    if (s.result === 'abnormal' && photoInfo.captured === 0) {
      wx.showToast({ title: '异常项必须拍照', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    const photoUrls = JSON.stringify(s.photos);
    const localPhotos = s.localPhotos.slice();
    getGps().then(gps => {
      const payload = {
        item_id: s.item.item_id,
        plan_id: s.item.plan_id,
        result: s.result,
        remark: s.remark,
        photo_urls: photoUrls,
        calibrator: s.calibrator,
        calibration_values: s.calValues,
        // 离线闭环关键：携带站点与本地照片路径，联网后同步引擎先传图再提交
        siteId: this.data.selSiteId,
        localPhotos: localPhotos
      };
      if (gps) { payload.gps_lat = gps.lat; payload.gps_lng = gps.lng; }
      // 本地先落库：无论网络成败都先存实体，断网可走完闭环
      const opId = localStore.addOp('submit', payload);
      api.trackEvent('inspection.item.queued', { site_id: this.data.selSiteId, item_id: s.item.item_id, plan_id: s.item.plan_id, operation_id: opId, offline: localPhotos.length > 0 });
      const submitPromise = localPhotos.length
        ? flushLocalOps().then(() => {
            const stillPending = localStore.getPending().some(op => op.id === opId);
            if (stillPending) return Promise.reject(new Error('等待同步'));
            return { success: true };
          })
        : api.submitItem(payload);
      submitPromise
        .then((res) => {
          localStore.markSynced(opId);
          this._afterSubmit(s);
          wx.showToast({ title: res && res.order_no ? '异常已转工单' : '已提交', icon: 'success' });
        })
        .catch(() => {
          // 离线/弱网：实体已本地留存，联网后静默同步
          this._afterSubmit(s);
          wx.showToast({ title: '已本地保存，联网自动同步', icon: 'none' });
        });
    });
  },

  _afterSubmit(s) {
    this.setData({ submitting: false, 'sheet.open': false });
    this.updateItemResult(s.item.item_id, s.result, s.photos.concat(s.localPhotos));
    this.setData({ syncCount: queueCount() });
  },

  onSyncNow() {
    wx.showLoading({ title: '同步中' });
    flushQueue(captureFlushedPhoto);
    Promise.resolve(flushLocalOps()).catch(() => {}).then(() => {
      setTimeout(() => {
        wx.hideLoading();
        this.setData({ syncCount: queueCount() });
        if (this.data.selSiteId) this.loadTasks(this.data.selSiteId);
        if (this.data.syncCount === 0) wx.showToast({ title: '同步完成', icon: 'success' });
      }, 1000);
    });
  }
});
