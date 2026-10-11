const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

function setPath(target, key, value) {
  const parts = key.split('.');
  let current = target;
  while (parts.length > 1) {
    const part = parts.shift();
    if (!current[part] || typeof current[part] !== 'object') current[part] = {};
    current = current[part];
  }
  current[parts[0]] = value;
}

const storage = { user: { id: 2, role: 'operator' }, sites: [] };
const app = { globalData: { token: 'test', executionTarget: null, sites: [] } };
let definition;
let downloadImpl;
let downloadCalls = [];
let previewCalls = [];
let removedFiles = [];
let abortedTasks = 0;

global.getApp = () => app;
global.Page = page => { definition = page; };
global.wx = {
  getStorageSync: key => storage[key],
  setStorageSync: (key, value) => { storage[key] = value; },
  removeStorageSync: key => { delete storage[key]; },
  showToast: () => {}, showModal: () => {}, reLaunch: () => {},
  stopPullDownRefresh: () => {}, setNavigationBarTitle: () => {},
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
  previewImage: options => { previewCalls.push(options); },
  removeSavedFile: ({ filePath }) => { removedFiles.push(filePath); },
  getFileSystemManager: () => ({
    unlink: ({ filePath, success }) => {
      removedFiles.push(filePath);
      if (success) success();
    },
  }),
  downloadFile: options => {
    downloadCalls.push(options.url);
    const task = { abort() { abortedTasks += 1; } };
    downloadImpl(options, task);
    return task;
  },
};

const api = require('../services/api.js');
const originalExecutionSiteTasks = api.executionSiteTasks;
require('../pages/inspection/inspection.js');

function createPage() {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
  });
  page.setData = (patch, done) => {
    Object.keys(patch).forEach(key => setPath(page.data, key, patch[key]));
    if (done) done();
  };
  page.onLoad();
  page.data.viewMode = 'readonly';
  page.data.selectedPlanId = 141;
  page.data.selSiteId = 362;
  return page;
}

function resultItem(displayUrl) {
  const hasPhoto = !!displayUrl;
  return {
    item_id: 7095, plan_id: 141, site_id: 362,
    item_name: '分析仪状态', result: 'normal', review_status: 2,
    photo_urls: hasPhoto ? '["/uploads/site_photos/display.jpg"]' : '[]',
    current_photo_urls: hasPhoto ? ['/uploads/site_photos/display.jpg'] : [],
    required_photos: hasPhoto ? 1 : 0,
    evidence_attachments: hasPhoto ? [{
      id: 140, uploader_id: 2,
      stored_path: '/uploads/site_photos/display.jpg', display_url: displayUrl,
      review_status: 'approved', evidence_qualification: 'qualified',
      extra_json: '{"material_role":"formal"}',
    }] : [],
  };
}

function openItem(page, item) {
  page.data.categories = [{ category: 'equipment', items: [item] }];
  page.onOpenItem({ currentTarget: { dataset: { id: item.item_id } } });
}

