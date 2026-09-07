const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const api = require('../services/api.js');
const viewPath = require.resolve('../pages/review/view.js');

let definition;
const toasts = [];
const modals = [];
const scrolls = [];

global.getApp = () => ({ globalData: { token: 'test-token' } });
global.Page = page => { definition = page; };
global.wx = {
  showToast(options) { toasts.push(options); },
  showModal(options) { modals.push(options); },
  pageScrollTo(options) { scrolls.push(options); },
  stopPullDownRefresh() {},
  navigateBack() {},
  reLaunch() {}
};

delete require.cache[viewPath];
require(viewPath);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

async function flush() {
  await new Promise(resolve => setImmediate(resolve));
  await Promise.resolve();
}

function pageInstance() {
  const page = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)) });
  page._alive = true;
  page.setData = (patch, done) => {
    Object.assign(page.data, patch);
    if (done) done();
  };
  return page;
}

function reviewItem(sourceType, id) {
  return { id: id || sourceType + '_1', source_type: sourceType, title: sourceType };
}

const original = {};
[
  'auditPending', 'dataReviewDetail', 'reviewInspectionItem', 'reviewInspectionBatch',
  'reviewInspectionPhotoSelection', 'approveWorkorder', 'rejectWorkorder', 'reviewPhoto',
  'reviewPhotoSelection', 'approvePartsRequest', 'rejectPartsRequest', 'approveSparePart',
  'rejectSparePart', 'approveVehicle', 'approvePlanSchedule', 'rejectPlanSchedule', 'reviewDataReview'
  , 'auditTargetStatus'
].forEach(key => { original[key] = api[key]; });

