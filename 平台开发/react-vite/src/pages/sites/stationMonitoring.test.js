import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AXIS_META,
  MONITORING_STATUS_META,
  axisView,
  communicationStatusView,
  cycleStatusView,
  hasAdminRole,
  mergeMonitoringSites,
  mergeMonitoringTrendPages,
  monitoringStatusView,
  monitoringSummaryItems,
  monitoringDefaultTrendMetric,
  monitoringTrendFactors,
  monitoringCoverageLabel,
  monitoringTrendChartOption,
  monitoringTrendView,
  monitoringFactorName,
  monitoringResultSummary,
  formatMonitoringTime,
  clearSiteDirectorySnapshots,
  readSiteDirectorySnapshot,
  siteDirectoryLoadMode,
  stationDirectorySnapshotKey,
  stationServicePolicyView,
  validateMonitoringDirectoryPayload,
  writeSiteDirectorySnapshot,
} from './stationMonitoring.js';

test('monitoring status metadata covers the eight product states', () => {
  assert.deepEqual(Object.keys(MONITORING_STATUS_META), [
    'not_connected',
    'awaiting_first_frame',
    'raw_received_config_pending',
    'waiting_first_valid',
    'interval_unconfigured',
    'normal',
    'attention',
    'data_unavailable',
  ]);
  assert.deepEqual(Object.keys(AXIS_META), ['communication', 'data']);
  assert.equal(MONITORING_STATUS_META.raw_received_config_pending.label, '应监测因子未设置');
});

test('monitoring status never falls back to the legacy station status', () => {
  const view = monitoringStatusView({ status: 'normal', status_label: '旧状态' });
  assert.equal(view.contractMissing, true);
  assert.equal(view.label, '监测状态待确认');
});

test('monitoring merge preserves the legacy site status and retains last success on refresh failure', () => {
  const legacy = [{ id: 7, name: '一号站', status: 'maintenance' }];
  const first = mergeMonitoringSites(legacy, {
    items: [{ id: 7, monitoring_status: 'normal', monitoring_reason: '有效观测在周期内' }],
  });
  assert.equal(first[0].status, 'maintenance');
  assert.equal(first[0].monitoring_status, 'normal');

  const retained = mergeMonitoringSites(legacy, null, first);
  assert.equal(retained[0].monitoring_status, 'normal');
  assert.equal(retained[0].monitoring_reason, '有效观测在周期内');
});

test('public monitoring can add current-service stations without widening their management fields', () => {
  const merged = mergeMonitoringSites(
    [{ id: 7, name: '本人站点', private_management_field: '仅本人可见' }],
    { items: [
      { site_id: 7, name: '本人站点', monitoring_status: 'normal' },
      { site_id: 8, name: '公共监测站点', monitoring_status: 'attention' },
    ] },
  );
  assert.deepEqual(merged.map((item) => item.id), [7, 8]);
  assert.equal(merged[0].private_management_field, '仅本人可见');
  assert.equal(merged[1].private_management_field, undefined);
  assert.equal(merged[1].monitoring_status, 'attention');
});

test('monitoring directory rejects scope total and site-set mismatches', () => {
  const sites = [{ id: 7 }, { id: 8 }];
  const valid = {
    scope: 'all', available_scopes: ['mine', 'all'],
    service_scope: 'current', available_service_scopes: ['current', 'all'],
    items: [{ site_id: 7 }, { site_id: 8 }], summary: { total: 2 },
  };
  assert.equal(validateMonitoringDirectoryPayload(
    sites, valid, { scope: 'all', serviceScope: 'current' }).ok, true);
  assert.equal(validateMonitoringDirectoryPayload(
    sites, { ...valid, service_scope: 'all' },
    { scope: 'all', serviceScope: 'current' }).ok, false);
  assert.equal(validateMonitoringDirectoryPayload(
    sites, { ...valid, summary: { total: 36 } },
    { scope: 'all', serviceScope: 'current' }).ok, false);
  assert.equal(validateMonitoringDirectoryPayload(
    sites, { ...valid, items: [{ site_id: 7 }, { site_id: 9 }] },
    { scope: 'all', serviceScope: 'current' }).ok, false);
  assert.equal(validateMonitoringDirectoryPayload(
    [{ id: 7 }], valid,
    { scope: 'all', serviceScope: 'current', allowMonitoringSuperset: true }).ok, true);
  assert.equal(validateMonitoringDirectoryPayload(
    [{ id: 7 }, { id: 9 }], valid,
    { scope: 'all', serviceScope: 'current', allowMonitoringSuperset: true }).ok, false);
});

