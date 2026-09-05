const assert = require('assert');
const {
  buildPackageOption,
  buildScheduleExecutionTarget,
  closedResultPackages,
  itemBelongsToCategories,
  resolveExecutionTarget,
  shouldApplyAsyncResult,
  uniqueExecutableTarget,
} = require('../utils/executionTarget.js');

const sourcePackages = [
  {
    plan_id: 101, schedule_id: 11, schedule_type: 'weekly', plan_name: '东线周检',
    work_date: '2026-08-28', site_order: [1, 2],
    sites: [
      { site_id: 2, name: '乙站', total: 4, completed: 1, abnormal: 1 },
      { site_id: 1, name: '甲站', total: 3, completed: 3, abnormal: 0, checked_out: true },
    ],
  },
  {
    plan_id: 102, schedule_id: 12, schedule_type: 'monthly', plan_name: '返场',
    work_date: '2026-08-28', site_order: [2],
    sites: [{ site_id: 2, name: '乙站返场', total: 1, completed: 0, abnormal: 0 }],
  },
];
const packages = sourcePackages.map(buildPackageOption);

assert.deepEqual(packages[0].sites.map(site => site.siteId), [1, 2], 'site_order is the only route order');
assert.deepEqual(
  { total: packages[0].total, completed: packages[0].completed, abnormal: packages[0].abnormal },
  { total: 7, completed: 4, abnormal: 1 },
  'today-execution total/completed/abnormal remain authoritative',
);
const invalidRoute = buildPackageOption(Object.assign({}, sourcePackages[0], { site_order: [1] }));
assert.equal(invalidRoute.routeAvailable, false);
assert.equal(invalidRoute.routeMessage, '路线信息暂不可用');
assert.match(invalidRoute.scheduleDateText, /路线信息暂不可用/);

[
  [{ executionPlanId: 101, siteId: 2 }, 'ready_to_enter', 101, 2, true],
  [{ executionPlanId: 101, siteId: 1 }, 'selecting', 101, 1, true],
  [{ executionPlanId: 999, siteId: 2 }, 'target_unavailable', null, null, false],
  [{ scheduleId: 11, workDate: '2026-08-28', siteId: 2 }, 'ready_to_enter', 101, 2, true],
  [{ siteId: 2 }, 'selecting', null, null, false],
].forEach(([target, state, packageId, siteId, canEnter]) => {
  const result = resolveExecutionTarget(packages, target);
  assert.deepEqual(
    [result.state, result.selectedPackageId, result.selectedSiteId, result.canEnter],
    [state, packageId, siteId, canEnter],
    JSON.stringify(target),
  );
});

const scheduleCandidates = resolveExecutionTarget(packages, { scheduleId: 11 });
assert.deepEqual(scheduleCandidates.packages.map(item => item.executionPlanId), [101],
  'a schedule target may render only its matching packages');
const siteCandidates = resolveExecutionTarget(packages, { siteId: 2 });
assert.deepEqual(siteCandidates.packages.map(item => [item.executionPlanId, item.sites.map(site => site.siteId)]), [
  [101, [2]], [102, [2]],
], 'a site target may render only the matching station in each candidate package');
const closedOnly = resolveExecutionTarget([
  buildPackageOption({ plan_id: 201, site_order: [21], sites: [{ site_id: 21, checked_out: true, total: 1, completed: 1, abnormal: 0 }] }),
  buildPackageOption({ plan_id: 202, site_order: [22], sites: [{ site_id: 22, checked_out: true, total: 1, completed: 1, abnormal: 0 }] }),
], {});
assert.equal(closedOnly.state, 'closed_only', 'multiple fully closed packages are not an ambiguous entry choice');

const mixedDefault = resolveExecutionTarget([
  buildPackageOption({
    plan_id: 210, schedule_id: 20, site_order: [21],
    sites: [{ site_id: 21, checked_out: true, total: 1, completed: 1, abnormal: 0 }],
  }),
  buildPackageOption({
    plan_id: 211, schedule_id: 21, work_date: '2026-08-28', site_order: [22],
    sites: [{ site_id: 22, total: 1, completed: 0, abnormal: 0 }],
  }),
], {});
assert.deepEqual(mixedDefault.packages.map(pkg => pkg.executionPlanId), [211],
  'default executable selection excludes fully closed packages');
