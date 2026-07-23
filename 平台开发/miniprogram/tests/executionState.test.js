const assert = require('assert');

const {
  selectExecutionSite,
  photoRequirement,
} = require('../utils/executionState.js');

const packages = [
  { plan_id: 11, sites: [{ site_id: 21, name: '甲站' }] },
  { plan_id: 12, sites: [{ site_id: 22, name: '乙站' }] },
];

const selected = selectExecutionSite(packages, 11, 22);
assert.equal(selected.currentPackage.plan_id, 12);
assert.equal(selected.site.site_id, 22);

assert.deepEqual(
  photoRequirement(2, 1, 0),
  { required: 2, captured: 1, missing: 1, ready: false },
);
assert.deepEqual(
  photoRequirement(2, 1, 1),
  { required: 2, captured: 2, missing: 0, ready: true },
);

console.log('executionState tests passed');