test('summary and axes expose explicit unavailable and unknown states', () => {
  const summary = monitoringSummaryItems({ data_unavailable: 2 });
  assert.equal(summary.find((item) => item.key === 'data_unavailable').value, 2);
  assert.equal(axisView('communication', { state: 'reported' }).stateLabel, '已上报');
  assert.equal(axisView('data', { state: 'interval_unconfigured' }).stateLabel, '数据周期未配置');
  assert.equal(axisView('data', { state: 'waiting_first_valid' }).stateLabel, '等待首个有效观测');
});

test('admin detection evaluates the complete role set', () => {
  assert.equal(hasAdminRole({ role: 'reviewer', roles: ['reviewer', 'admin'] }), true);
  assert.equal(hasAdminRole({ role: 'admin', roles: ['reviewer'] }), false);
  assert.equal(hasAdminRole({ role: 'admin' }), true);
  assert.equal(hasAdminRole({ role: 'operator' }), false);
});

test('every server status renders its independent label and reason', () => {
  for (const [key, meta] of Object.entries(MONITORING_STATUS_META)) {
    const view = monitoringStatusView({
      monitoring_status: key, monitoring_reason: `服务端原因:${key}`, status: 'offline',
    });
    assert.equal(view.label, meta.label);
    assert.equal(view.reason, `服务端原因:${key}`);
    assert.equal(view.contractMissing, false);
  }
});

test('a successful response missing a site cannot reuse its previous monitoring facts', () => {
  const legacy = [{ id: 7, status: 'maintenance', last_communication_at: '旧通信时间' }];
  const previous = [{ id: 7, monitoring_status: 'normal', monitoring_reason: '旧原因' }];
  const next = mergeMonitoringSites(legacy, { items: [] }, previous);
  assert.equal(next[0].status, 'maintenance');
  assert.equal(next[0].monitoring_status, null);
  assert.equal(next[0].monitoring_reason, undefined);
  assert.equal(next[0].last_communication_at, undefined);
});

test('scope removal never restores previous rows on refresh failure', () => {
  const previous = [{ id: 7, monitoring_status: 'normal' }, { id: 8, monitoring_status: 'normal' }];
  assert.deepEqual(mergeMonitoringSites([], null, previous), []);
  assert.deepEqual(mergeMonitoringSites([{ id: 7 }], null, previous).map((item) => item.id), [7]);
});

test('trend view preserves the server window, hourly sampling metadata and zero values', () => {
  assert.equal(monitoringTrendView(null).available, false);
  const available = monitoringTrendView({
    factor_name_cn: '酸碱度', standard_unit: 'pH',
    points: [{ scheduled_at: '2026-09-18T00:00:00+00:00', observed_at: '2026-09-18T00:00:00+00:00', value: 0 }],
    coverage: {
      aggregation_source: 'hourly_sample',
      window_start: '2026-09-18T00:00:00+00:00', window_end: '2026-09-18T01:00:00+00:00',
      coverage_rate: 1, valid_points: 6, displayed_points: 6, expected_points: 6, gap_count: 0, missing_points: 0,
      late_points: 1, suspect_points: 4, duplicate_records: 2, conflict_slots: 3,
    },
    pagination: { has_more: true, next_cursor: 'older-page' },
  });
  assert.equal(available.available, true);
  assert.equal(available.items[0].value, 0);
  assert.equal(available.unit, 'pH');
  assert.equal(available.coverageRate, 1);
  assert.equal(available.validPoints, 6);
  assert.equal(available.gapCount, 0);
  assert.equal(available.latePoints, 1);
  assert.equal(available.suspectPoints, 4);
  assert.equal(available.duplicateRecords, 2);
  assert.equal(available.conflictSlots, 3);
  assert.equal(available.hasMore, true);
  assert.equal(available.nextCursor, 'older-page');
  assert.deepEqual(monitoringTrendChartOption(available).series[0].data, [
    ['2026-09-18T00:00:00+00:00', 0],
  ]);
  const suspectOnly = monitoringTrendView({
    points: [{ scheduled_at: '2026-09-18T00:00:00+00:00', value: 7.1, quality: 'suspect' }],
    coverage: {
      coverage_rate: 0, valid_points: 0, displayed_points: 1, expected_points: 6,
      suspect_points: 1,
    },
  });
  assert.equal(monitoringCoverageLabel(available), '小时取样值');
  assert.equal(monitoringCoverageLabel(suspectOnly), '按测量时间展示');
  assert.equal(suspectOnly.suspectPoints, 1);
});

