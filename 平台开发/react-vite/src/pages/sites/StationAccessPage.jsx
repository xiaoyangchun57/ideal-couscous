import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Spin, Typography } from 'antd';
import { api } from '../../services/api';

export default function StationAccessPage() {
  const [data, setData] = useState(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  const load = useCallback(async () => { setLoading(true); setError(''); try { setData(await api.stationMonitoringAccessSummary()); } catch (e) { setError(e?.message || '接入摘要加载失败'); } finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  if (loading && !data) return <div style={{ padding: 32, textAlign: 'center' }}><Spin /></div>;
  return <div style={{ padding: 24 }}><Typography.Title level={3}>监测数据接入中心</Typography.Title>{error && <Alert type="error" message={error} action={<Button onClick={load}>重试</Button>} />}{data && <Card><Descriptions column={1} items={[{ key: 'runtime', label: '运行摘要', children: `启用端点 ${data.runtime?.enabled_endpoints ?? '-'}，已绑定站点 ${data.runtime?.bound_sites ?? '-'}` }, { key: 'quality', label: '质量事项', children: `开放事项 ${data.quality?.open_items ?? '-'}` }, { key: 'storage', label: '存储摘要', children: `原文 ${data.storage?.raw_frames ?? '-'}，观测批次 ${data.storage?.observation_batches ?? '-'}` }]} /></Card>}</div>;
}
