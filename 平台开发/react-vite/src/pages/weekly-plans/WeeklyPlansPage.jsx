import { useState, useEffect } from 'react';
import { Table, Card, Button, Space, Tag, Typography, message, Modal, Form, Select, Input, Empty, Statistic, Row, Col } from 'antd';
import { PlusOutlined, ReloadOutlined, CalendarOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';

const { Text, Title } = Typography;

const STATUS_MAP = { draft: { label: '草稿', color: 'default' }, submitted: { label: '已提交', color: 'blue' }, approved: { label: '已批准', color: 'green' }, archived: { label: '已归档', color: 'default' } };

export default function WeeklyPlansPage() {
  const { themeConfig } = useTheme();
  const { user } = useAuth();
  const reviewerId = user?.id || 1;
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(false);
  const [users, setUsers] = useState([]);
  const [sites, setSites] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [form] = Form.useForm();
  const [createOpen, setCreateOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try { setList((await api.get('/weekly-plans')) || []); }
    catch (e) { message.error('加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    load();
    api.get('/users').then(u => setUsers(Array.isArray(u) ? u : [])).catch(() => {});
    api.get('/sites').then(s => setSites(Array.isArray(s) ? s : [])).catch(() => {});
    api.get('/vehicles').then(v => setVehicles(Array.isArray(v) ? v : [])).catch(() => {});
  }, []);

  const onCreate = async () => {
    const v = await form.validateFields();
    try {
      // plan_data 简单把每天的站点打包成 JSON
      const data = { ...v, plan_data: { 周一: [], 周二: [], 周三: [], 周四: [], 周五: [], 周六: [], 周日: [] } };
      await api.post('/weekly-plans', data);
      message.success('周计划已保存（提交时自动生成用车申请）');
      setCreateOpen(false); form.resetFields(); load();
    } catch (e) { message.error('失败：' + e.message); }
  };
  const onApprove = async (id, action) => {
    try { await api.post(`/weekly-plans/${id}/approve`, { action, approver_id: reviewerId }); message.success('已' + (action==='approve'?'批准':'驳回')); load(); }
    catch (e) { message.error('失败：' + e.message); }
  };

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={4} style={{ margin: 0 }}>周巡检计划</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建周计划</Button>
        </Space>
      </div>
      <Card bodyStyle={{ padding: 0, height: 'calc(100vh - 220px)' }} style={{ flex: 1 }}>
        <div style={{ height: '100%', overflow: 'auto', scrollbarWidth: 'none' }}>
          <Table rowKey="id" dataSource={list} loading={loading} size="small" pagination={false} scroll={{ y: 'calc(100vh - 280px)' }} className="review-scroll-table"
            columns={[
              { title: '巡检人', dataIndex: 'user_name', width: 100 },
              { title: '周开始', dataIndex: 'week_start', width: 110, render: v => <Text code style={{fontSize:11}}>{v}</Text> },
              { title: '状态', dataIndex: 'status', width: 90, render: v => { const s = STATUS_MAP[v] || {label:v,color:'default'}; return <Tag color={s.color}>{s.label}</Tag>; } },
              { title: '提交时间', dataIndex: 'submitted_at', width: 160, render: v => <Text style={{fontSize:11}}>{v || '-'}</Text> },
              { title: '审批人', dataIndex: 'approver_id', width: 80 },
              { title: '备注', dataIndex: 'remarks', ellipsis: true },
              { title: '操作', width: 160, render: (_, r) => r.status === 'submitted' ? (
                <Space>
                  <Button size="small" type="primary" onClick={() => onApprove(r.id, 'approve')}>批准</Button>
                  <Button size="small" danger onClick={() => onApprove(r.id, 'reject')}>驳回</Button>
                </Space>
              ) : <Text type="secondary">-</Text> },
            ]}
            locale={{ emptyText: <Empty description="暂无周计划" /> }} />
        </div>
      </Card>
      <Modal open={createOpen} onCancel={() => setCreateOpen(false)} onOk={onCreate} title="新建周计划" okText="保存草稿" cancelText="取消" width={520} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="user_id" label="巡检人" rules={[{ required: true }]} initialValue={1}>
            <Select options={users.map(u => ({ value: u.id, label: u.real_name || u.username }))} />
          </Form.Item>
          <Form.Item name="week_start" label="周开始日期（周一）" rules={[{ required: true }]}><Input placeholder="2026-07-13" /></Form.Item>
          <Form.Item name="vehicle_id" label="派车（可选）">
            <Select allowClear options={vehicles.map(v => ({ value: v.id, label: `${v.plate_no} (${v.model || ''})` }))} />
          </Form.Item>
          <Form.Item name="remarks" label="备注"><Input.TextArea rows={2} /></Form.Item>
          <Text type="secondary" style={{fontSize:12}}>提交时若指定车辆，自动创建用车申请</Text>
        </Form>
      </Modal>
    </div>
  );
}