test('trend page merge prepends older results, removes boundary duplicates and keeps the next cursor', () => {
  const merged = mergeMonitoringTrendPages({
    site_id: 7,
    metric: 'ph',
    points: [
      { business_metric: 'ph', observed_at: '2026-10-20T08:00:00+00:00', endpoint_id: 1, value: 7.2 },
      { business_metric: 'ph', observed_at: '2026-10-20T12:00:00+00:00', endpoint_id: 1, value: 7.3 },
    ],
    pagination: { has_more: true, next_cursor: 'page-2' },
  }, {
    site_id: 7,
    metric: 'ph',
    points: [
      { business_metric: 'ph', observed_at: '2026-10-20T04:00:00+00:00', endpoint_id: 1, value: 7.1 },
      { business_metric: 'ph', observed_at: '2026-10-20T08:00:00+00:00', endpoint_id: 1, value: 7.2 },
    ],
    pagination: { has_more: false, next_cursor: null },
  });
  assert.deepEqual(merged.points.map((item) => item.observed_at), [
    '2026-10-20T04:00:00+00:00',
    '2026-10-20T08:00:00+00:00',
    '2026-10-20T12:00:00+00:00',
  ]);
  assert.deepEqual(merged.pagination, { has_more: false, next_cursor: null });
});

test('site directory snapshot survives page re-entry but is isolated by session identity roles capabilities and scope', () => {
  clearSiteDirectorySnapshots();
  const user = {
    id: 7, username: 'operator-a', roles: ['reviewer', 'operator'],
    capabilities: { station_monitoring_public: true, archive_read: true },
  };
  const key = stationDirectorySnapshotKey(user, 'mine', 'session-a');
  writeSiteDirectorySnapshot(key, {
    ready: true, rows: [{ id: 9, name: '缓存站点' }], summary: { total: 1 },
    scopeFacts: { scope: 'mine', availableScopes: ['mine'] },
  });
  const restored = readSiteDirectorySnapshot(key);
  assert.equal(siteDirectoryLoadMode(restored), 'refresh');
  assert.deepEqual(restored.rows.map((item) => item.id), [9]);
  assert.equal(readSiteDirectorySnapshot(stationDirectorySnapshotKey(user, 'all', 'session-a')), null);
  assert.equal(readSiteDirectorySnapshot(stationDirectorySnapshotKey(
    user, 'mine', 'session-a', 'all')),
  null);
  assert.equal(readSiteDirectorySnapshot(stationDirectorySnapshotKey(
    { ...user, roles: ['operator'] }, 'mine', 'session-a')),
  null);
  assert.equal(readSiteDirectorySnapshot(stationDirectorySnapshotKey(
    { ...user, capabilities: { station_monitoring_public: false, archive_read: true } },
    'mine', 'session-a')),
  null);
  assert.equal(readSiteDirectorySnapshot(stationDirectorySnapshotKey(user, 'mine', 'session-b')), null);
  assert.equal(siteDirectoryLoadMode(null), 'initial');
  clearSiteDirectorySnapshots();
});

test('communication remains independent while hourly trend gaps stay null', () => {
  assert.equal(communicationStatusView({ status: 'normal' }).label, '接收正常');
  assert.equal(cycleStatusView({ status: 'incomplete' }).label, '按因子展示');
  assert.equal(cycleStatusView({
    status: 'not_configured', reason_code: 'expected_factors_unconfigured',
  }).label, '应监测因子未设置');
  assert.equal(cycleStatusView({
    status: 'not_configured', reason_code: 'time_configuration_missing',
  }).label, '时间配置未完成');
  assert.equal(cycleStatusView({
    status: 'not_configured', reason_code: 'time_configuration_missing',
  }).description, '不影响已有有效测量值展示');
  assert.equal(cycleStatusView({
    status: 'not_configured', reason_code: 'no_evaluable_period',
  }).label, '暂无可评价时段');
  const trend = monitoringTrendView({
    points: [
      { sample_hour: '2026-10-09T00:00:00+08:00', observed_at: '2026-10-09T00:15:00+08:00', status: 'value', value: 7.1 },
      { sample_hour: '2026-10-09T02:00:00+08:00', observed_at: '2026-10-09T02:20:00+08:00', status: 'value', value: 7.2 },
    ],
    coverage: {},
  });
  assert.deepEqual(monitoringTrendChartOption(trend).series[0].data, [
    ['2026-10-09T00:15:00+08:00', 7.1],
    ['2026-10-08T17:00:00.000Z', null],
    ['2026-10-09T02:20:00+08:00', 7.2],
  ]);
  assert.equal(monitoringTrendChartOption(trend).series[0].connectNulls, false);
});

