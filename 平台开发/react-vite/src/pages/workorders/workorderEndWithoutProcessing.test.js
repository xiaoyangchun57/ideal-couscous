import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent' });
const {
  buildWorkorderTimelineFacts,
  createWorkorderDetailRequestGuard,
  executeNoActionClose,
  loadWorkorderDetailBundle,
  shouldShowEndWithoutProcessingBlock,
} = await vite.ssrLoadModule('/src/pages/workorders/WorkOrdersPage.jsx');

test.after(async () => {
  await vite.close();
});

test('pending no-action close renders only persisted events and its reason', () => {
  const facts = buildWorkorderTimelineFacts({
    status: 'closed',
    closure_type: 'no_action_required',
    closure_reason: '误建工单',
    flow_events: [
      { type: 'created', label: '创建工单', time: '10:00', remark: '工单创建' },
      { type: 'ended_without_processing', label: '无需处理，提前结束', time: '10:05', remark: '误建工单' },
    ],
  });
  assert.deepEqual(facts.map((item) => item.type), ['created', 'ended_without_processing']);
  assert.equal(facts[1].detail, '原因：误建工单');
  assert.equal(facts.some((item) => ['accepted', 'in_progress', 'approved'].includes(item.type)), false);
});

test('no-action close preserves real prior handling history without adding review approval', () => {
  const facts = buildWorkorderTimelineFacts({
    status: 'closed',
    closure_type: 'no_action_required',
    flow_events: [
      { type: 'created', label: '创建工单' },
      { type: 'accepted', label: '接单' },
      { type: 'in_progress', label: '开始处置' },
      { type: 'ended_without_processing', label: '无需处理，提前结束', remark: '现场确认无需维修' },
    ],
  });
  assert.deepEqual(facts.map((item) => item.type), [
    'created', 'accepted', 'in_progress', 'ended_without_processing',
  ]);
  assert.equal(facts.some((item) => item.type === 'approved'), false);
});

test('normal completion keeps the server-provided review timeline', () => {
  const events = [
    { type: 'created', label: '创建工单' },
    { type: 'accepted', label: '接单' },
    { type: 'in_progress', label: '开始处置' },
    { type: 'submit_review', label: '提交核验' },
    { type: 'approved', label: '审核通过' },
  ];
  const facts = buildWorkorderTimelineFacts({ status: 'closed', flow_events: events });
  assert.deepEqual(facts.map((item) => item.type), events.map((item) => item.type));
});

test('missing flow events shows a limited status instead of invented handling steps', () => {
  const facts = buildWorkorderTimelineFacts({ status: 'closed', resolved_at: '11:00' });
  assert.deepEqual(facts.map((item) => item.type), ['created', 'status']);
  assert.match(facts[1].detail, /未推断受理、处置或审核节点/);
});

test('successful write stays committed when detail refresh fails', async () => {
  const calls = [];
  const result = await executeNoActionClose({
    order: { order_no: 'WO-1', status: 'pending', can_end_without_processing: true },
    reason: '误建工单',
    submit: async () => {
      calls.push('submit');
      return { closure_reason: '误建工单', closure_time: '10:05' };
    },
    onCommitted: (order) => calls.push(`committed:${order.status}`),
    loadDetail: async () => {
      calls.push('detail');
      throw new Error('detail unavailable');
    },
    refreshOrders: async () => calls.push('list'),
  });
  assert.equal(result.committedOrder.status, 'closed');
  assert.equal(result.committedOrder.can_end_without_processing, false);
  assert.deepEqual(result.refreshFailures, ['详情']);
  assert.deepEqual(calls, ['submit', 'committed:closed', 'detail', 'list']);
});

test('failed write does not commit or start refreshes', async () => {
  const calls = [];
  await assert.rejects(executeNoActionClose({
    order: { order_no: 'WO-1', status: 'pending' },
    reason: '保留的输入',
    submit: async () => {
      calls.push('submit');
      throw new Error('write failed');
    },
    onCommitted: () => calls.push('committed'),
    loadDetail: async () => calls.push('detail'),
    refreshOrders: async () => calls.push('list'),
  }), /write failed/);
  assert.deepEqual(calls, ['submit']);
});

