const assert = require('assert');

const storage = { user: { id: 2 } };
global.getApp = () => null;
global.wx = {
  getStorageSync: key => storage[key],
  setStorageSync: (key, value) => { storage[key] = value; },
  removeStorageSync: key => { delete storage[key]; },
  getSystemInfoSync: () => ({ platform: 'devtools' }),
};

const localStore = require('../utils/localStore.js');
const requestQueue = require('../utils/request.js');

const checkinId = localStore.addOp('checkin', { site_id: 1 });
assert.ok(checkinId);
assert.equal(
  localStore.addOp('checkin', { site_id: '1' }),
  checkinId,
  'number/string site IDs must reuse the same pending check-in'
);
const plannedCheckinId = localStore.addOp('checkin', { site_id: 1, plan_id: 20 });
assert.notEqual(plannedCheckinId, checkinId, 'a plan check-in must not reuse a generic site check-in');
assert.equal(
  localStore.addOp('checkin', { site_id: '1', plan_id: '20' }),
  plannedCheckinId,
  'number/string plan IDs must reuse the same pending plan check-in'
);
assert.notEqual(
  localStore.addOp('checkin', { site_id: 1, plan_id: 21 }),
  plannedCheckinId,
  'different plans at one site must retain separate pending check-ins'
);

storage.user = { id: 3 };
assert.equal(localStore.getPending().length, 0, 'account B must not read account A local operations');
const otherId = localStore.addOp('checkin', { site_id: 1 });
assert.notEqual(otherId, checkinId);
assert.equal(localStore.getPending().length, 1);

storage.user = { id: 2 };
assert.equal(localStore.getPending().length, 3);
localStore.markRejected(checkinId, 'server rejected');
assert.equal(localStore.getPending().length, 2);
assert.equal(localStore.getSiteCheckIn(1).id !== checkinId, true,
  'rejected generic check-ins cannot hide pending plan check-ins');

localStore.write(localStore.readAll().concat({
  id: 'legacy-ownerless', type: 'checkin', data: { site_id: 1 }, syncStatus: 'pending', createdAt: 1,
}));
assert.equal(localStore.getPending().length, 2, 'ownerless legacy records must never replay under a logged-in account');
storage.user = { id: 3 };
assert.equal(localStore.getPending().length, 1, 'account B still retains only its own pending operation');

requestQueue.saveQueue([{ url: '/api/mobile/check-in', method: 'POST', data: { site_id: 1 } }]);
assert.equal(requestQueue.getQueue().length, 1);
storage.user = { id: 2 };
assert.equal(requestQueue.getQueue().length, 0, 'request queue must be hidden from another account');
storage.user = { id: 3 };
assert.equal(requestQueue.getQueue().length, 1);

const allRequests = requestQueue.getAllQueue();
storage.fail_queue = allRequests.concat({
  url: '/legacy', method: 'POST', data: {}, ts: 1,
});
assert.equal(requestQueue.getQueue().length, 1, 'ownerless legacy requests must not replay');

console.log('offline queue ownership tests passed');
