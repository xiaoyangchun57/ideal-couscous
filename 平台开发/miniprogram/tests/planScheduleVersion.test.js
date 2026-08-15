const assert = require('node:assert/strict');
const test = require('node:test');

const requestPath = require.resolve('../utils/request.js');
const apiPath = require.resolve('../services/api.js');
const originalRequestModule = require.cache[requestPath];

test.after(() => {
  delete require.cache[apiPath];
  if (originalRequestModule) require.cache[requestPath] = originalRequestModule;
  else delete require.cache[requestPath];
});

test('plan submit sends the current version to the server', async () => {
  const calls = [];
  require.cache[requestPath] = {
    id: requestPath,
    filename: requestPath,
    loaded: true,
    exports: {
      request(path, method, data) {
        calls.push({ path, method, data });
        return Promise.resolve({ success: true });
      },
    },
  };
  delete require.cache[apiPath];
  const api = require(apiPath);

  await api.submitPlanSchedule(42, 7);

  assert.deepEqual(calls, [{
    path: '/api/plan-schedules/42/submit',
    method: 'POST',
    data: { version: 7 },
  }]);
});
