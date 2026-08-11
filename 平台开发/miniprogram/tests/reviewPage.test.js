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
      { id: 11, is_flagged: 1, flag_reason: 'GPS偏离', selectedForReject: false },
      { id: 12, selectedForReject: selectedRejectCount > 0 }
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
    const cancelPage = makePage(riskyItem('photo_cancel'));
    cancelPage.onSubmitPhotoReview(eventFor('photo_cancel'));
    cancelPage.onSubmitPhotoReview(eventFor('photo_cancel'));
    assert.equal(modalCalls.length, 1, 'rapid taps should open only one risk confirmation');
    assert.equal(cancelPage.data.submittingId, 'photo_cancel', 'risk confirmation should lock the item');
    modalCalls[0].success({ confirm: false, cancel: true });
    assert.equal(cancelPage.data.submittingId, '', 'cancel should release the risk lock');
    cancelPage.onSubmitPhotoReview(eventFor('photo_cancel'));
    assert.equal(modalCalls.length, 2, 'cancelled risk confirmation should be retryable');

    resetObservations();
    const failModalPage = makePage(riskyItem('photo_modal_fail'));
    failModalPage.onSubmitPhotoReview(eventFor('photo_modal_fail'));
    modalCalls[0].fail({ errMsg: 'showModal:fail' });
    assert.equal(failModalPage.data.submittingId, '', 'modal failure should release the risk lock');

    resetObservations();
    requestCount = 0;
    request = deferred();
    const approvePage = makePage(riskyItem('photo_approve'));
    approvePage.onSubmitPhotoReview(eventFor('photo_approve'));
    const riskModal = modalCalls[0];
    riskModal.success({ confirm: true, cancel: false });
    assert.equal(requestCount, 1, 'risk confirmation should dispatch one request');
    assert.equal(approvePage.data.submittingId, 'photo_approve', 'confirmed review should retain the same lock');
    approvePage.onSubmitPhotoReview(eventFor('photo_approve'));
    riskModal.success({ confirm: true, cancel: false });
    assert.equal(modalCalls.length, 1, 'confirmed request should block later confirmation dialogs');
    assert.equal(requestCount, 1, 'repeated confirmation should not duplicate the request');
    request.resolve({ ok: true });
    await flushPromises();
    assert.equal(approvePage.data.submittingId, '', 'successful request should release the lock');

    resetObservations();
    request = deferred();
    api.reviewPhoto = () => { requestCount += 1; return request.promise; };
    const errorPage = makePage(riskyItem('photo_error'));
    errorPage.onSubmitPhotoReview(eventFor('photo_error'));
    modalCalls[0].success({ confirm: true });
    request.reject({ error: '服务拒绝' });
    await flushPromises();
    assert.equal(errorPage.data.submittingId, '', 'failed request should release the lock');
    assert.equal(modalCalls.length, 2, 'failed request should show the failure reason');
    assert.match(modalCalls[1].content, /服务拒绝/);
    errorPage.onSubmitPhotoReview(eventFor('photo_error'));
    assert.equal(modalCalls.length, 3, 'failed review should be retryable');

    resetObservations();
    requestCount = 0;
    request = deferred();
    api.reviewPhotoSelection = () => { requestCount += 1; return request.promise; };
    const selectivePage = makePage(riskyItem('photo_selective', 1));
    selectivePage.onSubmitPhotoReview(eventFor('photo_selective'));
    modalCalls[0].success({ confirm: true });
    assert.equal(selectivePage.data.rejectShow, true, 'confirmed selective review should open its reason sheet');
    assert.equal(selectivePage.data.submittingId, 'photo_selective', 'reason sheet should retain the same lock');
    selectivePage.closeReject();
    assert.equal(selectivePage.data.submittingId, '', 'closing the reason sheet should release the risk lock');

    resetObservations();
    request = deferred();
    const selectiveSubmitPage = makePage(riskyItem('photo_selective_submit', 1));
    selectiveSubmitPage.onSubmitPhotoReview(eventFor('photo_selective_submit'));
    modalCalls[0].success({ confirm: true });
    selectiveSubmitPage.data.rejectReason = '影像不完整';
    selectiveSubmitPage.rejectConfirm();
    selectiveSubmitPage.rejectConfirm();
    assert.equal(requestCount, 1, 'confirmed selective review should issue one request');
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
