import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

test('all URL-backed realtime text searches use the shared IME-safe draft', () => {
  const pages = [
    '../pages/sites/SitesPage.jsx',
    '../pages/vehicles/VehiclesPage.jsx',
    '../pages/equipment/EquipmentPage.jsx',
    '../pages/reagents/ReagentMasterPage.jsx',
    '../pages/users/UsersPage.jsx',
    '../pages/alerts/AlertsPage.jsx',
  ];
  pages.forEach((path) => {
    const text = source(path);
    assert.match(text, /useUrlSyncedSearch/);
    assert.match(text, /\.\.\.searchInputProps/);
  });
});

test('remote device search cancels stale requests and filters the current draft immediately', () => {
  const text = source('../pages/equipment/EquipmentPage.jsx');
  assert.match(text, /deviceRequestRef\.current\.controller\?\.abort\(\)/);
  assert.match(text, /signal: controller\.signal/);
  assert.match(text, /const filteredDevices = useMemo/);
  assert.match(text, /dataSource=\{filteredDevices\}/);
});

test('known-good archive and work-order search implementations remain local', () => {
  const archive = source('../pages/archive/ArchivePage.jsx');
  const workorders = source('../pages/workorders/WorkOrdersPage.jsx');
  assert.doesNotMatch(archive, /useUrlSyncedSearch/);
  assert.match(archive, /const \[filters, setFilters\] = useState/);
  assert.doesNotMatch(workorders, /useUrlSyncedSearch/);
  assert.match(workorders, /const \[search, setSearch\] = useState/);
});

test('current concentrated rework keeps list identity columns and vehicle permissions scoped', () => {
  const sites = source('../pages/sites/SitesPage.jsx');
  const equipment = source('../pages/equipment/EquipmentPage.jsx');
  const vehicles = source('../pages/vehicles/VehiclesPage.jsx');
  const users = source('../pages/users/UsersPage.jsx');

  const identityCell = sites.slice(sites.indexOf("title: '站点身份'"), sites.indexOf("title: '监测状态'"));
  assert.doesNotMatch(identityCell, /试点|copyable|record\.code/);
  assert.match(identityCell, /record\.name/);
  assert.match(identityCell, /stationTypeMap/);

  const ledgerColumns = equipment.slice(equipment.indexOf("title: '设备编码'"), equipment.indexOf("title: '操作'"));
  assert.doesNotMatch(ledgerColumns, /最后数据|运行状态/);
  assert.match(ledgerColumns, /生命周期/);

  assert.match(vehicles, /const canMaintain = isAdmin \|\| roles\.includes\('operator'\)/);
  assert.match(vehicles, /_idempotency_key: documentIdempotencyKey\.current/);
  assert.match(vehicles, /sort\(\(left, right\) => Number\(right\.id/);
  assert.match(vehicles, /\{isAdmin && <Form\.Item name="plate_no"/);
  assert.match(vehicles, /\{isAdmin && <Form\.Item name="status"/);
  assert.match(users, /users\/\$\{record\.id\}\/permanent/);
});
