const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const apiPath = require.resolve('../services/api.js');
const viewPath = require.resolve('../pages/review/view.js');
const api = require(apiPath);

let pageDefinition;
let downloadCalls = [];
let previewCalls = [];
let toastCalls = [];
let removedFiles = [];
let imageInfoResponses = [];

global.getApp = () => ({ globalData: { token: 'test-token' } });
global.Page = definition => { pageDefinition = definition; };
global.wx = {
  downloadFile(options) {
    const task = { aborted: false, abort() { this.aborted = true; } };
    downloadCalls.push({ options, task });
    return task;
  },
  getFileSystemManager() {
    return { unlink(options) { removedFiles.push(options.filePath); } };
  },
  getImageInfo(options) {
    const next = imageInfoResponses.shift();
    if (next === 'fail') options.fail({ errMsg: 'getImageInfo:fail decode busy' });
    else options.success({ width: 1200, height: 1600, path: options.src });
  },
  previewImage(options) { previewCalls.push(options); },
  showToast(options) { toastCalls.push(options); },
  showModal() {},
  pageScrollTo() {},
  stopPullDownRefresh() {},
  navigateBack() {},
  navigateTo() {},
  reLaunch() {}
};

delete require.cache[viewPath];
require(viewPath);

function pendingInspection(displayUrl, options) {
  const settings = options || {};
  const attachments = settings.noPhotos ? [] : [{
    id: settings.attachmentId || 11,
    item_id: 70,
    item_name: '余氯检查',
    stored_path: '/private/site-photos/display.jpg',
    display_url: displayUrl,
    taken_at: '2026-10-10 14:55:00',
    review_status_label: '待审核'
  }];
  return {
    id: settings.id || 'inspection_batch_1',
    source_type: 'inspection_batch',
    source_label: '巡检审核',
    title: '一号站现场结果',
    site_name: '一号站',
    attachment_ids: attachments.map(row => row.id),
    item_ids: [70],
    attachment_details: attachments,
    item_details: [{ id: 70, item_name: '余氯检查', result: 'normal', remark: '' }]
  };
}

function makePage() {
  const page = Object.assign({}, pageDefinition, {
    data: Object.assign({}, pageDefinition.data, {
      loading: true, loadState: 'loading', groups: [], total: 0
    })
  });
  page.setData = (patch, done) => {
    Object.assign(page.data, patch);
    if (done) done();
  };
  page.onLoad({});
  return page;
}

function resetObservations() {
  downloadCalls = [];
  previewCalls = [];
  toastCalls = [];
  removedFiles = [];
  imageInfoResponses = [];
}

async function flushPromises() {
  await new Promise(resolve => setImmediate(resolve));
  await Promise.resolve();
}

function currentItem(page, id = 'inspection_batch_1') {
  return page._findItem(id);
}

function photoEvent(item, photo) {
  return { currentTarget: { dataset: { id: item.id, photoKey: photo.mediaKey } } };
}

const originalAuditPending = api.auditPending;

