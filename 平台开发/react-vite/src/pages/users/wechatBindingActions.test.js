import assert from 'node:assert/strict';
import { finishWechatBindingUnbind } from './wechatBindingActions.js';

const events = [];
const current = await finishWechatBindingUnbind({
  isCurrentUser: true,
  logout: async () => { events.push('logout'); },
  redirectToLogin: () => { events.push('redirect'); },
  refreshUsers: async () => { events.push('refresh'); },
});
assert.equal(current, 'logged_out');
assert.deepEqual(events, ['logout', 'redirect']);

events.length = 0;
const other = await finishWechatBindingUnbind({
  isCurrentUser: false,
  logout: async () => { events.push('logout'); },
  redirectToLogin: () => { events.push('redirect'); },
  refreshUsers: async () => { events.push('refresh'); },
});
assert.equal(other, 'refreshed');
assert.deepEqual(events, ['refresh']);

console.log('UsersPage wechat binding action tests passed');
