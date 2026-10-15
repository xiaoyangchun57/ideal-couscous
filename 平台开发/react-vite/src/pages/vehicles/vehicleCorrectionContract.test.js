import assert from 'node:assert/strict';
import test from 'node:test';
import { formatVehicleCorrectionLog, runVehicleEditSubmission } from './vehicleCorrectionContract.js';

test('vehicle correction details use business labels instead of raw JSON', () => {
  const formatted = formatVehicleCorrectionLog({
    id: 9,
    operator: '管理员甲',
    created_at: '2026-10-15 09:30:00',
    details: JSON.stringify({
      reason: '核对仪表盘后修正',
      changes: {
        current_mileage: { before: 57002, after: 57012 },
        status: { before: 'in_use', after: 'restricted' },
      },
    }),
  });
  assert.equal(formatted.reason, '核对仪表盘后修正');
  assert.deepEqual(formatted.changes, [
    { field: 'current_mileage', label: '当前里程', before: '57,002 km', after: '57,012 km' },
    { field: 'status', label: '车辆状态', before: '出车中', after: '限制使用' },
  ]);
  assert.equal(JSON.stringify(formatted).includes('"changes"'), true);
  assert.equal(formatVehicleCorrectionLog({ details: '{broken' }).changes.length, 0);
});

test('vehicle edit submission ignores double trigger and reloads only after success', async () => {
  const guard = { current: false };
  let releaseSave;
  const calls = [];
  const first = runVehicleEditSubmission({
    guard,
    vehicleId: 7,
    validate: async () => ({ model: '新车型' }),
    buildPayload: values => ({ ...values, seats: 5 }),
    save: async (vehicleId, payload) => {
      calls.push(['save', vehicleId, payload]);
      await new Promise(resolve => { releaseSave = resolve; });
    },
    reload: async () => { calls.push(['reload']); },
  });
  await Promise.resolve();
  const duplicate = await runVehicleEditSubmission({
    guard,
    vehicleId: 8,
    validate: async () => { throw new Error('duplicate must not validate'); },
    buildPayload: values => values,
    save: async () => {},
    reload: async () => {},
  });
  assert.equal(duplicate.status, 'ignored');
  assert.deepEqual(calls, [['save', 7, { model: '新车型', seats: 5 }]]);
  releaseSave();
  assert.equal((await first).status, 'saved');
  assert.deepEqual(calls, [
    ['save', 7, { model: '新车型', seats: 5 }],
    ['reload'],
  ]);
});

test('vehicle edit failure keeps the guard retryable and does not reload', async () => {
  const guard = { current: false };
  let reloads = 0;
  const failure = await runVehicleEditSubmission({
    guard,
    vehicleId: 3,
    validate: async () => ({ correction_reason: '保留输入' }),
    buildPayload: values => values,
    save: async () => { throw new Error('网络失败'); },
    reload: async () => { reloads += 1; },
  });
  assert.equal(failure.status, 'failed');
  assert.equal(failure.error.message, '网络失败');
  assert.equal(reloads, 0);
  assert.equal(guard.current, false);
});
