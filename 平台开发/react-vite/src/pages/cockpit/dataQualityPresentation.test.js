import test from 'node:test';
import assert from 'node:assert/strict';
import { completenessPresentation, qualityRate } from './dataQualityPresentation.js';

test('zero denominators stay neutral even when an API supplies zero rates', () => {
  const empty = { expected: 0, actual: 0, sampled_metric_count: 0, completeness_rate: 0, validity_rate: 0, timeliness_rate: 0 };
  assert.equal(qualityRate(empty, 'completeness_rate'), null);
  assert.equal(qualityRate(empty, 'validity_rate'), null);
  assert.equal(qualityRate(empty, 'timeliness_rate'), null);
  assert.deepEqual(completenessPresentation(empty), { rate: null, hasSample: false, label: '无样本' });
});

test('rates remain visible when their server denominators are positive', () => {
  const sampled = { expected: 10, actual: 8, sampled_metric_count: 7, completeness_rate: 80, validity_rate: 95, timeliness_rate: 90 };
  assert.equal(qualityRate(sampled, 'completeness_rate'), 80);
  assert.equal(qualityRate(sampled, 'validity_rate'), 95);
  assert.equal(qualityRate(sampled, 'timeliness_rate'), 90);
  assert.equal(qualityRate({ actual: 8, timeliness_rate: 90 }, 'timeliness_rate'), 90);
});

test('missing rate values stay neutral even when their denominator is positive', () => {
  for (const missingRate of [null, undefined, '', '   ']) {
    assert.equal(qualityRate({ actual: 8, timeliness_rate: missingRate }, 'timeliness_rate'), null);
  }
  assert.equal(qualityRate({ actual: 8, timeliness_rate: 0 }, 'timeliness_rate'), 0);
  assert.equal(qualityRate({ actual: 8, timeliness_rate: '0' }, 'timeliness_rate'), 0);
});
