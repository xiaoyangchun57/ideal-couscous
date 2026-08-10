import assert from 'node:assert/strict';
import test from 'node:test';
import { replaceReworkWithSchedule, resolveReworkScheduleId } from './planScheduleNavigation.js';

test('rework notification resolves an inspection package to its schedule', async () => {
  const calls = [];
  const scheduleId = await resolveReworkScheduleId('18', async (planId) => {
    calls.push(planId);
    return { id: planId, plan_schedule_id: 38 };
  });
  assert.deepEqual(calls, [18]);
  assert.equal(scheduleId, 38);
});

test('invalid or unlinked rework packages do not invent a schedule', async () => {
  let called = false;
  assert.equal(await resolveReworkScheduleId('bad', async () => { called = true; }), null);
  assert.equal(called, false);
  assert.equal(await resolveReworkScheduleId(18, async () => ({ plan_schedule_id: null })), null);
});

test('resolved navigation replaces the execution-package query with schedule detail', () => {
  const next = replaceReworkWithSchedule('rework_plan=18&attention=resource', 38);
  assert.equal(next.get('rework_plan'), null);
  assert.equal(next.get('schedule'), '38');
  assert.equal(next.get('attention'), 'resource');
});
