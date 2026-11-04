import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent' });
const { buildWorkorderListRequest } = await vite.ssrLoadModule('/src/pages/workorders/WorkOrdersPage.jsx');

test.after(async () => {
  await vite.close();
});

test('workorder list request keeps server pagination and encoded filters', () => {
  const request = buildWorkorderListRequest(
    '?page=3&page_size=50&search=WO-%E5%90%8E%E9%A1%B5&status=closed&level=critical&assignee=%E5%BC%A0%E4%B8%89&order=WO-1',
  );
  const url = new URL(request.path, 'http://local.test');
  assert.equal(request.page, 3);
  assert.equal(request.pageSize, 50);
  assert.equal(url.pathname, '/workorders');
  assert.equal(url.searchParams.get('paginated'), '1');
  assert.equal(url.searchParams.get('search'), 'WO-后页');
  assert.equal(url.searchParams.get('status'), 'closed');
  assert.equal(url.searchParams.get('level'), 'critical');
  assert.equal(url.searchParams.get('assignee'), '张三');
  assert.equal(url.searchParams.has('order'), false);
});

test('workorder list request uses safe page defaults and caps page size', () => {
  const request = buildWorkorderListRequest('?page=0&page_size=1000');
  assert.equal(request.page, 1);
  assert.equal(request.pageSize, 100);
});
