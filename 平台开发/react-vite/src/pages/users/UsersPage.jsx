import { useState, useEffect, useCallback } from 'react';
import {
  Input, Select, Button, Space, Tag, Badge, Modal,
  Typography, message, Empty, Form, Popconfirm,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, PlusOutlined, EditOutlined,
  LockOutlined, StopOutlined, PlayCircleOutlined, DeleteOutlined,
  UserOutlined, SafetyOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import FilterBar from '../../components/FilterBar';
import ManagementPage, { UnifiedTable } from '../../components/ManagementPage';
import {
  filterInputWidth, filterSelectWidth, filterSmallSelectWidth,
} from '../../services/pageStyles';

const { Text } = Typography;

// 角色映射为本页专用小映射（仅 antd Tag 预设色名，符合状态色约定），保留页内定义
const roleMap = {
  admin: { label: '管理员', color: 'red', icon: <SafetyOutlined /> },
  operator: { label: '运维人员', color: 'blue' },
  reviewer: { label: '审核员', color: 'cyan' },
};

export default function UsersPage() {
  const { tokens } = useTheme();
  const [form] = Form.useForm();

  // Data state
  const [users, setUsers] = useState([]);
  const [siteOptions, setSiteOptions] = useState([]);
  const [loading, setLoading] = useState(false);

  // Filter state
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState(undefined);
  const [statusFilter, setStatusFilter] = useState(undefined);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [editingUser, setEditingUser] = useState(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (roleFilter) params.set('role', roleFilter);
      if (statusFilter) params.set('status', statusFilter);
      const data = await api.get(`/users?${params.toString()}`);
      setUsers(Array.isArray(data) ? data : (data?.users || []));
    } catch {
      message.error('加载用户数据失败');
      setUsers([]);
    } finally {
      setLoading(false);
    }
  }, [search, roleFilter, statusFilter]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);
  useEffect(() => { api.get('/sites').then(rows => setSiteOptions((rows || []).map(s => ({ value: s.id, label: s.name })))).catch(() => {}); }, []);

  const handleReset = () => {
    setSearch('');
    setRoleFilter(undefined);
    setStatusFilter(undefined);
  };

  const handleCreate = () => {
    setEditingUser(null);
    form.resetFields();
    form.setFieldsValue({ roles: ['operator'] });
    setModalOpen(true);
  };

  const handleEdit = (record) => {
    setEditingUser(record);
    form.setFieldsValue({
      login_name: record.login_name || record.real_name,
      real_name: record.real_name,
      roles: record.roles || [record.role],
      phone: record.phone,
      site_ids: record.site_ids || [],
      status: record.status || 'active',
    });
    setModalOpen(true);
  };

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields();
      setModalLoading(true);

      const payload = { ...values };

      const url = editingUser ? `/users/${editingUser.id}` : '/users';
      const method = editingUser ? 'put' : 'post';
      const result = await api[method](url, payload);

      if (result && !result.error) {
          message.success(editingUser ? '用户信息已更新' : '用户创建成功');
        setModalOpen(false);
        fetchUsers();
      } else {
        message.error(result?.error || '操作失败');
      }
    } catch {
      // validation error
    } finally {
      setModalLoading(false);
    }
  };

  const handleDelete = async (record) => {
    const result = await api.delete(`/users/${record.id}`);
    if (result && !result.error) {
        message.success('账号已注销');
      fetchUsers();
    } else {
      message.error('删除失败');
    }
  };

  const handleToggleStatus = async (record) => {
    const newStatus = record.status === 'active' ? 'inactive' : 'active';
    const result = await api.put(`/users/${record.id}/status`, { status: newStatus });
    if (result && !result.error) {
      message.success(newStatus === 'active' ? '已启用' : '已停用');
      fetchUsers();
    } else {
      message.error('操作失败');
    }
  };

  const handleResetPassword = (record) => {
    Modal.confirm({
      title: '重置密码',
      icon: <LockOutlined />,
      content: `确认将 ${record.login_name || record.real_name} 的密码重置为 yw123456？`,
      okText: '确认重置',
      cancelText: '取消',
      onOk: async () => {
        const result = await api.put(`/users/${record.id}/reset-password`, { new_password: 'yw123456' });
        if (result && !result.error) {
          message.success('密码已重置');
        } else {
          message.error('重置失败');
        }
      },
    });
  };

  const columns = [
    {
      title: '登录名',
      dataIndex: 'login_name',
      key: 'login_name',
      width: 170,
      render: (text, record) => (
        <Space>
          <div style={{
            width: 28, height: 28, borderRadius: '50%',
            background: `linear-gradient(135deg, ${tokens.colorPrimary}, ${tokens.colorPrimaryHover})`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', fontSize: 12, fontWeight: 600, flexShrink: 0,
          }}>
            {(record.real_name || text || '?')[0]}
          </div>
          <Text strong>{text || record.real_name || record.username}</Text>
        </Space>
      ),
    },
    {
      title: '姓名',
      dataIndex: 'real_name',
      key: 'real_name',
      width: 90,
      render: (text) => text || '-',
    },
    {
      title: '角色',
      dataIndex: 'roles',
      key: 'roles',
      width: 180,
      render: (roles, record) => {
        const list = roles?.length ? roles : [record.role];
        return <Space size={4} wrap>{list.map((role) => {
          const cfg = roleMap[role] || { label: role || '-', color: 'default' };
          return <Tag key={role} color={cfg.color} icon={cfg.icon}>{cfg.label}</Tag>;
        })}</Space>;
      },
    },
    {
      title: '手机号',
      dataIndex: 'phone',
      key: 'phone',
      width: 140,
      render: (text) => text || '-',
    },
    {
      title: '负责站点',
      dataIndex: 'sites',
      key: 'sites',
      width: 360,
      render: (val, record) => {
        const sites = val || record.assigned_sites || [];
        if (Array.isArray(sites) && sites.length > 0) {
          return (
            <Space size={2} wrap>
              {sites.slice(0, 2).map((s, i) => <Tag key={i} style={{ fontSize: 11 }}>{s}</Tag>)}
              {sites.length > 2 && <Tag style={{ fontSize: 11 }}>+{sites.length - 2}</Tag>}
            </Space>
          );
        }
        return <Text style={{ color: tokens.colorTextTertiary }}>-</Text>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 84,
      render: (val) => {
        const isActive = val === 'active';
        return <Badge status={isActive ? 'success' : 'default'} text={isActive ? '启用' : '停用'} />;
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 330,
      render: (_, record) => (
        <Space size={0} wrap>
          <Button type="link" size="small" icon={<EditOutlined />}
            onClick={() => handleEdit(record)}>
            编辑
          </Button>
          <Button type="link" size="small" icon={<LockOutlined />} onClick={() => handleResetPassword(record)}>重置密码</Button>
          <Button type="link" size="small" icon={record.status === 'active' ? <StopOutlined /> : <PlayCircleOutlined />}
            onClick={() => handleToggleStatus(record)}>{record.status === 'active' ? '停用' : '启用'}</Button>
          <Popconfirm
            title="确认注销账号？"
            description="账号将不能登录，历史业务记录会保留。"
            okText="注销账号"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDelete(record)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>注销</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const roleOptions = Object.entries(roleMap).map(([value, cfg]) => ({ value, label: cfg.label }));

  return (
    <ManagementPage
      pageTitle="用户管理"
      headerExtra={<Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>添加用户</Button>}
      tableMode="content"
      filterSlot={<FilterBar
        extra={(
          <Space>
            <Button icon={<SearchOutlined />} onClick={() => fetchUsers()}>查询</Button>
            <Button icon={<ReloadOutlined />} onClick={handleReset}>重置</Button>
          </Space>
        )}
      >
        <Input
          placeholder="搜索登录名、姓名、手机号..."
          prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onPressEnter={() => fetchUsers()}
          style={{ width: filterInputWidth, borderRadius: 8 }}
        />
        <Select
          placeholder="角色"
          allowClear
          value={roleFilter}
          onChange={(val) => setRoleFilter(val)}
          style={{ width: filterSelectWidth }}
          options={roleOptions}
        />
        <Select
          placeholder="状态"
          allowClear
          value={statusFilter}
          onChange={(val) => setStatusFilter(val)}
          style={{ width: filterSmallSelectWidth }}
          options={[
            { value: 'active', label: '启用' },
            { value: 'inactive', label: '停用' },
          ]}
        />
        {(search || roleFilter || statusFilter) && (
          <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
            已筛选 {users.length} 条结果
          </Text>
        )}
      </FilterBar>}
      tableSlot={<UnifiedTable
          mode="content"
          columns={columns}
          dataSource={users}
          rowKey={(r) => r.id || r.username}
          loading={loading}
          pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="暂无用户数据" /> }}
        />}
    >

      {/* Create/Edit Modal */}
      <Modal
        title={editingUser ? '编辑用户' : '添加用户'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => setModalOpen(false)}
        confirmLoading={modalLoading}
        okText={editingUser ? '保存' : '创建'}
        cancelText="取消"
        width={520}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="login_name" label="中文登录名"
            rules={[{ required: true, message: '请输入中文登录名' }]}>
            <Input prefix={<UserOutlined style={{ color: tokens.colorTextTertiary }} />} placeholder="例如：李城亮" />
          </Form.Item>
          {!editingUser && (
            <Form.Item name="password" label="密码"
              rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '密码至少6个字符' }]}>
              <Input.Password prefix={<LockOutlined style={{ color: tokens.colorTextTertiary }} />} placeholder="请输入密码" />
            </Form.Item>
          )}
          <Form.Item name="real_name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}>
            <Input placeholder="请输入姓名" />
          </Form.Item>
          <Form.Item name="roles" label="角色" rules={[{ required: true, message: '请选择至少一个角色' }]}>
            <Select mode="multiple" placeholder="可同时选择运维人员和审核员" options={roleOptions} />
          </Form.Item>
          <Form.Item name="phone" label="手机号">
            <Input placeholder="请输入手机号" />
          </Form.Item>
          <Form.Item name="site_ids" label="负责站点">
            <Select mode="multiple" allowClear showSearch optionFilterProp="label" options={siteOptions} placeholder="选择该人员可执行和接收通知的站点" />
          </Form.Item>
        </Form>
      </Modal>
    </ManagementPage>
  );
}
