const assert = require('assert');

const requestPath = require.resolve('../utils/request.js');
const apiPath = require.resolve('../services/api.js');
const requestModule = require(requestPath);
const originalRequest = requestModule.request;
const calls = [];
requestModule.request = (...args) => {
  calls.push(args);
  return Promise.resolve({ success: true });
};
delete require.cache[apiPath];
const api = require('../services/api.js');

api.rejectPartsRequest(12, '库存不足，请改为采购').then(() => {
  assert.deepEqual(calls[0], [
    '/api/inspection-v2/parts-request/12/reject',
    'PUT',
    { comment: '库存不足，请改为采购' }
  ]);
  console.log('partsReview tests passed');
}).finally(() => {
  requestModule.request = originalRequest;
});
