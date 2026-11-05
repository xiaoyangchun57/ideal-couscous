import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { listLastPage, visiblePageRowCount } from './utils/listPagination.js';

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('shared workspace tables measure real pagination height and report true totals', () => {
  const table = source('./components/WorkspacePage.jsx');
  const height = source('./hooks/useTableAutoHeight.js');
  assert.match(table, /共\$\{total\}条，当前第\$\{start\}—\$\{end\}条/);
  assert.match(table, /paginationConfig\.total/);
  assert.match(table, /visiblePageRowCount/);
  assert.match(table, /hasPaginatedRecords/);
  assert.match(table, /pageSizeOptions: \[20, 50, 100\]/);
  assert.doesNotMatch(table, /calc\(100vh - 350px\)/);
  assert.match(height, /querySelector\('\.ant-table-pagination'\)/);
  assert.match(height, /paginationHeight/);
  assert.match(height, /MutationObserver/);
});

test('pagination helpers correct stale pages and report only visible client rows', () => {
  assert.equal(listLastPage(20, 20), 1);
  assert.equal(listLastPage(21, 20), 2);
  assert.equal(listLastPage(0, 20), 1);
  assert.equal(visiblePageRowCount(55, 1, 20), 20);
  assert.equal(visiblePageRowCount(55, 2, 20), 20);
  assert.equal(visiblePageRowCount(55, 3, 20), 15);
  assert.equal(visiblePageRowCount(20, 2, 20), 20);
});

test('remaining growth lists use the shared 20 50 100 pagination contract', () => {
  for (const path of [
    './pages/audit/AuditPage.jsx',
    './pages/reports/ReportsPage.jsx',
    './pages/vehicles/VehiclesPage.jsx',
    './pages/equipment/EquipmentPage.jsx',
    './pages/evaluation/EvaluationPage.jsx',
    './pages/users/UsersPage.jsx',
  ]) {
    assert.match(source(path), /standardListPagination/);
  }
  const archive = source('./pages/archive/archivePagination.js');
  assert.match(archive, /ARCHIVE_PAGE_SIZE = 20/);
  assert.match(archive, /ARCHIVE_PAGE_SIZES = \[20, 50, 100\]/);
  const reagents = source('./pages/reagents/ReagentMasterPage.jsx');
  assert.match(reagents, /pageSizeOptions: \[20, 50, 100\]/);
  assert.match(reagents, /showTotal: formatTablePaginationTotal/);
});

test('plans and alerts use explicit complete server pagination with latest-request guards', () => {
  const plans = source('./pages/plan-schedules/PlanSchedulesPage.jsx');
  const alerts = source('./pages/alerts/AlertsPage.jsx');
  assert.match(plans, /paginated: '1'/);
  assert.match(plans, /pageSizeOptions: \[20, 50, 100\]/);
  assert.match(plans, /requestId === listRequestRef\.current/);
  assert.match(plans, /listPage > lastPage/);
  assert.match(alerts, /paginated: '1'/);
  assert.match(alerts, /date_range: dateRange/);
  assert.match(alerts, /requestId !== alertListRequestRef\.current/);
  assert.match(alerts, /alertPage > lastPage/);
  assert.match(alerts, /pageSizeOptions: \[20, 50, 100\]/);
  assert.doesNotMatch(alerts, /alerts\?limit=500/);
});

test('work orders and operation logs also recover from stale server pages', () => {
  const workorders = source('./pages/workorders/WorkOrdersPage.jsx');
  const equipment = source('./pages/equipment/EquipmentPage.jsx');
  assert.match(workorders, /requestedPage > lastPage/);
  assert.match(workorders, /requestId !== orderListRequestRef\.current/);
  assert.match(equipment, /page > lastPage/);
  assert.match(equipment, /requestId !== requestRef\.current/);
});

test('major workspace lists no longer pin table bodies to page-specific viewport subtraction', () => {
  for (const path of [
    './pages/archive/ArchivePage.jsx',
    './pages/evaluation/EvaluationPage.jsx',
    './pages/reports/ReportsPage.jsx',
    './pages/sites/SitesPage.jsx',
  ]) {
    assert.doesNotMatch(source(path), /WorkspaceTable[\s\S]{0,500}calc\(100vh/);
  }
});
