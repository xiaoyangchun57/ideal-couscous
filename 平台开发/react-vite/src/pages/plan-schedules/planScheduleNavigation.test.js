import assert from 'node:assert/strict';
import test from 'node:test';
import {
  mergePlanEditSites, planEditSitesForDate, replaceReworkWithSchedule, resolveReworkScheduleId,
} from './planScheduleNavigation.js';

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

test('plan editing keeps an ended historical site only on its approved dates', () => {
  const schedule = {
    plan_data: {
      '2026-10-13': { sites: [1] },
      '2026-10-14': { sites: [2] },
    },
    previous_plan_data: {
      '2026-10-13': { sites: [1, 9] },
      '2026-10-14': { sites: [2] },
    },
    site_map: { 9: { id: 9, name: '已结束站点' } },
  };
  const sites = mergePlanEditSites([
    { id: 1, name: '当前站点一' },
    { id: 2, name: '当前站点二' },
  ], schedule);

  const archived = sites.find(site => site.id === 9);
  assert.equal(archived.name, '已结束站点');
  assert.equal(archived.is_historical_ended, true);
  assert.deepEqual(archived.historical_dates, ['2026-10-13']);
  assert.deepEqual(
    planEditSitesForDate(sites, '2026-10-13').map(site => site.id),
    [1, 2, 9],
  );
  assert.deepEqual(
    planEditSitesForDate(sites, '2026-10-14').map(site => site.id),
    [1, 2],
  );
});

test('manager editing still respects the execution user site scope', () => {
  const sites = mergePlanEditSites(
    [{ id: 1, name: '当前站点' }, { id: 2, name: '越权站点' }],
    {
      previous_plan_data: { '2026-10-13': { sites: [1, 9] } },
      site_map: { 9: { id: 9, name: '历史站点' } },
    },
    new Set([1]),
  );
  assert.deepEqual(sites.map(site => site.id), [1]);
});
