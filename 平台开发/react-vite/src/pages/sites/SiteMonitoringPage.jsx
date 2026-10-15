import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Card, Checkbox, Col, Descriptions, Empty, List, Row, Select, Space, Spin, Tag, Typography,
} from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../services/api';
import EChart from '../../components/EChart';
import {
  AXIS_META, axisView, capabilityLabel, communicationStatusView, cycleStatusView, formatMonitoringTime, monitoringCoverageLabel, monitoringStatusView,
  monitoringDefaultTrendMetric, monitoringFactorName, monitoringTrendChartOption, monitoringTrendFactors, monitoringTrendView,
} from './stationMonitoring';
import './SiteMonitoringPage.css';

const { Title, Text } = Typography;

function expectedFactorSaveKey(siteId) {
  const randomPart = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `station-expected-factors-${siteId}-${randomPart}`;
}

function AxisCard({ axis }) {
  return (
    <div role="group" aria-label={axis.label} style={{ border: '1px solid rgba(127, 127, 127, 0.24)', borderRadius: 6, padding: 12, minHeight: 112 }}>
      <Space direction="vertical" size={4}>
        <Text strong>{axis.label}</Text>
        <Badge status={axis.badgeStatus} text={axis.stateLabel} />
        {axis.lastRecordAt && <Text type="secondary">{axis.key === 'data' ? '最近正式观测' : '最近记录'}：{formatMonitoringTime(axis.lastRecordAt)}</Text>}
        {axis.nextExpectedAt && <Text type="secondary">下次应到：{formatMonitoringTime(axis.nextExpectedAt)}</Text>}
        {axis.reason && <Text type="secondary">{axis.reason}</Text>}
        {!axis.state && <Text type="secondary">服务端未提供该分轴事实</Text>}
      </Space>
    </div>
  );
}

function LatestValues({ values }) {
  if (!Array.isArray(values) || values.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已形成的有效观测" />;
  }
  return (
    <List
      size="small"
      dataSource={values}
      renderItem={(item) => (
        <List.Item>
          <Space direction="vertical" size={0}>
            <Text strong>{monitoringFactorName(item)}</Text>
            <Text>{item.standard_value ?? '暂无数值'} {item.standard_unit || ''}</Text>
          </Space>
          <Text type="secondary">{formatMonitoringTime(item.observed_at)}</Text>
        </List.Item>
      )}
    />
  );
}

function LatestCycle({ cycle }) {
  const view = cycleStatusView(cycle);
  const factors = Array.isArray(cycle?.factors) ? cycle.factors : [];
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Descriptions size="small" column={{ xs: 1, sm: 2, md: 4 }} items={[
        { key: 'status', label: '周期状态', children: <Tag color={view.color}>{view.label}</Tag> },
        { key: 'slot', label: '检测时点', children: formatMonitoringTime(cycle?.scheduled_at) },
        { key: 'deadline', label: '缺测截止', children: formatMonitoringTime(cycle?.deadline_at) },
        { key: 'count', label: '完整度', children: `${cycle?.formed_count || 0}/${cycle?.expected_count || 0}` },
        { key: 'last-complete', label: '最近完整时点（辅助）', children: formatMonitoringTime(cycle?.latest_complete_cycle_at) },
      ]} />
      {factors.length ? <List size="small" dataSource={factors} renderItem={(item) => {
        const labels = { value: '已形成', pending: '形成中', missing: '缺测', review_required: '待核对' };
        return <List.Item>
          <Space direction="vertical" size={0}>
            <Text strong>{monitoringFactorName(item)}</Text>
            <Text type="secondary">{labels[item.status] || item.status || '待确认'}{item.reason ? `：${item.reason}` : ''}</Text>
          </Space>
          <Text>{item.status === 'value' ? `${item.value} ${item.standard_unit || ''}` : '—'}</Text>
        </List.Item>;
      }} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无检测周期配置" />}
    </Space>
  );
}

