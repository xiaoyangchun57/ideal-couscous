import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Execute the page's actual save callback with only React state and transport replaced.
const source = fs.readFileSync(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
const start = source.indexOf('const saveEdit = useCallback(');
const end = source.indexOf('\n  useEffect(', start);
const expression = source.slice(start, end).trim().replace(/^const saveEdit = /, '').replace(/;$/, '');

function editor() {
  const calls = { put: [], confirm: [], messages: [] };
  let loseResponse = true;
  const state = {
    editDraft: { id: 5, version: 7, user_id: 2, vehicle_id: null,
      plan_data: { '2026-11-25': { sites: [1], inspection_items: { 1: [101] } } },
      vehicle_days: {}, no_vehicle_required: false, remarks: '保留输入' },
    editSaving: false, editResourcesReady: true, editItemsReady: true, editItemError: '',
    editConfirmationRef: { current: null },
    useCallback: callback => callback,
    setEditError: value => { state.error = value; },
    setEditSaving: value => { state.editSaving = value; },
    setEditDraft: update => { state.editDraft = update(state.editDraft); },
    setEditValidation() {},
    setEditOpen: value => { state.open = value; },
    refreshAll() {},
    openDetail: async () => {},
    message: { success: value => calls.messages.push(value) },
    api: {
      async putStrict(path, payload) {
        calls.put.push({ path, payload });
        return { version: payload.version + 1 };
      },
      async postStrict(path, payload) {
        if (path.endsWith('/validate')) return { errors: [], warnings: [] };
        calls.confirm.push({ path, payload });
        if (loseResponse) throw new Error('响应丢失');
        return { status: 'approved', already_confirmed: true };
      },
    },
  };
  const save = () => vm.runInNewContext(expression, state)(true);
  return { state, calls, save, deliver: () => { loseResponse = false; } };
}

test('web confirmation response loss retries the saved version without another PUT', async () => {
  const { state, calls, save, deliver } = editor();
  await save();
  assert.equal(calls.put.length, 1);
  assert.equal(state.editDraft.version, 8);
  assert.equal(state.editDraft.remarks, '保留输入');
  assert.match(state.error, /结果未确认.*直接重试/);
  assert.equal(calls.messages.length, 0);
  deliver();
  await save();
  assert.equal(calls.put.length, 1, '相同草稿只重试确认，不重复保存');
  assert.equal(calls.confirm.length, 2);
  assert.equal(calls.confirm[0].payload.version, 8);
  assert.equal(calls.confirm[1].payload.version, 8);
  assert.equal(state.open, false);
  assert.equal(state.error, '');
  assert.equal(state.editConfirmationRef.current, null);
  assert.equal(calls.messages.length, 1);
});

test('web edits after a failed confirmation form a new save attempt and preserve input', async () => {
  const { state, calls, save, deliver } = editor();
  await save();
  state.editDraft.remarks = '修改后的输入';
  deliver();
  await save();
  assert.equal(calls.put.length, 2);
  assert.equal(calls.put[1].payload.remarks, '修改后的输入');
  assert.equal(calls.confirm[1].payload.version, 9);
});
