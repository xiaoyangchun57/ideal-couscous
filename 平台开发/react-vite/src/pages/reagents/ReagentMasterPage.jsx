import { useState, useEffect, useCallback } from 'react';
import { Card, Table, Button, Modal, Form, Input, InputNumber, Space, Popconfirm, message, Typography } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ExperimentOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import AttachmentUpload from '../../components/AttachmentUpload';

const { Title, Text } = Typography;

export default function ReagentMasterPage() {
  const { tokens } = useTheme();
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(null); // null=新增，对象=编辑
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ name: '', manufacturer: '', spec: '', unit: '瓶', shelf_life_days: 365 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api.get('/reagents');
      setData(Array.isArray(d) ? d : []);
    } catch {
      message.error('加载试剂主数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', manufacturer: '', spec: '', unit: '瓶', shelf_life_days: 365 });
    setModalOpen(true);
  };
  const openEdit = (r) => {
    setEditing(r);
    setForm({
      name: r.name || '',
      manufacturer: r.manufacturer || '',
      spec: r.spec || '',
      unit: r.unit || '瓶',
      shelf_life_days: r.shelf_life_days ?? 365,
    });
    setModalOpen(true);
  };
  const submit = async () => {
    const name = (form.name || '').trim();
    if (!name) { message.warning('请填写试剂名称'); return; }
    setSubmitting(true);
    try {
      if (editing) {
        await api.put(`/reagents/${editing.id}`, { ...form, name });
        message.success('已保存修改');
      } else {
        await api.post('/reagents', { ...form, name });
        message.success('已新增试剂');
      }
      setModalOpen(false);
      await load();
    } catch (e) {
      const msg = e?.response?.data?.error || '操作失败，请重试';
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  };
  const del = async (r) => {
    try {
      await api.delete(`/reagents/${r.id}`);
      message.success(`已删除「${r.name}」`);
      await load();
    } catch (e) {
      const msg = e?.response?.data?.error || '删除失败';
      message.error(msg);
    }
  };

  const columns = [
    {
      title: '试剂名称', dataIndex: 'name', key: 'name', width: 180,
      render: (v) => <Text strong>{v}</Text>,
    },
    {
      title: '生产厂家', dataIndex: 'manufacturer', key: 'manufacturer', width: 140,
      render: (v) => v || <Text type="secondary">—</Text>,
    },
    {
      title: '规格', dataIndex: 'spec', key: 'spec', width: 150,
      render: (v) => v || <Text type="secondary">—</Text>,
    },
    { title: '单位', dataIndex: 'unit', key: 'unit', width: 90, render: (v) => v || '—' },
    {
      title: '保质期(天)', dataIndex: 'shelf_life_days', key: 'shelf_life_days', width: 120,
      render: (v) => (v == null ? '—' : `${v} 天`),
    },
    {
      title: '操作', key: 'op', width: 140,
      render: (_, r) => (
        <Space size={0}>
          {canWrite && <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(r)}>编辑</Button>}
          {canWrite && (
            <Popconfirm
              title={`确认删除「${r.name}」？`}
              description="删除后不可恢复"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => del(r)}
            >
              <Button size="small" type="link" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 16, height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>
            <ExperimentOutlined style={{ marginRight: 8, color: tokens.colorPrimary }} />
            试剂主数据管理
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            维护试剂目录（名称 / 厂家 / 规格 / 单位 / 保质期）。站点库存中的「新增试剂」从此处选取。
          </Text>
        </div>
        {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增试剂</Button>}
      </div>

      <Card bodyStyle={{ padding: 0 }}>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={data}
          columns={columns}
          pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 种试剂` }}
          size="small"
        />
      </Card>

      <Card title="试剂配置照片归档" size="small" style={{ marginTop: 16 }}>
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          流程外资料：站点试剂配置现场照片可在此就近归档，便于项目管理与检索（不参与审核链）。
        </Text>
        {canWrite ? (
          <AttachmentUpload sourceType="reagent" category="试剂配置" buttonText="上传试剂配置照片" />
        ) : (
          <Text type="secondary">仅管理员可归档试剂配置照片</Text>
        )}
      </Card>

      <Modal
        title={editing ? `编辑试剂 · ${editing.name}` : '新增试剂'}
        open={modalOpen}
        onOk={submit}
        confirmLoading={submitting}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item label="试剂名称" required>
            <Input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="如 氨氮标液"
              maxLength={50}
            />
          </Form.Item>
          <Form.Item label="生产厂家">
            <Input
              value={form.manufacturer}
              onChange={(e) => setForm((f) => ({ ...f, manufacturer: e.target.value }))}
              placeholder="如 哈希"
              maxLength={50}
            />
          </Form.Item>
          <Form.Item label="规格">
            <Input
              value={form.spec}
              onChange={(e) => setForm((f) => ({ ...f, spec: e.target.value }))}
              placeholder="如 500mL/瓶"
              maxLength={50}
            />
          </Form.Item>
          <Form.Item label="单位">
            <Input
              value={form.unit}
              onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))}
              placeholder="瓶 / 套 / 盒"
              maxLength={10}
            />
          </Form.Item>
          <Form.Item label="保质期（天）">
            <InputNumber
              min={1}
              max={3650}
              value={form.shelf_life_days}
              onChange={(v) => setForm((f) => ({ ...f, shelf_life_days: v }))}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
