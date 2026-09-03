const assert = require('node:assert/strict');

const definitions = {};
const previews = [];
const toasts = [];
const modals = [];
const navigations = [];
let chosenPaths = [];
global.Page = page => { definitions.current = page; };
global.getApp = () => ({ globalData: {} });
global.wx = {
  previewImage: options => previews.push(options),
  navigateTo: options => navigations.push(options),
  showToast: options => toasts.push(options),
  showModal: options => { modals.push(options); if (options.success) options.success({ confirm: true }); },
  showLoading() {}, hideLoading() {}, reLaunch() {},
  getStorageSync: () => '',
  chooseMedia: options => options.success({ tempFiles: chosenPaths.map(tempFilePath => ({ tempFilePath })) }),
  compressImage: options => options.success({ tempFilePath: options.src }),
  getFileSystemManager: () => ({ readFile: options => options.success({ data: 'image' }) }),
  authorize: options => options.success(),
  getLocation: options => options.success({ latitude: 28.68, longitude: 115.86 }),
};

const api = require('../services/api.js');
require('../pages/reports/reports.js');
const reportsPage = definitions.current;
require('../pages/workorder/workorder.js');
const workorderPage = definitions.current;

function pageInstance(definition, data) {
  const page = Object.assign({}, definition, {
    data: Object.assign(JSON.parse(JSON.stringify(definition.data)), data || {}),
  });
  page.setData = updates => Object.entries(updates).forEach(([key, value]) => {
    const parts = key.split('.');
    let target = page.data;
    while (parts.length > 1) {
      const part = parts.shift();
      target = target[part];
    }
    target[parts[0]] = value;
  });
  return page;
}

function reportSheet(sites, overrides) {
  return Object.assign({
    open: true, sites, siteId: sites[0] && sites[0].id, siteName: sites[0] && sites[0].name,
    siteIndex: 0, typeIndex: 0, description: '', photos: [], photoSiteId: null,
    uploading: false, submitting: false,
  }, overrides || {});
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setImmediate(resolve));
}

