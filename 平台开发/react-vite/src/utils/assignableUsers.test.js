import test from 'node:test';
import assert from 'node:assert/strict';
import { filterAssignableUsers, isAssignableUser, roleListOf } from './assignableUsers.js';

test('已注销账号不进入分配候选', () => {
  const rows = [
    { id: 1, status: 'active', role: 'operator', deleted_at: null },
    { id: 2, status: 'inactive', role: 'operator', deleted_at: '2026-09-22 10:45:41' },
  ];
  assert.deepEqual(filterAssignableUsers(rows).map((row) => row.id), [1]);
});

test('已停用账号（deleted_at 为空）同样不进入分配候选', () => {
  assert.deepEqual(
    filterAssignableUsers([
      { id: 3, status: 'inactive', role: 'operator', deleted_at: null },
    ]),
    [],
  );
});

test('异常态：status 仍为 active 但 deleted_at 非空时也不得入选', () => {
  const abnormal = { id: 4, status: 'active', role: 'operator', deleted_at: '2026-09-22 10:45:41' };
  assert.equal(isAssignableUser(abnormal), false);
  assert.deepEqual(filterAssignableUsers([abnormal]), []);
});

test('按角色筛选时以 roles 数组优先，缺失时回退到 role 字段', () => {
  const rows = [
    { id: 1, status: 'active', role: 'operator', roles: [] },
    { id: 2, status: 'active', role: 'admin', roles: ['admin'] },
    { id: 3, status: 'active', role: 'reviewer', roles: ['reviewer', 'operator'] },
    { id: 4, status: 'inactive', role: 'operator', roles: ['operator'] },
  ];
  assert.deepEqual(filterAssignableUsers(rows, { role: 'operator' }).map((r) => r.id), [1, 3]);
  assert.deepEqual(roleListOf({ role: 'operator' }), ['operator']);
  assert.deepEqual(roleListOf({ roles: ['a', null, 'b'] }), ['a', 'b']);
});

test('非数组或空值输入安全返回空数组', () => {
  assert.deepEqual(filterAssignableUsers(null), []);
  assert.deepEqual(filterAssignableUsers(undefined), []);
  assert.deepEqual(filterAssignableUsers('nope'), []);
  assert.deepEqual(filterAssignableUsers([null, undefined]), []);
});
