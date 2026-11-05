import test from 'node:test';
import assert from 'node:assert/strict';
import { vehicleSummary } from './vehicleSummary.js';

test('vehicle summary keeps in-use vehicles separate from restrictions and maintenance', () => {
  assert.deepEqual(vehicleSummary([
    { status: 'in_use', current_mileage: 5200, next_maintenance_mileage: 5000 },
    { status: 'maintenance' },
    { status: 'restricted' },
    { status: 'idle', current_mileage: 9900, next_maintenance_mileage: 10000 },
  ]), {
    inUse: 1,
    restrictedOrMaintenance: 2,
    maintenanceReminder: 1,
  });
});