async function main() {
  try {
    resetObservations();
    let auditCalls = 0;
    api.auditPending = () => {
      auditCalls += 1;
      return Promise.resolve([pendingInspection('http://127.0.0.1:5000/api/media/11?ticket=fresh')]);
    };
    const successPage = makePage();
    successPage.load();
    await flushPromises();
    assert.equal(auditCalls, 1);
    assert.equal(downloadCalls.length, 1);
    assert.equal(downloadCalls[0].options.url,
      'http://127.0.0.1:5000/api/media/11?ticket=fresh',
      'the review page downloads the signed display_url instead of stored_path');
    let item = currentItem(successPage);
    let photo = item.reviewPhotos[0];
    successPage.onPreviewPhoto(photoEvent(item, photo));
    assert.equal(previewCalls.length, 0);
    assert.equal(toastCalls[0].title, '照片尚未加载完成，请稍后重试');
    downloadCalls[0].options.success({ statusCode: 200, tempFilePath: 'wxfile://review-photo-11.jpg' });
    await flushPromises();
    item = currentItem(successPage);
    photo = item.reviewPhotos[0];
    assert.equal(photo.mediaStatus, 'ready');
    assert.equal(photo.localPath, 'wxfile://review-photo-11.jpg');
    successPage.onPreviewPhoto(photoEvent(item, photo));
    assert.deepEqual(previewCalls[0], {
      urls: ['wxfile://review-photo-11.jpg'], current: 'wxfile://review-photo-11.jpg'
    }, 'thumbnail and preview share the downloaded local file');
    assert.equal(previewCalls[0].urls.some(url => /^https?:\/\//.test(url)), false,
      'preview never receives a remote ticket URL');
    successPage.onToggleDetails({ currentTarget: { dataset: { id: item.id } } });
    assert.ok(removedFiles.includes('wxfile://review-photo-11.jpg'));
    assert.equal(currentItem(successPage).detailOpen, false);
    successPage.onToggleDetails({ currentTarget: { dataset: { id: item.id } } });
    assert.equal(downloadCalls.length, 2, 'reopening a folded review unit starts a fresh local load');

    resetObservations();
    const ticketRows = [
      [pendingInspection('http://127.0.0.1:5000/api/media/11?ticket=expired')],
      [pendingInspection('http://127.0.0.1:5000/api/media/11?ticket=renewed')]
    ];
    auditCalls = 0;
    api.auditPending = () => Promise.resolve(ticketRows[Math.min(auditCalls++, ticketRows.length - 1)]);
    const retryPage = makePage();
    retryPage.load();
    await flushPromises();
    downloadCalls[0].options.fail({ statusCode: 403 });
    await flushPromises();
    assert.equal(auditCalls, 2, 'an expired ticket refreshes its review unit once');
    assert.equal(downloadCalls.length, 2);

    assert.equal(downloadCalls[1].options.url,
      'http://127.0.0.1:5000/api/media/11?ticket=renewed');
    downloadCalls[1].options.fail({ statusCode: 403 });
    await flushPromises();
    assert.equal(auditCalls, 2, 'a second failure cannot refresh the ticket again');
    item = currentItem(retryPage);
    photo = item.reviewPhotos[0];
    assert.equal(photo.mediaStatus, 'error');

    assert.equal(photo.mediaError, '照片加载失败，点击重试');
    retryPage.onRetryProtectedReviewPhoto(photoEvent(item, photo));
    assert.equal(downloadCalls.length, 3);
    downloadCalls[2].options.success({ statusCode: 200, tempFilePath: 'wxfile://review-retry.jpg' });
    await flushPromises();
    item = currentItem(retryPage);
    photo = item.reviewPhotos[0];
    retryPage.onProtectedReviewPhotoRenderError(photoEvent(item, photo));
    assert.equal(currentItem(retryPage).reviewPhotos[0].mediaStatus, 'error');
    assert.ok(removedFiles.includes('wxfile://review-retry.jpg'));

    resetObservations();
    const staleRows = [
      [pendingInspection('http://127.0.0.1:5000/api/media/11?ticket=old')],
      [pendingInspection('http://127.0.0.1:5000/api/media/12?ticket=new', { attachmentId: 12 })]
    ];
    auditCalls = 0;
    api.auditPending = () => Promise.resolve(staleRows[Math.min(auditCalls++, staleRows.length - 1)]);
    const stalePage = makePage();
    stalePage.load();
    await flushPromises();
    const oldDownload = downloadCalls[0];
    stalePage.load();
    await flushPromises();
    assert.equal(oldDownload.task.aborted, true);

    oldDownload.options.success({ statusCode: 200, tempFilePath: 'wxfile://late-old.jpg' });
    await flushPromises();
    assert.ok(removedFiles.includes('wxfile://late-old.jpg'));
    item = currentItem(stalePage);
    assert.equal(item.reviewPhotos[0].id, 12);
    assert.notEqual(item.reviewPhotos[0].localPath, 'wxfile://late-old.jpg');
    downloadCalls[1].options.success({ statusCode: 200, tempFilePath: 'wxfile://current.jpg' });
    await flushPromises();
    stalePage.onUnload();
    assert.ok(removedFiles.includes('wxfile://current.jpg'));

    resetObservations();
    api.auditPending = () => Promise.resolve([pendingInspection('', { noPhotos: true })]);
    const emptyPhotoPage = makePage();
    emptyPhotoPage.load();
    await flushPromises();
    assert.equal(downloadCalls.length, 0);

    resetObservations();
    auditCalls = 0;
    api.auditPending = () => {
      auditCalls += 1;
      return Promise.resolve([pendingInspection('http://127.0.0.1:5000/api/media/11?ticket=decode')]);
    };
    const decodePage = makePage();
    decodePage.load();
    await flushPromises();
    imageInfoResponses.push('fail', 'success');
    downloadCalls[0].options.success({ statusCode: 200, tempFilePath: 'wxfile://decode-ready.jpg' });
    await new Promise(resolve => setTimeout(resolve, 100));
    await flushPromises();
    assert.equal(currentItem(decodePage).reviewPhotos[0].mediaStatus, 'ready');
    assert.equal(auditCalls, 1, 'local decoder readiness does not waste a fresh media ticket');
    assert.equal(downloadCalls.length, 1, 'decoder readiness retry reuses the downloaded local file');
    item = currentItem(decodePage);
    photo = item.reviewPhotos[0];
    decodePage.onProtectedReviewPhotoRenderError(photoEvent(item, photo));
    assert.equal(currentItem(decodePage).reviewPhotos[0].mediaStatus, 'error');
    assert.equal(auditCalls, 1, 'a local render failure does not refresh a valid media ticket');
    assert.equal(downloadCalls.length, 1, 'a local render failure waits for explicit retry');
    assert.ok(removedFiles.includes('wxfile://decode-ready.jpg'));

    const wxml = fs.readFileSync(path.join(__dirname, '../pages/review/view.wxml'), 'utf8');
    assert.match(wxml, /照片加载失败，点击重试/);
    assert.match(wxml, /mode="aspectFit"/);
    assert.match(wxml, /src="\{\{photo\.localPath\}\}"/);
    assert.doesNotMatch(wxml, /src="\{\{photo\.url\}\}"/);

    console.log('review protected media tests passed');
  } finally {
    api.auditPending = originalAuditPending;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
