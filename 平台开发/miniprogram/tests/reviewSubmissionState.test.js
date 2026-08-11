const assert = require('assert');
const {
  getSubmissionGuard,
  getSubmissionErrorMessage,
  getRetryErrorMessage
} = require('../utils/reviewSubmissionState.js');

assert.deepEqual(
  getSubmissionGuard('', 'ps_1'),
  { allowed: true, sameItem: false, otherItem: false, message: '' }
);
assert.equal(getSubmissionGuard('ps_1', 'ps_1').allowed, false);
assert.equal(getSubmissionGuard('ps_1', 'ps_1').sameItem, true);
assert.deepEqual(
  getSubmissionGuard('ps_1', 'ps_1', { allowSameItem: true }),
  { allowed: true, sameItem: true, otherItem: false, message: '' }
);
assert.equal(getSubmissionGuard('ps_1', 'ps_2').otherItem, true);
assert.equal(
  getSubmissionGuard('ps_1', 'ps_2').message,
  '已有其他审核事项正在提交，请等待完成后再操作'
);
assert.equal(getSubmissionGuard('', '').allowed, false);

const error = { errMsg: 'request:fail timeout' };
assert.equal(getSubmissionErrorMessage(error), 'timeout');
assert.equal(getRetryErrorMessage({ error: '服务暂不可用' }), '服务暂不可用，请重试');
assert.equal(getRetryErrorMessage(null), '操作失败，请重试');

console.log('reviewSubmissionState tests passed');
