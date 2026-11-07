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

test('every list footer is docked at one fixed bottom slot with identical geometry', () => {
  const table = source('./components/WorkspacePage.jsx');
  const css = source('./components/WorkspacePage.css');
  // 共享层：只要声明了分页配置就自动停靠，不再逐页手写 fillHeight。
  assert.match(table, /const dockPagination = fillHeight \|\| Boolean\(paginationConfig/);
  assert.match(table, /\$\{dockPagination \? ' workspace-table--fill'/);
  // 几何只有一个来源，且分页栏被推到容器底部。
  assert.match(css, /--list-pagination-height: 48px/);
  assert.match(css, /min-height: var\(--list-pagination-height\)/);
  assert.match(css, /line-height: 32px/);
  assert.match(css, /margin-top: auto !important/);
  assert.match(css, /\.list-pagination-dock \{ display: flex; flex: 1; min-height: 0; flex-direction: column; \}/);
  assert.match(css, /:is\(\.workspace-table--fill, \.list-pagination-dock\) \.ant-table-body/);
  // 不允许再出现各页私有的分页栏留白。
  assert.doesNotMatch(css, /margin: 12px 16px !important/);
  // 归档网格视图的独立 Pagination 也统一几何。
  assert.match(css, /\.archive-grid-footer \.ant-pagination/);
  // 不走 WorkspaceTable 的裸 Table 主列表接入同一 class。
  const alerts = source('./pages/alerts/AlertsPage.jsx');
  assert.equal((alerts.match(/list-pagination-dock/g) || []).length, 2);
  assert.match(source('./components/ManagementPage.jsx'), /tableMode === 'fill' \? 'list-pagination-dock'/);
  // 试剂主数据与用户管理从内容自适应改为撑满，分页栏才可能固定在底部。
  for (const path of [
    './pages/reagents/ReagentMasterPage.jsx',
    './pages/users/UsersPage.jsx',
  ]) {
    const page = source(path);
    assert.match(page, /tableMode="fill"/, `${path} 未撑满表格卡片`);
    assert.match(page, /mode="fill"/, `${path} 未启用自动高度表格`);
    assert.doesNotMatch(page, /tableMode="content"|mode="content"/);
  }
  // 管理页模板的包装层自身也要进入 flex 高度链，否则分页栏仍会随行数上浮。
  const management = source('./components/ManagementPage.jsx');
  assert.match(management, /<div ref=\{wrapRef\} className="list-pagination-dock"/);
  // antd 会把 Table size="small" 传给 Pagination：全站主列表因此都是 small 档，
  // 归档网格的独立 Pagination 必须显式对齐，且不得使用改变控件形态的私有开关。
  const archive = source('./pages/archive/ArchivePage.jsx');
  assert.match(table, /size="small"/);
  assert.match(management, /size = 'small'/);
  assert.match(alerts, /size="small"/);
  assert.match(archive, /<Pagination[\s\S]{0,200}size="small"/);
  assert.doesNotMatch(archive, /showLessItems|\sresponsive/);
  // 强制单行只作用于停靠容器，窄屏仍可换行。
  assert.match(css, /:is\(\.workspace-table--fill, \.list-pagination-dock\) \.ant-table-wrapper \.ant-table-pagination\.ant-pagination \{\s*flex-wrap: nowrap;/);
  assert.match(css, /\.archive-grid-footer \.ant-pagination \{ flex-wrap: wrap; \}/);
});


test('client-side lists keep the selected page size instead of pinning it back to 20', () => {
  const hook = source('./utils/clientPagination.js');
  assert.match(hook, /defaultPageSize: clientPage\.pageSize/);
  assert.match(hook, /defaultCurrent: clientPage\.current/);
  assert.match(hook, /setClientPage\(\{ current: next, pageSize: size \}\)/);
  assert.match(hook, /typeof pagination\.onChange === 'function'/);
  for (const path of ['./components/WorkspacePage.jsx', './components/ManagementPage.jsx']) {
    assert.match(source(path), /useClientPagination\(pagination\)/, `${path} 未接入客户端分页状态`);
  }
  // 服务端分页仍由调用方控制，不能被共享层改写
  assert.doesNotMatch(hook, /defaultPageSize:.*current:.*onChange:.*setSearchParams/);
});

test('work orders and plan schedules filter by site / person through the server', () => {
  const orders = source('./pages/workorders/WorkOrdersPage.jsx');
  const plans = source('./pages/plan-schedules/PlanSchedulesPage.jsx');
  assert.match(orders, /aria-label="所属站点"/);
  assert.match(orders, /aria-label="负责人"/);
  assert.match(orders, /'search', 'assignee', 'level', 'status', 'site_id'/);
  assert.match(plans, /aria-label="计划负责人"/);
  assert.match(plans, /params\.set\('user_id', userFilter\)/);
  const backend = readFileSync(new URL('../../backend/app.py', import.meta.url), 'utf8');
  assert.match(backend, /site_id = request\.args\.get\('site_id'/);
  assert.match(backend, /where\.append\('w\.site_id=\?'\)/);
});

test('unified review gets client-side site and person filters', () => {
  const audit = source('./pages/audit/AuditPage.jsx');
  assert.match(audit, /function collectAuditScopeOptions/);
  assert.match(audit, /function matchesAuditScope/);
  assert.equal((audit.match(/aria-label="审核站点"/g) || []).length, 2);
  assert.equal((audit.match(/aria-label="审核人员"/g) || []).length, 2);
});
