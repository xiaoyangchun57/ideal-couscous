import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Card, Col, Descriptions, Empty, List, Row, Space, Spin, Tag, Typography,
} from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../../services/api';
import {
  AXIS_META, axisView, capabilityLabel, formatMonitoringTime, monitoringStatusView,
} from './stationMonitoring';

const { Title, Text } = Typography;

function AxisCard({ axis }) {
  return (
    <div style={{ border: '1px solid rgba(127, 127, 127, 0.24)', borderRadius: 6, padding: 12, minHeight: 112 }}>
      <Space direction="vertical" size={4}>
        <Text strong>{axis.label}</Text>
        <Badge status={axis.state === 'attention' ? 'warning' : axis.state === 'normal' ? 'success' : 'default'} text={axis.stateLabel} />
        {axis.lastReceivedAt && <Text type="secondary">最近记录：{formatMonitoringTime(axis.lastReceivedAt)}</Text>}
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
            <Text strong>{item.business_metric || item.protocol_factor || '未命名因子'}</Text>
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
  const requestRef = useRef({ id: 0, controller: null });
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
      loadedSiteIdRef.current = siteId;
      setData(next);
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
    };
  }, [load]);

  const visibleData = data && String(data.site?.id) === String(siteId) ? data : null;

  if (loading && !visibleData) return <div style={{ padding: 40, textAlign: 'center' }}><Spin size="large" /></div>;
  if (error && !visibleData) {
    return <Alert type="error" showIcon message="站点监测加载失败" description={error} action={<Button icon={<ReloadOutlined />} onClick={load}>重试</Button>} style={{ margin: 24 }} />;
  }
  if (!visibleData) return <Empty description="暂无站点监测资料" style={{ margin: 40 }} />;

  const site = visibleData.site || {};
  const monitoring = visibleData.monitoring || {};
  const status = monitoringStatusView(site);
  const axesPayload = visibleData.axes || monitoring.axes || {};
  const axes = Object.keys(AXIS_META).map((key) => axisView(key, axesPayload[key] || {}));
  const capabilities = visibleData.capabilities || monitoring.capabilities || {};
  const instrumentsPayload = visibleData.instruments || monitoring.instruments;
  const recentItemsPayload = visibleData.recent_items || monitoring.recent_items;
  const instruments = Array.isArray(instrumentsPayload) ? instrumentsPayload : [];
  const recentItems = Array.isArray(recentItemsPayload) ? recentItemsPayload : [];

  return (
    <div className="workspace-page" style={{ padding: 24 }}>
      <Space direction="vertical" size={18} style={{ width: '100%' }}>
        <Space align="start" wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/sites')}>返回站点目录</Button>
          <div>
            <Space size={8} wrap>
              <Title level={3} style={{ margin: 0 }}>{site.name || '未命名站点'}</Title>
              <Tag color={status.color}>{status.label}</Tag>
            </Space>
            <Text type="secondary">{site.code || '未提供站点编码'}</Text>
          </div>
        </Space>

        {error && <Alert type="warning" showIcon message="刷新失败，当前保留上次成功结果" description={error} action={<Button size="small" icon={<ReloadOutlined />} onClick={load}>重新加载</Button>} />}
        {status.contractMissing && <Alert type="warning" showIcon message="监测状态暂不可确认" description="服务端尚未返回 monitoring_status 契约字段，页面不会用旧站点状态推断监测结果。" />}

        <Card title="可信状态">
          <Descriptions
            column={{ xs: 1, sm: 2, md: 3 }}
            items={[
              { key: 'status', label: '监测状态', children: <Tag color={status.color}>{status.label}</Tag> },
              { key: 'reason', label: '主原因', children: status.reason },
              { key: 'communication', label: '最后通信', children: formatMonitoringTime(site.last_communication_at) },
              { key: 'observation', label: '最后有效观测', children: formatMonitoringTime(site.last_valid_observation_at) },
              { key: 'district', label: '所属区县', children: site.district || '暂无资料' },
              { key: 'address', label: '地址', children: site.address || '暂无资料' },
            ]}
          />
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <Card title="最新有效值" extra={<Tag>{capabilityLabel(capabilities.latest)}</Tag>}><LatestValues values={monitoring.latest_values} /></Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card title="趋势" extra={<Tag color={capabilities.trend ? 'green' : 'default'}>{capabilityLabel(capabilities.trend)}</Tag>}>
              {capabilities.trend ? (
                Array.isArray(monitoring.trend) && monitoring.trend.length > 0
                  ? <List size="small" dataSource={monitoring.trend} renderItem={(item) => <List.Item>{item.label || item.business_metric || '趋势指标'}<Text type="secondary">{item.summary || item.value || '已提供聚合事实'}</Text></List.Item>} />
                  : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="服务端已声明趋势可用，但当前未返回趋势数据" />
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务端聚合事实，趋势暂不可用" />}
            </Card>
          </Col>
        </Row>

        <Card title="监测分轴">
          <Row gutter={[12, 12]}>{axes.map((axis) => <Col xs={24} sm={12} lg={6} key={axis.key}><AxisCard axis={axis} /></Col>)}</Row>
        </Card>

        <Card title="仪器与因子">
          {instruments.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已批准的仪器或因子配置" /> : (
            <List size="small" dataSource={instruments} renderItem={(item) => (
              <List.Item>
                <Space direction="vertical" size={0}><Text strong>{item.instrument_asset_code || '未提供仪器资产编码'}</Text><Text type="secondary">{item.business_metric || item.protocol_code || '未命名因子'}</Text></Space>
                <Tag color={item.status === 'has_valid_observation' ? 'green' : 'default'}>{item.status === 'has_valid_observation' ? '已有有效观测' : '健康状态未知'}</Tag>
              </List.Item>
            )} />
          )}
        </Card>

        <Card title="近期告警、工单与巡检">
          {recentItems.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务端返回的近期事项" /> : <List size="small" dataSource={recentItems} renderItem={(item) => <List.Item><Text>{item.title || item.type || '近期事项'}</Text><Text type="secondary">{formatMonitoringTime(item.occurred_at || item.created_at)}</Text></List.Item>} />}
        </Card>
      </Space>
    </div>
  );
}
