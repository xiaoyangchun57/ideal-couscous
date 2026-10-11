import test from 'node:test';
import assert from 'node:assert/strict';
import { createUrlSearchDraftController } from './useUrlSyncedSearch.js';

function fakeClock() {
  let nextId = 1;
  const tasks = new Map();
  return {
    schedule(callback) {
      const id = nextId++;
      tasks.set(id, callback);
      return id;
    },
    cancel(id) { tasks.delete(id); },
    flush() {
      const pending = [...tasks.values()];
      tasks.clear();
      pending.forEach((callback) => callback());
    },
    get size() { return tasks.size; },
  };
}

function setup(initial = '') {
  const drafts = [initial];
  const commits = [];
  const clock = fakeClock();
  const controller = createUrlSearchDraftController({
    onDraft: (value) => drafts.push(value),
    onCommit: (value) => commits.push(value),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  return { controller, drafts, commits, clock };
}

test('composition keeps draft live but commits only the final selected text', () => {
  const state = setup();
  state.controller.compositionStart();
  state.controller.change('n');
  state.controller.change('nanchang');
  state.clock.flush();
  assert.deepEqual(state.commits, []);
  assert.equal(state.drafts.at(-1), 'nanchang');
  state.controller.compositionEnd('南昌');
  state.controller.change('南昌');
  state.clock.flush();
  assert.deepEqual(state.commits, ['南昌']);
  assert.equal(state.drafts.at(-1), '南昌');
});

test('ordinary typing updates immediately and cancels stale URL persistence', () => {
  const state = setup();
  state.controller.change('A');
  assert.equal(state.drafts.at(-1), 'A');
  assert.deepEqual(state.commits, []);
  state.controller.change('AB');
  assert.equal(state.clock.size, 1);
  state.clock.flush();
  assert.deepEqual(state.commits, ['AB']);
  state.controller.change('南昌青云水厂');
  state.clock.flush();
  assert.deepEqual(state.commits, ['AB', '南昌青云水厂']);
});

test('external URL sync and clear cancel pending work', () => {
  const state = setup('旧值');
  state.controller.change('待提交');
  state.controller.syncExternal('跨模块带入');
  state.clock.flush();
  assert.deepEqual(state.commits, []);
  assert.equal(state.drafts.at(-1), '跨模块带入');
  state.controller.change('');
  assert.deepEqual(state.commits, ['']);
  state.controller.change('卸载前');
  state.controller.dispose();
  state.clock.flush();
  assert.deepEqual(state.commits, ['']);
});
