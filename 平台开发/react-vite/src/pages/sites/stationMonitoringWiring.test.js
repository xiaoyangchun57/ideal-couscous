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

test('site directory reuses an identity-scoped snapshot and keeps refresh non-blocking', () => {
  const sites = source('./SitesPage.jsx');
  assert.match(sites, /stationDirectorySnapshotKey\(\s*user, monitoringScope, token, serviceScope/);
  assert.match(sites, /readSiteDirectorySnapshot\(monitoringRequestKey\)/);
  assert.match(sites, /writeSiteDirectorySnapshot\(\s*monitoringRequestKey/);
  assert.match(sites, /setRefreshing\(loadMode === 'refresh'\)/);
  assert.match(sites, /loading=\{directoryLoading\}/);
  assert.match(sites, /站点台账已加载，监测状态仍在加载中/);
  assert.match(sites, /previousSnapshot\?\.rows \|\| \[\]/);
  assert.match(sites, /resultSnapshot/);
  assert.match(sites, /当前展示上次成功结果，状态待更新/);
  assert.match(sites, /监测结果更新时间/);
  const baseSnapshotCommit = sites.indexOf('const baseSnapshot = storeSnapshot');
  const monitoringAwait = sites.indexOf('await monitoringResultPromise');
  assert.ok(baseSnapshotCommit >= 0 && monitoringAwait > baseSnapshotCommit,
    'the site ledger must publish before waiting for monitoring');
  assert.doesNotMatch(sites, /Promise\.allSettled\(requests\)/);
});

test('closed monitoring capability hides entries and makes the site ledger skip monitoring requests', () => {
  const sites = source('./SitesPage.jsx');
  const layout = source('../../layouts/MainLayout.jsx');
  const cockpit = source('../cockpit/CockpitPage.jsx');
  const navigation = source('../../config/navigation.jsx');
  const search = source('../../components/GlobalSearch.jsx');

  assert.match(sites, /const monitoringResultPromise = monitoringPublic[^]*api\.stationMonitoringSites/);
  assert.match(sites, /monitoringPublic && <Button[^]*?record\.service_status === 'ended' \? '历史' : '监测'/);
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

test('single-site monitoring edits only the explicit nine-factor station denominator', () => {
  const detail = source('./SiteMonitoringPage.jsx');
  const api = source('../../services/api.js');
  assert.match(detail, /api\.stationExpectedFactors\(siteId/);
  assert.match(detail, /api\.updateStationExpectedFactors\(siteId/);
  assert.match(detail, /idempotency_key: expectedSaveKeyRef\.current\.key/);
  assert.match(detail, /expectedFactorVersion: next\.version/);
  assert.match(detail, /应监测因子未设置/);
  assert.match(detail, /至少选择一项应监测因子/);
  assert.match(detail, /当前账号仅可查看/);
  assert.match(api, /stationExpectedFactors:/);
  assert.match(api, /updateStationExpectedFactors:/);
  const refreshedOverview = detail.indexOf('const refreshed = await load({');
  const savedNotice = detail.indexOf("setExpectedNotice(next.replayed");
  assert.ok(refreshedOverview >= 0 && savedNotice > refreshedOverview,
    'save success must wait for the matching overview version');
});

test('site service policy stays in the station archive and isolates current/all list snapshots', () => {
  const sites = source('./SitesPage.jsx');
  const api = source('../../services/api.js');

  assert.match(sites, /service_scope=\$\{serviceScope\}/);
  assert.match(sites, /当前服务站点/);
  assert.match(sites, /含服务结束/);
  assert.match(sites, /searchParams\.get\('service_scope'\) === 'all'/);
  assert.match(sites, /monitoringDetailPath\(record\.id\)/);
  assert.match(sites, /zIndex=\{1100\}/);
  assert.match(sites, /维护服务政策/);
  assert.match(sites, /服务结束，不参与当前到报评价/);
  assert.match(sites, /当前输入已保留/);
  assert.match(sites, /expected_version: Number\(servicePolicyData\.version \|\| 0\)/);
  assert.match(sites, /idempotency_key: servicePolicyRetryRef\.current\.key/);
  assert.match(sites, /我确认该站点将退出当前服务范围、到报分母和新缺报提醒/);
  assert.match(api, /stationServicePolicy:/);
  assert.match(api, /updateStationServicePolicy:/);
});

test('monitoring detail returns with directory query state and limits trend candidates', () => {
  const detail = source('./SiteMonitoringPage.jsx');
  assert.match(detail, /const returnQuery = searchParams\.toString\(\)/);
  assert.match(detail, /monitoringTrendFactors\(/);
  assert.match(detail, /options=\{trendFactors\.map/);
});

test('access observation does not consume sensitive endpoint identity fields', () => {
  const access = source('./StationAccessPage.jsx');
  assert.doesNotMatch(access, /station_code|credential_hmac|raw_frame(?!s)/);
});