export default function SiteMonitoringPage() {
  const { siteId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [selectedMetric, setSelectedMetric] = useState('');
  const [trendData, setTrendData] = useState(null);
  const [trendError, setTrendError] = useState('');
  const [trendLoading, setTrendLoading] = useState(false);
  const [expectedConfig, setExpectedConfig] = useState(null);
  const [expectedDraft, setExpectedDraft] = useState([]);
  const [expectedError, setExpectedError] = useState('');
  const [expectedNotice, setExpectedNotice] = useState('');
  const [expectedLoading, setExpectedLoading] = useState(true);
  const [expectedSaving, setExpectedSaving] = useState(false);
  const requestRef = useRef({ id: 0, controller: null });
  const trendRequestRef = useRef({ id: 0, controller: null, metric: '' });
  const expectedRequestRef = useRef({ id: 0, controller: null });
  const expectedSiteIdRef = useRef(null);
  const expectedSaveKeyRef = useRef({ signature: '', key: '' });
  const loadedSiteIdRef = useRef(null);

  const loadExpectedFactors = useCallback(async () => {
    expectedRequestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = expectedRequestRef.current.id + 1;
    expectedRequestRef.current = { id: requestId, controller };
    if (expectedSiteIdRef.current !== siteId) {
      setExpectedConfig(null);
      setExpectedDraft([]);
    }
    setExpectedLoading(true);
    setExpectedError('');
    setExpectedNotice('');
    try {
      const next = await api.stationExpectedFactors(siteId, { signal: controller.signal });
      if (expectedRequestRef.current.id !== requestId) return;
      if (String(next?.site_id) !== String(siteId) || !Array.isArray(next?.candidates)) {
        throw new Error('服务端返回的应监测因子配置与当前站点不匹配，请重试');
      }
      expectedSiteIdRef.current = siteId;
      expectedSaveKeyRef.current = { signature: '', key: '' };
      setExpectedConfig(next);
      setExpectedDraft(Array.isArray(next.selected_metrics) ? next.selected_metrics : []);
    } catch (err) {
      if (expectedRequestRef.current.id !== requestId || err?.code === 'REQUEST_ABORTED') return;
      setExpectedError(err?.message || '应监测因子配置加载失败');
    } finally {
      if (expectedRequestRef.current.id === requestId) setExpectedLoading(false);
    }
  }, [siteId]);

  const load = useCallback(async (options = {}) => {
    const expectedFactorVersion = options?.expectedFactorVersion ?? null;
    requestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = requestRef.current.id + 1;
    requestRef.current = { id: requestId, controller };
    if (loadedSiteIdRef.current !== siteId) setData(null);
    setLoading(true);
    setError('');
    try {
      const next = await api.stationMonitoringOverview(siteId, { signal: controller.signal });
      if (requestRef.current.id !== requestId) return;
      if (!next?.site || String(next.site.id) !== String(siteId)) {
        throw new Error('服务端返回的站点监测资料与当前站点不匹配，请重试');
      }
      if (expectedFactorVersion != null
          && Number(next.site.expected_factor_version) !== Number(expectedFactorVersion)) {
        throw new Error('应监测因子已保存，但监测状态尚未同步到最新版本，请重试');
      }
      loadedSiteIdRef.current = siteId;
      setData(next);
      return next;
    } catch (err) {
      if (requestRef.current.id !== requestId || err?.code === 'REQUEST_ABORTED') return;
      setError(err?.message || '站点监测信息加载失败');
      return null;
    } finally {
      if (requestRef.current.id === requestId) setLoading(false);
    }
  }, [siteId]);

  useEffect(() => {
    load();
    loadExpectedFactors();
    return () => {
      requestRef.current.id += 1;
      requestRef.current.controller?.abort();
      trendRequestRef.current.id += 1;
      trendRequestRef.current.controller?.abort();
      expectedRequestRef.current.id += 1;
      expectedRequestRef.current.controller?.abort();
    };
  }, [load, loadExpectedFactors]);

  const loadTrend = useCallback(async (metric) => {
    trendRequestRef.current.controller?.abort();
    if (!metric) {
      setTrendData(null);
      setTrendError('');
      setTrendLoading(false);
      return;
    }
    const controller = new AbortController();
    const requestId = trendRequestRef.current.id + 1;
    const keepPrevious = trendRequestRef.current.metric === metric;
    trendRequestRef.current = { id: requestId, controller, metric };
    if (!keepPrevious) setTrendData(null);
    setTrendLoading(true);
    setTrendError('');
    try {
      const next = await api.stationMonitoringTrend(siteId, metric, { signal: controller.signal });
      if (trendRequestRef.current.id !== requestId) return;
      if (String(next?.site_id) !== String(siteId) || next?.metric !== metric) {
        throw new Error('服务端返回的趋势与当前站点或因子不匹配，请重试');
      }
      setTrendData(next);
    } catch (err) {
      if (trendRequestRef.current.id !== requestId || err?.code === 'REQUEST_ABORTED') return;
      setTrendError(err?.message || '趋势加载失败');
    } finally {
      if (trendRequestRef.current.id === requestId) setTrendLoading(false);
    }
  }, [siteId]);

  useEffect(() => {
    loadTrend(selectedMetric);
  }, [loadTrend, selectedMetric]);

  const overviewFactors = useMemo(() => {
    const payload = data?.factors || data?.monitoring?.factors;
    return Array.isArray(payload) ? payload : [];
  }, [data]);
  const trendFactors = useMemo(() => monitoringTrendFactors(
    overviewFactors,
    expectedConfig?.selected_metrics,
    expectedConfig?.configured === true,
  ), [expectedConfig, overviewFactors]);
  const latestValues = useMemo(() => (
    Array.isArray(data?.monitoring?.latest_values) ? data.monitoring.latest_values : []
  ), [data]);

  useEffect(() => {
    setSelectedMetric((current) => (
      trendFactors.some((item) => item.business_metric === current)
        ? current : monitoringDefaultTrendMetric(trendFactors, latestValues)
    ));
  }, [latestValues, trendFactors]);

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
      expectedSaveKeyRef.current = {
        signature,
        key: expectedFactorSaveKey(siteId),
      };
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
      const refreshed = await load({
        expectedFactorVersion: next.version,
      });
      setExpectedConfig(next);
      setExpectedDraft(Array.isArray(next.selected_metrics) ? next.selected_metrics : metrics);
      if (!refreshed) {
        setExpectedError('因子配置已保存，但监测状态刷新失败；当前勾选已保留，请重试页面刷新。');
        return;
      }
      setExpectedNotice(next.replayed ? '已确认此前保存结果，无需重复写入。' : '应监测因子已保存，列表与详情已按新分母更新。');
    } catch (err) {
      if (err?.code === 'EXPECTED_FACTORS_VERSION_CONFLICT') {
        setExpectedError('配置已被其他操作更新；当前勾选已保留，请先刷新配置再确认。');
      } else {
        setExpectedError(err?.message || '保存失败，当前勾选已保留，可直接重试');
      }
    } finally {
      setExpectedSaving(false);
    }
  }, [expectedConfig, expectedDraft, load, siteId]);

  const visibleData = data && String(data.site?.id) === String(siteId) ? data : null;

  if (loading && !visibleData) return <div style={{ padding: 40, textAlign: 'center' }}><Spin size="large" /></div>;
  if (error && !visibleData) {
    return <Alert type="error" showIcon message="站点监测加载失败" description={error} action={<Button aria-label="重试" icon={<ReloadOutlined />} onClick={load}>重试</Button>} style={{ margin: 24 }} />;
  }
  if (!visibleData) return <Empty description="暂无站点监测资料" style={{ margin: 40 }} />;

  const site = visibleData.site || {};
  const monitoring = visibleData.monitoring || {};
  const status = monitoringStatusView(site);
  const communication = communicationStatusView(monitoring.communication || site.communication);
  const latestCycle = monitoring.latest_cycle || site.latest_cycle || {};
  const axesPayload = visibleData.axes || monitoring.axes || {};
  const axes = Object.keys(AXIS_META).map((key) => axisView(key, axesPayload[key] || {}));
  const capabilities = visibleData.capabilities || monitoring.capabilities || {};
  const trend = monitoringTrendView(trendData, { loading: trendLoading, error: trendError });
  const savedExpectedMetrics = Array.isArray(expectedConfig?.selected_metrics)
    ? expectedConfig.selected_metrics : [];
  const expectedChanged = JSON.stringify(expectedDraft) !== JSON.stringify(savedExpectedMetrics);
  const returnQuery = searchParams.toString();
  const returnPath = `/sites${returnQuery ? `?${returnQuery}` : ''}`;
  const archiveParams = new URLSearchParams(searchParams);
  archiveParams.set('archive', siteId);

  return (
    <div className="workspace-page site-monitoring-page" style={{ padding: 24 }}>
      <div className="site-monitoring-page__scroll" role="region" aria-label="站点监测正文" tabIndex={0}>
      <Space direction="vertical" size={18} style={{ width: '100%' }}>
        <Space align="start" wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(returnPath)}>返回站点目录</Button>
          <Button onClick={() => navigate(`/sites?${archiveParams.toString()}`)}>站点档案</Button>
          <Button aria-label="刷新" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          <div>
            <Space size={8} wrap>
              <Title level={3} style={{ margin: 0 }}>{site.name || '未命名站点'}</Title>
              <Tag color={status.color}>{status.label}</Tag>
            </Space>
            <Text type="secondary">{site.code || '未提供站点编码'}</Text>
          </div>
        </Space>

        {error && <Alert type="warning" showIcon message="刷新失败，当前保留上次成功结果" description={error} action={<Button aria-label="重新加载" size="small" loading={loading} icon={<ReloadOutlined />} onClick={load}>重新加载</Button>} />}
        {status.contractMissing && <Alert type="warning" showIcon message="监测状态暂不可确认" description="服务端尚未返回 monitoring_status 契约字段，页面不会用旧站点状态推断监测结果。" />}

        <Card title="可信状态">
          <Descriptions
            column={{ xs: 1, sm: 2, md: 3 }}
            items={[
              { key: 'status', label: '监测状态', children: <Tag color={status.color}>{status.label}</Tag> },
              { key: 'communication-status', label: '数据接收', children: <Tag color={communication.color}>{communication.label}</Tag> },
              { key: 'reason', label: '主原因', children: status.reason },
              { key: 'communication', label: '最后收到报文', children: formatMonitoringTime(site.last_received_at || site.last_communication_at) },
              { key: 'observation', label: '最后有效观测', children: formatMonitoringTime(site.last_valid_observation_at) },
            ]}
          />
        </Card>

        <Card title="最近检测周期" extra={<Tag color={cycleStatusView(latestCycle).color}>{cycleStatusView(latestCycle).label}</Tag>}>
          <LatestCycle cycle={latestCycle} />
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <Card title="各因子最近可用值" extra={<Tag>{capabilityLabel(capabilities.latest)}</Tag>}>
              <Alert type="info" showIcon message="各项可能来自不同检测周期，不代表同一次完整检测" style={{ marginBottom: 12 }} />
              <LatestValues values={monitoring.latest_values} />
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card title="最近24小时趋势" extra={<Select aria-label="趋势因子" value={selectedMetric || undefined} placeholder="选择监测因子" onChange={setSelectedMetric} options={trendFactors.map((item) => ({ value: item.business_metric, label: `${monitoringFactorName(item)}${item.standard_unit ? ` (${item.standard_unit})` : ''}` }))} style={{ minWidth: 180 }} />}>
              {trend.loading && !trendData ? <div className="site-monitoring-trend__loading"><Spin /></div> : null}
              {trend.error ? <Alert type="warning" showIcon message="趋势加载失败" description={trend.error} action={<Button size="small" icon={<ReloadOutlined />} onClick={() => loadTrend(selectedMetric)}>重试</Button>} /> : null}
              {!trend.loading && !trend.error && !trend.available ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={trend.emptyReason} /> : null}
              {trend.available ? <>
                <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
                  { key: 'window', label: '时间窗口', children: `${formatMonitoringTime(trend.windowStart)} 至 ${formatMonitoringTime(trend.windowEnd)}` },
                  { key: 'unit', label: '单位', children: trend.unit || '单位待确认' },
                  { key: 'coverage', label: '覆盖率', children: monitoringCoverageLabel(trend) },
                  { key: 'gaps', label: '缺口', children: trend.gapCount == null ? '周期未配置' : `${trend.gapCount} 段，缺 ${trend.missingPoints} 点` },
                  { key: 'slot-state', label: '时点状态', children: `可疑 ${trend.suspectPoints}，迟到 ${trend.latePoints}，冲突 ${trend.conflictSlots}，重复记录 ${trend.duplicateRecords}` },
                ]} />
                <EChart aria-label="监测趋势图" option={monitoringTrendChartOption(trend)} className="site-monitoring-trend__chart" />
              </> : null}
            </Card>
          </Col>
        </Row>

        <Card title="数据事实">
          <Row gutter={[12, 12]}>{axes.map((axis) => <Col xs={24} sm={12} key={axis.key}><AxisCard axis={axis} /></Col>)}</Row>
        </Card>

        <Card
          title="应监测因子"
          extra={expectedConfig?.configured
            ? <Text type="secondary">版本 {expectedConfig.version} · {formatMonitoringTime(expectedConfig.effective_from)} 生效</Text>
            : null}
        >
          {expectedLoading && !expectedConfig ? <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div> : null}
          {expectedError ? <Alert
            type="warning"
            showIcon
            message="应监测因子未保存"
            description={expectedError}
            action={<Button size="small" onClick={loadExpectedFactors}>刷新配置</Button>}
            style={{ marginBottom: 12 }}
          /> : null}
          {expectedNotice ? <Alert type="success" showIcon message={expectedNotice} style={{ marginBottom: 12 }} /> : null}
          {expectedConfig && !expectedConfig.configured ? <Alert
            type="warning"
            showIcon
            message="应监测因子未设置"
            description="未设置前不会按协议映射自动判为正常；请按本站实际业务范围至少选择一项。"
            style={{ marginBottom: 12 }}
          /> : null}
          {expectedConfig ? <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Checkbox.Group
              value={expectedDraft}
              disabled={!expectedConfig.can_edit || expectedSaving}
              onChange={(metrics) => {
                setExpectedDraft(metrics);
                setExpectedNotice('');
              }}
              style={{ width: '100%' }}
            >
              <Row gutter={[12, 12]}>
                {expectedConfig.candidates.map((candidate) => <Col xs={24} sm={12} lg={8} key={candidate.business_metric}>
                  <Checkbox value={candidate.business_metric}>
                    {candidate.label}
                    {!candidate.mapping_available ? <Tag color="gold" style={{ marginLeft: 6 }}>当前无映射</Tag> : null}
                  </Checkbox>
                </Col>)}
              </Row>
            </Checkbox.Group>
            {!expectedConfig.can_edit ? <Text type="secondary">当前账号仅可查看，不能修改本站设置。</Text> : null}
            {expectedConfig.can_edit ? <Space wrap>
              <Button
                type="primary"
                loading={expectedSaving}
                disabled={!expectedChanged || expectedDraft.length === 0}
                onClick={saveExpectedFactors}
              >保存设置</Button>
              {expectedDraft.length === 0 ? <Text type="danger">至少选择一项应监测因子</Text> : null}
              <Text type="secondary">未勾选项不进入当前完整性分母；历史正式结果不会删除。</Text>
            </Space> : null}
          </Space> : null}
        </Card>
      </Space>
      </div>
    </div>
  );
}
