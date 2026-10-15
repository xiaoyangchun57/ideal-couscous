import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('./WorkOrdersPage.jsx', import.meta.url), 'utf8');

test('admin workorder creation submits a site-scoped assignee identity', () => {
  assert.match(source, /const isAdmin = userRoles\.includes\('admin'\)/);
  assert.match(source, /api\.get\('\/assignees'\)/);
  assert.match(source, /item\.site_ids/);
  assert.match(source, /name="assignee_id" label="负责人"/);
  assert.match(source, /form\.setFieldValue\('assignee_id', undefined\)/);
  assert.doesNotMatch(source, /name="assignee" label="负责人"/);
});

test('assignee loading distinguishes failure from an authorized empty list and supports retry', () => {
  assert.match(source, /const \[assigneesLoading, setAssigneesLoading\] = useState\(false\)/);
  assert.match(source, /const \[assigneesError, setAssigneesError\] = useState\(''\)/);
  assert.match(source, /const loadAssignees = useCallback\(async \(\) =>/);
  assert.match(source, /setAssigneesError\(error\?\.message \|\| '负责人列表加载失败'\)/);
  assert.match(source, /onClick=\{loadAssignees\}>重试<\/Button>/);
  assert.match(source, /该站点暂无可用负责人/);
  assert.doesNotMatch(source, /catch\(\(\) => setAssignees\(\[\]\)\)/);
});
