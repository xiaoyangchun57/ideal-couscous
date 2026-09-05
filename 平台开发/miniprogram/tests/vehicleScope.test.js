const assert = require('assert');
const {
  myVehicleQuery,
  isReturnedUse,
  activeUseFromRows,
  effectiveVehicleCanReturn,
} = require('../utils/vehicleScope.js');

assert.deepEqual(
  myVehicleQuery('current', { id: 8, roles: ['admin', 'operator'] }, { limit: 100 }),
  { scope: 'current', applicant_id: 8, limit: 100 },
);
assert.equal(myVehicleQuery('history', null, { page: 2 }).applicant_id, 0);
assert.equal(isReturnedUse({ returned_at: '2026-08-10 18:00:00', status: 'checked_out' }), true);
assert.equal(isReturnedUse({ returned_at: null, status: 'returned' }), true);
assert.equal(isReturnedUse({ returned_at: null, status: 'checked_out' }), false);
assert.equal(activeUseFromRows([{ id: 1, status: 'returned' }, { id: 2, status: 'checked_out' }]).id, 2);
assert.equal(activeUseFromRows([{ id: 1, status: 'returned' }]), null);
assert.equal(effectiveVehicleCanReturn({
  reason: '巡检计划#66用车',
  end_at: '2099-08-18 18:00:00',
  plan_vehicle_replaced: true,
  can_return: true,
}, '2026-08-17'), true);
assert.equal(effectiveVehicleCanReturn({
  reason: '巡检计划#66用车',
  end_at: '2099-08-18 18:00:00',
  can_return: false,
}, '2026-08-17'), false);
assert.equal(effectiveVehicleCanReturn({
  reason: '巡检计划#66用车',
  end_at: '2099-08-18 18:00:00',
}, '2026-08-17'), false);
assert.equal(effectiveVehicleCanReturn({
  reason: '巡检计划#66用车',
  end_at: '2026-08-16 18:00:00',
}, '2026-08-17'), false);

console.log('vehicleScope tests passed');
