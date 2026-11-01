import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent' });
const {
  buildWorkorderTimelineFacts,
  executeNoActionClose,
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
