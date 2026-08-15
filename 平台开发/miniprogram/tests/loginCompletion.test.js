const assert = require('node:assert/strict');
const test = require('node:test');
const { completeLoginSession } = require('../utils/loginCompletion.js');

function runLogin(refreshBadge) {
  const calls = [];
  completeLoginSession({
    setAuth: (token, user, sites) => calls.push(['auth', token, user.id, sites.length]),
    bindWechat: () => calls.push(['bind']),
    refreshBadge: () => {
      calls.push(['badge']);
      return refreshBadge();
    },
    navigateHome: () => calls.push(['home']),
  }, 'token', { id: 2 }, [{ id: 1 }]);
  return calls;
}

test('ordinary and forced-password login refresh badge after auth and before home', () => {
  const ordinary = runLogin(() => Promise.resolve());
  const forcedPassword = runLogin(() => Promise.resolve());
  [ordinary, forcedPassword].forEach(calls => {
    assert.deepEqual(calls, [
      ['auth', 'token', 2, 1], ['bind'], ['badge'], ['home'],
    ]);
  });
});

test('badge refresh failure never blocks home navigation', async () => {
  const calls = runLogin(() => Promise.reject(new Error('unread failed')));
  assert.equal(calls.filter(call => call[0] === 'badge').length, 1);
  assert.equal(calls.at(-1)[0], 'home');
  await new Promise(resolve => setImmediate(resolve));
});
