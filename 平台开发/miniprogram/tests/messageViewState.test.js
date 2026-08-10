const assert = require('assert');
const {
  getMessageViewState,
  shouldShowNoMore,
  shouldShowLoadMore,
  getMessageLoadError
} = require('../utils/messageViewState.js');

assert.equal(getMessageViewState({ list: [], loaded: false, loading: true }), 'loading');
assert.equal(getMessageViewState({ list: [], loaded: true, loading: false }), 'empty');
assert.equal(getMessageViewState({ list: [], loaded: true, loading: false, error: 'timeout' }), 'error');
assert.equal(getMessageViewState({ list: [{ id: 1 }], loaded: true, loading: false }), 'data');
assert.equal(
  getMessageViewState({ list: [{ id: 1 }], loaded: true, loading: false, error: 'timeout' }),
  'data',
  'Existing data remains the data state when a refresh fails.'
);

assert.equal(shouldShowNoMore([], true), false, 'An empty list must not show the end marker.');
assert.equal(shouldShowNoMore([{ id: 1 }], true), true);
assert.equal(shouldShowLoadMore([{ id: 1 }], false, false, ''), true);
assert.equal(shouldShowLoadMore([{ id: 1 }], false, false, 'timeout'), false);
assert.equal(getMessageLoadError({ errMsg: 'request:fail timeout' }), 'timeout');

console.log('messageViewState tests passed');
