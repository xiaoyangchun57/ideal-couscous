import assert from 'node:assert/strict';
import test from 'node:test';

import { applyPlanVehicleSelection, buildPlanValidationPayload } from './planVehicleState.js';

test('vehicle selection and no-vehicle reason remain mutually exclusive', () => {
  const draft = {
    vehicle_id: null,
    vehicle_days: { '2026-08-20': 8 },
    vehicle_exception_reason: '步行巡检',
  };

  const selected = applyPlanVehicleSelection(draft, 7);
  assert.equal(selected.vehicle_id, 7);
  assert.equal(selected.vehicle_exception_reason, '');
  assert.deepEqual(selected.vehicle_days, draft.vehicle_days);

  const cleared = applyPlanVehicleSelection(selected, undefined);
  assert.equal(cleared.vehicle_id, null);
  assert.equal(cleared.vehicle_exception_reason, '');
  assert.deepEqual(cleared.vehicle_days, {});
});

test('detail validation includes the persisted no-vehicle reason', () => {
  const payload = buildPlanValidationPayload({
    id: 41,
    user_id: 2,
    schedule_type: 'monthly',
    period_start: '2026-08-20',
    period_end: '2026-08-20',
    plan_data: { '2026-08-20': { sites: [1] } },
    vehicle_days: {},
    vehicle_exception_reason: '站点步行可达',
  });

  assert.equal(payload.vehicle_id, null);
  assert.equal(payload.vehicle_exception_reason, '站点步行可达');
  assert.equal(payload.exclude_schedule_id, 41);
});
