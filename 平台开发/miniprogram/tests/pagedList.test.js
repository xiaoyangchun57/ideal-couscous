const assert = require('assert');
const { normalizePagedList, appendPagedItems, hasMoreFromResponse, appendDistinctById } = require('../utils/pagedList.js');

assert.deepEqual(
  normalizePagedList([{ id: 1 }]),
  { items: [{ id: 1 }], total: 1, hasMore: false, page: 1, limit: 1 },
);

assert.equal(hasMoreFromResponse({ has_more: true }, 2, 50), true);
assert.equal(hasMoreFromResponse({ has_more: false }, 50, 50), false);
assert.equal(hasMoreFromResponse({}, 50, 50), true);
assert.equal(hasMoreFromResponse({}, 49, 50), false);
assert.deepEqual(
  appendDistinctById([{ id: 3 }, { id: 2 }], [{ id: 2 }, { id: 1 }]),
  [{ id: 3 }, { id: 2 }, { id: 1 }],
);

const first = normalizePagedList({ items: [{ id: 3 }], total: 3, has_more: true, page: 1, limit: 1 });
assert.deepEqual(first, { items: [{ id: 3 }], total: 3, hasMore: true, page: 1, limit: 1 });
assert.deepEqual(
  appendPagedItems(first.items, { items: [{ id: 2 }], total: 3, has_more: true, page: 2, limit: 1 }),
  { items: [{ id: 3 }, { id: 2 }], total: 3, hasMore: true, page: 2, limit: 1 },
);

console.log('pagedList tests passed');
