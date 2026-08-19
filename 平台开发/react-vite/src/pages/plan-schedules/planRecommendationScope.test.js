import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  DEFAULT_FOLLOW_UP_SCOPE, followUpRecommendationActionPayload,
  followUpRecommendationListPath, loadFollowUpRecommendations,
} from './planRecommendationScope.js';

test('systemic follow-up defaults to mine and only explicit team loads team data', () => {
  assert.equal(DEFAULT_FOLLOW_UP_SCOPE, 'mine');
  assert.equal(followUpRecommendationListPath(), '/plan-schedules/follow-up-recommendations?scope=mine');
  assert.equal(followUpRecommendationListPath('team'), '/plan-schedules/follow-up-recommendations?scope=team');
});

test('team follow-up action notifies the owner instead of creating their draft', () => {
  const item = {
    user_id: 8, site_id: 3, anomaly_type: '浊度异常',
  };
  assert.deepEqual(followUpRecommendationActionPayload('mine', item), item);
  assert.deepEqual(followUpRecommendationActionPayload('team', item), {
    ...item, action: 'notify_owner',
  });
});

test('mine to team follow-up reload uses one request and ignores the older response', async () => {
  const pending = new Map();
  const urls = [];
  const apiClient = {
    getStrict(url) {
      urls.push(url);
      return new Promise(resolve => pending.set(url, resolve));
    },
  };
  let currentRequest = 1;
  const mine = loadFollowUpRecommendations(apiClient, 'mine', () => currentRequest === 1);
  currentRequest = 2;
  const team = loadFollowUpRecommendations(apiClient, 'team', () => currentRequest === 2);

  assert.deepEqual(urls, [
    '/plan-schedules/follow-up-recommendations?scope=mine',
    '/plan-schedules/follow-up-recommendations?scope=team',
  ]);
  pending.get(urls[1])({ recommendations: [{ id: 'team-follow-up' }] });
  assert.deepEqual(await team, {
    followUpRecommendations: [{ id: 'team-follow-up' }],
    error: '',
  });
  pending.get(urls[0])({ recommendations: [{ id: 'stale-mine-follow-up' }] });
  assert.equal(await mine, null);
});

test('page only loads systemic follow-up while edit item callback stays scope-independent', async () => {
  const source = await readFile(new URL('./PlanSchedulesPage.jsx', import.meta.url), 'utf8');
  const helper = await readFile(new URL('./planRecommendationScope.js', import.meta.url), 'utf8');
  assert.match(source, /const loadFollowUps = useCallback\([\s\S]*?\n {2}}, \[followUpScope\]\);/);
  assert.match(source, /const updateEditSiteItems = useCallback\([\s\S]*?\n {2}}, \[\]\);/);
  assert.doesNotMatch(source, /draftRecommendations|draft-recommendations|生成待确认草稿|到期巡检建议/);
  assert.doesNotMatch(helper, /draft-recommendations|draftRecommendations|recommendationSiteSummary/);
  assert.match(source, /follow-up-recommendations|系统性异常复查/);
});
