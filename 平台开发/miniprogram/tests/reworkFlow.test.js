const assert = require('assert');
const { buildCheckinPayload, reworkResourcePresentation } = require('../utils/reworkFlow.js');

assert.deepEqual(
  buildCheckinPayload({ id: 7, name: 'Station' }, 99, { lat: 28.68, lng: 115.73 }, '2026-08-10 09:00:00'),
  { site_id: 7, site_name: 'Station', plan_id: 99, check_time: '2026-08-10 09:00:00', lat: 28.68, lng: 115.73 },
);
assert.deepEqual(
  reworkResourcePresentation({ is_rework: true, resource_state: 'pending_approval' }),
  { isRework: true, state: 'pending_approval', label: '资源待审批', canRequest: false },
);
assert.equal(reworkResourcePresentation({ is_rework: true, resource_state: 'arrangement_required' }).canRequest, true);
assert.equal(reworkResourcePresentation({ is_rework: true, resource_state: 'ready' }).canRequest, false);

console.log('reworkFlow tests passed');
