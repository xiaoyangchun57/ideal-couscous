import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Empty, Row, Space, Spin, Statistic, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../../services/api';
import { useAuth } from '../../hooks/useAuth';
import { formatMonitoringTime, hasAdminRole } from './stationMonitoring';

const { Title, Text } = Typography;

function countValue(value) {
  return value === null || value === undefined ? '暂无' : value;
}

function SummarySection({ title, description, items }) {
  return (
    <Card title={title} extra={<Text type="secondary">{description}</Text>}>
      <Row gutter={[16, 16]}>
        {items.map((item) => (
          <Col xs={12} sm={8} md={6} key={item.key}>
            <Statistic title={item.label} value={countValue(item.value)} valueStyle={{ fontSize: 22 }} />
          </Col>
        ))}
      </Row>
    </Card>
  );
}

export default function StationAccessPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = hasAdminRole(user);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const requestRef = useRef({ id: 0, controller: null });

  const load = useCallback(async () => {
    if (!isAdmin) return;
    requestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = requestRef.current.id + 1;
    requestRef.current = { id: requestId, controller };
    setLoading(true);
    setError('');
    try {
      const next = await api.stationMonitoringAccessSummary({ signal: controller.signal });
      if (requestRef.current.id !== requestId) return;
      setData(next);
    } catch (err) {
      if (requestRef.current.id !== requestId || err?.code === 'REQUEST_ABORTED') return;
      setError(err?.message || '接入摘要加载失败');
    } finally {
      if (requestRef.current.id === requestId) setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    load();
    return () => {
      requestRef.current.id += 1;
      requestRef.current.controller?.abort();
    };
  }, [load]);

  if (!isAdmin) {
    return <Alert type="error" showIcon message="无权访问监测接入观察" description="接入治理摘要仅对管理员开放。" style={{ margin: 24 }} />;
  }
  if (loading && !data) return <div style={{ padding: 40, textAlign: 'center' }}><Spin size="large" /></div>;
  if (error && !data) {
    return <Alert type="error" showIcon message="接入摘要加载失败" description={error} action={<Button aria-label="重试" icon={<ReloadOutlined />} onClick={load}>重试</Button>} style={{ margin: 24 }} />;
  }
  if (!data) return <Empty description="暂无接入观察摘要" style={{ margin: 40 }} />;

  const runtime = data.runtime || {};
  const identity = data.identity || {};
  const profile = data.profile || data.configuration || {};
  const observation = data.observation || data.observations || {};
  const quality = data.quality || {};
  const storage = data.storage || {};

  return (
    <div className="workspace-page" style={{ padding: 24 }}>
      <Space direction="vertical" size={18} style={{ width: '100%' }}>
        <Space align="start" wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/sites')}>返回站点目录</Button>
          <Button aria-label="刷新" icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          <div><Title level={3} style={{ margin: 0 }}>监测数据接入观察</Title><Text type="secondary">运行、配置、质量与存储概览</Text></div>
          <Tag color="blue">管理员</Tag>
        </Space>
        {error && <Alert type="warning" showIcon message="刷新失败，当前保留上次成功结果" description={error} action={<Button aria-label="重新加载" size="small" loading={loading} icon={<ReloadOutlined />} onClick={load}>重新加载</Button>} />}

        <SummarySection
          title="身份与运行"
          description="可信身份与认证原文到达情况"
          items={[
            { key: 'enabled', label: '启用端点', value: runtime.enabled_endpoints },
            { key: 'bound', label: '已绑定站点', value: identity.bound_identities ?? identity.bound_sites ?? runtime.bound_sites },
            { key: 'received', label: '收到认证原文', value: identity.authenticated_frame_endpoints ?? identity.received_raw_sites },
          ]}
        />
        <SummarySection
          title="监测档案"
          description="逐站监测配置摘要"
          items={[
            { key: 'profiles', label: '已配置站点', value: profile.configured_profiles ?? profile.configured_sites ?? profile.profiles },
            { key: 'factors', label: '已批准因子', value: profile.approved_factors },
            { key: 'intervals', label: '已配置周期', value: profile.configured_intervals },
          ]}
        />
        <SummarySection
          title="有效观测"
          description="只统计服务端确认的有效事实"
          items={[
            { key: 'sites', label: '形成观测站点', value: observation.sites_with_values ?? observation.valid_sites ?? observation.sites },
            { key: 'batches', label: '当前观测批次', value: observation.observation_batches },
            { key: 'values', label: '有效值数量', value: observation.valid_values },
          ]}
        />
        <SummarySection
          title="质量与存储"
          description="原文留存和开放质量事项"
          items={[
            { key: 'quality', label: '开放质量事项', value: quality.open_items },
            { key: 'raw', label: '留存原文', value: storage.raw_frames },
            { key: 'stored_batches', label: '留存观测批次', value: storage.observation_batches },
          ]}
        />
        <Card size="small">
          <Descriptions column={1} items={[{ key: 'updated', label: '摘要更新时间', children: data.updated_at ? formatMonitoringTime(data.updated_at) : '服务端未提供' }]} />
        </Card>
      </Space>
    </div>
  );
}
