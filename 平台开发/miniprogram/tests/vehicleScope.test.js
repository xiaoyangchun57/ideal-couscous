const assert = require('assert');
const { myVehicleQuery, isReturnedUse, activeUseFromRows } = require('../utils/vehicleScope.js');

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

console.log('vehicleScope tests passed');
