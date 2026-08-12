const assert = require('node:assert/strict');

const apiPath = require.resolve('../services/api.js');
const viewPath = require.resolve('../pages/review/view.js');
const api = require(apiPath);

let pageDefinition;
const modalCalls = [];
const toastCalls = [];

global.getApp = () => ({ globalData: { token: 'test-token' } });
global.Page = definition => { pageDefinition = definition; };
global.wx = {
  showModal(options) { modalCalls.push(options); },
  showToast(options) { toastCalls.push(options); },
  pageScrollTo() {},
  stopPullDownRefresh() {},
  navigateBack() {}
};

delete require.cache[viewPath];
require(viewPath);

function eventFor(id) {
  return { currentTarget: { dataset: { id } } };
}

function makePage(item) {
  const page = Object.assign({}, pageDefinition, {
    data: Object.assign({}, pageDefinition.data, {
      loading: false,
      groups: [{ label: 'photo', items: [item] }]
    })
  });
  page._submittingId = '';
  page._submissionPhase = '';
  page.reloads = 0;
  page.setData = (patch, done) => {
    Object.assign(page.data, patch);
    if (done) done();
  };
  page.load = () => { page.reloads += 1; };
  return page;
}

function riskyItem(id, selectedRejectCount = 0) {
  return {
    id,
    source_type: 'photo_review',
    attachment_ids: [11, 12],
    selectedRejectCount,
    reviewPhotos: [
      { id: 11, is_flagged: 1, flag_reason: 'GPS偏离', evidence_qualification: 'qualified', selectedForReject: false },
      { id: 12, evidence_qualification: 'qualified', selectedForReject: selectedRejectCount > 0 }
    ]
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function resetObservations() {
  modalCalls.length = 0;
  toastCalls.length = 0;
}

async function flushPromises() {
  await new Promise(resolve => setImmediate(resolve));
  await Promise.resolve();
}

const originalReviewPhoto = api.reviewPhoto;
const originalReviewPhotoSelection = api.reviewPhotoSelection;

async function main() {
  try {
    resetObservations();
    let requestCount = 0;
    let request = deferred();
    api.reviewPhoto = () => { requestCount += 1; return request.promise; };

    const ordinaryPage = makePage(riskyItem('photo_ordinary', 0));
    ordinaryPage.data.groups[0].items[0].reviewPhotos[0].is_flagged = 0;
    ordinaryPage.data.groups[0].items[0].reviewPhotos[0].flag_reason = '';
    ordinaryPage.onSubmitPhotoReview(eventFor('photo_ordinary'));
    assert.equal(modalCalls.length, 0, 'ordinary photo review should not open a risk modal');
    assert.equal(requestCount, 1, 'ordinary photo review should dispatch immediately');
    assert.equal(ordinaryPage.data.submittingId, 'photo_ordinary');
    ordinaryPage.onSubmitPhotoReview(eventFor('photo_ordinary'));
    assert.equal(requestCount, 1, 'ordinary review double tap should remain single-request');
    request.resolve({ ok: true });
    await flushPromises();
    assert.equal(ordinaryPage.data.submittingId, '');

    resetObservations();
    requestCount = 0;
    request = deferred();
    api.reviewPhotoSelection = () => { requestCount += 1; return request.promise; };
    const selectivePage = makePage(riskyItem('photo_selective', 1));
    selectivePage.data.groups[0].items[0].reviewPhotos[1].evidence_qualification = 'ineligible';
    selectivePage.onSubmitPhotoReview(eventFor('photo_selective'));
    assert.equal(selectivePage.data.rejectShow, true, 'rejecting the nonqualified photo should open its reason sheet');
    selectivePage.closeReject();
    assert.equal(selectivePage.data.submittingId, '', 'closing the reason sheet should leave submission unlocked');

    resetObservations();
    request = deferred();
    const selectiveSubmitPage = makePage(riskyItem('photo_selective_submit', 1));
    selectiveSubmitPage.data.groups[0].items[0].reviewPhotos[1].evidence_qualification = 'ineligible';
    selectiveSubmitPage.onSubmitPhotoReview(eventFor('photo_selective_submit'));
    selectiveSubmitPage.data.rejectReason = '影像不完整';
    selectiveSubmitPage.rejectConfirm();
    selectiveSubmitPage.rejectConfirm();
    assert.equal(requestCount, 1, 'selective rejection should issue one request');
    request.resolve({ ok: true });
    await flushPromises();
    assert.equal(selectiveSubmitPage.data.submittingId, '');

    console.log('reviewPage tests passed');
  } finally {
    api.reviewPhoto = originalReviewPhoto;
    api.reviewPhotoSelection = originalReviewPhotoSelection;
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