test('detail bundle reads the exact order instead of searching the recent list', async () => {
  const calls = [];
  const result = await loadWorkorderDetailBundle({
    orderNo: 'WO-OLD-1',
    fallbackOrder: { order_no: 'WO-OLD-1' },
    loadDetail: async (orderNo) => {
      calls.push(`detail:${orderNo}`);
      return { order_no: orderNo, status: 'pending', display_title: '旧工单' };
    },
    loadRelated: async (orderNo) => {
      calls.push(`related:${orderNo}`);
      return { parts: [], recycles: [] };
    },
    loadPhotos: async (orderNo) => {
      calls.push(`photos:${orderNo}`);
      return { item_progress: [] };
    },
  });
  assert.equal(result.detail.display_title, '旧工单');
  assert.deepEqual(result.errors, []);
  const auxiliary = await result.auxiliaryPromise;
  assert.deepEqual(auxiliary.errors, []);
  assert.deepEqual(auxiliary.related, { parts: [], recycles: [] });
  assert.deepEqual(calls.sort(), [
    'detail:WO-OLD-1', 'photos:WO-OLD-1', 'related:WO-OLD-1',
  ]);
});

test('detail bundle exposes an exact-detail 404 so the UI does not render a fake fallback order', async () => {
  const notFound = Object.assign(new Error('工单不存在'), {
    status: 404,
    code: 'WORKORDER_NOT_FOUND',
  });
  const result = await loadWorkorderDetailBundle({
    orderNo: 'WO-NOT-EXIST',
    fallbackOrder: { order_no: 'WO-NOT-EXIST' },
    loadDetail: async () => { throw notFound; },
    loadRelated: async () => { throw notFound; },
    loadPhotos: async () => { throw notFound; },
  });
  assert.equal(result.detail.order_no, 'WO-NOT-EXIST');
  assert.equal(result.detailError, notFound);
  assert.equal(result.detailError.status, 404);
  assert.deepEqual(result.errors, ['最新工单信息']);
  const auxiliary = await result.auxiliaryPromise;
  assert.deepEqual(auxiliary.errors, ['关联业务', '处置影像']);
});

test('primary detail resolves before delayed related data and photos', async () => {
  let releaseRelated;
  let releasePhotos;
  const result = await loadWorkorderDetailBundle({
    orderNo: 'WO-FAST-DETAIL',
    fallbackOrder: { order_no: 'WO-FAST-DETAIL' },
    loadDetail: async () => ({ order_no: 'WO-FAST-DETAIL', title: '主详情已到' }),
    loadRelated: () => new Promise((resolve) => { releaseRelated = resolve; }),
    loadPhotos: () => new Promise((resolve) => { releasePhotos = resolve; }),
  });
  assert.equal(result.detail.title, '主详情已到');

  releaseRelated({ parts: [{ id: 1 }], recycles: [] });
  releasePhotos({ item_progress: [] });
  const auxiliary = await result.auxiliaryPromise;
  assert.equal(auxiliary.related.parts[0].id, 1);
  assert.deepEqual(auxiliary.errors, []);
});

test('request guard rejects stale detail responses after a quick order switch', () => {
  const guard = createWorkorderDetailRequestGuard();
  const first = guard.begin();
  const second = guard.begin();
  assert.equal(guard.isCurrent(first), false);
  assert.equal(guard.isCurrent(second), true);
  guard.invalidate();
  assert.equal(guard.isCurrent(second), false);
});

test('closed early-end orders hide the redundant denial alert', () => {
  assert.equal(shouldShowEndWithoutProcessingBlock({
    status: 'closed',
    closure_type: 'no_action_required',
    can_end_without_processing: false,
    end_without_processing_block_reason: '工单已经办结，不能改为提前结束',
  }), false);
  assert.equal(shouldShowEndWithoutProcessingBlock({
    status: 'in_progress',
    can_end_without_processing: false,
    end_without_processing_block_reason: '当前阶段只能由负责人提前结束',
  }), true);
});