async function run() {
  const report = pageInstance(reportsPage, { reports: [{
    id: 1, photo_urls: JSON.stringify(['/uploads/site_photos/old.jpg', 'bad-path', 'https://other.example/x.jpg'])
  }] });
  report.onOpenDetail({ currentTarget: { dataset: { id: 1 } } });
  assert.equal(report.data.detail.photos[0].url.endsWith('/uploads/site_photos/old.jpg'), true);
  assert.equal(report.data.detail.photos[1].url, '');
  assert.equal(report.data.detail.photos[2].url, 'https://other.example/x.jpg');
  report.onPreviewPhoto({ currentTarget: { dataset: { src: report.data.detail.photos[0].url } } });
  assert.deepEqual(previews.at(-1).urls, [report.data.detail.photos[0].url, 'https://other.example/x.jpg']);

  api.workorderDetail = orderNo => Promise.resolve({ order_no: orderNo, status: 'reviewing', flow_events: [{ type: 'accepted', label: '接单' }] });
  api.workorderRelated = () => Promise.resolve({});
  const workorder = pageInstance(workorderPage);
  workorder.openWorkorderSheet({ order_no: 'WO-1', status: 'reviewing' });
  await settle();
  assert.equal(workorder.data.sheet.item.flowUnavailable, false);
  assert.deepEqual(workorder.data.sheet.item.flowEvents.map(event => event.type), ['accepted']);
  api.workorderDetail = orderNo => Promise.resolve({ order_no: orderNo, status: 'reviewing' });
  workorder.openWorkorderSheet({ order_no: 'WO-2', status: 'reviewing' });
  await settle();
  assert.equal(workorder.data.sheet.item.flowUnavailable, true);

  // A single current server-authorized site opens the form directly.
  api.sites = () => Promise.resolve([{ id: 21, name: '单站' }]);
  const single = pageInstance(reportsPage);
  await single.onGoReport();
  assert.equal(single.data.reportSheet.open, true);
  assert.equal(single.data.reportSheet.siteId, 21);

  // Several sites deliberately require an explicit choice, not a guessed first site.
  api.sites = () => Promise.resolve([{ id: 21, name: '甲站' }, { id: 22, name: '乙站' }]);
  const multiple = pageInstance(reportsPage);
  await multiple.onGoReport();
  assert.equal(multiple.data.reportSheet.open, true);
  assert.equal(multiple.data.reportSheet.siteId, null);
  multiple.onReportSite({ detail: { value: '1' } });
  assert.equal(multiple.data.reportSheet.siteId, 22);

  // No sites never opens a fake form, and a failed request can be retried in place.
  api.sites = () => Promise.resolve([]);
  const empty = pageInstance(reportsPage);
  await empty.onGoReport();
  assert.equal(empty.data.reportSheet.open, false);
  let siteAttempts = 0;
  api.sites = () => {
    siteAttempts += 1;
    return siteAttempts === 1 ? Promise.reject({ error: '网络异常' }) : Promise.resolve([{ id: 23, name: '重试站' }]);
  };
  const retry = pageInstance(reportsPage);
  await retry.onGoReport();
  assert.equal(retry.data.reportSiteLoadError, '网络异常');
  await retry.onRetryReportSites();
  assert.equal(retry.data.reportSheet.siteId, 23);

  // Repeated taps share one site read; an unloaded page ignores that old response.
  const pendingSites = deferred();
  let siteReads = 0;
  api.sites = () => { siteReads += 1; return pendingSites.promise; };
  const repeated = pageInstance(reportsPage);
  const firstOpen = repeated.onGoReport();
  const secondOpen = repeated.onGoReport();
  assert.equal(siteReads, 1);
  assert.equal(firstOpen, secondOpen);
  pendingSites.resolve([{ id: 24, name: '去重站' }]);
  await firstOpen;
  assert.equal(repeated.data.reportSheet.open, true);
  const staleSites = deferred();
  api.sites = () => staleSites.promise;
  const stale = pageInstance(reportsPage);
  const staleOpen = stale.onGoReport();
  stale.onHide();
  staleSites.resolve([{ id: 25, name: '过期站' }]);
  await staleOpen;
  assert.equal(stale.data.reportSheet.open, false);

  // Upload is bound to the selected site and a photo-bearing draft cannot switch site.
  const siteA = { id: 31, name: '甲站' };
  const siteB = { id: 32, name: '乙站' };
  const uploadCalls = [];
  api.uploadSitePhoto = (siteId, image) => {
    uploadCalls.push({ siteId, image });
    return Promise.resolve({ url: '/uploads/site_photos/31-a.jpg' });
  };
  chosenPaths = ['/tmp/31-a.jpg'];
  const photos = pageInstance(reportsPage, { reportSheet: reportSheet([siteA, siteB]) });
  await photos.onAddReportPhoto();
  assert.deepEqual(uploadCalls.map(call => call.siteId), [31]);
  assert.equal(photos.data.reportSheet.photoSiteId, 31);
  photos.onReportSite({ detail: { value: '1' } });
  assert.equal(photos.data.reportSheet.siteId, 31);
  const backgroundUpload = deferred();
  api.uploadSitePhoto = () => backgroundUpload.promise;
  chosenPaths = ['/tmp/31-background.jpg'];
  const hiddenUpload = pageInstance(reportsPage, { reportSheet: reportSheet([siteA]) });
  const uploadWhileHidden = hiddenUpload.onAddReportPhoto();
  await settle();
  hiddenUpload.onHide();
  backgroundUpload.resolve({ url: '/uploads/site_photos/31-background.jpg' });
  await uploadWhileHidden;
  assert.equal(hiddenUpload.data.reportSheet.uploading, false);
  assert.equal(hiddenUpload.data.reportSheet.photos[0].endsWith('/uploads/site_photos/31-background.jpg'), true);

  // Client validation fails before any write, including a mismatched photo-site binding.
  let submitCalls = 0;
  api.submitManualReport = () => { submitCalls += 1; return Promise.resolve({ order_no: 'MR-ignored' }); };
  const invalid = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], { description: '', photos: [] }) });
  await invalid.onSubmitReport();
  invalid.setData({ 'reportSheet.description': '现场异味', 'reportSheet.photos': ['http://invalid/photo.jpg'], 'reportSheet.photoSiteId': 31 });
  await invalid.onSubmitReport();
  assert.equal(submitCalls, 0);

  // One valid draft writes once despite repeated submit; a failure keeps all user input for retry.
  const submitDeferred = deferred();
  let submittedPayload = null;
  api.submitManualReport = payload => { submittedPayload = payload; submitCalls += 1; return submitDeferred.promise; };
  const validDraft = reportSheet([siteA], {
    description: '现场异味', photos: ['/uploads/site_photos/31-a.jpg'], photoSiteId: 31,
  });
  const duplicate = pageInstance(reportsPage, { reportSheet: validDraft });
  const firstSubmit = duplicate.onSubmitReport();
  const secondSubmit = duplicate.onSubmitReport();
  await settle();
  assert.equal(submitCalls, 1);
  assert.equal(firstSubmit, secondSubmit);
  assert.equal(submittedPayload.site_id, 31);
  assert.deepEqual(submittedPayload.photo_urls, ['/uploads/site_photos/31-a.jpg']);
  submitDeferred.resolve({ order_no: 'MR202609030001' });
  api.manualReports = () => Promise.resolve([]);
  await firstSubmit;
  assert.equal(duplicate.data.reportSheet.open, false);
  assert.equal(modals.at(-1).content.includes('MR202609030001'), true);
  const backgroundSubmit = deferred();
  api.submitManualReport = () => backgroundSubmit.promise;
  const hiddenSubmit = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], {
    description: '后台返回测试', photos: ['/uploads/site_photos/31-background.jpg'], photoSiteId: 31,
  }) });
  const submitWhileHidden = hiddenSubmit.onSubmitReport();
  await settle();
  hiddenSubmit.onHide();
  backgroundSubmit.resolve({ order_no: 'MR202609030002' });
  await submitWhileHidden;
  assert.equal(hiddenSubmit.data.reportSheet.open, false);
  assert.equal(hiddenSubmit.data.reportSheet.submitting, false);
  assert.equal(hiddenSubmit.data.reportSheet.description, '');
  assert.equal(hiddenSubmit._reportSuccessNotice.orderNo, 'MR202609030002');
  api.manualReports = () => Promise.resolve([]);
  hiddenSubmit.onShow();
  assert.equal(modals.at(-1).content.includes('MR202609030002'), true);
  const backgroundFailure = deferred();
  api.submitManualReport = () => backgroundFailure.promise;
  const hiddenFailure = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], {
    description: '后台失败测试', photos: ['/uploads/site_photos/31-background.jpg'], photoSiteId: 31,
  }) });
  const failedWhileHidden = hiddenFailure.onSubmitReport();
  await settle();
  hiddenFailure.onHide();
  backgroundFailure.reject({ error: '网络中断' });
  await failedWhileHidden;
  assert.equal(hiddenFailure.data.reportSheet.submitting, false);
  assert.equal(hiddenFailure.data.reportSheet.description, '后台失败测试');
  const failed = pageInstance(reportsPage, { reportSheet: reportSheet([siteA], {
    description: '泵房异响', photos: ['/uploads/site_photos/31-b.jpg'], photoSiteId: 31,
  }) });
  api.submitManualReport = () => Promise.reject({ error: '网络中断' });
  await failed.onSubmitReport();
  assert.equal(failed.data.reportSheet.description, '泵房异响');
  assert.equal(failed.data.reportSheet.photos.length, 1);
  assert.equal(failed.data.reportSheet.submitting, false);
  api.submitManualReport = () => Promise.resolve({});
  await failed.onSubmitReport();
  assert.equal(failed.data.reportSheet.open, true);
  assert.equal(failed.data.reportSheet.description, '泵房异响');
  assert.equal(navigations.some(item => /inspection/.test(item.url || '')), false);

  console.log('reportWorkorderConnection tests passed');
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
