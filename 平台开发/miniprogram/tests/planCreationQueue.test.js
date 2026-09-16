const assert = require('node:assert/strict');
const test = require('node:test');

test('plan creation automatic retry, offline replay and manual retry preserve identical intent', async t => {
  const configPath = require.resolve('../utils/config.js');
  const originalConfig = require.cache[configPath];
  const originalTimer = global.setTimeout;
  const storage = { token: 'isolated-token', user: { id: 2 } };
  const calls = [];
  let offline = true;
  require.cache[configPath] = { id: configPath, filename: configPath, loaded: true,
    exports: { API_PROFILE: 'online', BASE_URL: 'https://isolated.invalid' } };
  global.wx = {
    getStorageSync: key => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value; },
    request: options => {
      calls.push(JSON.parse(JSON.stringify(options.data)));
      assert.equal(options.url, 'https://isolated.invalid/api/plan-schedules');
      if (offline) options.fail({ errMsg: 'simulated response loss' });
      else options.success({ statusCode: 200, data: { id: 71, status: 'submitted' } });
    }
  };
  global.setTimeout = callback => { callback(); return 1; };
  t.after(() => {
    global.setTimeout = originalTimer;
    if (originalConfig) require.cache[configPath] = originalConfig;
    else delete require.cache[configPath];
    delete global.wx;
  });
  const api = require('../services/api.js');
  const { getQueue, flushQueue } = require('../utils/request.js');
  const payload = { _idempotency_key: 'stable-plan-intent', schedule_type: 'weekly', submit: true };
  await assert.rejects(api.createPlanSchedule(payload), error => error.queued === true);
  assert.equal(calls.length, 3, 'all automatic attempts retain the request body');
  assert.equal(getQueue().length, 1);
  assert.deepEqual(getQueue()[0].data, payload);
  offline = false;
  const replay = await flushQueue();
  assert.equal(replay.synced, 1);
  assert.equal(getQueue().length, 0);
  const recovered = await api.createPlanSchedule(payload);
  assert.deepEqual(recovered, { id: 71, status: 'submitted' });
  assert.equal(calls.length, 5);
  calls.forEach(body => assert.deepEqual(body, payload));
});
