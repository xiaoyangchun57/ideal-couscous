import test from 'node:test';
import assert from 'node:assert/strict';
import { dataReviewMetric, dataReviewValue, formatReviewTime } from './dataReviewPresentation.js';

test('data review values include known units and unknown metrics remain explicit', () => {
  assert.equal(dataReviewValue({ metric: 'ammonia', value: 0.25 }), '0.25 mg/L');
  assert.deepEqual(dataReviewMetric('vendor_x'), { name: '未识别因子（vendor_x）', unit: '' });
});

test('data review timestamps display an explicit China timezone', () => {
  assert.match(formatReviewTime('2026-10-31T05:52:00Z'), /13:52:00/);
  assert.match(formatReviewTime('2026-10-31T05:52:00Z'), /北京时间/);
});
