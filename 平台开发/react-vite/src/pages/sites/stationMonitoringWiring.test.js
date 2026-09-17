import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8');

test('monitoring routes use exact role keys and keep the access center admin-only', () => {
  const app = source('../../App.jsx');
  const navigation = source('../../config/navigation.jsx');

  assert.match(app, /PageRoute path="\/sites\/data-access"/);
  assert.match(app, /PageRoute path="\/sites"[^]*SiteMonitoringPage/);
  assert.match(app, /CapabilityRoute capability="station_monitoring_public"/);
  assert.match(navigation, /'\/sites': \['admin', 'reviewer', 'operator'\]/);
  assert.match(navigation, /'\/sites\/data-access': \['admin'\]/);
});

test('site entry points distinguish monitoring details from archive workflows', () => {
  const cockpit = source('../cockpit/CockpitPage.jsx');
  const alerts = source('../alerts/AlertsPage.jsx');
  const search = source('../../components/GlobalSearch.jsx');

  assert.match(cockpit, /navigate\(`\/sites\/\$\{site\.id\}`\)/);
  assert.match(cockpit, /navigate\(`\/sites\?archive=\$\{site\.id\}`\)/);
  assert.match(alerts, /navigate\(`\/sites\/\$\{record\.site_id\}`\)/);
  assert.match(alerts, /navigate\(`\/sites\?archive=\$\{r\.site_id\}`\)/);
  assert.match(search, /item\.type === 'site' \? buildGlobalSearchPath\(item\)/);
});

test('site directory retains create import archive profile and reagent workflows', () => {
  const sites = source('./SitesPage.jsx');

  for (const workflow of [
    'openSiteCreate', 'submitSiteCreate', 'handleImportFile', 'openArchive',
    'openProfileEdit', 'saveProfile', 'openReagentCreate', 'submitReagentUpd', 'submitQc',
  ]) {
    assert.match(sites, new RegExp(`\\b${workflow}\\b`));
  }
  assert.match(sites, /站点业务状态/);
});

test('monitoring pages cancel superseded requests and ignore stale responses', () => {
  for (const relativePath of ['./SitesPage.jsx', './SiteMonitoringPage.jsx', './StationAccessPage.jsx']) {
    const page = source(relativePath);
    assert.match(page, /\.controller\?\.abort\(\)/, relativePath);
    assert.match(page, /\.current\.id !== requestId/, relativePath);
    assert.match(page, /REQUEST_ABORTED/, relativePath);
  }
  assert.match(
    source('./SiteMonitoringPage.jsx'),
    /String\(data\.site\?\.id\) === String\(siteId\)/,
  );
});

test('closed monitoring capability hides entries and makes the site ledger skip monitoring requests', () => {
  const sites = source('./SitesPage.jsx');
  const layout = source('../../layouts/MainLayout.jsx');
  const cockpit = source('../cockpit/CockpitPage.jsx');
  const navigation = source('../../config/navigation.jsx');
  const search = source('../../components/GlobalSearch.jsx');

  assert.match(sites, /if \(monitoringPublic\) requests\.push\(api\.stationMonitoringSites/);
  assert.match(sites, /monitoringPublic && <Button[^]*?>监测<\/Button>/);
  assert.match(sites, /isAdmin && monitoringPublic && <Button[^]*?>接入观察<\/Button>/);
  assert.match(sites, /\.\.\.\(monitoringPublic \? \[\{[^]*?label: '数据接入状态'/);
  assert.match(sites, /\{monitoringPublic && \([^]*?<ArchiveTrendPanel/);
  assert.match(layout, /monitoringPublic && new URLSearchParams/);
  assert.match(layout, /\.\.\.\(monitoringPublic \? \[\{ value: 'sites'/);
  assert.match(cockpit, /monitoringPublic && requestedView === 'sites'/);
  assert.match(navigation, /capabilities\.station_monitoring_public === true/);
  assert.match(search, /getSearchablePages\([^,]+, user\?\.capabilities\)/);
});

test('single-site monitoring stays a data fact page without archive or device-health sections', async () => {
  const detail = source('./SiteMonitoringPage.jsx');
  assert.match(detail, /最后收到报文/);
  assert.match(detail, /站点档案/);
  assert.match(detail, /监测因子/);
  for (const removed of ['所属区县', '地址', '仪器与因子', '近期告警、工单与巡检', '健康状态未知']) {
    assert.doesNotMatch(detail, new RegExp(removed));
  }
  assert.deepEqual(Object.keys((await import('./stationMonitoring.js')).AXIS_META), ['communication', 'data']);
});

test('access observation does not consume sensitive endpoint identity fields', () => {
  const access = source('./StationAccessPage.jsx');
  assert.doesNotMatch(access, /station_code|credential_hmac|raw_frame(?!s)/);
});
