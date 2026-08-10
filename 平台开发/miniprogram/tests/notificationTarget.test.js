const assert = require('assert');
const { planScheduleDetailUrl } = require('../utils/notificationTarget.js');

assert.equal(planScheduleDetailUrl(42), '/pages/plan-detail/plan-detail?id=42');
assert.equal(planScheduleDetailUrl('42'), '/pages/plan-detail/plan-detail?id=42');
assert.equal(planScheduleDetailUrl(''), '/pages/plan/plan');

console.log('notificationTarget tests passed');
