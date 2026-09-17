import assert from 'node:assert/strict';
import test from 'node:test';
import { ALL_LIST_FILTER_VALUE, listFilterOptions, listFilterValue } from './listFilterOptions.js';
import { readFileSync } from 'node:fs';

test('list filters expose a first explicit all option without mutating business options', () => {
  const source = [{ value: 'active', label: '启用' }];
  const result = listFilterOptions('全部状态', source);
  assert.deepEqual(result, [
    { value: ALL_LIST_FILTER_VALUE, label: '全部状态' },
    { value: 'active', label: '启用' },
  ]);
  assert.deepEqual(source, [{ value: 'active', label: '启用' }]);
});

test('only the explicit all option maps to an omitted filter value', () => {
  assert.equal(listFilterValue(ALL_LIST_FILTER_VALUE), undefined);
  assert.equal(listFilterValue('active'), 'active');
  assert.equal(listFilterValue(0), 0);
});

test('every contracted Web data list uses the shared explicit all-option contract', () => {
  const targets = [
    ['../pages/sites/SitesPage.jsx', ['全部站点类型', '全部区县', '全部负责人']],
    ['../pages/workorders/WorkOrdersPage.jsx', ['全部级别', '全部状态']],
    ['../pages/alerts/AlertsPage.jsx', ['全部告警状态', '全部告警等级']],
    ['../pages/archive/ArchivePage.jsx', ['全部站点', '全部业务来源']],
    ['../pages/plan-schedules/PlanSchedulesPage.jsx', ['全部状态', '全部类型']],
    ['../pages/equipment/EquipmentPage.jsx', ['全部设备类型', '全部站点']],
    ['../pages/audit/AuditPage.jsx', ['全部审核类型', '全部来源计划']],
    ['../pages/reports/ReportsPage.jsx', ['全部状态', '全部类型']],
    ['../pages/users/UsersPage.jsx', ['全部角色', '全部状态']],
    ['../pages/alerts/components/DataReviewTab.jsx', ['全部复核级别', '全部复核状态']],
  ];
  for (const [relativePath, labels] of targets) {
    const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
    assert.match(source, /listFilterOptions\(/, relativePath);
    assert.match(source, /listFilterValue\(/, relativePath);
    for (const label of labels) assert.ok(source.includes(`'${label}'`), `${relativePath}: ${label}`);
  }
});
