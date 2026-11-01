import test from 'node:test';
import assert from 'node:assert/strict';
import dayjs from 'dayjs';
import {
  archiveDownloadScopeKey,
  archiveDownloadFailureSummary,
  archiveDownloadFilterSelection,
  isCurrentArchiveDownloadSelectionRequest,
  nextArchiveDownloadSelectionRequest,
  toggleArchiveDownloadItem,
  toggleArchiveDownloadPage,
} from './archiveDownloadSelection.js';

test('download selection survives page changes and page toggles only affect that page', () => {
  let selected = toggleArchiveDownloadPage([], [1, 2, 3], true);
  selected = toggleArchiveDownloadPage(selected, [101, 102], true);
  assert.deepEqual(selected, [1, 2, 3, 101, 102]);
  selected = toggleArchiveDownloadPage(selected, [1, 2, 3], false);
  assert.deepEqual(selected, [101, 102]);
  assert.deepEqual(toggleArchiveDownloadItem(selected, 101, false), [102]);
});

test('filter-wide selection uses the applied filters and archive category', () => {
  const selection = archiveDownloadFilterSelection({
    keyword: '余氯', site_id: 7, business_type: 'inspection',
    date_range: [dayjs('2026-10-01'), dayjs('2026-10-31')],
  }, 'history');
  assert.deepEqual(selection, {
    mode: 'filter',
    filters: {
      archive_mode: 'history', keyword: '余氯', site_id: 7,
      business_type: 'inspection', date_from: '2026-10-01', date_to: '2026-10-31',
    },
  });
});

test('download failures are grouped into a readable summary', () => {
  assert.equal(archiveDownloadFailureSummary([
    { reason: '文件不存在' }, { reason: '文件不存在' }, { reason: '状态已变化' },
  ]), '文件不存在 2 条；状态已变化 1 条');
});

test('late filter selection responses cannot replace a newer scope or request', () => {
  const scopeA = archiveDownloadScopeKey('current', 'site_id=1');
  const first = nextArchiveDownloadSelectionRequest(null, scopeA);
  assert.equal(isCurrentArchiveDownloadSelectionRequest(first, first, scopeA), true);

  const scopeB = archiveDownloadScopeKey('current', 'site_id=2');
  const invalidated = nextArchiveDownloadSelectionRequest(first, scopeB);
  assert.equal(isCurrentArchiveDownloadSelectionRequest(first, invalidated, scopeB), false);

  const second = nextArchiveDownloadSelectionRequest(invalidated, scopeB);
  assert.equal(isCurrentArchiveDownloadSelectionRequest(invalidated, second, scopeB), false);
  assert.equal(isCurrentArchiveDownloadSelectionRequest(second, second, scopeB), true);
  assert.equal(isCurrentArchiveDownloadSelectionRequest(second, second, scopeB, false), false);
});
