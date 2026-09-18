import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Card, Col, Descriptions, Empty, List, Row, Select, Space, Spin, Tag, Typography,
} from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../../services/api';
import EChart from '../../components/EChart';
import {
  AXIS_META, axisView, capabilityLabel, formatMonitoringTime, monitoringCoverageLabel, monitoringStatusView,
  monitoringDefaultTrendMetric, monitoringFactorName, monitoringTrendChartOption, monitoringTrendView,
} from './stationMonitoring';
import './SiteMonitoringPage.css';

const { Title, Text } = Typography;

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

export default function SiteMonitoringPage() {
  const { siteId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [selectedMetric, setSelectedMetric] = useState('');
  const [trendData, setTrendData] = useState(null);
  const [trendError, setTrendError] = useState('');
  const [trendLoading, setTrendLoading] = useState(false);
  const requestRef = useRef({ id: 0, controller: null });
  const trendRequestRef = useRef({ id: 0, controller: null, metric: '' });
  const loadedSiteIdRef = useRef(null);

  const load = useCallback(async () => {
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
      loadedSiteIdRef.current = siteId;
      setData(next);
      const factors = Array.isArray(next.factors) ? next.factors : (next.monitoring?.factors || []);
      const latest = next.monitoring?.latest_values || [];
      setSelectedMetric((current) => (
        factors.some((item) => item.business_metric === current)
          ? current : monitoringDefaultTrendMetric(factors, latest)
      ));
    } catch (err) {
      if (requestRef.current.id !== requestId || err?.code === 'REQUEST_ABORTED') return;
      setError(err?.message || '站点监测信息加载失败');
    } finally {
      if (requestRef.current.id === requestId) setLoading(false);
    }
  }, [siteId]);

  useEffect(() => {
    load();
    return () => {
      requestRef.current.id += 1;
      requestRef.current.controller?.abort();
      trendRequestRef.current.id += 1;
      trendRequestRef.current.controller?.abort();
    };
  }, [load]);

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

  const visibleData = data && String(data.site?.id) === String(siteId) ? data : null;

  if (loading && !visibleData) return <div style={{ padding: 40, textAlign: 'center' }}><Spin size="large" /></div>;
  if (error && !visibleData) {
    return <Alert type="error" showIcon message="站点监测加载失败" description={error} action={<Button aria-label="重试" icon={<ReloadOutlined />} onClick={load}>重试</Button>} style={{ margin: 24 }} />;
  }
  if (!visibleData) return <Empty description="暂无站点监测资料" style={{ margin: 40 }} />;

  const site = visibleData.site || {};
  const monitoring = visibleData.monitoring || {};
  const status = monitoringStatusView(site);
  const axesPayload = visibleData.axes || monitoring.axes || {};
  const axes = Object.keys(AXIS_META).map((key) => axisView(key, axesPayload[key] || {}));
  const capabilities = visibleData.capabilities || monitoring.capabilities || {};
  const factorsPayload = visibleData.factors || monitoring.factors;
  const factors = Array.isArray(factorsPayload) ? factorsPayload : [];
  const trend = monitoringTrendView(trendData, { loading: trendLoading, error: trendError });

  return (
    <div className="workspace-page site-monitoring-page" style={{ padding: 24 }}>
      <div className="site-monitoring-page__scroll" role="region" aria-label="站点监测正文" tabIndex={0}>
      <Space direction="vertical" size={18} style={{ width: '100%' }}>
        <Space align="start" wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/sites')}>返回站点目录</Button>
          <Button onClick={() => navigate(`/sites?archive=${encodeURIComponent(siteId)}`)}>站点档案</Button>
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
              { key: 'reason', label: '主原因', children: status.reason },
              { key: 'communication', label: '最后收到报文', children: formatMonitoringTime(site.last_received_at || site.last_communication_at) },
              { key: 'observation', label: '最后有效观测', children: formatMonitoringTime(site.last_valid_observation_at) },
            ]}
          />
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <Card title="最新有效值" extra={<Tag>{capabilityLabel(capabilities.latest)}</Tag>}><LatestValues values={monitoring.latest_values} /></Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card title="最近24小时趋势" extra={<Select aria-label="趋势因子" value={selectedMetric || undefined} placeholder="选择监测因子" onChange={setSelectedMetric} options={factors.filter((item) => item.business_metric).map((item) => ({ value: item.business_metric, label: `${monitoringFactorName(item)}${item.standard_unit ? ` (${item.standard_unit})` : ''}` }))} style={{ minWidth: 180 }} />}>
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

        <Card title="监测因子">
          {factors.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已批准的监测因子" /> :
            <List size="small" dataSource={factors} renderItem={(item) => <List.Item>
              <Text strong>{monitoringFactorName(item)}</Text><Text type="secondary">{item.standard_unit || '未配置单位'}</Text>
            </List.Item>} />}
        </Card>
      </Space>
      </div>
    </div>
  );
}
