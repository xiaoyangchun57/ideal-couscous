import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Table, Card, Button, Space, Tag, Typography, message, Modal, Form, Select, Input, Empty, Tooltip } from 'antd';
import { PlusOutlined, ReloadOutlined, EnvironmentOutlined, FileTextOutlined, CheckOutlined, InboxOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useAuth } from '../../hooks/useAuth';
import { useTheme } from '../../hooks/useTheme';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import { reportStatusMap } from '../../services/constants';
import FilterBar from '../../components/FilterBar';
import {
  pageRootStyle, tableCardStyle, tableCardBody, filterSelectWidth,
} from '../../services/pageStyles';

const { Text, Title } = Typography;

// 上报类型为本页专用枚举（无配色，仅标签文案），保留页内定义
const REPORT_TYPE = { sensory: '感官异常', equipment: '设备异常', environment: '环境异常', violation: '违规操作', pollution: '污染事件' };

export default function ReportsPage() {
  const { user } = useAuth();
  const { tokens, isDark } = useTheme();
  const [wrapRef, bodyHeight] = useTableAutoHeight();
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
    { title: '异常', key: 'context', width: 180, ellipsis: true, render: (_, r) => <>
      <Tag color="orange">{REPORT_TYPE[r.report_type] || r.report_type || '异常'}</Tag>
      <Text strong ellipsis style={{ display: 'block', marginTop: 4 }}>{r.site_name || '未关联站点'}</Text>
    </> },
    { title: '现场描述', dataIndex: 'description', ellipsis: true },
    { title: '上报信息', key: 'reported', width: 160, render: (_, r) => <>
      <Text>{r.reporter_name || '—'}</Text>
      <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{r.reported_at || '—'}</Text>
      {r.gps_lat && <Tooltip title={`定位：${Number(r.gps_lat).toFixed(5)}, ${Number(r.gps_lng).toFixed(5)}`}><Text type="secondary" style={{ fontSize: 12 }}>已附定位</Text></Tooltip>}
    </> },
    { title: '关联工单', dataIndex: 'order_no', width: 130, render: v => v ? <Link to={`/workorders?search=${encodeURIComponent(v)}`}><Tag color="blue">{v}</Tag></Link> : <Text type="secondary">未生成</Text> },
    { title: '闭环状态', dataIndex: 'status', width: 160, render: (v, r) => {
      const s = reportStatusMap[v] || { label: v, color: 'default' };
      const progress = r.archived_at ? '已归档' : r.resolved_at ? '已解决' : r.verified_at ? '已核实' : '待处置';
      return <><Tag color={s.color}>{s.label}</Tag><Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>{progress}</Text></>;
    } },
    ...(canManage ? [{
      title: '操作', width: 100,
      render: (_, r) => <Space size={4}>
        {r.status === 'dispatched' && <Button size="small" icon={<CheckOutlined />} onClick={() => onVerify(r)}>核实</Button>}
        {r.status === 'resolved' && <Button size="small" icon={<InboxOutlined />} onClick={() => onArchive(r)}>归档</Button>}
        {!['dispatched', 'resolved'].includes(r.status) && <Text type="secondary">—</Text>}
      </Space>,
    }] : []),
  ];

  const statusSummary = useMemo(() => Object.keys(reportStatusMap).map((status) => ({
    status,
    ...reportStatusMap[status],
    count: list.filter((item) => item.status === status).length,
  })).filter((item) => item.count > 0), [list]);

  return (
    <div style={{ ...pageRootStyle, gap: 12, minWidth: 0 }}>
      <Title level={4} style={{ margin: 0, flexShrink: 0 }}>异常上报</Title>
      <FilterBar
        extra={(
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            {canCreate && <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建上报</Button>}
          </Space>
        )}
      >
        <Select value={filterStatus || undefined} onChange={v => setFilterStatus(v || '')} placeholder="全部状态" allowClear style={{ width: filterSelectWidth }}
          options={Object.entries(reportStatusMap).map(([k, v]) => ({ value: k, label: v.label }))} />
        <Select value={filterType || undefined} onChange={v => setFilterType(v || '')} placeholder="全部类型" allowClear style={{ width: filterSelectWidth }}
          options={Object.entries(REPORT_TYPE).map(([k, v]) => ({ value: k, label: v }))} />
      </FilterBar>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, minHeight: 22, flexShrink: 0 }} aria-label="异常上报状态汇总">
        {statusSummary.length ? statusSummary.map((item) => (
          <Tag key={item.status} color={item.color}>{item.label} {item.count}</Tag>
        )) : <Text type="secondary">暂无异常上报记录</Text>}
      </div>
      <Card style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
        <div ref={wrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <Table rowKey="id" columns={columns} dataSource={list} loading={loading} size="small" className="review-scroll-table"
            pagination={false}
            scroll={bodyHeight ? { y: bodyHeight } : undefined}
            locale={{ emptyText: <Empty description="暂无上报记录" /> }} />
        </div>
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
    </div>
  );
}
