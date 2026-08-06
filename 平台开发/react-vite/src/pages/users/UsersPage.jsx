import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  App, Input, Select, Button, Space, Tag, Badge, Modal,
  Typography, Empty, Form, Checkbox, Alert, Dropdown, Tooltip,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, PlusOutlined, EditOutlined,
  LockOutlined, StopOutlined, PlayCircleOutlined, DeleteOutlined,
  UserOutlined, SafetyOutlined, MoreOutlined, ClearOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
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
  const { message, modal } = App.useApp();
  const { tokens } = useTheme();
  const { user: currentUser } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [form] = Form.useForm();

  // Data state
  const [users, setUsers] = useState([]);
  const [siteOptions, setSiteOptions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [sitesLoading, setSitesLoading] = useState(false);
  const [sitesError, setSitesError] = useState('');

  // Filter state
  const search = searchParams.get('q') || '';
  const roleFilter = searchParams.get('role') || undefined;
  const statusFilter = searchParams.get('status') || undefined;
  const showTestAccounts = searchParams.get('tests') === '1';

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [editingUser, setEditingUser] = useState(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getStrict('/users');
      setUsers(Array.isArray(data) ? data : (data?.users || []));
      setLoadError('');
    } catch (error) {
      setLoadError(error.message || '人员数据加载失败，请检查网络后重试');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSites = useCallback(async () => {
    setSitesLoading(true);
    try {
      const rows = await api.getStrict('/sites');
      setSiteOptions((Array.isArray(rows) ? rows : []).map((site) => ({ value: site.id, label: site.name })));
      setSitesError('');
    } catch (error) {
      setSitesError(error.message || '站点范围加载失败');
    } finally {
      setSitesLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);
  useEffect(() => { fetchSites(); }, [fetchSites]);

  const updateFilters = useCallback((patch) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      Object.entries(patch).forEach(([key, value]) => {
        if (value === undefined || value === null || value === '') next.delete(key);
        else next.set(key, String(value));
      });
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const handleReset = () => {
    updateFilters({ q: null, role: null, status: null });
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
    if (sitesError) {
      message.error('站点范围尚未加载，暂不能保存人员信息');
      return;
    }
    try {
      const values = await form.validateFields();
      setModalLoading(true);
      const url = editingUser ? `/users/${editingUser.id}` : '/users';
      const method = editingUser ? 'put' : 'post';
      await api[`${method}Strict`](url, values);
      message.success(editingUser ? `已更新“${values.real_name}”的人员信息` : `已创建“${values.real_name}”账号`);
      setModalOpen(false);
      await fetchUsers();
    } catch (error) {
      if (!error?.errorFields) message.error(error.message || '保存失败，请重试');
    } finally {
      setModalLoading(false);
    }
  };

  const handleDelete = (record) => {
    modal.confirm({
      title: `注销“${record.real_name}”的账号？`,
      content: '账号将立即失效；姓名、角色、站点和历史业务关联继续保留。如有未完成工作，系统会阻止注销。',
      okText: '确认注销账号',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.deleteStrict(`/users/${record.id}`);
          message.success(`“${record.real_name}”的账号已注销，人员档案和历史关联已保留`);
          await fetchUsers();
        } catch (error) {
          message.error(error.message || '注销失败');
          throw error;
        }
      },
    });
  };

  const handleToggleStatus = async (record) => {
    const newStatus = record.status === 'active' ? 'inactive' : 'active';
    const saveStatus = async () => {
      await api.putStrict(`/users/${record.id}/status`, { status: newStatus });
      message.success(newStatus === 'active' ? `已启用“${record.real_name}”` : `已停用“${record.real_name}”`);
      await fetchUsers();
    };
    if (newStatus === 'active') {
      try { await saveStatus(); } catch (error) { message.error(error.message || '启用失败'); }
      return;
    }
    try {
      const pending = await api.getStrict(`/users/${record.id}/pending-work`);
      if (!pending.total) {
        modal.confirm({
          title: `停用“${record.real_name}”？`,
          content: '停用后该人员当前会话立即失效，但人员档案和历史记录会保留。',
          okText: '确认停用', cancelText: '取消', okButtonProps: { danger: true }, onOk: saveStatus,
        });
        return;
      }
      let targetId;
      const candidates = users.filter((user) => user.id !== record.id && user.status === 'active'
        && (user.roles || [user.role]).includes('operator'));
      modal.confirm({
        title: `先转交“${record.real_name}”的未完成工作`,
        content: <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Text>仍有 {Object.entries(pending.pending_work).map(([name, count]) => `${name}${count}项`).join('、')}。停用前必须完成转交；个人用车申请需先归还或取消。</Text>
          <Select style={{ width: '100%' }} placeholder="选择接收运维人员"
            options={candidates.map((user) => ({ value: user.id, label: user.real_name }))}
            onChange={(value) => { targetId = value; }} />
        </Space>,
        okText: '转交并停用', cancelText: '取消', okButtonProps: { danger: true },
        onOk: async () => {
          if (!targetId) { message.error('请选择接收人员'); return Promise.reject(new Error('target required')); }
          await api.postStrict(`/users/${record.id}/transfer-work`, { target_user_id: targetId });
          await saveStatus();
          message.success('未完成工作已转交');
        },
      });
    } catch (error) {
      message.error(error.message || '无法检查该人员的未完成工作');
    }
  };

  const handleResetPassword = (record) => {
    modal.confirm({
      title: `重置“${record.real_name}”的密码？`,
      icon: <LockOutlined />,
      content: `账号“${record.login_name || record.real_name}”的当前登录会话将立即失效，并生成一次性临时密码。`,
      okText: '确认重置',
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await api.putStrict(`/users/${record.id}/reset-password`, {});
          if (!result?.temporary_password) throw new Error('服务器未返回临时密码');
          modal.success({
            title: `“${record.real_name}”的临时密码已生成`,
            content: (
              <Space direction="vertical" size={8}>
                <Text>请安全地交给本人。该密码只在这里显示一次，首次登录后必须修改。</Text>
                <Text code copyable strong>{result.temporary_password}</Text>
              </Space>
            ),
            okText: '我已记录',
          });
        } catch (error) {
          message.error(error.message || '密码重置失败');
          throw error;
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
        return (
          <Space size={4} wrap>
            {list.map((role) => {
              const cfg = roleMap[role] || { label: role || '-', color: 'default' };
              return <Tag key={role} color={cfg.color} icon={cfg.icon}>{cfg.label}</Tag>;
            })}
          </Space>
        );
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
      width: 260,
      render: (val, record) => {
        const sites = val || record.assigned_sites || [];
        if (Array.isArray(sites) && sites.length > 0) {
          const summary = sites.length <= 2 ? sites.join('、') : `${sites.slice(0, 2).join('、')}等 ${sites.length} 个站点`;
          return <Tooltip title={sites.join('、')}><Text ellipsis style={{ maxWidth: 230 }}>{summary}</Text></Tooltip>;
        }
        return <Text type="secondary">未分配</Text>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 84,
      render: (val, record) => {
        if (record.deleted_at) return <Badge status="default" text="已注销" />;
        const isActive = val === 'active';
        return <Badge status={isActive ? 'success' : 'default'} text={isActive ? '启用' : '停用'} />;
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      fixed: 'right',
      render: (_, record) => {
        if (record.deleted_at) return <Text type="secondary">已注销</Text>;
        const isCurrent = Number(record.id) === Number(currentUser?.id);
        const menuItems = [
          { key: 'reset', icon: <LockOutlined />, label: '重置密码' },
          {
            key: 'status',
            icon: record.status === 'active' ? <StopOutlined /> : <PlayCircleOutlined />,
            label: record.status === 'active' ? (isCurrent ? '停用（当前账号）' : '停用账号') : '启用账号',
            disabled: isCurrent && record.status === 'active',
          },
          { type: 'divider' },
          { key: 'delete', icon: <DeleteOutlined />, label: isCurrent ? '注销（当前账号）' : '注销账号', danger: true, disabled: isCurrent },
        ];
        const handleMenu = ({ key }) => {
          if (key === 'reset') handleResetPassword(record);
          if (key === 'status') handleToggleStatus(record);
          if (key === 'delete') handleDelete(record);
        };
        return (
          <Space size={0}>
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
              编辑
            </Button>
            <Dropdown menu={{ items: menuItems, onClick: handleMenu }} trigger={['click']}>
              <Button type="link" size="small" icon={<MoreOutlined />} aria-label={`更多操作：${record.real_name}`}>
                更多
              </Button>
            </Dropdown>
          </Space>
        );
      },
    },
  ];

  const roleOptions = Object.entries(roleMap).map(([value, cfg]) => ({ value, label: cfg.label }));
  const isTestAccount = (record) => /体验|测试|test|demo/i.test([
    record.login_name, record.username, record.real_name,
  ].filter(Boolean).join(' '));
  const filteredUsers = useMemo(() => users.filter((record) => {
    const q = search.trim().toLowerCase();
    const searchable = [record.login_name, record.username, record.real_name, record.phone]
      .filter(Boolean).join(' ').toLowerCase();
    const roles = record.roles?.length ? record.roles : [record.role];
    return (!q || searchable.includes(q))
      && (!roleFilter || roles.includes(roleFilter))
      && (!statusFilter || record.status === statusFilter);
  }), [roleFilter, search, statusFilter, users]);
  const hiddenTestCount = filteredUsers.filter(isTestAccount).length;
  const visibleUsers = showTestAccounts ? filteredUsers : filteredUsers.filter((record) => !isTestAccount(record));

  return (
    <ManagementPage
      pageTitle="人员与权限"
      pageSub="维护登录账号、业务角色和可访问站点范围。"
      tableMode="content"
      filterSlot={<Space direction="vertical" size={8} style={{ width: '100%' }}>
        {loadError && (
          <Alert
            type="error"
            showIcon
            message={users.length ? '刷新失败，当前显示上次成功加载的数据' : '人员数据加载失败'}
            description={loadError}
            action={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={fetchUsers}>重新加载</Button>}
          />
        )}
        <FilterBar
          extra={(
            <Space wrap>
              <Checkbox
                checked={showTestAccounts}
                onChange={(event) => updateFilters({ tests: event.target.checked ? '1' : null })}
              >
                显示测试账户{hiddenTestCount > 0 ? `（${hiddenTestCount}）` : ''}
              </Checkbox>
              <Button icon={<ClearOutlined />} onClick={handleReset}>重置筛选</Button>
              <Tooltip title="刷新人员数据">
                <Button icon={<ReloadOutlined />} aria-label="刷新人员数据" loading={loading} onClick={fetchUsers} />
              </Tooltip>
              <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>添加人员</Button>
            </Space>
          )}
        >
          <Input
            placeholder="搜索登录名、姓名、手机号..."
            prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
            allowClear
            value={search}
            onChange={(event) => updateFilters({ q: event.target.value })}
            style={{ width: filterInputWidth, borderRadius: 8 }}
          />
          <Select
            placeholder="角色"
            aria-label="按角色筛选"
            allowClear
            value={roleFilter}
            onChange={(value) => updateFilters({ role: value })}
            style={{ width: filterSelectWidth }}
            options={roleOptions}
          />
          <Select
            placeholder="状态"
            aria-label="按账号状态筛选"
            allowClear
            value={statusFilter}
            onChange={(value) => updateFilters({ status: value })}
            style={{ width: filterSmallSelectWidth }}
            options={[
              { value: 'active', label: '启用' },
              { value: 'inactive', label: '停用' },
            ]}
          />
          {(search || roleFilter || statusFilter) && <Text type="secondary">共 {visibleUsers.length} 条</Text>}
        </FilterBar>
      </Space>}
      tableSlot={<UnifiedTable
          mode="content"
          columns={columns}
          dataSource={visibleUsers}
          rowKey={(r) => r.id || r.username}
          loading={loading}
          pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="暂无用户数据" /> }}
          scrollX={1090}
        />}
    >

      {/* Create/Edit Modal */}
      <Modal
        title={editingUser ? `编辑人员 · ${editingUser.real_name}` : '添加人员'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => setModalOpen(false)}
        confirmLoading={modalLoading}
        okText={editingUser ? '保存' : '创建'}
        okButtonProps={{ disabled: Boolean(sitesError) }}
        cancelText="取消"
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="login_name" label="登录名"
            extra="建议使用姓名，便于现场人员记忆和管理员辨认。"
            rules={[{ required: true, whitespace: true, message: '请输入登录名' }]}>
            <Input prefix={<UserOutlined style={{ color: tokens.colorTextTertiary }} />} placeholder="例如：李城亮" maxLength={50} />
          </Form.Item>
          {!editingUser && (
            <Form.Item name="password" label="密码"
              rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '密码至少6个字符' }]}>
              <Input.Password prefix={<LockOutlined style={{ color: tokens.colorTextTertiary }} />} placeholder="请输入密码" />
            </Form.Item>
          )}
          <Form.Item name="real_name" label="姓名" rules={[{ required: true, whitespace: true, message: '请输入姓名' }]}>
            <Input placeholder="请输入姓名" maxLength={30} />
          </Form.Item>
          <Form.Item name="roles" label="角色" rules={[{ required: true, message: '请选择至少一个角色' }]}>
            <Select mode="multiple" placeholder="可同时选择运维人员和审核员" options={roleOptions} />
          </Form.Item>
          <Form.Item
            name="phone"
            label="手机号"
            rules={[{ pattern: /^1\d{10}$/, message: '请输入正确的11位手机号' }]}
          >
            <Input placeholder="请输入11位手机号" maxLength={11} />
          </Form.Item>
          {sitesError && (
            <Alert
              type="error"
              showIcon
              message="站点范围加载失败，已暂停修改"
              description={sitesError}
              action={<Button size="small" loading={sitesLoading} onClick={fetchSites}>重试</Button>}
              style={{ marginBottom: 16 }}
            />
          )}
          <Form.Item name="site_ids" label="负责站点" extra="未分配站点的非管理员账号将看不到任何站点数据。">
            <Select
              mode="multiple"
              allowClear
              showSearch
              optionFilterProp="label"
              loading={sitesLoading}
              disabled={Boolean(sitesError)}
              options={siteOptions}
              placeholder="选择该人员可执行和接收通知的站点"
            />
          </Form.Item>
        </Form>
      </Modal>
    </ManagementPage>
  );
}
