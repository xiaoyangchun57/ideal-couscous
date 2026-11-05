import test from 'node:test';
import assert from 'node:assert/strict';
import { analysisMetricPresentation } from './analysisPresentation.js';

test('analysis metric presentation uses known Chinese labels and units', () => {
  assert.deepEqual(analysisMetricPresentation('ammonia'), { name: '氨氮', unit: 'mg/L' });
  assert.deepEqual(analysisMetricPresentation('codmn'), { name: '高锰酸盐指数', unit: 'mg/L' });
});

test('unknown analysis metrics keep the source field without inventing a unit', () => {
  assert.deepEqual(analysisMetricPresentation('vendor_x'), { name: '未识别因子（vendor_x）', unit: '' });
});
