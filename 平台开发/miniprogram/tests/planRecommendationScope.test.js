const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const requestPath = require.resolve('../utils/request.js');
const apiPath = require.resolve('../services/api.js');
const { rolesForUser } = require('../utils/reviewAccess.js');
const planPagePath = require.resolve('../pages/plan/plan.js');

global.getApp = () => ({ globalData: { token: 'token' } });
global.Page = () => {};
delete require.cache[planPagePath];
const { planFilterGroup } = require(planPagePath);
delete global.getApp;
delete global.Page;

test('plan filters use execution state and keep legacy approved plans active', () => {
  for (const executionStatus of ['pending', 'partial', 'rework']) {
    assert.equal(planFilterGroup({ status: 'approved', execution_status: executionStatus }), 'active');
  }
  assert.equal(planFilterGroup({ status: 'approved', execution_status: 'completed' }), 'done');
  assert.equal(planFilterGroup({ status: 'approved', execution_status: 'partial', execution_completed: true }), 'active');
  assert.equal(planFilterGroup({ status: 'approved', execution_status: 'rework', execution_completed: true }), 'active');
  assert.equal(planFilterGroup({ status: 'approved', execution_status: 'completed', execution_completed: false }), 'done');
  assert.equal(planFilterGroup({ status: 'approved', execution_completed: true }), 'done');
  assert.equal(planFilterGroup({ status: 'approved', execution_completed: false }), 'active');
  assert.equal(planFilterGroup({ status: 'approved' }), 'active');
  assert.equal(planFilterGroup({ status: 'archived' }), 'done');
  for (const status of ['draft', 'submitted', 'rejected', 'modifying', 'change_submitted']) {
    assert.equal(planFilterGroup({ status }), 'active');
  }
});

test('only systemic follow-up APIs remain and use explicit mine/team scopes', async () => {
  const calls = [];
  require.cache[requestPath] = {
    id: requestPath, filename: requestPath, loaded: true,
    exports: { request(url, method) { calls.push({ url, method }); return Promise.resolve({ recommendations: [] }); } },
  };
  delete require.cache[apiPath];
  const api = require(apiPath);
  await api.planScheduleFollowUpRecommendations(false);
  await api.planScheduleFollowUpRecommendations(true);
  assert.equal(api.planScheduleDraftRecommendations, undefined);
  assert.equal(api.createPlanScheduleFromRecommendation, undefined);
  assert.deepEqual(calls.map(call => call.url), [
    '/api/plan-schedules/follow-up-recommendations?scope=mine',
    '/api/plan-schedules/follow-up-recommendations?scope=team',
  ]);
  delete require.cache[apiPath]; delete require.cache[requestPath];
});

test('plan page retains follow-up scope and favorites without due recommendations', () => {
  const js = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.js'), 'utf8');
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.wxml'), 'utf8');
  const wxss = fs.readFileSync(path.join(__dirname, '../pages/plan/plan.wxss'), 'utf8');
  assert.match(js, /isTeamView:\s*false/);
  assert.match(js, /followUpRecommendationsExpanded:\s*false/);
  assert.match(js, /onToggleFollowUpRecommendations[\s\S]*followUpRecommendationsExpanded:\s*!this\.data\.followUpRecommendationsExpanded/);
  assert.match(js, /onToggleRecommendationScope[\s\S]*isTeamView:\s*!this\.data\.isTeamView[\s\S]*followUpRecommendationsExpanded:\s*false/);
  assert.doesNotMatch(js, /isTeamView:\s*roles\.includes\('admin'\)/);
  assert.match(wxml, /isTeamView[\s\S]*onNotifyFollowUpOwner/);
  assert.match(wxml, /返回我的复查/);
  assert.match(wxml, /bindtap="onToggleFollowUpRecommendations"[\s\S]*wx:if="{{followUpRecommendationsExpanded}}"/);
  assert.match(wxml, /seg-strip[\s\S]*class="new-plan-entry" bindtap="onNewPlan"/);
  assert.doesNotMatch(wxml, /class="fab"/);
  assert.match(wxss, /\.new-plan-entry\s*{/);
  assert.doesNotMatch(wxss, /position:\s*fixed|\.fab\s*{/);
  assert.match(js, /filter_group:\s*planFilterGroup\(item\)/);
  assert.doesNotMatch(js, /getFilteredList/);
  assert.match(wxml, /i\.filter_group === 'active'/);
  assert.match(wxml, /i\.filter_group === 'done'/);
  assert.doesNotMatch(wxml, /var ACTIVE|var DONE/);
  assert.deepEqual(rolesForUser({ roles: [], role: 'admin' }), ['admin']);
  assert.deepEqual(rolesForUser({ roles: [], role: 'operator' }), ['operator']);
  assert.match(js, /rolesForUser\(user\)/);
  assert.doesNotMatch(js, /planScheduleDraftRecommendations|createPlanScheduleFromRecommendation|creatingRecommendationKey|recommendationsExpanded/);
  assert.doesNotMatch(wxml, /到期巡检建议|onCreateRecommendedDraft|onNotifyRecommendationOwner/);
  assert.match(wxml, /系统性异常复查|常用计划/);
});
