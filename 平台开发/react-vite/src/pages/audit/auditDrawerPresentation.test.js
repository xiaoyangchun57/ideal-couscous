import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const auditSource = readFileSync(new URL('./AuditPage.jsx', import.meta.url), 'utf8');
const dataReviewSource = readFileSync(
  new URL('../alerts/components/DataReviewTab.jsx', import.meta.url), 'utf8');

test('shared audit drawers stay within the mobile viewport', () => {
  assert.match(auditSource, /width="min\(100vw, 680px\)"/);
  assert.match(dataReviewSource, /width="min\(100vw, 520px\)"/);
  assert.match(dataReviewSource, /width="min\(100vw, 640px\)"/);
  assert.match(dataReviewSource, /width="min\(100vw, 760px\)"/);
});
