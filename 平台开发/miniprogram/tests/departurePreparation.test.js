const test = require('node:test');
const assert = require('node:assert/strict');
const {
  buildDeparturePreparation,
  buildMode,
  buildParts,
} = require('../utils/departurePreparation.js');

const basePkg = {
  plan_id: 1,
  work_date: '2026-08-28',
  sites: [{ site_id: 1, name: '青云' }],
  departure_confirmation: null,
  resource_parts: [],
  is_rework: false,
  is_carryover: false,
  arrival_gate: { allowed: true, code: null, message: null },
};

function pkg(overrides) {
  return Object.assign({}, basePkg, overrides || {});
}

test('mode prioritizes rework over carryover', () => {
  assert.equal(buildMode({}), 'normal');
  assert.equal(buildMode({ is_carryover: true }), 'carryover');
  assert.equal(buildMode({ is_rework: true, is_carryover: true }), 'rework');
});

test('normal and carryover packages consume the authoritative vehicle arrival gate', () => {
  const normal = buildDeparturePreparation(pkg(), null);
  assert.equal(normal.canContinueToArrival, true);
  assert.equal(normal.vehicle.statusLabel, '未安排车辆');

  const noVehicle = buildDeparturePreparation(pkg({
    vehicle_exception_reason: '步行即可完成全部站点',
  }), null);
  assert.equal(noVehicle.vehicle.statusLabel, '计划无需用车');
  assert.equal(noVehicle.vehicle.exceptionReason, '步行即可完成全部站点');

  const carryover = buildDeparturePreparation(pkg({
    is_carryover: true,
    vehicle: { id: 10, plate_no: '赣A00001' },
    vehicle_application_id: 100,
    vehicle_needs_extension: true,
    arrival_gate: {
      allowed: false,
      code: 'VEHICLE_EXTENSION_REQUIRED',
      message: '连续行程的用车安排已超期，请先延续用车',
    },
  }), null);
  assert.equal(carryover.canContinueToArrival, false);
  assert.equal(carryover.vehicle.primaryAction, 'extend');
  assert.equal(carryover.vehicle.statusLabel, '用车安排已超期');
});

test('expired vehicle documents remain visible and do not offer checkout', () => {
  const vm = buildDeparturePreparation(pkg({
    vehicle: {
      id: 10, plate_no: '赣A00010',
      document_state: { expired: ['insurance'], due_soon: [] },
    },
    vehicle_application_id: 100,
  }), null);
  assert.equal(vm.vehicle.documentState, 'expired');
  assert.equal(vm.vehicle.statusTone, 'danger');
  assert.equal(vm.vehicle.primaryAction, 'none');
  assert.equal(vm.canContinueToArrival, true, 'the server gate remains authoritative');
});

test('planned vehicle gate distinguishes checkout, active trip, returned and unavailable facts', () => {
  const planned = {
    vehicle: { id: 10, plate_no: '赣A00010' },
    vehicle_application_id: 100,
  };
  const blocked = buildDeparturePreparation(pkg(Object.assign({}, planned, {
    arrival_gate: {
      allowed: false, code: 'VEHICLE_CHECKOUT_REQUIRED',
      message: '计划车辆尚未完成出车登记，出车后方可到站',
    },
  })), null);
  assert.equal(blocked.arrivalGate, 'blocked');
  assert.equal(blocked.canContinueToArrival, false);
  assert.equal(blocked.vehicle.primaryAction, 'checkout');
  assert.equal(blocked.todoItems.some(item => item.action === 'checkout'), false);

  const active = buildDeparturePreparation(pkg(Object.assign({}, planned, {
    vehicle_use: { id: 300, status: 'checked_out', returned_at: null },
    arrival_gate: { allowed: true },
  })), null);
  assert.equal(active.arrivalGate, 'allowed');
  assert.equal(active.canContinueToArrival, true);

  const returned = buildDeparturePreparation(pkg(Object.assign({}, planned, {
    vehicle_use: { id: 300, status: 'returned', returned_at: '2026-08-28 17:00:00' },
    arrival_gate: {
      allowed: false, code: 'VEHICLE_CHECKOUT_REQUIRED', message: '车辆已还车',
    },
  })), null);
  assert.equal(returned.canContinueToArrival, false);

  const unknown = buildDeparturePreparation(pkg(Object.assign({}, planned, {
    arrival_gate: null,
  })), null);
  assert.equal(unknown.arrivalGate, 'unknown');
  assert.equal(unknown.canContinueToArrival, false);
});