assert.deepEqual(uniqueExecutableTarget(mixedDefault.packages, 'home'), {
  executionPlanId: 211, scheduleId: 21, workDate: '2026-08-28',
  siteId: 22, itemId: null, source: 'home',
});
assert.equal(uniqueExecutableTarget(packages, 'home'), null,
  'multiple executable packages or stations require explicit selection');

assert.equal(buildPackageOption({
  plan_id: 230, schedule_id: 77, package_label: '八月补测路线', plan_name: '不应优先显示',
  site_order: [], sites: [],
}).packageName, '八月补测路线');
assert.equal(buildPackageOption({
  plan_id: 231, schedule_id: 78, plan_name: '', site_order: [], sites: [],
}).packageName, '计划#78', 'package identity falls back to its schedule ID when no name exists');

const closedBrowsePackages = [
  buildPackageOption({
    plan_id: 301, schedule_id: 31, site_order: [31, 32],
    sites: [
      { site_id: 31, checked_out: true, total: 2, completed: 2, abnormal: 1 },
      { site_id: 32, total: 3, completed: 0, abnormal: 0 },
    ],
  }),
  buildPackageOption({
    plan_id: 302, schedule_id: 32, site_order: [33],
    sites: [{ site_id: 33, checked_out: true, total: 1, completed: 1, abnormal: 0 }],
  }),
];
assert.deepEqual(
  closedResultPackages(closedBrowsePackages, { executionPlanId: 301 }).map(pkg => ({
    planId: pkg.executionPlanId,
    siteIds: pkg.sites.map(site => site.siteId),
    total: pkg.total,
    completed: pkg.completed,
    abnormal: pkg.abnormal,
  })),
  [{ planId: 301, siteIds: [31], total: 2, completed: 2, abnormal: 1 }],
  'closed result browsing retains the exact target and excludes active sites',
);
assert.deepEqual(
  closedResultPackages(closedBrowsePackages, {}).map(pkg => [pkg.executionPlanId, pkg.sites.map(site => site.siteId)]),
  [[301, [31]], [302, [33]]],
  'an untargeted entry may browse all account-level closed sites without exposing active sites',
);

assert.deepEqual(buildScheduleExecutionTarget({
  generated_site_tasks: [{ plan_id: 101, execution_date: '2026-08-28', site_id: 2 }]
}, 11), {
  executionPlanId: 101, scheduleId: 11, workDate: '2026-08-28', siteId: 2, itemId: null, source: 'plan_detail'
});
assert.deepEqual(buildScheduleExecutionTarget({
  generated_site_tasks: [
    { plan_id: 101, execution_date: '2026-08-28', site_id: 1 },
    { plan_id: 102, execution_date: '2026-08-29', site_id: 2 },
  ]
}, 11), {
  executionPlanId: null, scheduleId: 11, workDate: null, siteId: null, itemId: null, source: 'plan_detail'
}, 'plan detail must not invent a target from multiple generated tasks');
assert.deepEqual(buildScheduleExecutionTarget({
  execution_status: 'rework',
  rework_execution_target: {
    schedule_id: 43, execution_plan_id: 136, work_date: '2026-08-21',
    site_id: 362, item_id: 7089,
  },
  generated_site_tasks: [
    { plan_id: 136, execution_date: '2026-08-21', site_id: 362 },
    { plan_id: 137, execution_date: '2026-08-22', site_id: 363 },
  ],
}, 43), {
  executionPlanId: 136, scheduleId: 43, workDate: '2026-08-21',
  siteId: 362, itemId: 7089, reworkOnly: true, source: 'plan_detail_rework'
}, 'the authoritative remediation target wins over ambiguous historical route rows');
assert.deepEqual(buildScheduleExecutionTarget({
  execution_status: 'pending',
  execution_target: {
    schedule_id: 46, execution_plan_id: 139, work_date: '2026-08-28', site_id: 362,
  },
  generated_site_tasks: [
    { plan_id: 139, execution_date: '2026-08-28', site_id: 362 },
    { plan_id: 140, execution_date: '2026-08-29', site_id: 363 },
  ],
}, 46), {
  executionPlanId: 139, scheduleId: 46, workDate: '2026-08-28',
  siteId: 362, itemId: null, source: 'plan_detail_execution'
}, 'the authoritative ordinary target wins over ambiguous route presentation rows');
assert.deepEqual(buildScheduleExecutionTarget({
  execution_status: 'partial',
  execution_target: { schedule_id: 46, source: 'plan_detail_execution' },
}, 46), {
  executionPlanId: null, scheduleId: 46, workDate: null,
  siteId: null, itemId: null, source: 'plan_detail_execution'
}, 'multiple executable candidates retain schedule scope without guessing one package or site');

