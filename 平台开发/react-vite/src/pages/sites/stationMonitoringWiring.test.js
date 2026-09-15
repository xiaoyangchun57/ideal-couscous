import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8');

test('monitoring routes use exact role keys and keep the access center admin-only', () => {
  const app = source('../../App.jsx');
  const navigation = source('../../config/navigation.jsx');

  assert.match(app, /PageRoute path="\/sites\/data-access"/);
  assert.match(app, /PageRoute path="\/sites"[^]*SiteMonitoringPage/);
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

test('access observation does not consume sensitive endpoint identity fields', () => {
  const access = source('./StationAccessPage.jsx');
  assert.doesNotMatch(access, /station_code|credential_hmac|raw_frame(?!s)/);
});