test('cycle compatibility metadata no longer exposes completeness or source priority', () => {
  const realtimeFactor = {
    business_metric: 'ph', status: 'value', source_type: 'realtime_observation',
  };
  const formalFactor = {
    business_metric: 'ammonia', status: 'value', source_type: 'hj212_2061_avg',
  };
  const noPeriod = cycleStatusView({
    status: 'complete', reason_code: 'no_evaluable_period', factors: [realtimeFactor],
  });
  assert.equal(noPeriod.label, '按因子展示');
  assert.equal(noPeriod.description, undefined);

  const realtimeSlot = cycleStatusView({
    status: 'complete', scheduled_at: '2026-10-30T16:00:00+08:00',
    factors: [realtimeFactor, { ...realtimeFactor, business_metric: 'turbidity' }],
  });
  assert.equal(realtimeSlot.label, '按因子展示');
  assert.equal(realtimeSlot.description, undefined);

  const mixed = cycleStatusView({
    status: 'complete', factors: [formalFactor, realtimeFactor],
  });
  assert.equal(mixed.label, '按因子展示');
  assert.equal(mixed.description, undefined);

  assert.equal(cycleStatusView({
    status: 'complete', factors: [formalFactor, {
      ...formalFactor, business_metric: 'codmn', source_type: 'scheduled_protocol_observation',
    }],
  }).label, '按因子展示');
  assert.equal(cycleStatusView({ status: 'pending', factors: [realtimeFactor] }).label, '按因子展示');
  assert.equal(cycleStatusView({ status: 'incomplete', factors: [realtimeFactor] }).label, '按因子展示');
});

test('service policy keeps service membership separate from reporting conditions', () => {
  const noUpload = stationServicePolicyView({
    service_status: 'active',
    reporting_condition: 'no_upload_condition',
  });
  assert.equal(noUpload.service.label, '在服务');
  assert.equal(noUpload.reporting.label, '暂无上传条件');
  assert.equal(noUpload.showReportingCondition, true);
  assert.match(noUpload.notice.description, /不作为当前催报依据/);

  const normal = stationServicePolicyView({
    service_status: 'active',
    reporting_condition: 'normal',
  });
  assert.equal(normal.showReportingCondition, false);
  assert.equal(normal.notice, null);

  const ended = stationServicePolicyView({
    service_status: 'ended',
    reporting_condition: 'no_upload_condition',
  });
  assert.equal(ended.service.label, '服务结束');
  assert.equal(ended.showReportingCondition, false);
  assert.equal(ended.notice, null);
});

test('result summary separates new reception, historical values, and no-upload policy', () => {
  const historical = monitoringResultSummary({
    latestValues: [{
      business_metric: 'ph', standard_value: 7.1,
      observed_at: '2026-10-30T08:00:00+08:00', received_at: '2026-10-30T08:01:00+08:00',
    }],
    latestCycle: { status: 'pending' },
    lastReceivedAt: '2026-11-02T08:01:00+08:00',
  });
  assert.equal(historical.cycleLabel, '当前显示历史值');
  assert.match(historical.notice.message, /新报文/);
  assert.match(historical.notice.description, /当前有效值对应的接收时间/);

  const noValue = monitoringResultSummary({
    latestValues: [], latestCycle: { status: 'pending' },
    lastReceivedAt: '2026-11-02T08:01:00+08:00',
  });
  assert.equal(noValue.cycleLabel, '尚无有效测量值');
  assert.match(noValue.emptyDescription, /尚无/);

  const noUpload = monitoringResultSummary({
    latestValues: [], latestCycle: { status: 'pending' },
    reportingCondition: 'no_upload_condition',
    lastReceivedAt: null,
  });
  assert.equal(noUpload.cycleLabel, '');
  assert.equal(noUpload.notice, null);
  assert.match(noUpload.emptyDescription, /不要求上传/);
});