const exactReworkInClosedPackage = resolveExecutionTarget([buildPackageOption({
  plan_id: 136, schedule_id: 43, work_date: '2026-08-21', site_order: [362],
  is_rework: true,
  sites: [{ site_id: 362, checked_out: true, total: 2, completed: 0, abnormal: 2, rework_items: 1 }],
})], {
  executionPlanId: 136, scheduleId: 43, workDate: '2026-08-21',
  siteId: 362, itemId: 7089, reworkOnly: true, source: 'plan_detail_rework',
});
assert.equal(exactReworkInClosedPackage.state, 'ready_to_enter');
assert.equal(exactReworkInClosedPackage.canEnter, true);
assert.equal(exactReworkInClosedPackage.entryMode, undefined,
  'a server-authorized remediation target must enter the editable remediation chain');

const homeReworkInClosedPackage = resolveExecutionTarget([buildPackageOption({
  plan_id: 136, schedule_id: 43, work_date: '2026-08-21', site_order: [362],
  is_rework: false,
  sites: [{ site_id: 362, checked_out: true, total: 2, completed: 0, abnormal: 2, rework_items: 1 }],
})], {
  executionPlanId: 136, scheduleId: 43, workDate: '2026-08-21',
  siteId: 362, itemId: 7089, reworkOnly: true, source: 'home_rework',
});
assert.deepEqual(
  [homeReworkInClosedPackage.state, homeReworkInClosedPackage.canEnter, homeReworkInClosedPackage.entryMode],
  ['ready_to_enter', true, undefined],
  'the same exact remediation target continues from home without trusting its source label',
);

const staleHomeRework = resolveExecutionTarget([buildPackageOption({
  plan_id: 136, schedule_id: 43, work_date: '2026-08-21', site_order: [362],
  is_rework: false,
  sites: [{ site_id: 362, checked_out: true, total: 2, completed: 2, abnormal: 0 }],
})], {
  executionPlanId: 136, scheduleId: 43, workDate: '2026-08-21',
  siteId: 362, itemId: 7089, reworkOnly: true, source: 'home_rework',
});
assert.deepEqual(
  [staleHomeRework.state, staleHomeRework.canEnter, staleHomeRework.targetMessage],
  ['target_unavailable', false, '整改任务状态已更新，请刷新首页后重试'],
  'a closed non-rework package must not turn a stale remediation target into historical browsing',
);

assert.equal(itemBelongsToCategories([{ items: [{ item_id: 81 }] }], 81), true);
assert.equal(itemBelongsToCategories([{ items: [{ item_id: 81 }] }], 82), false,
  'a stale target item may not open a different inspection item');

[
  [true, 2, 2, true, true],
  [true, 1, 2, true, false],
  [false, 2, 2, true, false],
  [true, 2, 2, false, false],
].forEach(([alive, responseId, currentId, sameContext, expected]) => {
  assert.equal(shouldApplyAsyncResult(alive, responseId, currentId, sameContext), expected);
});

console.log('executionTarget tests passed');
