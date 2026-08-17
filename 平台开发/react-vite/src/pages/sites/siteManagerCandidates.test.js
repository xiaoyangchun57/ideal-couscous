import test from 'node:test';
import assert from 'node:assert/strict';
import { filterSiteManagerCandidates } from './siteManagerCandidates.js';

test('only active users with operator role can be selected as site managers', () => {
  const result = filterSiteManagerCandidates([
    { id: 1, status: 'active', role: 'operator', roles: [] },
    { id: 2, status: 'active', role: 'admin', roles: ['admin'] },
    { id: 3, status: 'active', role: 'reviewer', roles: ['reviewer', 'operator'] },
    { id: 4, status: 'inactive', role: 'operator', roles: ['operator'] },
  ]);
  assert.deepEqual(result.map((row) => row.id), [1, 3]);
});

test('legacy role fallback is used when roles is absent', () => {
  assert.deepEqual(
    filterSiteManagerCandidates([{ id: 7, status: 'active', role: 'operator' }]).map((row) => row.id),
    [7],
  );
});
