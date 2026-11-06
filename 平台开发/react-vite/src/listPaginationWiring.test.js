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

test('every major list uses the shared 20/50/100 pagination contract (设备与物资 reference)', () => {
  const listPages = [
    './pages/audit/AuditPage.jsx',
    './pages/alerts/AlertsPage.jsx',
    './pages/alerts/components/DataReviewTab.jsx',
    './pages/vehicles/VehiclesPage.jsx',
    './pages/equipment/EquipmentPage.jsx',
    './pages/evaluation/EvaluationPage.jsx',
    './pages/reports/ReportsPage.jsx',
    './pages/users/UsersPage.jsx',
    './pages/sites/SitesPage.jsx',
    './pages/plan-schedules/PlanSchedulesPage.jsx',
    './pages/reagents/ReagentMasterPage.jsx',
    './pages/workorders/WorkOrdersPage.jsx',
    './pages/archive/ArchivePage.jsx',
  ];
  for (const path of listPages) {
    // 归档页把三档抽成了 ARCHIVE_PAGE_SIZES 常量，同样属于共享契约。
    assert.match(source(path), /standardListPagination|pageSizeOptions: \[20, 50, 100\]|ARCHIVE_PAGE_SIZES/,
      `${path} 未接入共享分页契约`);
  }
  // 统一审核 -> 数据审核：默认 20、改条/页会触发重新请求、不再私自关掉选择器。
  const review = source('./pages/alerts/components/DataReviewTab.jsx');
  assert.match(review, /per_page: 20/);
  assert.match(review, /\[filters\.page, filters\.per_page,/);
  assert.doesNotMatch(review, /showSizeChanger: false/);
  // 空列表也要保留分页栏（车辆履历等功能页）。
  const table = source('./components/WorkspacePage.jsx');
  assert.match(table, /Boolean\(paginationConfig\)/);
  assert.match(table, /emptyText/);
  // 不再出现各自为政的分页大小/文案。
  assert.doesNotMatch(source('./pages/alerts/AlertsPage.jsx'), /pageSize: 10/);
  assert.doesNotMatch(source('./pages/plan-schedules/PlanSchedulesPage.jsx'), /共 \$\{value\} 条/);
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
