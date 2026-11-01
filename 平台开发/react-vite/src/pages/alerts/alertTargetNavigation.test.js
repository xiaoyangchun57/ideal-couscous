import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeAlertTargetId, pinTargetAlert } from './alertTargetNavigation.js';

test('alert target accepts only positive numeric ids used by the exact detail endpoint', () => {
  assert.equal(normalizeAlertTargetId(' 42 '), '42');
  assert.equal(normalizeAlertTargetId('alarm-42'), '');
  assert.equal(normalizeAlertTargetId('0'), '');
  assert.equal(normalizeAlertTargetId(''), '');
});

test('exact alert target is pinned first without duplicating the list record', () => {
  const target = { id: 42, message: 'exact target' };
  const alerts = [{ id: 7 }, { id: 42, message: 'bounded list copy' }, { id: 9 }];

  assert.deepEqual(pinTargetAlert(alerts, target), [target, alerts[0], alerts[2]]);
  assert.equal(pinTargetAlert(alerts, null), alerts);
});