(async () => {
  try {
    downloadImpl = options => setImmediate(() => options.success({
      statusCode: 200,
      tempFilePath: 'wxfile://tmp/protected-a.jpg',
    }));
    const successPage = createPage();
    openItem(successPage, resultItem(
      'http://127.0.0.1:5000/uploads/site_photos/display.jpg?ticket=a'));
    await flush();
    await flush();
    assert.equal(downloadCalls.length, 1);
    assert.equal(successPage.data.sheet.photoMedia[0].status, 'ready');
    assert.equal(successPage.data.sheet.photoMedia[0].localPath,
      'wxfile://tmp/protected-a.jpg');
    successPage.onPreview({
      currentTarget: { dataset: { src: 'wxfile://tmp/protected-a.jpg' } },
    });
    assert.deepEqual(previewCalls[0], {
      urls: ['wxfile://tmp/protected-a.jpg'],
      current: 'wxfile://tmp/protected-a.jpg',
    });
    successPage.onHide();
    assert.ok(removedFiles.includes('wxfile://tmp/protected-a.jpg'));

    downloadCalls = [];
    const emptyPage = createPage();
    openItem(emptyPage, resultItem(''));
    await flush();
    assert.equal(downloadCalls.length, 0);

    downloadCalls = [];
    let refreshCalls = 0;
    downloadImpl = options => setImmediate(() => {
      if (options.url.includes('expired')) {
        options.success({ statusCode: 403, tempFilePath: '' });
        return;
      }
      options.success({
        statusCode: 200,
        tempFilePath: 'wxfile://tmp/protected-fresh.jpg',
      });
    });
    const freshUrl = 'http://127.0.0.1:5000/uploads/site_photos/display.jpg?ticket=fresh';
    api.executionSiteTasks = () => {
      refreshCalls += 1;
      return Promise.resolve({
        categories: [{ items: [resultItem(freshUrl)] }],
      });
    };
    const refreshPage = createPage();
    openItem(refreshPage, resultItem(
      'http://127.0.0.1:5000/uploads/site_photos/display.jpg?ticket=expired'));
    await flush();
    await flush();
    await flush();
    await flush();
    assert.equal(refreshCalls, 1);
    assert.equal(downloadCalls.length, 2);
    assert.equal(refreshPage.data.sheet.photoMedia[0].status, 'ready');
    assert.equal(refreshPage.data.sheet.photoMedia[0].localPath,
      'wxfile://tmp/protected-fresh.jpg');

    downloadCalls = [];
    api.executionSiteTasks = () => Promise.reject({ status: 503 });
    downloadImpl = options => setImmediate(() => options.fail({ errMsg: 'download fail' }));
    const retryPage = createPage();
    openItem(retryPage, resultItem(
      'http://127.0.0.1:5000/uploads/site_photos/display.jpg?ticket=network'));
    await flush();
    await flush();
    assert.equal(retryPage.data.sheet.photoMedia[0].status, 'error');
    assert.equal(retryPage.data.sheet.photoMedia[0].error,
      '照片加载失败，点击重试');
    downloadImpl = options => setImmediate(() => options.success({
      statusCode: 200,
      tempFilePath: 'wxfile://tmp/protected-retry.jpg',
    }));
    retryPage.onRetryProtectedPhoto({
      currentTarget: { dataset: { list: 'photoMedia', index: 0 } },
    });
    await flush();
    assert.equal(retryPage.data.sheet.photoMedia[0].status, 'ready');
    assert.equal(retryPage.data.sheet.photoMedia[0].localPath,
      'wxfile://tmp/protected-retry.jpg');
    retryPage.onProtectedPhotoRenderError({
      currentTarget: { dataset: { list: 'photoMedia', index: 0 } },
    });
    assert.equal(retryPage.data.sheet.photoMedia[0].status, 'error');
    assert.ok(removedFiles.includes('wxfile://tmp/protected-retry.jpg'));

    let lateDownload;
    downloadCalls = [];
    downloadImpl = (options, task) => { lateDownload = { options, task }; };
    const stalePage = createPage();
    openItem(stalePage, resultItem(
      'http://127.0.0.1:5000/uploads/site_photos/display.jpg?ticket=old-target'));
    assert.ok(lateDownload);
    stalePage._resetForExecutionTarget();
    lateDownload.options.success({
      statusCode: 200,
      tempFilePath: 'wxfile://tmp/stale-target.jpg',
    });
    assert.deepEqual(stalePage.data.sheet.photoMedia, []);
    assert.ok(abortedTasks > 0);
    assert.ok(removedFiles.includes('wxfile://tmp/stale-target.jpg'));

    const template = fs.readFileSync(
      path.resolve(__dirname, '../pages/inspection/inspection.wxml'), 'utf8');
    assert.match(template, /照片加载失败，点击重试/);
    assert.match(template, /mode="aspectFit"/);
  } finally {
    api.executionSiteTasks = originalExecutionSiteTasks;
  }
  console.log('inspectionProtectedMedia tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