async function main() {
  try {
    api.auditPending = () => Promise.resolve([]);
    const emptyPage = pageInstance();
    emptyPage.load();
    await flush();
    assert.equal(emptyPage.data.loadState, 'empty');
    assert.equal(emptyPage.data.total, 0);

    api.auditTargetStatus = () => Promise.resolve({ state: 'processed', result_label: '已通过', result_detail: '已归档' });
    const processedTargetPage = pageInstance();
    processedTargetPage.onLoad({ target_type: 'vehicle_application', target_id: '55' });
    processedTargetPage.load();
    await flush();
    assert.equal(modals.at(-1).title, '事项已处理');
    assert.match(modals.at(-1).content, /已由其他审核人处理/);
    assert.match(modals.at(-1).content, /当前结果：已通过/);

    api.auditTargetStatus = () => Promise.reject({ error: '当前账号无权查看该审批事项' });
    const forbiddenTargetPage = pageInstance();
    forbiddenTargetPage.onLoad({ target_type: 'vehicle_application', target_id: '56' });
    forbiddenTargetPage.load();
    await flush();
    assert.equal(modals.at(-1).content, '当前账号无权访问该审核事项。');

    api.auditPending = () => Promise.reject(new Error('offline'));
    const errorPage = pageInstance();
    errorPage.load();
    await flush();
    assert.equal(errorPage.data.loadState, 'error');
    assert.equal(errorPage.data.total, 0);
    assert.match(errorPage.data.loadError, /加载失败/);

    let writeCount = 0;
    api.reviewPhoto = () => { writeCount += 1; return Promise.resolve({}); };
    const retained = Object.assign(reviewItem('photo_review', 'photo_7'), {
      canReview: true,
      detailOpen: true,
      selectedRejectCount: 1,
      reviewPhotos: [{ id: 7, selectedForReject: true }]
    });
    const stalePage = pageInstance();
    stalePage.data.groups = [
      { label: '影像审核', items: [retained] },
      { label: '车辆审核', items: [Object.assign(reviewItem('vehicle_application', 'va_stale'), { detailOpen: false })] }
    ];
    stalePage.data.total = 2;
    stalePage.data.showGroupHeaders = true;
    stalePage.data.loadState = 'data';
    stalePage.data.loading = false;
    stalePage.load();
    await flush();
    assert.equal(stalePage.data.loadState, 'stale');
    assert.equal(stalePage.data.groups[0].items[0], retained, 'failed refresh keeps the rendered item and local selection');
    assert.equal(stalePage.data.showGroupHeaders, true, 'failed refresh retains the existing group-header projection');
    stalePage.onSubmitPhotoReview({ currentTarget: { dataset: { id: 'photo_7' } } });
    assert.equal(writeCount, 0, 'stale data cannot submit a decision');
    assert.equal(toasts.at(-1).title, '请先刷新后再操作');

    api.auditPending = () => Promise.resolve([reviewItem('photo_review', 'photo_8')]);
    stalePage.onRetry();
    await flush();
    assert.equal(stalePage.data.loadState, 'data');
    assert.equal(stalePage.data.groups[0].items[0].id, 'photo_8');
    assert.equal(stalePage.data.groups[0].items[0].canReview, true);
    assert.equal(stalePage.data.groups[0].items[0].detailOpen, true, 'a successful single-item retry reapplies the default expansion');
    assert.equal(stalePage.data.showGroupHeaders, false);

    const singleTypes = ['plan_schedule', 'inspection_batch', 'workorder_review', 'parts_request', 'vehicle_application', 'data_review'];
    for (const sourceType of singleTypes) {
      api.auditPending = () => Promise.resolve([reviewItem(sourceType, sourceType + '_single')]);
      const page = pageInstance();
      page.load();
      await flush();
      assert.equal(page.data.groups[0].items[0].detailOpen, true, sourceType + ' auto-expands as a single review item');
      assert.equal(page.data.showGroupHeaders, false, 'a single group does not render a redundant title');
    }

    api.auditPending = () => Promise.resolve([
      reviewItem('parts_request', 'pr_1'), reviewItem('parts_request', 'pr_2')
    ]);
    const sameTypePage = pageInstance();
    sameTypePage.load();
    await flush();
    assert.equal(sameTypePage.data.showGroupHeaders, false);
    assert.deepEqual(sameTypePage.data.groups[0].items.map(item => item.detailOpen), [false, false], 'multiple same-type items default to summaries');

    api.auditPending = () => Promise.resolve([
      reviewItem('parts_request', 'pr_12'), reviewItem('vehicle_application', 'va_13')
    ]);
    const multiTypePage = pageInstance();
    multiTypePage.load();
    await flush();
    assert.equal(multiTypePage.data.showGroupHeaders, true, 'multiple groups expose their type headers');
    assert.deepEqual(multiTypePage.data.groups.flatMap(group => group.items).map(item => item.detailOpen), [false, false]);

    const targetMultiPage = pageInstance();
    targetMultiPage.onLoad({ target_type: 'parts_request', target_id: '12' });
    targetMultiPage.load();
    await flush();
    const targetItems = targetMultiPage.data.groups.flatMap(group => group.items);
    assert.equal(targetItems.find(item => item.id === 'pr_12').detailOpen, true, 'precise targets expand after the multi-item default projection');
    assert.equal(targetItems.find(item => item.id === 'va_13').detailOpen, false);

    let lookupCalls = 0;
    const currentWorkorder = Object.assign(reviewItem('workorder_review', 'wo_82'), {
      order_no: 'WO-82', review_cycle: 2
    });
    api.auditPending = () => Promise.resolve([currentWorkorder]);
    api.auditTargetStatus = (type, id, cycle) => {
      lookupCalls += 1;
      assert.deepEqual([type, id, cycle], ['workorder_review', 'WO-82', 'review:1']);
      return Promise.resolve({ state: 'processed', result_label: '已退回', result_detail: '请补拍' });
    };
    const oldCyclePage = pageInstance();
    oldCyclePage.onLoad({ target_type: 'workorder_review', target_id: 'WO-82', cycle_key: 'review:1' });
    oldCyclePage.load();
    await flush();
    assert.equal(lookupCalls, 1, 'cycle links always validate before matching a current list item');
    assert.equal(oldCyclePage.data.groups[0].items[0].detailOpen, false, 'old cycle never opens the current cycle item');
    assert.equal(modals.at(-1).title, '事项已处理');

    const currentPlan = Object.assign(reviewItem('plan_schedule', 'ps_81'), { schedule_id: 81 });
    api.auditPending = () => Promise.resolve([currentPlan]);
    api.auditTargetStatus = (type, id, cycle) => {
      lookupCalls += 1;
      assert.deepEqual([type, id, cycle], ['plan_schedule', '81', 'event:811']);
      return Promise.resolve({ state: 'pending', cycle_key: cycle });
    };
    const currentCyclePage = pageInstance();
    currentCyclePage.onLoad({ target_type: 'plan_schedule', target_id: '81', cycle_key: 'event:811' });
    currentCyclePage.load();
    await flush();
    assert.equal(currentCyclePage.data.groups[0].items[0].detailOpen, true, 'same-cycle pending targets still expand');

    const delayedStatus = deferred();
    api.auditPending = () => Promise.resolve([currentWorkorder]);
    api.auditTargetStatus = () => delayedStatus.promise;
    const failedCyclePage = pageInstance();
    failedCyclePage.onLoad({ target_type: 'workorder_review', target_id: 'WO-82', cycle_key: 'review:1' });
    failedCyclePage.load();
    await flush();
    assert.equal(failedCyclePage.data.groups[0].items[0].detailOpen, false);
    delayedStatus.reject({ error: '当前账号无权查看该审批事项' });
    await flush();
    assert.equal(failedCyclePage.data.groups[0].items[0].detailOpen, false, 'failed cycle lookup never opens a target');

    const hiddenCycleStatus = deferred();
    api.auditTargetStatus = () => hiddenCycleStatus.promise;
    const hiddenCyclePage = pageInstance();
    hiddenCyclePage.onLoad({ target_type: 'workorder_review', target_id: 'WO-82', cycle_key: 'review:1' });
    hiddenCyclePage.load();
    await flush();
    hiddenCyclePage.onHide();
    hiddenCycleStatus.resolve({ state: 'pending' });
    await flush();
    assert.equal(hiddenCyclePage.data.groups[0].items[0].detailOpen, false, 'hidden cycle responses never open an old target');

    const inspectionProjection = {
      id: 'insp_batch_projection', source_type: 'inspection_batch', title: '巡检审核',
      item_details: [
        { id: 10, item_name: '设备状态', result: 'normal', remark: '运行稳定' },
        { id: 11, item_name: '设备状态', result: 'abnormal', remark: '需要复核' },
        { id: 12, item_name: '零照片检查项', remark: '' },
        { id: 13, item_name: '未知结果检查项', result: 'unrecognized', remark: '' }
      ],
      attachment_details: [
        { id: 101, item_id: 10, item_name: '设备状态', stored_path: '/uploads/101.jpg' },
        { id: 102, item_id: 11, item_name: '设备状态', stored_path: '/uploads/102.jpg' },
        { id: 103, item_id: 999, item_name: '遗失归属', stored_path: '/uploads/103.jpg' }
      ]
    };
    api.auditPending = () => Promise.resolve([inspectionProjection]);
    const inspectionProjectionPage = pageInstance();
    inspectionProjectionPage.load();
    await flush();
    const projectionItem = inspectionProjectionPage.data.groups[0].items[0];
    assert.deepEqual(projectionItem.reviewItemGroups.map(group => ({
      itemId: group.itemId, itemLabel: group.itemLabel, result: group.result, resultLabel: group.result_label, remark: group.remark,
      photoIds: group.photos.map(photo => photo.id)
    })), [
      { itemId: 10, itemLabel: '设备状态', result: 'normal', resultLabel: '正常', remark: '运行稳定', photoIds: [101] },
      { itemId: 11, itemLabel: '设备状态', result: 'abnormal', resultLabel: '异常', remark: '需要复核', photoIds: [102] },
      { itemId: 12, itemLabel: '零照片检查项', result: '', resultLabel: '未填写', remark: '', photoIds: [] },
      { itemId: 13, itemLabel: '未知结果检查项', result: 'unrecognized', resultLabel: '状态待确认', remark: '', photoIds: [] },
      { itemId: null, itemLabel: '关联检查项暂不可用', result: '', resultLabel: '未填写', remark: '', photoIds: [103] }
    ], 'all inspection items retain the raw result and expose a conservative presentation label');
    inspectionProjectionPage.onTogglePhotoReject({ currentTarget: { dataset: { id: 'insp_batch_projection', photoId: 101 } } });
    const toggledProjection = inspectionProjectionPage.data.groups[0].items[0];
    assert.equal(toggledProjection.reviewPhotos[0].selectedForReject, true, 'reviewPhotos remains the submission state source');
    assert.equal(toggledProjection.reviewPhotoGroups[0].photos[0].selectedForReject, true);
    assert.equal(toggledProjection.reviewItemGroups[0].photos[0].selectedForReject, true, 'item groups re-project the current reviewPhotos selection');

    const contentProjectionCases = [
      [{ id: 'va_1', source_type: 'vehicle_application', title: '用车', applicant_name: '车辆申请人' }, item => {
        assert.equal(item.requester_name, '车辆申请人');
      }],
      [{ id: 'wo_1', source_type: 'workorder_review', title: '工单', assignee: '工单执行人' }, item => {
        assert.equal(item.executor_name, '工单执行人');
      }],
      [{ id: 'wo_2', source_type: 'workorder_review', title: '工单', work_order_no: 'WO-2026-001', plan_name: '不应优先' }, item => {
        assert.equal(item.verifiedRelatedTaskLabel, '关联工单：WO-2026-001');
      }],
      [{ id: 'va_2', source_type: 'vehicle_application', title: '整改', plan_name: '整改计划', rework_plan_id: 99 }, item => {
        assert.equal(item.verifiedRelatedTaskLabel, '关联计划：整改计划');
      }],
      [{ id: 'ps_3', source_type: 'plan_schedule', title: '排程', plan_schedule_id: 46 }, item => {
        assert.equal(item.verifiedRelatedTaskLabel, '关联巡检计划：46');
      }],
      [{ id: 'va_4', source_type: 'vehicle_application', title: '无关联', rework_plan_id: 100 }, item => {
        assert.equal(item.verifiedRelatedTaskLabel, '', 'unverified IDs never fabricate a related task label');
      }]
    ];
    for (const [row, assertProjection] of contentProjectionCases) {
      api.auditPending = () => Promise.resolve([row]);
      const page = pageInstance();
      page.load();
      await flush();
      assertProjection(page.data.groups[0].items[0]);
    }

    const first = deferred();
    const second = deferred();
    let loadCalls = 0;
    api.auditPending = () => (++loadCalls === 1 ? first.promise : second.promise);
    const latestPage = pageInstance();
    latestPage.load();
    latestPage.load();
    second.resolve([]);
    await flush();
    first.resolve([reviewItem('parts_request', 'pr_99')]);
    await flush();
    assert.equal(latestPage.data.loadState, 'empty', 'late responses cannot replace the latest load');

    const hidden = deferred();
    api.auditPending = () => hidden.promise;
    const hiddenPage = pageInstance();
    hiddenPage.load();
    const groupsBeforeHide = hiddenPage.data.groups;
    hiddenPage.onHide();
    hidden.resolve([reviewItem('parts_request', 'pr_100')]);
    await flush();
    assert.equal(hiddenPage.data.groups, groupsBeforeHide, 'hidden pages do not receive late load results');

    const targetPage = pageInstance();
    targetPage.onLoad({ target_type: 'parts_request', target_id: '12' });
    api.auditPending = () => Promise.reject(new Error('offline'));
    targetPage.load();
    await flush();
    assert.equal(targetPage._reviewTargetHandled, undefined, 'failed target loads remain retryable');
    api.auditPending = () => Promise.resolve([reviewItem('parts_request', 'pr_12')]);
    targetPage.onRetry();
    await flush();
    assert.equal(targetPage._reviewTargetHandled, true);
    assert.equal(targetPage.data.groups[0].items[0].targeted, true);
    assert.equal(scrolls.length > 0, true);

    const hiddenSuccessRequest = deferred();
    let hiddenSuccessWrites = 0;
    api.reviewPhoto = () => { hiddenSuccessWrites += 1; return hiddenSuccessRequest.promise; };
    const hiddenSuccessPage = pageInstance();
    hiddenSuccessPage.data.loading = false;
    hiddenSuccessPage.data.loadState = 'data';
    hiddenSuccessPage.data.groups = [{ label: '影像', items: [Object.assign(reviewItem('photo_review', 'photo_hidden_ok'), {
      canReview: true, selectedRejectCount: 0, reviewPhotos: [], attachment_ids: [1]
    })] }];
    hiddenSuccessPage.onSubmitPhotoReview({ currentTarget: { dataset: { id: 'photo_hidden_ok' } } });
    hiddenSuccessPage.onHide();
    hiddenSuccessRequest.resolve({ success: true });
    await flush();
    assert.equal(hiddenSuccessWrites, 1);
    assert.equal(hiddenSuccessPage._submittingId, '', 'hidden successful submissions release the internal lock');
    assert.equal(hiddenSuccessPage.data.submittingId, 'photo_hidden_ok', 'hidden callbacks do not write the old view');
    api.auditPending = () => Promise.resolve([]);
    hiddenSuccessPage.onShow();
    await flush();
    assert.equal(hiddenSuccessPage.data.submittingId, '', 'returning to the page synchronizes the released lock');

    const hiddenFailureRequest = deferred();
    let hiddenFailureWrites = 0;
    api.reviewPhotoSelection = () => { hiddenFailureWrites += 1; return hiddenFailureRequest.promise; };
    const hiddenFailurePage = pageInstance();
    const failedItem = Object.assign(reviewItem('photo_review', 'photo_hidden_fail'), {
      canReview: true, reviewPhotos: [], attachment_ids: [1]
    });
    hiddenFailurePage.data.loading = false;
    hiddenFailurePage.data.loadState = 'data';
    hiddenFailurePage.data.groups = [{ label: '影像', items: [failedItem] }];
    hiddenFailurePage.data.curId = 'photo_hidden_fail';
    hiddenFailurePage.data.curType = 'photo_review';
    hiddenFailurePage.data.curAction = 'selective';
    hiddenFailurePage.data.rejectReason = '影像不完整';
    hiddenFailurePage.data.rejectShow = true;
    hiddenFailurePage._dispatch('selective', '影像不完整', 'photo_hidden_fail');
    hiddenFailurePage.onHide();
    hiddenFailureRequest.reject(new Error('offline'));
    await flush();
    assert.equal(hiddenFailureWrites, 1);
    assert.equal(hiddenFailurePage._submittingId, '', 'hidden failed submissions release the internal lock');
    assert.equal(hiddenFailurePage.data.submittingId, 'photo_hidden_fail', 'hidden failures do not mutate the old view');
    api.auditPending = () => Promise.resolve([reviewItem('photo_review', 'photo_hidden_fail')]);
    hiddenFailurePage.onShow();
    await flush();
    assert.equal(hiddenFailurePage.data.submittingId, '');
    assert.equal(hiddenFailurePage.data.rejectShow, true, 'returning after a rejected submission restores retry input');
    assert.equal(hiddenFailurePage.data.rejectReason, '影像不完整');
    api.reviewPhotoSelection = () => { hiddenFailureWrites += 1; return Promise.resolve({ success: true }); };
    hiddenFailurePage.rejectConfirm();
    await flush();
    assert.equal(hiddenFailureWrites, 2, 'the recovered reason can be retried once without retaining an old lock');

    const supportedCalls = {};
    Object.keys(original).forEach(key => {
      if (key !== 'auditPending' && key !== 'dataReviewDetail') {
        api[key] = () => { supportedCalls[key] = (supportedCalls[key] || 0) + 1; return Promise.resolve({}); };
      }
    });
    const supportedTypes = [
      ['inspection', 'reviewInspectionItem'],
      ['inspection_batch', 'reviewInspectionBatch'],
      ['workorder_status', 'approveWorkorder'],
      ['workorder_review', 'approveWorkorder'],
      ['workorder_photo', 'reviewPhoto'],
      ['photo_review', 'reviewPhoto'],
      ['parts_request', 'approvePartsRequest'],
      ['spare_part_request', 'approveSparePart'],
      ['vehicle_application', 'approveVehicle'],
      ['plan_schedule', 'approvePlanSchedule'],
      ['data_review', 'reviewDataReview']
    ];
    for (const [sourceType, apiMethod] of supportedTypes) {
      const item = Object.assign(reviewItem(sourceType), { canReview: true, order_no: 'WO-1', item_ids: [1], attachment_ids: [1] });
      const page = pageInstance();
      page.data.loading = false;
      page.data.loadState = 'data';
      page.data.groups = [{ label: '审核', items: [item] }];
      page._dispatch('approve', '', item.id);
      await flush();
      assert.equal(supportedCalls[apiMethod] > 0, true, sourceType + ' retains its supported dispatch branch');
    }

    writeCount = 0;
    Object.keys(original).forEach(key => {
      if (key !== 'auditPending' && key !== 'dataReviewDetail') api[key] = () => { writeCount += 1; return Promise.resolve({}); };
    });
    const unknown = Object.assign(reviewItem('future_review', 'future_1'), { canReview: false, reviewPhotos: [] });
    const unknownPage = pageInstance();
    unknownPage.data.loading = false;
    unknownPage.data.loadState = 'data';
    unknownPage.data.groups = [{ label: '未知', items: [unknown] }];
    unknownPage.onApprove({ currentTarget: { dataset: { id: 'future_1', type: 'future_review' } } });
    unknownPage.onReject({ currentTarget: { dataset: { id: 'future_1', type: 'future_review' } } });
    unknownPage.onSubmitPhotoReview({ currentTarget: { dataset: { id: 'future_1' } } });
    unknownPage._dispatch('approve', '', 'future_1');
    assert.equal(writeCount, 0, 'unknown review types never invoke a write API');
    assert.equal(toasts.at(-1).title, '该审核类型暂不支持处理，请刷新或联系管理员');
    const reviewWxml = fs.readFileSync(path.join(__dirname, '../pages/review/view.wxml'), 'utf8');
    assert.match(reviewWxml, /loadState === 'error'/, 'first load errors have their own rendered state');
    assert.match(reviewWxml, /loadState === 'stale'/, 'retained cards expose a stale-data warning');
    assert.match(reviewWxml, /it\.detailOpen && it\.canReview/, 'unknown types cannot render decision controls');

    console.log('reviewConnectionState tests passed');
  } finally {
    Object.assign(api, original);
    delete global.getApp;
    delete global.Page;
    delete global.wx;
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
