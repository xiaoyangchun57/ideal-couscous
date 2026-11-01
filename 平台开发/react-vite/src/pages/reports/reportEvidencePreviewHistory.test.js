import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  buildReportEvidenceState,
  buildReportEvidencePreviewState,
  isReportEvidenceState,
  isReportEvidencePreviewState,
  reportEvidenceHistoryLayer,
  reportEvidencePreviewHistoryAction,
} from './reportEvidencePreviewHistory.js';

const reportsSource = readFileSync(new URL('./ReportsPage.jsx', import.meta.url), 'utf8');

test('evidence and preview history preserve router state and form two local layers', () => {
  const routerState = { idx: 8, key: 'router-key', usr: { from: 'workorders' } };
  const evidenceState = buildReportEvidenceState(routerState, 'evidence-1');
  const previewState = buildReportEvidencePreviewState(evidenceState, 'evidence-1', 'preview-1');

  assert.deepEqual(previewState.usr, routerState.usr);
  assert.equal(previewState.idx, 8);
  assert.equal(isReportEvidenceState(evidenceState, 'evidence-1'), true);
  assert.equal(isReportEvidencePreviewState(previewState, 'preview-1'), true);
  assert.equal(reportEvidenceHistoryLayer(previewState, 'evidence-1', 'preview-1'), 'preview');
  assert.equal(reportEvidenceHistoryLayer(evidenceState, 'evidence-1', 'preview-1'), 'evidence');
  assert.equal(reportEvidenceHistoryLayer(routerState, 'evidence-1', 'preview-1'), 'list');
});

test('opening pushes once, repeated visibility does not duplicate, and close returns one level', () => {
  const token = 'preview-1';
  const baseState = { idx: 8 };
  const previewState = buildReportEvidencePreviewState(baseState, 'evidence-1', token);

  assert.equal(reportEvidencePreviewHistoryAction(true, baseState, token), 'push');
  assert.equal(reportEvidencePreviewHistoryAction(true, previewState, token), 'show');
  assert.equal(reportEvidencePreviewHistoryAction(false, previewState, token), 'back');
  assert.equal(reportEvidencePreviewHistoryAction(false, baseState, token), 'hide');
});

test('reports evidence preview is controlled by the local history hook only', () => {
  assert.match(reportsSource, /useReportEvidenceHistory\(\)/);
  assert.match(reportsSource, /onClick=\{\(\) => openEvidence\(record\)\}/);
  assert.match(reportsSource, /onCancel=\{closeEvidence\}/);
  assert.match(reportsSource, /<Image\.PreviewGroup preview=\{evidencePreview\}>/);
  assert.match(reportsSource, /if \(target\) openEvidence\(target\)/);
  assert.doesNotMatch(reportsSource, /setEvidenceRecord/);
  assert.doesNotMatch(reportsSource, /window\.addEventListener\('popstate'/);
});
