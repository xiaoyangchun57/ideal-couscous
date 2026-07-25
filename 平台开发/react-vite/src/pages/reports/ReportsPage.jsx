import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Table, Card, Button, Space, Tag, Typography, message, Modal, Form, Select, Input, Empty } from 'antd';
import { PlusOutlined, ReloadOutlined, EnvironmentOutlined, FileTextOutlined, CheckOutlined, InboxOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useAuth } from '../../hooks/useAuth';

const { Text, Title } = Typography;

const REPORT_TYPE = { sensory: '感官异常', equipment: '设备异常', environment: '环境异常', violation: '违规操作', pollution: '污染事件' };
const STATUS_MAP = { open: { label: '待处置', color: 'orange' }, dispatched: { label: '已派单', color: 'blue' }, verified: { label: '已核实', color: 'cyan' }, resolved: { label: '已解决', color: 'green' }, archived: { label: '已归档', color: 'default' } };

export default function ReportsPage() {
  const { user } = useAuth();
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sites, setSites] = useState([]);
  const [filterStatus, setFilterStatus] = useState('');
  const [filterType, setFilterType] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const canCreate = ['admin', 'manager', 'operator'].includes(user?.role);
  const canManage = ['admin', 'manager'].includes(user?.role);

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set('status', filterStatus);
      if (filterType) params.set('report_type', filterType);
      const data = await api.get('/manual-reports?' + params.toString()) || [];
      setList(data);
    } catch (e) { message.error('加载失败：' + e.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); api.get('/sites').then(s => setSites(Array.isArray(s) ? s : [])); }, [filterStatus, filterType]);

  const onCreate = async () => {
    try {
      const v = await form.validateFields();
      const v2 = { ...v, photo_urls: v.photo_urls ? v.photo_urls.split('\n').filter(Boolean) : [] };
      await api.post('/manual-reports', v2);
      message.success('上报成功，已自动生成工单');
      setCreateOpen(false);
      form.resetFields();
      load();
    } catch (e) { message.error('提交失败：' + e.message); }
  };

  const onVerify = async (record) => {
    try {
      await api.post(`/manual-reports/${record.id}/verify`, {});
      message.success('上报已核实，关联工单继续处置');
      load();
    } catch (e) { message.error(`核实失败：${e.message}`); }
  };

  const onArchive = async (record) => {
    try {
      await api.post(`/manual-reports/${record.id}/archive`, {});
      message.success('上报已归档');
      load();
    } catch (e) { message.error(`归档失败：${e.message}`); }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '类型', dataIndex: 'report_type', width: 90, render: v => <Tag color="orange">{REPORT_TYPE[v] || v}</Tag> },
    { title: '站点', dataIndex: 'site_name', width: 120 },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '位置', width: 110, render: (_, r) => r.gps_lat ? <Text code style={{fontSize:11}}>{Number(r.gps_lat).toFixed(3)},{Number(r.gps_lng).toFixed(3)}</Text> : '-' },
    { title: '工单', dataIndex: 'order_no', width: 150, render: v => v ? <Link to={`/workorders?search=${encodeURIComponent(v)}`}><Tag color="blue">{v}</Tag></Link> : '-' },
    { title: '上报人', dataIndex: 'reporter_name', width: 100 },
    { title: '时间', dataIndex: 'reported_at', width: 160, render: v => <Text style={{fontSize:11}}>{v}</Text> },
    { title: '状态', dataIndex: 'status', width: 90, render: v => { const s = STATUS_MAP[v] || {label:v,color:'default'}; return <Tag color={s.color}>{s.label}</Tag>; } },
    { title: '闭环', width: 150, render: (_, r) => <Space direction="vertical" size={0} style={{ fontSize: 11 }}>
      <Text type="secondary">上报 {r.reported_at || '—'}</Text>
      {r.verified_at && <Text type="secondary">核实 {r.verified_at}</Text>}
      {r.resolved_at && <Text type="secondary">解决 {r.resolved_at}</Text>}
      {r.archived_at && <Text type="secondary">归档 {r.archived_at}</Text>}
    </Space> },
    ...(canManage ? [{
      title: '操作', width: 170, fixed: 'right',
      render: (_, r) => <Space size={4}>
        {r.status === 'dispatched' && <Button size="small" icon={<CheckOutlined />} onClick={() => onVerify(r)}>核实</Button>}
        {r.status === 'resolved' && <Button size="small" icon={<InboxOutlined />} onClick={() => onArchive(r)}>归档</Button>}
        {!['dispatched', 'resolved'].includes(r.status) && <Text type="secondary">—</Text>}
      </Space>,
    }] : []),
  ];

  const statusSummary = useMemo(() => Object.keys(STATUS_MAP).map((status) => ({
    status,
    ...STATUS_MAP[status],
    count: list.filter((item) => item.status === status).length,
  })).filter((item) => item.count > 0), [list]);

  return (
    <div className="reports-page">
      <div className="reports-header">
        <Title level={4} style={{ margin: 0 }}>异常上报</Title>
        <Space className="reports-actions" wrap>
          <Select value={filterStatus || undefined} onChange={v => setFilterStatus(v || '')} placeholder="全部状态" allowClear style={{ width: 130 }}
            options={Object.entries(STATUS_MAP).map(([k, v]) => ({ value: k, label: v.label }))} />
          <Select value={filterType || undefined} onChange={v => setFilterType(v || '')} placeholder="全部类型" allowClear style={{ width: 130 }}
            options={Object.entries(REPORT_TYPE).map(([k, v]) => ({ value: k, label: v }))} />
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          {canCreate && <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建上报</Button>}
        </Space>
      </div>
      <div className="reports-summary" aria-label="异常上报状态汇总">
        {statusSummary.length ? statusSummary.map((item) => (
          <Tag key={item.status} color={item.color}>{item.label} {item.count}</Tag>
        )) : <Text type="secondary">暂无异常上报记录</Text>}
      </div>
      <Card bodyStyle={{ padding: 0 }}>
        <Table rowKey="id" columns={columns} dataSource={list} loading={loading} size="small" scroll={{ x: 1080 }} className="review-scroll-table"
          pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
          locale={{ emptyText: <Empty description="暂无上报记录" /> }} />
      </Card>
      <Modal open={createOpen} title="新建异常上报" onCancel={() => setCreateOpen(false)} onOk={onCreate} okText="提交" cancelText="取消" width={520} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ report_type: 'sensory' }}>
          <Form.Item name="report_type" label="类型" rules={[{ required: true }]}>
            <Select options={Object.entries(REPORT_TYPE).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="site_id" label="关联站点" rules={[{ required: true, message: '请选择关联站点' }]}>
            <Select showSearch optionFilterProp="label" allowClear placeholder="可选" options={sites.map(s => ({ value: s.id, label: s.name }))} />
          </Form.Item>
          <Form.Item name="description" label="现场描述" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="请详细描述异常情况" />
          </Form.Item>
          <Form.Item name="photo_urls" label="照片链接（每行一个）">
            <Input.TextArea rows={2} placeholder="https://..." />
          </Form.Item>
        </Form>
      </Modal>
      <style>{`
        .reports-page { padding: 24px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
        .reports-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
        .reports-actions { justify-content: flex-end; }
        .reports-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; min-height: 22px; }
        @media (max-width: 639px) {
          .reports-page { padding: 16px 12px; }
          .reports-header { align-items: flex-start; }
          .reports-actions { width: 100%; justify-content: flex-start; }
        }
      `}</style>
    </div>
  );
}
