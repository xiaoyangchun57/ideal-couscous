const assert = require('node:assert/strict');
const test = require('node:test');
const { canReview, loadReviewTodoCount } = require('../utils/reviewAccess.js');

test('review access uses all roles and falls back to the legacy single role', () => {
  assert.equal(canReview({ role: 'operator', roles: ['operator', 'reviewer'] }), true);
  assert.equal(canReview({ role: 'operator', roles: ['operator', 'admin'] }), true);
  assert.equal(canReview({ role: 'reviewer' }), true);
  assert.equal(canReview({ role: 'admin', roles: [] }), true);
  assert.equal(canReview({ role: 'operator' }), false);
});

test('operators never request the reviewer endpoint', async () => {
  let calls = 0;
  const count = await loadReviewTodoCount({ role: 'operator' }, () => {
    calls += 1;
    return Promise.resolve([1, 2]);
  });
  assert.equal(count, 0);
  assert.equal(calls, 0);
});

test('reviewers request and count the pending review rows', async () => {
  let calls = 0;
  const count = await loadReviewTodoCount({ roles: ['operator', 'reviewer'] }, () => {
    calls += 1;
    return Promise.resolve([1, 2]);
  });
  assert.equal(count, 2);
  assert.equal(calls, 1);
});
