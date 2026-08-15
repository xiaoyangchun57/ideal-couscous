const assert = require('assert');

const {
  selectExecutionSite,
  photoRequirement,
  inspectionPhotoProgress,
} = require('../utils/executionState.js');

const packages = [
  { plan_id: 11, sites: [{ site_id: 21, name: '甲站' }] },
  { plan_id: 12, sites: [{ site_id: 22, name: '乙站' }] },
];

const selected = selectExecutionSite(packages, 11, 22);
assert.equal(selected.currentPackage.plan_id, 12);
assert.equal(selected.site.site_id, 22);

const selectedFromHome = selectExecutionSite(packages, null, '22');
assert.equal(selectedFromHome.currentPackage.plan_id, 12);
assert.equal(selectedFromHome.site.site_id, 22);

const overlappingPackages = [
  { plan_id: 31, sites: [{ site_id: 41, name: '同站今日包' }] },
  { plan_id: 32, sites: [{ site_id: 41, name: '同站返场包' }, { site_id: 42, name: '另一站' }] },
];
const selectedReworkPackage = selectExecutionSite(overlappingPackages, 32, 41);
assert.equal(selectedReworkPackage.currentPackage.plan_id, 32);
assert.equal(selectedReworkPackage.site.site_id, 41);

const stalePlanFallsBackToSite = selectExecutionSite(overlappingPackages, 999, 42);
assert.equal(stalePlanFallsBackToSite.currentPackage.plan_id, 32);
assert.equal(stalePlanFallsBackToSite.site.site_id, 42);

assert.deepEqual(
  photoRequirement(2, 1, 0),
  { required: 2, captured: 1, missing: 1, ready: false },
);
assert.deepEqual(
  photoRequirement(2, 1, 1),
  { required: 2, captured: 2, missing: 0, ready: true },
);

assert.deepEqual(
  inspectionPhotoProgress([{ items: [
    { required_photos: 1, review_status: 2, evidence_status: 'effective', effective_evidence_count: 0, photo_urls: '["historical-approved.jpg"]' },
    { required_photos: 1, review_status: 3, evidence_status: 'supplement_required', effective_evidence_count: 2, photo_urls: '["rejected-a.jpg","rejected-b.jpg"]' },
  ] }]),
  { req: 2, taken: 1, missing: 1 },
  'rejected history must not satisfy the current photo requirement',
);

assert.deepEqual(
  inspectionPhotoProgress([{ items: [{ required_photos: 1, photo_urls: '["legacy.jpg"]' }] }]),
  { req: 1, taken: 1, missing: 0 },
  'legacy payloads without evidence counts remain readable',
);

console.log('executionState tests passed');
