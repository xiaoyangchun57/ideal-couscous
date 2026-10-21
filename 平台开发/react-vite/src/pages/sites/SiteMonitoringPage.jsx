import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Checkbox, DatePicker, Drawer, Empty, Modal, Radio, Select, Space, Spin, Tag, Typography,
} from 'antd';
import {
  ArrowsAltOutlined, FileSearchOutlined, ReloadOutlined, SettingOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { api } from '../../services/api';
import EChart from '../../components/EChart';
import {
  communicationStatusView, formatMonitoringTime, mergeMonitoringTrendPages,
  monitoringDefaultTrendMetric, monitoringFactorName, monitoringTrendChartOption,
  monitoringTrendFactors, monitoringTrendView,
} from './stationMonitoring';
import './SiteMonitoringPage.css';

const { Text, Title } = Typography;
const TREND_PAGE_SIZE = 100;

function expectedFactorSaveKey(siteId) {
  const randomPart = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `station-expected-factors-${siteId}-${randomPart}`;
}

function trendRequestOptions(rangeKey, customRange, cursor) {
  const options = { range: rangeKey === 'custom' ? '7d' : rangeKey, limit: TREND_PAGE_SIZE };
  if (rangeKey === 'custom' && customRange?.[0] && customRange?.[1]) {
    options.start = customRange[0].startOf('day').toISOString();
    options.end = customRange[1].endOf('day').toISOString();
  }
  if (cursor) options.cursor = cursor;
  return options;
}

function LatestResults({ factors, latestValues, latestCycle, selectedMetric, onSelectMetric }) {
  const valueByMetric = new Map(latestValues.map((item) => [item.business_metric, item]));
  const resultTime = latestValues[0]?.observed_at || latestCycle?.scheduled_at || null;

  if (!latestValues.length) {
    return (
      <section className="station-monitoring-section" aria-labelledby="latest-results-title">
        <div className="station-monitoring-section__heading">
          <Title id="latest-results-title" level={4}>最新结果</Title>
          <Text className="station-monitoring-fact">结果时间：暂无结果</Text>
        </div>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前站点暂无可展示的有效结果" />
      </section>
    );
  }

  return (
    <section className="station-monitoring-section" aria-labelledby="latest-results-title">
      <div className="station-monitoring-section__heading">
        <Title id="latest-results-title" level={4}>最新结果</Title>
        <Text className="station-monitoring-fact">结果时间：{formatMonitoringTime(resultTime)}</Text>
      </div>
      <div className="station-monitoring-metrics">
        {factors.map((factor) => {
          const metric = factor.business_metric;
          const value = valueByMetric.get(metric);
          const missing = !value || value.status === 'review_required';
          return (
            <button
              type="button"
              key={metric}
              className={`station-monitoring-metric${selectedMetric === metric ? ' is-selected' : ''}`}
              onClick={() => onSelectMetric(metric)}
            >
              <span className="station-monitoring-metric__name">{monitoringFactorName(factor)}</span>
              <span className={`station-monitoring-metric__value${missing ? ' is-missing' : ''}`}>
                {missing ? '缺失' : (value.standard_value_display ?? value.standard_value)}
              </span>
              <span className="station-monitoring-metric__unit">{value?.standard_unit || factor.standard_unit || ' '}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function StationMonitoringDrawer({ siteId, open, onClose, onOpenArchive }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState('monitoring');
  const [selectedMetric, setSelectedMetric] = useState('');
  const [rangeKey, setRangeKey] = useState('7d');
  const [customRange, setCustomRange] = useState(() => [dayjs().subtract(7, 'day'), dayjs()]);
  const [trendData, setTrendData] = useState(null);
  const [trendError, setTrendError] = useState('');
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendExpanded, setTrendExpanded] = useState(false);
  const [expectedConfig, setExpectedConfig] = useState(null);
  const [expectedDraft, setExpectedDraft] = useState([]);
  const [expectedError, setExpectedError] = useState('');
  const [expectedErrorTitle, setExpectedErrorTitle] = useState('监测因子设置加载失败');
  const [expectedOutcomeUncertain, setExpectedOutcomeUncertain] = useState(false);
  const [expectedRetrySave, setExpectedRetrySave] = useState(false);
  const [expectedNotice, setExpectedNotice] = useState('');
  const [expectedLoading, setExpectedLoading] = useState(false);
  const [expectedSaving, setExpectedSaving] = useState(false);
  const requestRef = useRef({ id: 0, controller: null });
  const trendRequestRef = useRef({ id: 0, controller: null, signature: '' });
  const expectedRequestRef = useRef({ id: 0, controller: null });
  const expectedSaveKeyRef = useRef({ signature: '', key: '' });

  const loadOverview = useCallback(async (options = {}) => {
    if (!siteId) return null;
    requestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = requestRef.current.id + 1;
    requestRef.current = { id: requestId, controller };
    setLoading(true);
    setError('');
    try {
      const next = await api.stationMonitoringOverview(siteId, { signal: controller.signal });
      if (requestRef.current.id !== requestId) return null;
      if (!next?.site || String(next.site.id) !== String(siteId)) {
        throw new Error('服务端返回的站点监测资料与当前站点不匹配，请重试');
      }
      if (options.expectedFactorVersion != null
          && Number(next.site.expected_factor_version) !== Number(options.expectedFactorVersion)) {
        throw new Error('监测因子已保存，但结果范围尚未同步到最新版本，请重试刷新');
      }
      setData(next);
      return next;
    } catch (loadError) {
      if (requestRef.current.id !== requestId || loadError?.code === 'REQUEST_ABORTED') return null;
      setError(loadError?.message || '站点监测信息加载失败');
      return null;
    } finally {
      if (requestRef.current.id === requestId) setLoading(false);
    }
  }, [siteId]);

  const loadExpectedFactors = useCallback(async () => {
    if (!siteId) return;
    expectedRequestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = expectedRequestRef.current.id + 1;
    expectedRequestRef.current = { id: requestId, controller };
    setExpectedLoading(true);
    setExpectedError('');
    setExpectedErrorTitle('监测因子设置加载失败');
    setExpectedNotice('');
    try {
      const next = await api.stationExpectedFactors(siteId, { signal: controller.signal });
      if (expectedRequestRef.current.id !== requestId) return;
      if (String(next?.site_id) !== String(siteId) || !Array.isArray(next?.candidates)) {
        throw new Error('服务端返回的监测因子设置与当前站点不匹配，请重试');
      }
      setExpectedConfig(next);
      setExpectedDraft(Array.isArray(next.selected_metrics) ? next.selected_metrics : []);
      expectedSaveKeyRef.current = { signature: '', key: '' };
      setExpectedOutcomeUncertain(false);
      setExpectedRetrySave(false);
    } catch (loadError) {
      if (expectedRequestRef.current.id !== requestId || loadError?.code === 'REQUEST_ABORTED') return;
      setExpectedError(loadError?.message || '监测因子设置加载失败');
    } finally {
      if (expectedRequestRef.current.id === requestId) setExpectedLoading(false);
    }
  }, [siteId]);

  useEffect(() => {
    if (!open || !siteId) return undefined;
    setData(null);
    setMode('monitoring');
    setRangeKey('7d');
    setCustomRange([dayjs().subtract(7, 'day'), dayjs()]);
    setTrendData(null);
    setTrendError('');
    loadOverview();
    loadExpectedFactors();
    return () => {
      requestRef.current.id += 1;
      requestRef.current.controller?.abort();
      trendRequestRef.current.id += 1;
      trendRequestRef.current.controller?.abort();
      expectedRequestRef.current.id += 1;
      expectedRequestRef.current.controller?.abort();
    };
  }, [loadExpectedFactors, loadOverview, open, siteId]);

  const overviewFactors = useMemo(() => {
    const payload = data?.factors || data?.monitoring?.factors;
    return Array.isArray(payload) ? payload : [];
  }, [data]);
  const latestValues = useMemo(() => (
    Array.isArray(data?.monitoring?.latest_values) ? data.monitoring.latest_values : []
  ), [data]);
  const trendFactors = useMemo(() => monitoringTrendFactors(
    overviewFactors,
    expectedConfig?.selected_metrics,
    expectedConfig?.configured === true,
  ), [expectedConfig, overviewFactors]);

  useEffect(() => {
    setSelectedMetric((current) => (
      trendFactors.some((item) => item.business_metric === current)
        ? current : monitoringDefaultTrendMetric(trendFactors, latestValues)
    ));
  }, [latestValues, trendFactors]);

  const loadTrend = useCallback(async ({ cursor = null } = {}) => {
    if (!open || mode !== 'monitoring' || !siteId || !selectedMetric) return;
    if (rangeKey === 'custom' && (!customRange?.[0] || !customRange?.[1])) return;
    trendRequestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = trendRequestRef.current.id + 1;
    const options = trendRequestOptions(rangeKey, customRange, cursor);
    const signature = JSON.stringify({ siteId, selectedMetric, ...options, cursor: undefined });
    trendRequestRef.current = { id: requestId, controller, signature };
    setTrendLoading(true);
    setTrendError('');
    try {
      const next = await api.stationMonitoringTrend(siteId, selectedMetric, {
        ...options,
        signal: controller.signal,
      });
      if (trendRequestRef.current.id !== requestId) return;
      if (String(next?.site_id) !== String(siteId) || next?.metric !== selectedMetric) {
        throw new Error('服务端返回的趋势与当前站点或因子不匹配，请重试');
      }
      setTrendData((current) => (
        cursor && trendRequestRef.current.signature === signature
          ? mergeMonitoringTrendPages(current, next)
          : next
      ));
    } catch (loadError) {
      if (trendRequestRef.current.id !== requestId || loadError?.code === 'REQUEST_ABORTED') return;
      setTrendError(loadError?.message || '趋势加载失败');
    } finally {
      if (trendRequestRef.current.id === requestId) setTrendLoading(false);
    }
  }, [customRange, mode, open, rangeKey, selectedMetric, siteId]);

  useEffect(() => {
    setTrendData(null);
    setTrendError('');
    loadTrend();
  }, [loadTrend]);

  const saveExpectedFactors = useCallback(async () => {
    if (!expectedConfig?.can_edit || expectedDraft.length === 0) return;
    const candidateOrder = new Map((expectedConfig.candidates || []).map(
      (item, index) => [item.business_metric, index],
    ));
    const metrics = [...new Set(expectedDraft)].sort(
      (left, right) => (candidateOrder.get(left) ?? 999) - (candidateOrder.get(right) ?? 999),
    );
    const signature = JSON.stringify(metrics);
    if (expectedSaveKeyRef.current.signature !== signature) {
      expectedSaveKeyRef.current = { signature, key: expectedFactorSaveKey(siteId) };
    }
    setExpectedSaving(true);
    setExpectedError('');
    setExpectedNotice('');
    try {
      const next = await api.updateStationExpectedFactors(siteId, {
        metrics,
        version: expectedConfig.version || 0,
        idempotency_key: expectedSaveKeyRef.current.key,
      });
      if (String(next?.site_id) !== String(siteId)) {
        throw new Error('服务端返回的保存结果与当前站点不匹配，请重试');
      }
      expectedSaveKeyRef.current = { signature: '', key: '' };
      setExpectedOutcomeUncertain(false);
      setExpectedRetrySave(false);
      setExpectedConfig(next);
      setExpectedDraft(Array.isArray(next.selected_metrics) ? next.selected_metrics : metrics);
      const refreshed = await loadOverview({ expectedFactorVersion: next.version });
      if (!refreshed) {
        setExpectedErrorTitle('设置已保存，结果刷新失败');
        setExpectedError('监测因子已保存，当前选择已保留；请重试刷新结果。');
        return;
      }
      setExpectedNotice(next.replayed ? '已确认此前保存结果。' : '监测因子已保存。');
      setMode('monitoring');
    } catch (saveError) {
      if (saveError?.code === 'EXPECTED_FACTORS_VERSION_CONFLICT') {
        setExpectedOutcomeUncertain(false);
        setExpectedRetrySave(false);
        setExpectedErrorTitle('设置版本冲突');
        setExpectedError('设置已被其他操作更新；当前选择已保留，请刷新后再确认。');
      } else if (saveError?.code === 'REQUEST_TIMEOUT' || saveError?.code === 'NETWORK_ERROR') {
        setExpectedOutcomeUncertain(true);
        setExpectedRetrySave(true);
        setExpectedErrorTitle('保存结果待确认');
        setExpectedError('响应中断，设置可能已经提交。当前选择和重试标识已保留，请再次提交确认。');
      } else if (saveError?.code === 'EXPECTED_FACTORS_BUSY') {
        setExpectedOutcomeUncertain(false);
        setExpectedRetrySave(true);
        setExpectedErrorTitle('设置保存暂时繁忙');
        setExpectedError('本次尚未写入，当前选择已保留，请直接重试。');
      } else {
        setExpectedOutcomeUncertain(false);
        setExpectedRetrySave(false);
        setExpectedErrorTitle('监测因子保存失败');
        setExpectedError(saveError?.message || '保存失败，当前选择已保留，可直接重试');
      }
    } finally {
      setExpectedSaving(false);
    }
  }, [expectedConfig, expectedDraft, loadOverview, siteId]);

  const visibleData = data && String(data.site?.id) === String(siteId) ? data : null;
  const site = visibleData?.site || {};
  const monitoring = visibleData?.monitoring || {};
  const communication = communicationStatusView(monitoring.communication || site.communication);
  const latestCycle = monitoring.latest_cycle || site.latest_cycle || {};
  const trend = monitoringTrendView(trendData, { loading: trendLoading, error: trendError });
  const savedExpectedMetrics = Array.isArray(expectedConfig?.selected_metrics)
    ? expectedConfig.selected_metrics : [];
  const expectedChanged = JSON.stringify(expectedDraft) !== JSON.stringify(savedExpectedMetrics);
  const factorOptions = trendFactors.map((factor) => ({
    value: factor.business_metric,
    label: monitoringFactorName(factor),
  }));

  const trendPanel = (
    <div className="station-monitoring-trend">
      <div className="station-monitoring-trend__controls">
        <Select
          aria-label="趋势因子"
          value={selectedMetric || undefined}
          options={factorOptions}
          onChange={setSelectedMetric}
          placeholder="选择监测因子"
          style={{ minWidth: 150 }}
        />
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={rangeKey}
          onChange={(event) => setRangeKey(event.target.value)}
          options={[
            { label: '24小时', value: '24h' },
            { label: '7天', value: '7d' },
            { label: '30天', value: '30d' },
            { label: '自定义', value: 'custom' },
          ]}
        />
        {rangeKey === 'custom' ? <DatePicker.RangePicker
          value={customRange}
          allowClear={false}
          onChange={(value) => setCustomRange(value)}
          disabledDate={(current) => current && current.isAfter(dayjs(), 'day')}
        /> : null}
        <Button aria-label="放大趋势" icon={<ArrowsAltOutlined />} onClick={() => setTrendExpanded(true)} />
      </div>
      {trendError ? <Alert type="warning" showIcon message="趋势加载失败" description={trendError}
        action={<Button size="small" onClick={() => loadTrend()}>重试</Button>} /> : null}
      {trendLoading && !trend.available ? <div className="site-monitoring-trend__loading"><Spin /></div> : null}
      {!trendLoading && !trend.available && !trendError ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={trend.emptyReason} /> : null}
      {trend.available ? <>
        <EChart aria-label="监测趋势图" option={monitoringTrendChartOption(trend)} className="site-monitoring-trend__chart" />
        <div className="station-monitoring-trend__footer">
          <Text className="station-monitoring-fact">已加载 {trend.items.length} 条结果</Text>
          {trend.hasMore ? <Button loading={trendLoading} onClick={() => loadTrend({ cursor: trend.nextCursor })}>继续加载</Button> : null}
        </div>
      </> : null}
    </div>
  );

  const drawerTitle = visibleData ? (
    <div className="station-monitoring-drawer__title">
      <div>
        <Text className="station-monitoring-drawer__code">{site.code || '未提供站点编码'}</Text>
        <Title level={3}>{site.name || '未命名站点'}</Title>
        <Space wrap size={12}>
          <Tag color={communication.color}>{communication.label}</Tag>
          <Text className="station-monitoring-fact">最新接收：{formatMonitoringTime(site.last_received_at || site.last_communication_at)}</Text>
          {site.service_policy?.service_status === 'ended' ? <Tag>服务已结束</Tag> : null}
        </Space>
      </div>
      <Space wrap>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => loadOverview()}>刷新</Button>
        <Button icon={<FileSearchOutlined />} onClick={() => onOpenArchive?.(siteId)}>站点档案</Button>
        <Button icon={<SettingOutlined />} onClick={() => setMode((current) => current === 'settings' ? 'monitoring' : 'settings')}>
          {mode === 'settings' ? '返回监测' : '因子设置'}
        </Button>
      </Space>
    </div>
  ) : '站点监测';

  return (
    <>
      <Drawer
        rootClassName="station-monitoring-drawer"
        title={drawerTitle}
        placement="right"
        width="min(960px, 64vw)"
        open={open}
        onClose={onClose}
        destroyOnHidden={false}
      >
        {loading && !visibleData ? <div className="station-monitoring-drawer__loading"><Spin size="large" /></div> : null}
        {error && !visibleData ? <Alert type="error" showIcon message="站点监测加载失败" description={error}
          action={<Button icon={<ReloadOutlined />} onClick={() => loadOverview()}>重试</Button>} /> : null}
        {visibleData && mode === 'monitoring' ? <div className="station-monitoring-drawer__body">
          {expectedNotice ? <Alert type="success" showIcon message={expectedNotice} /> : null}
          {error ? <Alert type="warning" showIcon message="刷新失败，当前保留上次成功结果" description={error}
            action={<Button size="small" onClick={() => loadOverview()}>重新加载</Button>} /> : null}
          <LatestResults
            factors={trendFactors}
            latestValues={latestValues}
            latestCycle={latestCycle}
            selectedMetric={selectedMetric}
            onSelectMetric={setSelectedMetric}
          />
          <section className="station-monitoring-section" aria-labelledby="trend-title">
            <div className="station-monitoring-section__heading">
              <Title id="trend-title" level={4}>结果趋势</Title>
            </div>
            {trendPanel}
          </section>
        </div> : null}
        {visibleData && mode === 'settings' ? <section className="station-monitoring-settings" aria-labelledby="settings-title">
          <div className="station-monitoring-section__heading">
            <Title id="settings-title" level={4}>监测因子设置</Title>
            <Button onClick={() => setMode('monitoring')}>返回监测</Button>
          </div>
          {expectedLoading && !expectedConfig ? <div className="station-monitoring-drawer__loading"><Spin /></div> : null}
          {expectedError ? <Alert type="warning" showIcon message={expectedErrorTitle} description={expectedError}
            action={<Button size="small" onClick={expectedRetrySave ? saveExpectedFactors : loadExpectedFactors}>
              {expectedOutcomeUncertain ? '再次提交确认' : expectedRetrySave ? '重试保存' : '刷新设置'}
            </Button>} /> : null}
          {expectedNotice ? <Alert type="success" showIcon message={expectedNotice} /> : null}
          {expectedConfig ? <>
            <Checkbox.Group
              value={expectedDraft}
              disabled={!expectedConfig.can_edit || expectedSaving}
              onChange={(metrics) => {
                setExpectedDraft(metrics);
                setExpectedNotice('');
              }}
              className="station-monitoring-settings__grid"
            >
              {(expectedConfig.candidates || []).map((candidate) => (
                <Checkbox value={candidate.business_metric} key={candidate.business_metric}>
                  {candidate.label}
                  {!candidate.mapping_available ? <Tag color="gold">当前无映射</Tag> : null}
                </Checkbox>
              ))}
            </Checkbox.Group>
            {!expectedConfig.can_edit ? <Text className="station-monitoring-fact">当前账号仅可查看本站设置。</Text> : null}
            {expectedConfig.can_edit ? <Space wrap>
              <Button type="primary" loading={expectedSaving}
                disabled={!expectedChanged || expectedDraft.length === 0} onClick={saveExpectedFactors}>保存设置</Button>
              {expectedDraft.length === 0 ? <Text type="danger">至少选择一项监测因子</Text> : null}
              <Button onClick={() => {
                setExpectedDraft(savedExpectedMetrics);
                setExpectedError('');
                setMode('monitoring');
              }}>取消</Button>
            </Space> : null}
          </> : null}
        </section> : null}
      </Drawer>
      <Modal
        open={trendExpanded}
        onCancel={() => setTrendExpanded(false)}
        footer={null}
        width={1100}
        title={`${monitoringFactorName(trendFactors.find((item) => item.business_metric === selectedMetric) || {})}结果趋势`}
        destroyOnHidden={false}
      >
        <div className="station-monitoring-trend station-monitoring-trend--expanded">{trendPanel}</div>
      </Modal>
    </>
  );
}

export default StationMonitoringDrawer;
