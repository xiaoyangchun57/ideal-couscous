const test = require('node:test');
const assert = require('node:assert/strict');

const definitions = [];
const app = { globalData: {} };
let confirm = true;
let confirmation = null;
const deletedPhotos = [];
global.getApp = () => app;
global.Page = definition => definitions.push(definition);
global.wx = {
  getStorageSync: () => [], setStorageSync() {}, removeStorageSync() {},
  showToast() {}, showLoading() {}, hideLoading() {}, setNavigationBarTitle() {},
  showModal(options) {
    if (confirmation) confirmation(options);
    else if (options.success) options.success({ confirm });
  },
  authorize: options => options.success(),
  getLocation: options => options.success({ latitude: 28.68, longitude: 115.86 }),
  getFileSystemManager: () => ({ readFile: options => options.success({ data: 'original-image' }) }),
  saveFile: options => options.success({ savedFilePath: options.tempFilePath }),
  removeSavedFile() {},
};
const api = require('../services/api.js');
const targets = require('../utils/executionTarget.js');
const { createPendingReportPhoto } = require('../utils/reportPhotoDraft.js');
require('../pages/reports/reports.js');
require('../pages/inspection/inspection.js');
require('../pages/vehicle/vehicle.js');
const [reports, inspection, vehicle] = definitions;
const sites = [{ id: 31, name: '甲站' }, { id: 32, name: '乙站' }];

