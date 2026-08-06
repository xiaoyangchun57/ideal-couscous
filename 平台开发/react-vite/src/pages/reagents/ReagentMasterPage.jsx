import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { App, Alert, Button, Modal, Form, Input, InputNumber, Space, Popconfirm, Typography, Empty } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import FilterBar from '../../components/FilterBar';
import ManagementPage, { UnifiedTable } from '../../components/ManagementPage';
import { filterInputWidth } from '../../services/pageStyles';

const { Text } = Typography;

export default function ReagentMasterPage() {
  const { message } = App.useApp();
  const { tokens } = useTheme();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const canWrite = (user?.roles || [user?.role]).includes('admin');
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState([]);
  const [loadError, setLoadError] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(null); // null=新增，对象=编辑
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ name: '', manufacturer: '', spec: '', unit: '瓶', shelf_life_days: 365 });
  const search = searchParams.get('q') || '';

  const updateSearch = useCallback((value) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      const q = value.trim();
      if (q) next.set('q', q);
      else next.delete('q');
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const filteredData = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data;
    return data.filter(r =>
      (r.name && r.name.toLowerCase().includes(q)) ||
      (r.manufacturer && r.manufacturer.toLowerCase().includes(q)) ||
      (r.spec && r.spec.toLowerCase().includes(q))
    );
  }, [data, search]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api.getStrict('/reagents');
      setData(Array.isArray(d) ? d : []);
      setLoadError('');
    } catch (error) {
      setLoadError(error.message || '试剂主数据加载失败，请检查网络后重试');
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
    const unit = (form.unit || '').trim();
    if (!unit) { message.warning('请填写计量单位'); return; }
    if (!Number.isInteger(form.shelf_life_days) || form.shelf_life_days < 1 || form.shelf_life_days > 3650) {
      message.warning('保质期应为 1 至 3650 天的整数');
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        await api.putStrict(`/reagents/${editing.id}`, { ...form, name, unit });
        message.success(`已保存“${name}”`);
      } else {
        await api.postStrict('/reagents', { ...form, name, unit });
        message.success(`已新增“${name}”`);
      }
      setModalOpen(false);
      await load();
    } catch (e) {
      message.error(e?.message || '保存失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };
  const del = async (r) => {
    try {
      await api.deleteStrict(`/reagents/${r.id}`);
      message.success(`已删除「${r.name}」`);
      await load();
    } catch (e) {
      message.error(e?.message || '删除失败');
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
      title: '保质期（天）', dataIndex: 'shelf_life_days', key: 'shelf_life_days', width: 120,
      render: (v) => (v == null ? '—' : `${v} 天`),
    },
    {
      title: '操作', key: 'op', width: 140,
      render: (_, r) => (
        <Space size={0}>
          {canWrite && (
            <Button size="small" type="link" icon={<EditOutlined />} aria-label={`编辑试剂：${r.name}`} onClick={() => openEdit(r)}>
              编辑
            </Button>
          )}
          {canWrite && (
            <Popconfirm
              title={`确认删除「${r.name}」？`}
              description="仅尚未产生库存、告警或用量记录的误建项可以删除，删除后不可恢复。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => del(r)}
            >
              <Button size="small" type="link" danger icon={<DeleteOutlined />} aria-label={`删除试剂：${r.name}`}>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <ManagementPage
      pageTitle="试剂主数据"
      pageSub="维护试剂目录；站点库存新增试剂时从此处选取。"
      tableMode="content"
      filterSlot={<Space direction="vertical" size={8} style={{ width: '100%' }}>
        {loadError && (
          <Alert
            type="error"
            showIcon
            message={data.length ? '刷新失败，当前显示上次成功加载的数据' : '试剂主数据加载失败'}
            description={loadError}
            action={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>重新加载</Button>}
          />
        )}
        <FilterBar
          extra={<Space>
            <Button icon={<ReloadOutlined />} aria-label="刷新试剂主数据" loading={loading} onClick={load} />
            {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增试剂</Button>}
          </Space>}
        >
          <Input
            placeholder="搜索试剂名称、厂家、规格..."
            prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
            allowClear
            value={search}
            onChange={(event) => updateSearch(event.target.value)}
            style={{ width: filterInputWidth, borderRadius: 8 }}
          />
          <Text type="secondary">共 {filteredData.length} 项</Text>
        </FilterBar>
      </Space>}
      tableSlot={<UnifiedTable
          mode="content"
          rowKey="id"
          loading={loading}
          dataSource={filteredData}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description={search ? '没有符合筛选条件的试剂' : '暂无试剂主数据'} /> }}
        />}
    >
      <Modal
        title={editing ? `编辑试剂 · ${editing.name}` : '新增试剂'}
        open={modalOpen}
        onOk={submit}
        confirmLoading={submitting}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
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
          <Form.Item label="单位" required>
            <Input
              value={form.unit}
              onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))}
              placeholder="瓶 / 套 / 盒"
              maxLength={10}
            />
          </Form.Item>
          <Form.Item label="保质期（天）" required>
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
    </ManagementPage>
  );
}
