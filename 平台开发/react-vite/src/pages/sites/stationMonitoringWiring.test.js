import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8');

test('monitoring routes use exact role keys and keep the access center admin-only', () => {
  const app = source('../../App.jsx');
  const navigation = source('../../config/navigation.jsx');

  assert.match(app, /PageRoute path="\/sites\/data-access"/);
  assert.match(app, /Route path="sites\/:siteId"[^]*<SitesPage \/>/);
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
  const monitoring = source('./stationMonitoring.js');
  assert.match(sites, /stationDirectorySnapshotKey\(\s*user, monitoringScope, token, serviceScope/);
  assert.match(sites, /serviceScope, signal: controller\.signal/);
  assert.match(sites, /validateMonitoringDirectoryPayload\(/);
  assert.match(monitoring, /监测汇总与当前站点列表数量不一致/);
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

test('single-site monitoring is a focused drawer without archive or device-health sections', async () => {
  const detail = source('./SiteMonitoringPage.jsx');
  assert.match(detail, /最新接收/);
  assert.match(detail, /站点档案/);
  assert.match(detail, /最新结果/);
  assert.match(detail, /结果趋势/);
  assert.match(detail, /监测因子设置/);
  for (const removed of ['所属区县', '地址', '仪器与因子', '近期告警、工单与巡检', '健康状态未知', '覆盖率']) {
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
  assert.match(detail, /saveError\?\.code === 'REQUEST_TIMEOUT' \|\| saveError\?\.code === 'NETWORK_ERROR'/);
  assert.match(detail, /setExpectedErrorTitle\('保存结果待确认'\)/);
  assert.match(detail, /saveError\?\.code === 'EXPECTED_FACTORS_BUSY'/);
  assert.match(detail, /setExpectedErrorTitle\('设置保存暂时繁忙'\)/);
  assert.match(detail, /expectedRetrySave \? saveExpectedFactors : loadExpectedFactors/);
  assert.match(detail, /expectedOutcomeUncertain \? '再次提交确认' : expectedRetrySave \? '重试保存' : '刷新设置'/);
  assert.match(detail, /factors=\{trendFactors\}/);
  assert.match(detail, /latestValues=\{latestValues\}/);
  assert.match(detail, /expectedFactorVersion: next\.version/);
  assert.match(detail, /至少选择一项监测因子/);
  assert.match(detail, /当前账号仅可查看/);
  assert.match(api, /stationExpectedFactors:/);
  assert.match(api, /updateStationExpectedFactors:/);
  const refreshedOverview = detail.indexOf('const refreshed = await loadOverview({');
  const savedNotice = detail.indexOf("setExpectedNotice(next.replayed");
  assert.ok(refreshedOverview >= 0 && savedNotice > refreshedOverview,
    'save success must wait for the matching overview version');
});

test('site service policy has a direct admin row entry and isolates current/all list snapshots', () => {
  const sites = source('./SitesPage.jsx');
  const api = source('../../services/api.js');

  assert.match(sites, /service_scope=\$\{serviceScope\}/);
  assert.match(sites, /当前服务站点/);
  assert.match(sites, /含服务结束/);
  assert.match(sites, /searchParams\.get\('service_scope'\) === 'all'/);
  assert.match(sites, /openMonitoring\(record\.id\)/);
  assert.match(sites, /zIndex=\{1100\}/);
  assert.match(sites, /维护服务政策/);
  assert.match(sites, /isAdmin && <Button[^]*?openServicePolicy\(record\)[^]*?>服务状态<\/Button>/);
  assert.match(sites, /const \[servicePolicySite, setServicePolicySite\] = useState\(null\)/);
  assert.match(sites, /服务结束，不参与当前到报评价/);
  assert.match(sites, /当前输入已保留/);
  assert.match(sites, /expected_version: Number\(servicePolicyData\.version \|\| 0\)/);
  assert.match(sites, /idempotency_key: servicePolicyRetryRef\.current\.key/);
  assert.match(sites, /我确认该站点将退出当前服务范围、到报分母和新缺报提醒/);
  assert.match(api, /stationServicePolicy:/);
  assert.match(api, /updateStationServicePolicy:/);
});

test('monitoring detail returns with directory query state and limits trend candidates', () => {
  const sites = source('./SitesPage.jsx');
  const detail = source('./SiteMonitoringPage.jsx');
  assert.match(sites, /next\.delete\('monitoring'\)/);
  assert.match(sites, /navigate\(`\/sites\$\{listQuery \? `\?\$\{listQuery\}` : ''\}`/);
  assert.match(sites, /scrollToFirstRowOnChange: false/);
  assert.match(detail, /monitoringTrendFactors\(/);
  assert.match(detail, /const factorOptions = trendFactors\.map/);
  assert.match(detail, /options=\{factorOptions\}/);
});

test('monitoring drawer keeps the first layer light and omits diagnostics from normal UI', () => {
  const detail = source('./SiteMonitoringPage.jsx');
  const sites = source('./SitesPage.jsx');
  const api = source('../../services/api.js');
  assert.match(api, /overview\?detail=summary/);
  assert.match(api, /stationMonitoringDiagnostics:/);
  assert.match(detail, /<Drawer/);
  assert.match(detail, /mode === 'monitoring'/);
  assert.match(detail, /mode === 'settings'/);
  assert.doesNotMatch(detail, /api\.stationMonitoringDiagnostics\(siteId/);
  assert.doesNotMatch(detail, /接收与排障详情|排障详情加载失败|周期未配置|结果形成中/);
  assert.doesNotMatch(detail, /cycleByMetric/);
  assert.match(detail, /const missing = !value \|\| value\.status === 'review_required'/);
  assert.match(sites, /const resultAt = record\.last_valid_observation_at;/);
  assert.doesNotMatch(sites, /record\.last_valid_observation_at \|\| cycle\.scheduled_at/);
  assert.match(sites, /const resultMatchesCycle = Boolean\([^]*record\.last_valid_observation_at[^]*cycle\.scheduled_at[^]*dayjs\(record\.last_valid_observation_at\)\.isSame\(dayjs\(cycle\.scheduled_at\)\)/);
  assert.match(sites, /resultMatchesCycle && missingCount > 0/);
  assert.doesNotMatch(sites, /resultAt && missingCount > 0/);
});

test('access observation does not consume sensitive endpoint identity fields', () => {
  const access = source('./StationAccessPage.jsx');
  assert.doesNotMatch(access, /station_code|credential_hmac|raw_frame(?!s)/);
});