function page(definition, data) {
  const instance = Object.assign({}, definition, { _alive: true,
    data: Object.assign(JSON.parse(JSON.stringify(definition.data)), data || {}) });
  instance.setData = (patch, done) => {
    for (const [key, value] of Object.entries(patch)) {
      const parts = key.split('.');
      let target = instance.data;
      while (parts.length > 1) {
        const part = parts.shift();
        target = target[part] || (target[part] = {});
      }
      target[parts[0]] = value;
    }
    if (done) done();
  };
  instance.load = () => {};
  return instance;
}
function draft(overrides) {
  return Object.assign({ open: true, sites, siteId: 31, siteName: '甲站', siteIndex: 0,
    description: '保留水泵异响说明', typeIndex: 1, photos: [], photoItems: [],
    uploading: false, submitting: false }, overrides || {});
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const pickB = instance => instance.onReportSite({ detail: { value: '1' } });
function uploadResult(index) {
  const url = '/uploads/site_photos/original-' + index + '.jpg';
  return { id: index + 100, url, display_url: url };
}

for (const [name, definition] of [['我的', reports], ['现场', inspection]]) {
  test(name + '：3张在途照片更正站点、失败原图重试和重复提交保留同一草稿', async () => {
    const instance = page(definition, { reportSheet: draft(), selSiteId: 31 });
    const context = instance.reportPhotoContext();
    const items = [0, 1, 2].map(index => Object.assign(createPendingReportPhoto(
      'wxfile://usr/original-' + index + '.jpg', 'stable-key-' + index,
      { capture_session: 'original-session-' + index, capture_source: 'camera' }
    ), { uploadSiteId: 31 }));
    instance.data.reportSheet.photoItems = items;
    instance.data.reportSheet.uploading = true;
    const waits = [deferred(), deferred(), deferred()];
    const calls = [];
    api.uploadSitePhoto = (site, image, key, metadata) => {
      calls.push({ site, image, key, metadata });
      return waits[calls.length - 1].promise;
    };
    api.deletePendingSitePhoto = path => { deletedPhotos.push(path); return Promise.resolve(); };
    const upload = instance.uploadReportPhotoItems(items, context);
    await settle();
    confirm = false;
    pickB(instance);
    assert.equal(instance.data.reportSheet.siteId, 31, '取消更正保留原站');
    confirm = true;
    pickB(instance);
    assert.equal(instance.data.reportSheet.siteId, 32, '上传中也可确认更正');
    waits[2].resolve(uploadResult(2));
    waits[0].resolve(uploadResult(0));
    waits[1].reject({ error: '弱网上传失败' });
    await upload;
    instance.finishReportPhotoAction();
    assert.equal(instance.data.reportSheet.siteId, 32, '旧站回调不覆盖新站');
    assert.equal(instance.data.reportSheet.description, '保留水泵异响说明');
    assert.equal(instance.data.reportSheet.typeIndex, 1);
    assert.equal(instance.data.reportSheet.photoItems.length, 3);
    assert.equal(instance.data.reportSheet.photoItems[1].status, 'failed');
    assert.equal(instance.data.reportSheet.photoItems[1].localPath, items[1].localPath);
    assert.equal(deletedPhotos.length, 0, '更正站点不删除上传结果');
    const failed = instance.data.reportSheet.photoItems[1];
    api.uploadSitePhoto = (site, image, key, metadata) => {
      assert.equal(site, 31, '失败原图仍按原上传站点和拍摄会话重试');
      assert.equal(image, calls[1].image);
      assert.equal(key, calls[1].key);
      assert.deepEqual(metadata, calls[1].metadata);
      return Promise.resolve(uploadResult(1));
    };
    await instance.onRetryReportPhoto({ currentTarget: { dataset: { key: failed.key } } });
    assert.equal(instance.data.reportSheet.siteId, 32);
    assert.equal(instance.data.reportSheet.photos.length, 3);
    const submitWait = deferred();
    const submits = [];
    api.submitManualReport = payload => { submits.push(payload); return submitWait.promise; };
    const submission = instance.onSubmitReport();
    assert.equal(instance.onSubmitReport(), submission, '重复点击复用同一个请求');
    instance.onReportDescription({ detail: { value: '提交中不得覆盖' } });
    instance.onReportType({ detail: { value: 3 } });
    await settle();
    assert.equal(submits.length, 1);
    assert.equal(submits[0].site_id, 32, '只把最终站点交给服务端核归属');
    assert.deepEqual(submits[0].photo_urls, [0, 1, 2].map(i => uploadResult(i).url));
    submitWait.reject({ error: '提交响应中断' });
    await submission;
    assert.equal(instance.data.reportSheet.description, '保留水泵异响说明');
    assert.equal(instance.data.reportSheet.typeIndex, 1);
    assert.equal(instance.data.reportSheet.photos.length, 3);
    const retry = deferred();
    api.submitManualReport = payload => { submits.push(payload); return retry.promise; };
    const retryTask = instance.onSubmitReport();
    await settle();
    assert.deepEqual(submits[1], submits[0], '失败重试保持服务端去重所用的上报内容与照片顺序');
    instance.loadTasks = () => {};
    instance.showReportSuccess = () => {};
    retry.resolve({ order_no: 'MR-isolated-1', deduplicated: true });
    await retryTask;
    assert.equal(instance.data.reportSheet.open, false);
    assert.equal(instance.data.reportSheet.photos.length, 0, '仅成功后清空');
  });

  test(name + '：迟到确认不能更改已替换草稿，键盘高度可恢复', () => {
    const instance = page(definition, { reportSheet: draft({ photos: [uploadResult(0).url] }) });
    let modal;
    confirmation = options => { modal = options; };
    pickB(instance);
    confirmation = null;
    instance._reportDraftVersion = 1;
    instance.data.reportSheet = draft({ description: '另一份新草稿' });
    modal.success({ confirm: true });
    assert.equal(instance.data.reportSheet.siteId, 31);
    instance.onPanelKeyboardHeightChange({ detail: { height: 300 } });
    assert.equal(instance.data.panelKeyboardHeight, 300);
    instance.onPanelKeyboardHeightChange({ detail: { height: 0 } });
    assert.equal(instance.data.panelKeyboardHeight, 0);
  });
}

test('多站新上报不默认旧站，恢复草稿明确提示；现场切站不丢照片说明', async () => {
  const fresh = page(reports);
  fresh.openReportSheetForSites(sites);
  assert.equal(fresh.data.reportSheet.siteId, null);
  const restored = page(reports, { reportSheet: draft({ open: false, photos: [uploadResult(0).url] }) });
  restored.openReportSheetForSites(sites);
  assert.equal(restored.data.reportSheet.siteId, 31);
  assert.equal(restored.data.reportSheet.restored, true);
  const field = page(inspection, { reportSheet: draft({ open: false, photos: [uploadResult(0).url] }),
    selSiteId: 31, site: sites[0], selSite: sites[0], sites });
  field.loadTasks = () => {};
  field.onSelectSite({ currentTarget: { dataset: { id: 32 } } });
  assert.equal(field.data.selSiteId, 32);
  assert.equal(field.data.reportSheet.siteId, 31, '作业站切换不悄悄改上报草稿归属');
  assert.equal(field.data.reportSheet.photos.length, 1);
  api.sites = () => Promise.resolve(sites);
  field.onOpenReport();
  await settle();
  assert.equal(field.data.reportSheet.restored, true);
  pickB(field);
  assert.equal(field.data.reportSheet.siteId, 32);
  assert.equal(field.data.reportSheet.description, '保留水泵异响说明');
});

test('6/12、待同步和已离站均不写普通离站；正式完成不另卡照片审核', async () => {
  const site = { id: 31, site_id: 31, checked_in: true, checked_out: false };
  const instance = page(inspection, { viewMode: 'execute', site, selSite: site, selSiteId: 31,
    selectedPlanId: 10, sites: [site], total: 12, completed: 6,
    categories: [{ items: [{ item_id: 1, plan_id: 10, result: 'normal' }] }] });
  let writes = 0;
  api.checkOutExecutionSite = () => { writes += 1; return Promise.resolve({ check_out_time: '2026-11-10 10:00:00' }); };
  instance.onCheckOut();
  await settle();
  assert.equal(writes, 0);
  instance.data.completed = 12;
  instance.data.categories[0].items[0].sync_pending = true;
  instance.data.stationSyncPending = false;
  instance.onCheckOut();
  await settle();
  assert.equal(writes, 0, '点击前重读待同步事实');
  instance.data.categories[0].items[0].sync_pending = false;
  instance.data.categories[0].items[0].review_status = 'rejected';
  instance.loadTasks = () => {};
  instance.loadExecution = () => {};
  api.todayExecution = () => Promise.resolve({ packages: [] });
  instance.onCheckOut();
  await settle();
  assert.equal(writes, 1);
  assert.equal(instance.data.site.checked_out, true);
  instance.onCheckOut();
  await settle();
  assert.equal(writes, 1, '已离站补填不能再次离站');
});

test('昨天已离站缺结果仍可选原任务；父计划修改中保留展示并拦普通执行', async () => {
  const raw = { plan_id: 10, schedule_id: 100, schedule_status: 'approved',
    work_date: '2026-11-09', is_carryover: true, vehicle_needs_extension: false,
    vehicle_use_shared: true, vehicle: { id: 5, plate_no: '隔离测试车' },
    vehicle_application_id: 9, vehicle_use: { id: 20, application_id: 8 },
    arrival_gate: { allowed: true }, site_order: [31],
    sites: [{ site_id: 31, name: '甲站', checked_in: true, checked_out: true, total: 12, completed: 6 }] };
  const option = targets.buildPackageOption(raw);
  assert.equal(option.sites[0].phase, 'in_progress');
  assert.equal(targets.packageIsFullyClosed(option), false);
  assert.equal(targets.resolveExecutionTarget([option], {}).selectedPackageId, 10);
  const instance = page(inspection, { entryCanEnter: true, selectedEntryPackageId: 10,
    selectedEntrySiteId: 31, entryPackages: [option] });
  instance._rawExecutionPackages = [raw];
  let loadedPlan;
  instance.loadTasks = (site, done, response) => { loadedPlan = instance.data.selectedPlanId; assert.equal(response.site.checked_out, true); };
  let reads = 0;
  api.executionSiteTasks = () => { reads += 1; return Promise.resolve({
    site: raw.sites[0], categories: [], arrival_gate: { allowed: true } }); };
  instance.onEnterSite();
  await settle();
  assert.equal(instance.data.viewPhase, 'inspection');
  assert.equal(loadedPlan, 10);
  assert.equal(instance.data.currentPackage.work_date, '2026-11-09');
  assert.equal(instance.data.currentPackage.vehicle_use.id, 20);
  assert.equal(instance.data.currentPackage.sites[0].checked_out, true);
  const modifying = targets.buildPackageOption(Object.assign({}, raw, { schedule_status: 'modifying' }));
  assert.match(modifying.scheduleStatusLabel, /变更中/);
  instance.data.entryPackages = [modifying];
  instance.onEnterSite();
  await settle();
  assert.equal(reads, 1, '未批准父计划不能绕过审批执行');
  let nextRead;
  instance.data.sites = [Object.assign({ id: 31 }, raw.sites[0])];
  instance.data.stationOutcome = { siteId: 31 };
  instance.loadTasks = siteId => { nextRead = siteId; };
  instance.onGoToNextSite();
  assert.equal(nextRead, 31, '沿原路线可进入已离站待补填站点');
  assert.equal(instance.data.site.checked_out, true);
});

test('本人持车沿用服务端真实行程，过期可还车时优先打开还车', () => {
  const field = page(inspection, { currentPackage: { vehicle: { id: 5 }, vehicle_application_id: 9,
    vehicle_use: { id: 20 }, vehicle_use_shared: true, vehicle_needs_extension: false }, departureVm: {} });
  field.onOpenVehicleCheckout();
  assert.equal(field.data.vehicleSheet.open, false, '不重复出车');
  const car = page(vehicle, { activeUse: { id: 20, can_return: true, needs_extension: true } });
  let returns = 0, extensions = 0;
  car.onOpenReturn = () => { returns += 1; };
  car.onOpenExtension = () => { extensions += 1; };
  car.onVehiclePrimaryAction();
  assert.equal(returns, 1);
  assert.equal(extensions, 0);
});
