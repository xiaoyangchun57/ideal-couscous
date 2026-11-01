import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('web navigation exposes the confirmed read pages without opening admin-only modules', () => {
  const navigation = source('./config/navigation.jsx');
  assert.match(navigation, /'\/workorders': \['admin', 'operator', 'reviewer'\]/);
  assert.match(navigation, /'\/plan-schedules': \['admin', 'operator', 'reviewer'\]/);
  assert.match(navigation, /'\/archive': \['admin', 'reviewer', 'operator'\]/);
  assert.match(navigation, /'\/alerts': \['admin', 'reviewer', 'operator'\]/);
  assert.match(navigation, /'\/analysis': \['admin', 'reviewer', 'operator'\]/);
  assert.match(navigation, /'\/reports': \['admin', 'reviewer', 'operator'\]/);
  assert.match(navigation, /'\/vehicles': \['admin', 'operator'\]/);
  assert.match(navigation, /'\/equipment': \['admin'\]/);
  assert.match(navigation, /'\/users': \['admin'\]/);
});

test('operator alert entry stays read-only and does not expose rule configuration', () => {
  const alerts = source('./pages/alerts/AlertsPage.jsx');
  assert.match(alerts, /const canManage = roles\.includes\('admin'\)/);
  assert.match(alerts, /ALERT_TABS\.filter\(\(tab\) => tab\.key !== 'rules' && tab\.key !== 'thresholds'\)/);
  assert.match(alerts, /const scope = canViewAllSites \? 'all' : 'mine'/);
  assert.match(alerts, /rowSelection=\{canManage \? rowSelection : undefined\}/);
});

test('reviewer work-order and plan pages keep write controls separated', () => {
  const workorders = source('./pages/workorders/WorkOrdersPage.jsx');
  const plans = source('./pages/plan-schedules/PlanSchedulesPage.jsx');
  assert.match(workorders, /const canCreateWorkorder = userRoles\.some\(\(role\) => role === 'admin' \|\| role === 'operator'\)/);
  assert.match(workorders, /\{canCreateWorkorder && <Button/);
  assert.match(workorders, />新建工单<\/Button>/);
  assert.match(plans, /const canApprove = userRoles\.some\(role => role === 'admin' \|\| role === 'manager'\)/);
  assert.match(plans, /const canViewTeam = userRoles\.some\(role => role === 'admin' \|\| role === 'manager' \|\| role === 'reviewer'\)/);
  assert.match(plans, /const canUseFollowUps = userRoles\.some\(role => role === 'admin' \|\| role === 'operator'\)/);
  assert.match(plans, /if \(!canUseFollowUps\) \{[\s\S]*setFollowUpRecommendations\(\[\]\);[\s\S]*setFollowUpError\(''\);[\s\S]*return;/);
});

test('archive download selection remains independent from purge selection', () => {
  const archive = source('./pages/archive/ArchivePage.jsx');
  assert.match(archive, /const \[selectedIds, setSelectedIds\] = useState\(\[\]\)/);
  assert.match(archive, /const \[downloadIds, setDownloadIds\] = useState\(\[\]\)/);
  assert.match(archive, /attachment_ids: \[\.\.\.selectedIds\]/);
  assert.match(archive, /requestDirectDownload\(\[\.\.\.downloadIds\]\)/);
});
