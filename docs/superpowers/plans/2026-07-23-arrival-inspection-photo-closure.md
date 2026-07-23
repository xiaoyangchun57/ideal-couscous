# Arrival Inspection and Photo Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a frontline operator open the exact assigned station from the home page, check in, submit inspection evidence, and have offline work reliably complete its server-side closure.

**Architecture:** Keep the current local-first mini-program outbox. Extract deterministic station-selection and photo-requirement decisions into a small pure utility so they can be regression-tested without a WeChat runtime. Add a focused telemetry blueprint for anonymized field-action events; `app.py` only registers it.

**Tech Stack:** WeChat Mini Program JavaScript/WXML/WXSS, Flask, SQLite, Node built-in assertions, Python unittest.

---

### Task 1: Test and extract field-execution decisions

**Files:**
- Create: `平台开发/miniprogram/utils/executionState.js`
- Create: `平台开发/miniprogram/tests/executionState.test.js`
- Modify: `平台开发/miniprogram/pages/inspection/inspection.js`

- [ ] **Step 1: Write the failing Node test for a home-selected station and required photos**

```js
assert.equal(selectExecutionSite(packages, null, 22).site_id, 22);
assert.deepEqual(photoRequirement(2, 1, 0), { required: 2, captured: 1, missing: 1, ready: false });
```

- [ ] **Step 2: Run the test to verify it fails because the utility does not exist**

Run: `node 平台开发/miniprogram/tests/executionState.test.js`
Expected: module-not-found failure.

- [ ] **Step 3: Implement the pure utility and wire it into `loadExecution` and item submission**

```js
function selectExecutionSite(packages, selectedPlanId, preferredSiteId) {
  // Prefer a package containing the explicit home-page station, then retain the prior package.
}
function photoRequirement(required, remotePhotos, localPhotos) {
  // Count both uploaded and retained-local photos before deciding whether the item can submit.
}
```

- [ ] **Step 4: Run the Node test to verify it passes**

Run: `node 平台开发/miniprogram/tests/executionState.test.js`
Expected: `executionState tests passed`.

### Task 2: Make current-station progress and offline state visible

**Files:**
- Modify: `平台开发/miniprogram/pages/inspection/inspection.js`
- Modify: `平台开发/miniprogram/pages/inspection/inspection.wxml`
- Modify: `平台开发/miniprogram/pages/inspection/inspection.wxss`
- Modify: `平台开发/miniprogram/utils/localStore.js`
- Modify: `平台开发/miniprogram/utils/sync.js`

- [ ] **Step 1: Write a failing test for an un-synced check-in being recognized for its station**

```js
store.write([{ id: 'checkin-1', type: 'checkin', syncStatus: 'pending', data: { site_id: 22 } }]);
assert.equal(store.getLocalCheckIn(22).id, 'checkin-1');
```

- [ ] **Step 2: Run the test and verify it fails only for the new status helper**

Run: `node 平台开发/miniprogram/tests/executionState.test.js`
Expected: assertion failure for the missing station execution status.

- [ ] **Step 3: Implement a compact current-station panel**

```js
// State: unvisited | local_pending | checked_in
// UI: station identity, check-in action/status, item completion, required-photo completion, pending-sync count.
```

The panel must not add a separate wizard or a second submit action. A normal item with `required_photos > 0` must remain blocked until uploaded plus local photos meet the configured count; abnormal items continue to require at least one photo.

- [ ] **Step 4: Run mini-program syntax checks and the Node test**

Run: `node --check 平台开发/miniprogram/pages/inspection/inspection.js; node --check 平台开发/miniprogram/utils/sync.js; node 平台开发/miniprogram/tests/executionState.test.js`
Expected: all commands exit 0.

### Task 3: Collect baseline field-closure events without adding `app.py` debt

**Files:**
- Create: `平台开发/backend/telemetry.py`
- Create: `平台开发/backend/test_telemetry.py`
- Modify: `平台开发/backend/app.py`
- Modify: `平台开发/miniprogram/services/api.js`
- Modify: `平台开发/miniprogram/pages/index/index.js`
- Modify: `平台开发/miniprogram/pages/inspection/inspection.js`
- Modify: `平台开发/miniprogram/utils/sync.js`

- [ ] **Step 1: Write a failing Flask test for idempotent, authenticated field-action ingestion**

```python
response = client.post('/api/telemetry/events', json={
    'event_id': 'evt-arrival-1', 'event_name': 'inspection.checkin.queued',
    'occurred_at': '2026-07-23T10:00:00', 'context': {'site_id': 22}
}, headers=auth_header)
assert response.status_code == 201
assert client.post(...same_payload...).status_code == 200
```

- [ ] **Step 2: Run the test to verify the endpoint is absent**

Run: `python -m unittest 平台开发.backend.test_telemetry -v`
Expected: failure because the telemetry module/route is missing.

- [ ] **Step 3: Add a small telemetry blueprint and register it from `app.py`**

The table stores `event_id`, server/user IDs, event name, client occurrence time, app version, and a bounded JSON context. Accept only these V1 event names: `inspection.station_opened`, `inspection.checkin.queued`, `inspection.checkin.synced`, `inspection.photo.captured`, `inspection.item.queued`, `inspection.item.synced`, `inspection.sync.failed`. Do not store photo bytes, precise GPS, credentials, or free-text remarks in telemetry.

- [ ] **Step 4: Emit the V1 events from the home-to-station, check-in, photo, submit, and replay paths**

```js
api.trackEvent('inspection.station_opened', { site_id: selectedSiteId, entry: 'home' });
api.trackEvent('inspection.item.queued', { site_id, item_id, offline: true });
```

Events must be best-effort and must never block the operational request or erase a local operation after telemetry failure.

- [ ] **Step 5: Run focused backend and mini-program verification**

Run: `python -m unittest 平台开发.backend.test_telemetry -v; python -m py_compile 平台开发/backend/app.py 平台开发/backend/telemetry.py; node --check 平台开发/miniprogram/services/api.js`
Expected: tests pass and syntax checks exit 0.

### Task 4: Verify the actual closure contract

**Files:**
- Modify: `平台开发/dev_scripts/test_closed_loop.py`

- [ ] **Step 1: Add an integration assertion that a station outside the execution package is rejected and a required-photo normal item cannot be submitted short of its required count**

```python
assert response.status_code == 400
assert '照片' in response.json()['error']
```

- [ ] **Step 2: Run the focused closure test against the local server**

Run: `python 平台开发/dev_scripts/test_closed_loop.py`
Expected: each assertion passes without modifying production data beyond the script's existing fixture behavior.

- [ ] **Step 3: Build the existing web client as a guardrail**

Run: `npm run build`
Working directory: `平台开发/react-vite`
Expected: exit 0; pre-existing lint warnings, if any, are reported separately and do not mask a build error.

## Scope Check

This iteration improves frontline `核心动作触达深度`, `单站巡检耗时中位数`, and `离线闭环成功率` by removing an incorrect station transition, exposing state already held locally, enforcing evidence at the actual submit point, and collecting the baseline events needed to measure the outcome. It deliberately does not add a manager dashboard or alter approval/review rules; those require their own telemetry baseline and separate plan.
