const assert = require('node:assert/strict');
const test = require('node:test');
const { homeSummary, homeSite, homeSiteSelection } = require('../utils/homeTaskState.js');

test('homepage todo count keeps pending, rework and alerts on separate axes', () => {
  assert.deepEqual(homeSummary({
    total_sites: 2,
    pending_items: 3,
    rework_items: 2,
    pending_workorders: 1,
    pending_alerts: 4,
    abnormal_items: 1,
  }), { sites: 2, todo: 5, workorders: 1, alerts: 5 });
  assert.deepEqual(homeSummary({}), { sites: 0, todo: 0, workorders: 0, alerts: 0 });
});

test('homepage site text covers pending, rework and combined work', () => {
  const reworkSite = homeSite({
    site_id: 9,
    pending_items: 2,
    rework_items: 1,
    target_plan_id: 21,
    target_item_id: 34,
  });
  assert.equal(reworkSite.todo_text,
    '待检 2 · 需补拍 1');
  assert.deepEqual(homeSiteSelection(reworkSite), { siteId: 9, planId: 21, itemId: 34 });
  assert.equal(homeSite({ pending_items: 0, rework_items: 3 }).todo_text,
    '3 项需补拍');
  assert.equal(homeSite({ pending_items: 4, rework_items: 0 }).todo_text,
    '4 项待检');
  assert.deepEqual(homeSiteSelection({ site_id: 8 }), { siteId: 8, planId: null, itemId: null });
});
