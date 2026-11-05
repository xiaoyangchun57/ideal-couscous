import test from 'node:test';
import assert from 'node:assert/strict';
import { runIndependentLoads } from './independentLoads.js';

test('primary content becomes available before a slow auxiliary request settles', async () => {
  let releaseAuxiliary;
  const auxiliary = new Promise((resolve) => { releaseAuxiliary = resolve; });
  const events = [];
  const pending = runIndependentLoads([
    { key: 'primary', load: async () => ['row'], onSuccess: () => events.push('primary') },
    { key: 'auxiliary', load: () => auxiliary, onSuccess: () => events.push('auxiliary') },
  ]);

  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(events, ['primary']);
  releaseAuxiliary(['history']);
  await pending;
  assert.deepEqual(events, ['primary', 'auxiliary']);
});

test('an auxiliary failure does not suppress successful primary content', async () => {
  const events = [];
  const results = await runIndependentLoads([
    { key: 'primary', load: async () => ['row'], onSuccess: () => events.push('primary') },
    { key: 'auxiliary', load: async () => { throw new Error('slow service failed'); }, onError: () => events.push('auxiliary-error') },
  ]);

  assert.deepEqual(events, ['primary', 'auxiliary-error']);
  assert.deepEqual(results.map((item) => item.status), ['fulfilled', 'rejected']);
});
