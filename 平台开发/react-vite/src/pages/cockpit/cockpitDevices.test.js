import test from 'node:test';
import assert from 'node:assert/strict';
import { buildMonitoringDeviceIndex, devicesForSite } from './cockpitDevices.js';

test('monitoring devices merge site id and code and deduplicate the same device', () => {
  const shared = { id: 1, site_id: '8', site_code: 'S-8', monitoring_enabled: 1,
    management_scope: 'managed', last_data_time: '2026-08-13 10:00:00' };
  const codeOnly = { id: 2, site_code: 'S-8', monitoring_enabled: 1,
    management_scope: 'managed', last_data_time: '2026-08-13 10:01:00' };
  const index = buildMonitoringDeviceIndex([shared, codeOnly]);
  assert.deepEqual(devicesForSite(index, { id: 8, code: 'S-8' }).map(item => item.id), [1, 2]);
});

test('monitoring device index excludes retired, disabled and never-reporting devices', () => {
  const base = { site_id: 8, site_code: 'S-8', monitoring_enabled: 1,
    management_scope: 'managed', last_data_time: '2026-08-13 10:00:00' };
  const index = buildMonitoringDeviceIndex([
    { ...base, id: 1 },
    { ...base, id: 2, management_scope: 'retired' },
    { ...base, id: 3, monitoring_enabled: 0 },
    { ...base, id: 4, last_data_time: null },
  ]);
  assert.deepEqual(devicesForSite(index, { id: 8, code: 'S-8' }).map(item => item.id), [1]);
});
