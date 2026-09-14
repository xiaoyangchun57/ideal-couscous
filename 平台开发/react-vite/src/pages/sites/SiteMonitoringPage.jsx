import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Empty, Space, Spin, Tag, Typography } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../../services/api';

const { Title, Text } = Typography;

export default function SiteMonitoringPage() {
  const { siteId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { setData(await api.stationMonitoringOverview(siteId)); }
    catch (err) { setError(err?.message || '站点监测信息加载失败'); }
    finally { setLoading(false); }
  }, [siteId]);
  useEffect(() => { load(); }, [load]);
  if (loading && !data) return <div style={{ padding: 32, textAlign: 'center' }}><Spin /></div>;
  if (error && !data) return <Alert type="error" showIcon message="加载失败" description={error} action={<Button onClick={load}>重试</Button>} />;
  if (!data) return <Empty description="暂无站点监测资料" />;
  const site = data.site || {};
  const monitoring = data.monitoring || {};
  return <div style={{ padding: 24 }}>
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space><Button onClick={() => navigate('/sites')}>返回站点目录</Button><Title level={3} style={{ margin: 0 }}>{site.name}</Title><Tag>{site.status_label}</Tag></Space>
      {error && <Alert type="warning" message={error} action={<Button size="small" onClick={load}>重试</Button>} />}
      <Card title="站点身份"><Descriptions column={2} items={[{ key: 'code', label: '站点编码', children: site.code || '-' }, { key: 'district', label: '所属区县', children: site.district || '-' }, { key: 'address', label: '地址', children: site.address || '-' }, { key: 'communication', label: '最后通信', children: site.last_communication_at || '-' }, { key: 'observation', label: '最后有效观测', children: site.last_valid_observation_at || '-' }, { key: 'reason', label: '状态说明', children: site.reason || '-' }]} /></Card>
      <Card title="最新有效值">{monitoring.latest_values?.length ? monitoring.latest_values.map((item) => <div key={`${item.business_metric}-${item.protocol_code}`}><Text strong>{item.business_metric}</Text>：{item.standard_value} {item.standard_unit || ''} <Text type="secondary">{item.observed_at || ''}</Text></div>) : <Text type="secondary">当前无可展示的有效观测，数值区未配置或等待首个有效观测。</Text>}</Card>
      <Card title="监测区状态"><Descriptions column={1} items={Object.entries(data.section_status || {}).map(([key, value]) => ({ key, label: key, children: value === 'ready' ? '可用' : '未配置' }))} /></Card>
    </Space>
  </div>;
}