test('checkout stays on the vehicle card while parts and acknowledgement todos remain', () => {
  const vm = buildDeparturePreparation(pkg({
    vehicle: { id: 10, plate_no: '赣A00010' },
    vehicle_application_id: 100,
    resource_parts: [
      { part_id: 1, part_name: '滤芯', planned_quantity: 2, issued_quantity: 0, remaining_quantity: 2 },
    ],
    arrival_gate: {
      allowed: false, code: 'VEHICLE_CHECKOUT_REQUIRED',
      message: '计划车辆尚未完成出车登记，出车后方可到站',
    },
  }), null);

  assert.equal(vm.vehicle.primaryAction, 'checkout');
  assert.equal(vm.todoItems.some(item => item.action === 'checkout'), false);
  assert.equal(vm.todoItems.some(item => item.action === 'issue'), true);
  assert.equal(vm.todoItems.some(item => item.action === 'ack'), true);
});

const reworkCases = [
  {
    name: 'arrangement required', state: 'arrangement_required', allowed: false,
    gate: 'blocked', label: '待安排资源', action: 'request_resource',
  },
  {
    name: 'pending approval', state: 'pending_approval', allowed: false,
    gate: 'blocked', label: '资源待审批', action: 'none', application_status: 'pending',
  },
  {
    name: 'rejected', state: 'rejected', allowed: false,
    gate: 'blocked', label: '申请被退回', action: 'request_resource', application_status: 'rejected',
  },
  {
    name: 'approved vehicle awaits checkout', state: 'vehicle_checkout_required', allowed: false,
    gate: 'blocked', label: '车辆待出车', action: 'checkout', application_status: 'approved',
    vehicle: { id: 20, plate_no: '赣A00020' }, vehicle_application_id: 200,
  },
  {
    name: 'current rework vehicle checked out', state: 'vehicle_checked_out', allowed: true,
    gate: 'allowed', label: '车辆已出车', action: 'none', application_status: 'approved',
    vehicle: { id: 20, plate_no: '赣A00020' }, vehicle_application_id: 200,
    vehicle_use: { id: 300, status: 'checked_out', checked_out_at: '2026-08-28 08:00:00' },
  },
  {
    name: 'approved no-vehicle exception', state: 'no_vehicle_approved', allowed: true,
    gate: 'allowed', label: '无车例外已批准', action: 'none', application_status: 'approved',
    vehicle_exception_reason: '站点步行可达',
  },
  {
    name: 'unknown facts', state: 'unknown', allowed: false,
    gate: 'unknown', label: '资源状态暂不可用', action: 'retry',
  },
];

for (const scenario of reworkCases) {
  test(`rework matrix: ${scenario.name}`, () => {
    const reworkResource = {
      state: scenario.state,
      arrival_allowed: scenario.allowed,
      application_status: scenario.application_status || null,
      vehicle_exception_reason: scenario.vehicle_exception_reason || '',
    };
    const vm = buildDeparturePreparation(pkg({
      is_rework: true,
      arrival_gate: scenario.state === 'unknown' ? null : {
        allowed: scenario.allowed,
        code: scenario.allowed ? null : `REWORK_${scenario.state.toUpperCase()}`,
        message: scenario.allowed ? null : undefined,
      },
      resource_state: 'ready',
      rework_resource: reworkResource,
      vehicle: scenario.vehicle || null,
      vehicle_application_id: scenario.vehicle_application_id || null,
      vehicle_use: scenario.vehicle_use || null,
      vehicle_exception_reason: scenario.vehicle_exception_reason || '',
    }), null);
    assert.equal(vm.arrivalGate, scenario.gate);
    assert.equal(vm.canContinueToArrival, scenario.allowed);
    assert.equal(vm.vehicle.statusLabel, scenario.label);
    assert.equal(vm.vehicle.primaryAction, scenario.action);
  });
}

