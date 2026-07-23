# Operations Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calculate and present the V1.1 operational baseline without assigning target scores or adding frontline work.

**Architecture:** Add a focused Flask blueprint which reads existing operational tables and analytics events, returning values together with a source and sample count. Extend the existing evaluation page with a manager-only baseline section. Missing evidence remains `null` and is labelled “待采集”, rather than being converted to 0.

**Tech Stack:** Flask, SQLite, React, Ant Design, Python unittest.

---

### Task 1: Baseline calculation module

**Files:**
- Create: `平台开发/backend/operations_baseline.py`
- Create: `平台开发/backend/test_operations_baseline.py`
- Modify: `平台开发/backend/app.py`

- [ ] Write a failing test with a temporary SQLite schema proving that `inspection_coverage` is completed items divided by active planned items, and that an absent telemetry series returns `value: null`.
- [ ] Run `python -m unittest 平台开发/backend/test_operations_baseline.py -v` and verify it fails because the module is absent.
- [ ] Implement pure aggregation helpers plus authenticated `GET /api/operations/baseline?period=30d`; return north-star components, frontline time/offline samples, and a data-collection ledger.
- [ ] Register the blueprint from `app.py` and rerun the test until it passes.

### Task 2: Manager baseline view

**Files:**
- Modify: `平台开发/react-vite/src/pages/evaluation/EvaluationPage.jsx`

- [ ] Add a testable response fixture for the returned metric status semantics.
- [ ] Add a compact manager-only section which shows value, sample count and collection state, without targets, rankings or extra cards inside cards.
- [ ] Run `npm run build` and verify no build error.

### Task 3: Verification and versioning

- [ ] Run the focused Python tests, `py_compile`, mini-program regression test, and production build.
- [ ] Restart Flask and verify the real authenticated endpoint returns the expected baseline shape.
- [ ] Commit and push the change after checking that runtime data and uploads remain excluded.
