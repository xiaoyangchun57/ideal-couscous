import test from 'node:test';
import assert from 'node:assert/strict';
import { archiveLastPage, archiveNavigationState, archivePageNumber } from './archivePagination.js';

test('archive page numbers reject invalid pages and totals clamp to a usable last page', () => {
  for (const page of [null, '', '0', '-1', 'abc', '1.5', 'Infinity', '9007199254740992']) assert.equal(archivePageNumber(page), 1);
  assert.equal(archivePageNumber('2'), 2);
  assert.equal(archiveLastPage(0), 1);
  assert.equal(archiveLastPage(100), 1);
  assert.equal(archiveLastPage(101), 2);
});

test('table and grid share server pagination, encoded filters and history scope', () => {
  const params = new URLSearchParams({ keyword: '测试 & 档案', site_id: '7', business_type: 'inspection', date_from: '2026-09-01', date_to: '2026-09-15', scope: 'history', page: '2' });
  const table = archiveNavigationState(params);
  params.set('view', 'grid');
  const grid = archiveNavigationState(params);
  assert.equal(table.requestQuery, grid.requestQuery);
  assert.equal(grid.page, 2);
  assert.equal(grid.archiveMode, 'history');
  const request = new URLSearchParams(grid.requestQuery);
  assert.equal(request.get('keyword'), '测试 & 档案');
  assert.equal(request.get('limit'), '100');
  assert.equal(request.get('page'), '2');
  assert.equal(request.get('history_archive'), '1');
  assert.equal(request.get('include_voided'), '1');
  assert.equal(request.has('current_archive'), false);
});

test('current archive uses the current server filter rather than fetching all records', () => {
  const request = new URLSearchParams(archiveNavigationState(new URLSearchParams()).requestQuery);
  assert.equal(request.get('current_archive'), '1');
  assert.equal(request.get('limit'), '100');
  assert.equal(request.has('history_archive'), false);
});