test('resource_state=ready alone never proves rework arrival readiness', () => {
  const vm = buildDeparturePreparation(pkg({
    is_rework: true, resource_state: 'ready', arrival_gate: null,
  }), null);
  assert.equal(vm.arrivalGate, 'unknown');
  assert.equal(vm.canContinueToArrival, false);
  assert.notEqual(vm.vehicle.statusLabel, '资源已就绪');
});

test('rework rejection keeps the server reason and next action in the gate message', () => {
  const vm = buildDeparturePreparation(pkg({
    is_rework: true,
    arrival_gate: {
      allowed: false, code: 'REWORK_RESOURCE_APPROVAL_REQUIRED',
      message: '整改补检资源申请已退回：车辆冲突，请重新安排',
    },
    rework_resource: {
      state: 'rejected', arrival_allowed: false,
      gate_message: '整改补检资源申请已退回：车辆冲突，请重新安排',
    },
  }), null);
  assert.equal(vm.gateMessage, '整改补检资源申请已退回：车辆冲突，请重新安排');
  assert.equal(vm.vehicle.primaryAction, 'request_resource');
});

test('returned rework vehicle does not become checked out or offer an impossible repeat checkout', () => {
  const vm = buildDeparturePreparation(pkg({
    is_rework: true,
    arrival_gate: {
      allowed: false, code: 'REWORK_VEHICLE_CHECKOUT_REQUIRED',
      message: '整改补检车辆已归还，请重新安排',
    },
    rework_resource: {
      state: 'vehicle_checkout_required', arrival_allowed: false, application_status: 'approved',
    },
    vehicle: { id: 20, plate_no: '赣A00020' },
    vehicle_application_id: 200,
    vehicle_use: { id: 300, status: 'returned', returned_at: '2026-08-28 17:00:00' },
  }), null);
  assert.equal(vm.vehicle.useState, 'returned');
  assert.equal(vm.vehicle.primaryAction, 'none');
  assert.equal(vm.canContinueToArrival, false);
});

test('parts retain unavailable quantities instead of fabricating zero', () => {
  const parts = buildParts(pkg({
    resource_parts: [
      { part_id: 1, part_name: '滤芯', planned_quantity: null, issued_quantity: '', remaining_quantity: 'bad' },
    ],
  }));
  assert.deepEqual(
    [parts[0].plannedQuantity, parts[0].issuedQuantity, parts[0].remainingQuantity],
    [null, null, null],
  );
  const vm = buildDeparturePreparation(pkg({ resource_parts: [{ part_id: 1, part_name: '滤芯' }] }), null);
  assert.equal(vm.partsSummary.planned, null);
  assert.equal(vm.partsSummary.issued, null);
  assert.equal(vm.partsSummary.remaining, null);
  assert.equal(vm.partsSummary.remainingKinds, null);
  assert.equal(vm.partsSummary.quantitiesAvailable, false);
  assert.equal(vm.partsSummary.canIssue, false);
  assert.equal(vm.partsSummary.progressFillStyle, '');
  assert.equal(vm.partsAcknowledgement.status, 'unavailable');
});

test('known parts support partial issue without becoming an arrival hard gate', () => {
  const vm = buildDeparturePreparation(pkg({
    resource_parts: [
      { part_id: 1, part_name: '滤芯', unit: '个', planned_quantity: 2, issued_quantity: 1, remaining_quantity: 1 },
      { part_id: 2, part_name: '软管', unit: '米', planned_quantity: 3, issued_quantity: 3, remaining_quantity: 0 },
    ],
  }), null);
  assert.deepEqual(
    [vm.partsSummary.planned, vm.partsSummary.issued, vm.partsSummary.remaining],
    [5, 4, 1],
  );
  assert.equal(vm.partsSummary.remainingKinds, 1);
  assert.equal(vm.partsSummary.canIssue, true);
  assert.equal(vm.partsSummary.progressFillStyle, 'width:80%');
  assert.equal(vm.canContinueToArrival, true);
});