test('trend tooltip preserves both measurement and reception time', () => {
  const option = monitoringTrendChartOption({
    unit: 'mg/L', factorName: '氨氮', items: [{
      observed_at: '2026-11-02T08:00:00+08:00',
      received_at: '2026-11-02T08:01:00+08:00', value: 0.3, status: 'value',
    }],
  });
  assert.deepEqual(option.series[0].data[0], [
    '2026-11-02T08:00:00+08:00', 0.3, '2026-11-02T08:01:00+08:00',
  ]);
  const tooltip = option.tooltip.formatter([{ data: option.series[0].data[0] }]);
  assert.match(tooltip, /测量时间/);
  assert.match(tooltip, /接收时间/);
});

test('observation axis exposes the next authoritative business slot', () => {
  const view = axisView('data', {
    state: 'fresh', last_valid_observation_at: '2026-09-18T00:00:00+00:00',
    next_expected_at: '2026-09-18T04:00:00+00:00',
  });
  assert.equal(view.lastRecordAt, '2026-09-18T00:00:00+00:00');
  assert.equal(view.nextExpectedAt, '2026-09-18T04:00:00+00:00');
});

test('trend defaults to a configured factor with a latest valid value', () => {
  const factors = [{ business_metric: 'ph' }, { business_metric: 'ammonia' }];
  assert.equal(monitoringDefaultTrendMetric(factors, [{ business_metric: 'ammonia' }]), 'ammonia');
  assert.equal(monitoringDefaultTrendMetric(factors, []), 'ph');
  assert.equal(monitoringDefaultTrendMetric([], [{ business_metric: 'ph' }]), '');
});

test('trend factors stay within the nine station metrics and honor the saved site subset', () => {
  const factors = [
    { business_metric: 'total_tin' },
    { business_metric: 'ph' },
    { business_metric: 'dissolved_oxygen' },
    { business_metric: 'total_lead' },
    { business_metric: 'ammonia' },
    { business_metric: 'ph' },
  ];
  assert.deepEqual(
    monitoringTrendFactors(factors).map((item) => item.business_metric),
    ['ph', 'dissolved_oxygen', 'ammonia'],
  );
  assert.deepEqual(
    monitoringTrendFactors(factors, ['dissolved_oxygen', 'ammonia'], true)
      .map((item) => item.business_metric),
    ['dissolved_oxygen', 'ammonia'],
  );
});

test('data reception and observation axes preserve server labels and status visual semantics', () => {
  for (const key of Object.keys(AXIS_META)) {
    for (const [status, badge] of [['normal', 'success'], ['attention', 'warning'], ['missing', 'default'], ['unavailable', 'error']]) {
      const view = axisView(key, { status, status_label: `服务端自定义:${key}:${status}`, state: 'unknown' });
      assert.equal(view.label, AXIS_META[key]);
      assert.equal(view.state, status);
      assert.equal(view.stateLabel, `服务端自定义:${key}:${status}`);
      assert.equal(view.badgeStatus, badge);
    }
  }
});

test('known data fact states are never collapsed to unknown or device health', () => {
  for (const [status, label, badge] of [
    ['fresh', '在配置周期内', 'success'], ['stale', '超出配置周期', 'warning'],
    ['has_valid_observation', '已有有效观测', 'default'], ['no_valid_observation', '暂无有效观测', 'default'],
  ]) {
    const view = axisView('data', { state: status });
    assert.equal(view.stateLabel, label);
    assert.equal(view.badgeStatus, badge);
  }
  assert.equal(axisView('data').stateLabel, '暂无分轴事实');
  assert.equal(axisView('data', { status: 'server_new_status' }).stateLabel, '服务端未提供状态名称');
});

test('Chinese factor names precede business display names and internal metric identifiers', () => {
  assert.equal(monitoringFactorName({ factor_name_cn: '酸碱度', business_name: '业务名称', business_metric: 'ph_internal' }), '酸碱度');
  assert.equal(monitoringFactorName({ business_name: '业务名称', label: '业务标签', business_metric: 'ph_internal' }), '业务名称');
  assert.equal(monitoringFactorName({ label: '业务标签', business_metric: 'ph_internal' }), '业务标签');
  assert.equal(monitoringFactorName({ business_metric: 'ph_internal' }), 'ph_internal');
});

test('monitoring timestamps render as local user-facing time instead of ISO source text', () => {
  const source = '2026-09-16T06:51:11+00:00';
  const formatted = formatMonitoringTime(source);
  assert.doesNotMatch(formatted, /T|\+00:00|Z$/);
  assert.match(formatted, /2026/);
});