test('vehicle and parts acknowledgements remain independent facts', () => {
  const vehicleOnly = buildDeparturePreparation(pkg({
    departure_confirmation: {
      vehicle_confirmed: 1, parts_confirmed: 0, confirmed_at: '2026-08-28 08:00:00',
    },
  }), null);
  assert.equal(vehicleOnly.vehicleAcknowledgement.status, 'confirmed');
  assert.equal(vehicleOnly.partsAcknowledgement.status, 'unconfirmed');

  const partsOnly = buildDeparturePreparation(pkg({
    departure_confirmation: {
      vehicle_confirmed: 0, parts_confirmed: 1, confirmed_at: '2026-08-28 08:10:00',
    },
  }), null);
  assert.equal(partsOnly.vehicleAcknowledgement.status, 'unconfirmed');
  assert.equal(partsOnly.partsAcknowledgement.status, 'confirmed');
});

test('preparation completion requires an exact target and every authoritative resource fact', () => {
  const target = { executionPlanId: 1, siteId: 1, workDate: '2026-08-28' };
  const ready = buildDeparturePreparation(pkg({
    vehicle: { id: 10, plate_no: '赣A00010' },
    vehicle_application_id: 100,
    vehicle_use: { id: 300, status: 'checked_out', returned_at: null },
    departure_confirmation: {
      vehicle_confirmed: 1, parts_confirmed: 1, confirmed_at: '2026-08-28 08:10:00',
    },
    resource_parts: [
      { part_id: 1, part_name: '滤芯', planned_quantity: 2, issued_quantity: 2, remaining_quantity: 0 },
    ],
    arrival_gate: { allowed: true },
  }), target);
  assert.equal(ready.canContinueToArrival, true);
  assert.equal(ready.preparationCompleted, true);

  const cases = [
    ['vehicle checkout pending', {
      vehicle: { id: 10 }, vehicle_application_id: 100,
      departure_confirmation: { vehicle_confirmed: 1, parts_confirmed: 1 },
      arrival_gate: { allowed: false, code: 'VEHICLE_CHECKOUT_REQUIRED' },
    }, target],
    ['parts still need issue', {
      vehicle: { id: 10 }, vehicle_application_id: 100,
      vehicle_use: { id: 300, status: 'checked_out' },
      departure_confirmation: { vehicle_confirmed: 1, parts_confirmed: 1 },
      resource_parts: [
        { part_id: 1, planned_quantity: 2, issued_quantity: 1, remaining_quantity: 1 },
      ],
    }, target],
    ['parts quantities unavailable', {
      vehicle_exception_reason: '站点步行可达',
      resource_parts: [{ part_id: 1, planned_quantity: null }],
    }, target],
    ['arrival gate unavailable', {
      vehicle_exception_reason: '站点步行可达', arrival_gate: null,
    }, target],
    ['target is not exact', {
      vehicle_exception_reason: '站点步行可达',
    }, { executionPlanId: 1, siteId: 99 }],
    ['vehicle acknowledgement missing', {
      vehicle: { id: 10 }, vehicle_application_id: 100,
      vehicle_use: { id: 300, status: 'checked_out' },
      departure_confirmation: { vehicle_confirmed: 0, parts_confirmed: 0 },
    }, target],
  ];
  cases.forEach(([name, overrides, caseTarget]) => {
    assert.equal(buildDeparturePreparation(pkg(overrides), caseTarget).preparationCompleted,
      false, name);
  });

  const explicitNoVehicle = buildDeparturePreparation(pkg({
    vehicle_exception_reason: '站点步行可达', arrival_gate: { allowed: true },
  }), target);
  assert.equal(explicitNoVehicle.preparationCompleted, true);
});
