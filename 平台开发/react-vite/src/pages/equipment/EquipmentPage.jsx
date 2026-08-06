import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  Table, Input, Select, Button, Space, Tag, Tabs,
  Typography, message, Spin, Empty, Modal, Form, Dropdown, Alert,
  Row, Col, Descriptions, Drawer, Divider, DatePicker, Upload,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, PlusOutlined, EyeOutlined,
  EditOutlined, DeleteOutlined,
  InboxOutlined, SwapOutlined,
  ArrowUpOutlined, ArrowDownOutlined, MoreOutlined,
  DownloadOutlined, UploadOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import AttachmentUpload from '../../components/AttachmentUpload';
import { deviceTypeMap } from '../../services/constants';
import { statusColors } from '../../theme/tokens';
import { filterInputWidth, filterSelectWidth } from '../../services/pageStyles';
import WorkspacePage, { StatusStrip, TableLongText, ToolbarMeta, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';

const { Title, Text } = Typography;

const hasRole = (user, role) => (user?.roles || [user?.role]).includes(role);
const isRetired = (record) => record?.management_scope === 'retired'
  || Boolean(record?.recycle_date || record?.recycle_destination);

function BulkImportModal({ kind, open, onClose, onImported }) {
  const isDevice = kind === 'devices';
  const label = isDevice ? '设备' : '备品备件';
  const [uploading, setUploading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [preview, setPreview] = useState(null);

  const downloadTemplate = async () => {
    try {
      const token = localStorage.getItem('water_ops_token') || '';
      const response = await fetch(`/api/import-templates/${kind}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error('模板下载失败');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${label}批量导入模板.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message.error(error.message || '模板下载失败');
    }
  };

  const validateFile = async (file) => {
    setUploading(true);
    setPreview(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const result = await api.postForm(`/imports/${kind}/validate`, formData, 60000);
      if (result?.error) {
        message.error(result.error);
      } else {
        setPreview(result);
        message.success(`已完成校验：${result.valid_count}/${result.total} 行可导入`);
      }
    } finally {
      setUploading(false);
    }
    return false;
  };

  const commitImport = async () => {
    const rows = preview?.rows?.filter(row => row.status === 'valid') || [];
    if (!rows.length) return;
    setCommitting(true);
    try {
      const result = await api.post(`/imports/${kind}/commit`, { rows }, 60000);
      if (result?.error) {
        message.error(result.error);
        if (result.rows) setPreview(prev => ({ ...prev, rows: result.rows, valid_count: 0, error_count: result.rows.length }));
        return;
      }
      message.success(result.message || '批量导入成功');
      onImported?.();
      onClose();
    } finally {
      setCommitting(false);
    }
  };

  const columns = [
    { title: '行号', dataIndex: 'row_no', width: 72 },
    { title: '校验结果', dataIndex: 'status', width: 100, render: status => (
      <Tag color={status === 'valid' ? 'green' : status === 'warning' ? 'orange' : 'red'}>
        {status === 'valid' ? '可导入' : status === 'warning' ? '提示' : '需修正'}
      </Tag>
    ) },
    { title: isDevice ? '设备编码' : '备件编码', key: 'code', width: 140,
      render: (_, row) => row.data?.[isDevice ? 'device_code' : 'part_code'] || '-' },
    { title: isDevice ? '设备名称' : '备件名称', key: 'name', width: 180,
      render: (_, row) => row.data?.[isDevice ? 'device_name' : 'part_name'] || '-' },
    { title: '校验信息', dataIndex: 'messages', render: messages => messages?.join('；') || '字段完整' },
  ];

  return (
    <Modal
      title={`批量导入${label}`}
      open={open}
      onCancel={onClose}
      width={880}
      destroyOnHidden
      footer={preview ? [
        <Button key="cancel" onClick={onClose}>关闭</Button>,
        <Button key="import" type="primary" loading={committing}
          disabled={!preview.valid_count || preview.error_count > 0} onClick={commitImport}>
          确认导入 {preview.valid_count || 0} 条
        </Button>,
      ] : <Button onClick={onClose}>关闭</Button>}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Text type="secondary">先下载系统模板并填写。上传后会校验编码、站点关联和必填字段；存在错误时不会写入数据。</Text>
        <Button icon={<DownloadOutlined />} onClick={downloadTemplate}>下载导入模板</Button>
        <Upload.Dragger accept=".xlsx,.xlsm" maxCount={1} showUploadList={false}
          beforeUpload={validateFile} disabled={uploading || committing}>
          <p className="ant-upload-drag-icon"><UploadOutlined /></p>
          <p className="ant-upload-text">点击或拖拽 Excel 文件到此处校验</p>
          <p className="ant-upload-hint">仅支持系统模板的 .xlsx 文件，单次最多 1000 行。</p>
        </Upload.Dragger>
        {preview && (
          <>
            <Text strong>校验结果：{preview.total} 行，{preview.valid_count} 行可导入，{preview.error_count} 行需修正。</Text>
            <Table columns={columns} dataSource={preview.rows} rowKey="row_no" size="small" pagination={false} scroll={{ y: 280 }} />
          </>
        )}
      </Space>
    </Modal>
  );
}

// ---------- Device Ledger Tab ----------
function DeviceLedgerTab() {
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const canWrite = hasRole(user, 'admin');
  const infoColor = statusColors.info[isDark ? 'dark' : 'light'];
  const [searchParams, setSearchParams] = useSearchParams();
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState('');
  const search = searchParams.get('q') || '';
  const typeFilter = searchParams.get('type') || undefined;
  const siteFilterValue = searchParams.get('site');
  const siteFilter = siteFilterValue ? Number(siteFilterValue) : undefined;

  // View / Create / Edit state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingDevice, setViewingDevice] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDevice, setEditingDevice] = useState(null);
  const [modalLoading, setModalLoading] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [sites, setSites] = useState([]);
  const [sitesError, setSitesError] = useState('');
  const [form] = Form.useForm();

  const fetchDevices = useCallback(async () => {
    setLoading(true);
    setFetchError('');
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (typeFilter) params.set('type', typeFilter);
      if (siteFilter) params.set('site_id', siteFilter);
      const data = await api.getStrict(`/devices?${params.toString()}`);
      setDevices(Array.isArray(data) ? data : (data?.devices || []));
    } catch (error) {
      setFetchError(error?.message || '设备列表加载失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  }, [search, typeFilter, siteFilter]);

  useEffect(() => { fetchDevices(); }, [fetchDevices]);

  // Fetch sites for form dropdown
  useEffect(() => {
    api.getStrict('/sites').then(data => {
      const list = Array.isArray(data) ? data : (data?.sites || []);
      setSites(list);
      setSitesError('');
    }).catch(error => setSitesError(error?.message || '站点选项加载失败'));
  }, []);

  const updateFilter = useCallback((key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value !== undefined && value !== null && String(value).trim() !== '') next.set(key, String(value));
    else next.delete(key);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleReset = () => {
    const next = new URLSearchParams(searchParams);
    ['q', 'type', 'site'].forEach(key => next.delete(key));
    setSearchParams(next, { replace: true });
  };

  // ---- View detail ----
  const loadDeviceDetails = useCallback(async (record) => {
    setViewLoading(true);
    setViewError('');
    try {
      const data = await api.getStrict(`/devices/${record.id}`);
      if (data && data.device) {
        setViewingDevice({ ...data.device, _logs: data.logs || [], _op_logs: data.operation_logs || [] });
      }
    } catch (error) {
      setViewError(error?.message || '设备详情加载失败');
    } finally {
      setViewLoading(false);
    }
  }, []);

  const handleView = useCallback((record) => {
    setViewingDevice(record);
    setViewOpen(true);
    loadDeviceDetails(record);
  }, [loadDeviceDetails]);

  // ---- Create ----
  const handleCreate = useCallback(() => {
    setEditingDevice(null);
    form.resetFields();
    setModalOpen(true);
  }, [form]);

  // ---- 登记设备回收处置（立即生效） ----
  const [recycleOpen, setRecycleOpen] = useState(false);
  const [recycleDevice, setRecycleDevice] = useState(null);
  const [recycleForm] = Form.useForm();
  const [recycleLoading, setRecycleLoading] = useState(false);
  const handleRecycleOpen = useCallback((record) => {
    setRecycleDevice(record);
    recycleForm.resetFields();
    recycleForm.setFieldsValue({
      destination: 'scrap',
      recycle_date: dayjs(),
    });
    setRecycleOpen(true);
  }, [recycleForm]);
  const handleRecycleOk = useCallback(async () => {
    let values;
    try {
      values = await recycleForm.validateFields();
    } catch (e) {
      message.error(e?.errorFields?.[0]?.errors?.[0] || '请检查表单');
      return;
    }
    setRecycleLoading(true);
    try {
      const result = await api.postStrict('/device-recycle', {
        device_id: recycleDevice.id,
        reason: values.reason || '',
        destination: values.destination || 'scrap',
        recycle_date: values.recycle_date ? values.recycle_date.format('YYYY-MM-DD') : '',
      });
      message.success(result.message || '设备已退出运维管理');
      setRecycleOpen(false);
      setRecycleDevice(null);
      fetchDevices();
    } catch (e) {
      message.error(e?.message || '回收登记失败');
    } finally {
      setRecycleLoading(false);
    }
  }, [recycleForm, recycleDevice, user, fetchDevices]);

  // ---- Edit (asset profile and relocation) ----
  const handleEdit = useCallback((record) => {
    setEditingDevice(record);
    form.setFieldsValue({
      device_code: record.device_code,
      device_name: record.device_name,
      device_type: record.device_type,
      site_id: record.site_id,
      device_model: record.device_model || '',
      manufacturer: record.manufacturer || '',
      install_date: record.install_date ? dayjs(record.install_date) : null,
    });
    setModalOpen(true);
  }, [form]);

  // ---- Modal submit ----
  const handleModalOk = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setModalLoading(true);
      if (editingDevice) {
        const result = await api.put(`/devices/${editingDevice.id}`, {
          site_id: values.site_id,
          device_model: values.device_model || '',
          manufacturer: values.manufacturer || '',
          install_date: values.install_date ? values.install_date.format('YYYY-MM-DD') : '',
        });
        if (result && !result.error) {
          message.success('设备移站成功');
          setModalOpen(false);
          fetchDevices();
        } else {
          message.error(result?.error || '移站失败');
        }
      } else {
        const result = await api.post('/devices', {
          ...values,
          install_date: values.install_date ? values.install_date.format('YYYY-MM-DD') : '',
        });
        if (result && !result.error) {
          message.success('设备注册成功');
          setModalOpen(false);
          fetchDevices();
        } else {
          message.error(result?.error || '注册失败');
        }
      }
    } catch { /* validation error */ }
    setModalLoading(false);
  }, [form, editingDevice, fetchDevices]);

  const typeOptions = Object.entries(deviceTypeMap).map(([value, label]) => ({ value, label }));
  const siteOptions = sites.map(s => ({ value: s.id, label: s.name || s.code }));

  const columns = [
    {
      title: '设备编码',
      dataIndex: 'code',
      key: 'code',
      width: 130,
      fixed: 'left',
      render: (text, record) => (
        <Text strong style={{ color: tokens.colorPrimary }}>{text || record.device_code || `#${record.id}`}</Text>
      ),
    },
    {
      title: '设备名称',
      dataIndex: 'device_name',
      key: 'device_name',
      width: 160,
      ellipsis: true,
      render: (text) => <span>{text || '-'}</span>,
    },
    {
      title: '厂商',
      dataIndex: 'manufacturer',
      key: 'manufacturer',
      width: 130,
      ellipsis: true,
      render: (text) => text || '-',
    },
    {
      title: '型号',
      dataIndex: 'device_model',
      key: 'device_model',
      width: 130,
      ellipsis: true,
      render: (text) => text || <Text type="secondary">未录入</Text>,
    },
    {
      title: '所属站点',
      dataIndex: 'site_name',
      key: 'site_name',
      width: 150,
      ellipsis: true,
      render: (text) => <span title={text}>{text || '-'}</span>,
    },
    {
      title: '最后数据',
      dataIndex: 'last_data_time',
      key: 'last_data_time',
      width: 150,
      render: (text, record) => Number(record.monitoring_enabled) !== 1
        ? <Tag>非采集设备</Tag>
        : text ? (
          <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>{text}</Text>
        ) : <Tag color="default">采集尚未接入</Tag>,
    },
    {
      title: '运行状态',
      key: 'operational_status',
      width: 120,
      render: (_, record) => {
        if (Number(record.monitoring_enabled) !== 1) return <Text type="secondary">不适用</Text>;
        const statusMap = {
          online: { color: 'green', text: '在线' },
          normal: { color: 'green', text: '在线' },
          offline: { color: 'red', text: '离线' },
          maintenance: { color: 'orange', text: '维护中' },
        };
        const status = statusMap[record.status] || { color: 'default', text: record.status || '未知' };
        return <Tag color={status.color}>{status.text}</Tag>;
      },
    },
    {
      title: '生命周期',
      key: 'lifecycle',
      width: 120,
      render: (_, record) => isRetired(record)
        ? <Tag color="default">已回收/退役</Tag>
        : <Tag color="green">在管</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      fixed: 'right',
      render: (_, record) => {
        const retired = isRetired(record);
        const managementItems = canWrite ? [
          { key: 'edit', label: '移站/编辑', icon: <EditOutlined />, disabled: retired, onClick: () => handleEdit(record) },
          { key: 'recycle', label: retired ? '已登记回收' : '登记回收处置', icon: <SwapOutlined />, disabled: retired, onClick: () => handleRecycleOpen(record) },
          { type: 'divider' },
          {
            key: 'delete', label: retired ? '退役档案不可删除' : '删除设备', danger: true, disabled: retired, icon: <DeleteOutlined />,
            onClick: () => {
              Modal.confirm({ title: '确定删除该设备吗？', content: `设备「${record.device_name || record.device_code}」将被永久删除。`, okText: '删除', okButtonProps: { danger: true }, cancelText: '取消', onOk: async () => {
                const result = await api.delete(`/devices/${record.id}`);
                if (result && !result.error) { message.success('设备已删除'); fetchDevices(); }
                else message.error(result?.error || '删除失败');
              } });
            },
          },
        ] : [];
        return (
        <Space size={2}>
          <Button type="link" size="small" icon={<EyeOutlined />} aria-label={`查看 ${record.device_name || record.device_code} 的设备详情`} onClick={() => handleView(record)}>
            详情
          </Button>
          {managementItems.length > 0 && <Dropdown menu={{ items: managementItems }}><Button type="link" size="small" icon={<MoreOutlined />} aria-label={`管理 ${record.device_name || record.device_code}`}>管理</Button></Dropdown>}
        </Space>
        );
      },
    },
  ];

  return (
    <div className="workspace-embedded-page">
      <WorkspaceToolbar
        actions={(
          <Space>
            <Button icon={<ReloadOutlined />} onClick={handleReset}>重置</Button>
            {canWrite && (
              <>
                <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>批量导入</Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}
                  style={{ background: `linear-gradient(135deg, ${tokens.colorPrimary}, ${tokens.colorPrimaryHover})`, border: 'none' }}>
                  注册设备
                </Button>
              </>
            )}
          </Space>
        )}
      >
        <Input
          aria-label="设备搜索"
          placeholder="搜索设备编码、名称..."
          prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
          allowClear
          value={search}
          onChange={(e) => updateFilter('q', e.target.value)}
          style={{ width: filterInputWidth, borderRadius: 8 }}
        />
        <Select aria-label="设备类型" placeholder="设备类型" allowClear value={typeFilter} onChange={(value) => updateFilter('type', value)}
          style={{ width: filterSelectWidth }} options={typeOptions} showSearch
          filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())} />
        <Select aria-label="所属站点" placeholder="所属站点" allowClear value={siteFilter} onChange={(value) => updateFilter('site', value)}
          style={{ width: filterSelectWidth }} options={siteOptions} showSearch
          filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())} />
        {(search || typeFilter || siteFilter) && <ToolbarMeta label="当前结果">已筛选 {devices.length} 条</ToolbarMeta>}
      </WorkspaceToolbar>
      {sitesError ? (
        <Alert type="warning" showIcon message="站点选项暂未加载" description={sitesError} style={{ marginBottom: 12 }} />
      ) : null}
      {fetchError ? (
        <Alert
          type="warning"
          showIcon
          message={devices.length > 0 ? '设备列表刷新失败，当前保留上次加载的数据' : '设备列表加载失败'}
          description={fetchError}
          action={<Button size="small" onClick={fetchDevices}>重新加载</Button>}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <WorkspaceTable
        columns={columns}
        dataSource={devices}
        rowKey={(r) => r.id || r.code || r.device_code}
        loading={loading}
        emptyType={search || typeFilter || siteFilter ? 'filtered' : 'empty'}
        onRefresh={fetchDevices}
        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100], showTotal: (total) => `共 ${total} 台设备` }}
      />

      {/* ===== View Drawer ===== */}
      <Drawer
        title="设备详情"
        open={viewOpen}
        onClose={() => { setViewOpen(false); setViewingDevice(null); setViewError(''); }}
      width="min(760px, calc(100vw - 24px))"
        destroyOnHidden
      >
        {viewLoading ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        ) : viewingDevice ? (
          <div>
            {viewError ? (
              <Alert
                type="warning"
                showIcon
                message="设备详情未完整加载"
                description={viewError}
                action={<Button size="small" onClick={() => loadDeviceDetails(viewingDevice)}>重新加载</Button>}
                style={{ marginBottom: 12 }}
              />
            ) : null}
            <Descriptions column={1} bordered size="small" styles={{ label: { width: 100 } }}>
              <Descriptions.Item label="设备编码">{viewingDevice.device_code || viewingDevice.code || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备名称">{viewingDevice.device_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备类型">{deviceTypeMap[viewingDevice.device_type] || viewingDevice.device_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备型号">{viewingDevice.device_model || '未录入'}</Descriptions.Item>
              <Descriptions.Item label="生产厂家">{viewingDevice.manufacturer || '未录入'}</Descriptions.Item>
              <Descriptions.Item label="管理范围">
                {isRetired(viewingDevice)
                  ? <Tag>已退出运维管理</Tag>
                  : <Tag color="green">纳入运维管理</Tag>}
              </Descriptions.Item>
              {(viewingDevice.recycle_date || viewingDevice.recycle_destination) && (
                <Descriptions.Item label="回收处置">
                  {viewingDevice.recycle_date || '日期未记录'} · {{ repair: '维修', replace: '更换', scrap: '报废', return: '退回' }[viewingDevice.recycle_destination] || viewingDevice.recycle_destination || '方式未记录'}
                </Descriptions.Item>
              )}
              <Descriptions.Item label="安装日期">{viewingDevice.install_date || '-'}</Descriptions.Item>
              <Descriptions.Item label="所属站点">{viewingDevice.site_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="采集属性">
                {Number(viewingDevice.monitoring_enabled) === 1 ? '采集设备' : '非采集设备，不参与在线率统计'}
              </Descriptions.Item>
              {Number(viewingDevice.monitoring_enabled) === 1 && (
                <Descriptions.Item label="最后数据时间">{viewingDevice.last_data_time || '尚未接入采集'}</Descriptions.Item>
              )}
              {viewingDevice.district && <Descriptions.Item label="所属区域">{viewingDevice.district}</Descriptions.Item>}
              {viewingDevice.manager && <Descriptions.Item label="负责人">{viewingDevice.manager}</Descriptions.Item>}
            </Descriptions>

            <Title level={5} style={{ marginTop: 24, marginBottom: 12 }}>操作历史</Title>
            {(() => {
              const maintLogs = viewingDevice?._logs || [];
              const opLogs = viewingDevice?._op_logs || [];
              const allLogs = [...maintLogs.map(l => ({...l, _src: 'maintenance'})),
                              ...opLogs.map(l => ({...l, _src: 'operation'}))];
              allLogs.sort((a, b) => {
                const tA = a.created_at || a.timestamp || '';
                const tB = b.created_at || b.timestamp || '';
                return tB.localeCompare(tA);
              });
              const displayLogs = allLogs.slice(0, 30);
              if (displayLogs.length === 0) return <Empty description="暂无操作记录" style={{ margin: '16px 0' }} />;
              const actionLabel = { create: '注册', update: '更新', delete: '删除', recycle: '回收处置' };
              return (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {displayLogs.map((log, i) => (
                    <div key={log.id || i} style={{ padding: '8px 12px', borderRadius: 8, background: tokens.colorBgTextHover, fontSize: 13 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {log._src === 'operation' ? (
                          <Tag color={log.action === 'delete' ? 'red' : log.action === 'create' ? 'green' : 'blue'} style={{ fontSize: 11, margin: 0 }}>
                            {actionLabel[log.action] || log.action}
                          </Tag>
                        ) : (
                          <Tag color="purple" style={{ fontSize: 11, margin: 0 }}>维护</Tag>
                        )}
                        <Text style={{ fontWeight: 500 }}>{log.details || log.action || log.remark || '操作记录'}</Text>
                      </div>
                      <div style={{ color: tokens.colorTextSecondary, marginTop: 2 }}>
                        {log.created_at || log.timestamp || ''}
                        {log.operator ? ` · ${log.operator}` : ''}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })()}

            <Divider style={{ margin: '16px 0' }} />
            <Title level={5} style={{ marginBottom: 12 }}>养护记录照片</Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              流程外资料：设备养护 / 维修现场照片在此就近归档，便于项目管理（不参与审核链）。
            </Text>
            {viewingDevice && !isRetired(viewingDevice) && (
              <AttachmentUpload
                sourceType="maintenance"
                category="养护记录"
                sourceId={viewingDevice.id}
                siteId={viewingDevice.site_id}
                buttonText="上传养护照片"
                maxCount={5}
              />
            )}
          </div>
        ) : null}
      </Drawer>

      <BulkImportModal kind="devices" open={importOpen} onClose={() => setImportOpen(false)} onImported={fetchDevices} />

      {/* ===== Create / Edit Modal ===== */}
      <Modal
        open={modalOpen}
        title={editingDevice ? '编辑设备' : '注册设备'}
        onOk={handleModalOk}
        onCancel={() => { setModalOpen(false); setEditingDevice(null); form.resetFields(); }}
        confirmLoading={modalLoading}
        okText={editingDevice ? '确认移站' : '注册'}
        cancelText="取消"
        destroyOnHidden
      >
        {editingDevice && (
          <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 8, background: `${infoColor}0F`, border: `1px solid ${infoColor}26` }}>
            <Text style={{ fontSize: 13, color: tokens.colorTextSecondary }}>
              设备基础信息不可直接修改。如需变更设备类型、名称等，请通过设备回收后重新注册。
            </Text>
          </div>
        )}
        <Form form={form} layout="vertical" style={{ marginTop: editingDevice ? 0 : 16 }}>
          <Form.Item name="device_code" label="设备编码" rules={[{ required: true, message: '请输入设备编码' }]}>
            <Input placeholder="如：设备出厂编号" disabled={!!editingDevice} />
          </Form.Item>
          <Form.Item name="device_name" label="设备名称" rules={[{ required: true, message: '请输入设备名称' }]}>
            <Input placeholder="请输入设备名称" disabled={!!editingDevice} />
          </Form.Item>
          <Form.Item name="device_type" label="设备类型" rules={[{ required: true, message: '请选择设备类型' }]}>
            <Select placeholder="请选择设备类型" options={typeOptions} showSearch disabled={!!editingDevice}
              filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())} />
          </Form.Item>
          <Form.Item name="site_id" label="所属站点" rules={[{ required: true, message: '请选择所属站点' }]}
            tooltip={editingDevice ? '可调整设备所属站点' : undefined}>
            <Select placeholder="请选择站点" options={siteOptions} showSearch
              filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())} />
          </Form.Item>
          <Form.Item name="device_model" label="设备型号">
            <Input placeholder="未录入时可留空，后续补充" />
          </Form.Item>
          <Form.Item name="manufacturer" label="生产厂家">
            <Input placeholder="未录入时可留空，后续补充" />
          </Form.Item>
          <Form.Item name="install_date" label="安装日期">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          {editingDevice && !canWrite ? (
            <div style={{ padding: '8px 0' }}>
              <Text style={{ fontSize: 12, color: tokens.colorTextTertiary }}>
                设备基础信息不可更改，仅可调整所属站点
              </Text>
            </div>
          ) : null}
        </Form>
      </Modal>

      {/* ===== 设备回收处置 Modal（立即生效） ===== */}
      <Modal
        title="登记设备回收处置"
        open={recycleOpen}
        onOk={handleRecycleOk}
        onCancel={() => { setRecycleOpen(false); setRecycleDevice(null); recycleForm.resetFields(); }}
        confirmLoading={recycleLoading}
        okText="确认并立即生效"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        destroyOnHidden
      >
        {recycleDevice && (
          <div style={{ marginBottom: 12, padding: '10px 14px', borderRadius: 8, background: `${tokens.colorWarningBg}`, border: `1px solid ${tokens.colorWarningBorder}` }}>
            <Text style={{ fontSize: 13 }}>
              设备：<Text strong>{recycleDevice.device_name}</Text>（<Text type="secondary">{recycleDevice.device_code}</Text>）
            </Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              操作人：{user?.real_name || user?.name || user?.username || '当前登录用户'}。确认后设备会立即退出在管范围，不经过审批，且档案不可删除。
            </Text>
          </div>
        )}
        <Form form={recycleForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="destination" label="回收方式" rules={[{ required: true, message: '请选择回收方式' }]}>
            <Select placeholder="请选择回收方式" options={[
              { value: 'repair', label: '维修' },
              { value: 'replace', label: '更换' },
              { value: 'scrap', label: '报废' },
              { value: 'return', label: '退回' },
            ]} />
          </Form.Item>
          <Form.Item name="recycle_date" label="回收日期" rules={[{ required: true, message: '请选择回收日期' }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="reason" label="回收原因" rules={[{ required: true, message: '请填写回收原因' }]}>
            <Input.TextArea rows={3} placeholder="如：设备老化、精度下降、无法修复" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ---------- Spare Parts Inventory Tab ----------
function SparePartsTab() {
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const canWrite = hasRole(user, 'admin');
  const successColor = statusColors.success[isDark ? 'dark' : 'light'];
  const warningColor = statusColors.warning[isDark ? 'dark' : 'light'];
  const accentColor = statusColors.accent[isDark ? 'dark' : 'light'];
  const [parts, setParts] = useState([]);
  const [partRequests, setPartRequests] = useState([]);
  const [requestDrawerOpen, setRequestDrawerOpen] = useState(false);
  const [requestAction, setRequestAction] = useState(null);
  const [actionRequest, setActionRequest] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [requestActionForm] = Form.useForm();
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [requestLedger, setRequestLedger] = useState(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState('');
  const [search, setSearch] = useState('');

  // View / Create / Edit state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingPart, setViewingPart] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPart, setEditingPart] = useState(null);
  const [modalLoading, setModalLoading] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [sites, setSites] = useState([]);
  const [form] = Form.useForm();

  // In/Out stock modal state
  const [stockModalOpen, setStockModalOpen] = useState(false);
  const [stockType, setStockType] = useState('in'); // 'in' or 'out'
  const [stockPart, setStockPart] = useState(null);
  const [stockLoading, setStockLoading] = useState(false);
  const [stockForm] = Form.useForm();

  // 旧件回收 modal state
  const [recoverModalOpen, setRecoverModalOpen] = useState(false);
  const [recoverPart, setRecoverPart] = useState(null);
  const [recoverLoading, setRecoverLoading] = useState(false);
  const [recoverForm] = Form.useForm();

  const fetchParts = useCallback(async () => {
    setLoading(true);
    setFetchError('');
    try {
      const params = search ? `?search=${encodeURIComponent(search)}` : '';
      const [inventoryResult, requestsResult] = await Promise.allSettled([
        api.getStrict(`/parts/inventory${params}`),
        api.getStrict('/parts/requests'),
      ]);
      if (inventoryResult.status === 'fulfilled') {
        const data = inventoryResult.value;
        setParts(Array.isArray(data) ? data : (data?.parts || []));
      }
      if (requestsResult.status === 'fulfilled') {
        setPartRequests(Array.isArray(requestsResult.value) ? requestsResult.value : []);
      }
      const failures = [inventoryResult, requestsResult].filter(result => result.status === 'rejected');
      if (failures.length) {
        setFetchError(failures.map(result => result.reason?.message || '数据加载失败').join('；'));
      }
    } catch (error) {
      setFetchError(error?.message || '备件数据加载失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => { fetchParts(); }, [fetchParts]);

  useEffect(() => {
    api.getStrict('/sites').then(data => {
      const list = Array.isArray(data) ? data : (data?.sites || []);
      setSites(list);
    }).catch(() => {});
  }, []);

  const handleView = useCallback(async (record) => {
    setViewingPart(record);
    setViewOpen(true);
    // Fetch inventory logs for this part
    try {
      const logs = await api.getStrict(`/parts/inventory/${record.id}/logs`);
      setViewingPart(prev => ({ ...prev, _logs: Array.isArray(logs) ? logs : [] }));
    } catch {
      setViewingPart(prev => ({ ...prev, _logs: [] }));
    }
  }, []);

  const handleCreate = useCallback(() => {
    setEditingPart(null);
    form.resetFields();
    form.setFieldsValue({ quantity: 0, min_quantity: 5, unit: '个' });
    setModalOpen(true);
  }, [form]);

  // Edit only basic info (no quantity, no location)
  const handleEdit = useCallback((record) => {
    setEditingPart(record);
    form.setFieldsValue({
      part_code: record.part_code,
      part_name: record.part_name,
      category: record.category,
      unit: record.unit,
      min_quantity: record.min_quantity,
      remark: record.remark,
    });
    setModalOpen(true);
  }, [form]);

  const handleModalOk = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setModalLoading(true);
      if (editingPart) {
        // Only submit editable fields
        const result = await api.put(`/parts/inventory/${editingPart.id}`, {
          part_name: values.part_name,
          category: values.category,
          unit: values.unit,
          min_quantity: values.min_quantity,
          remark: values.remark,
        });
        if (result && !result.error) {
          message.success('备件信息已更新');
          setModalOpen(false);
          fetchParts();
        } else {
          message.error(result?.error || '更新失败');
        }
      } else {
        const result = await api.post('/parts/inventory', values);
        if (result && !result.error) {
          message.success('备件新增成功');
          setModalOpen(false);
          fetchParts();
        } else {
          message.error(result?.error || '新增失败');
        }
      }
    } catch { /* validation error */ }
    setModalLoading(false);
  }, [form, editingPart, fetchParts]);

  // In/Out stock handlers
  const handleStockOpen = useCallback((record, type) => {
    setStockPart(record);
    setStockType(type);
    stockForm.resetFields();
    stockForm.setFieldsValue({ quantity: 1 });
    setStockModalOpen(true);
  }, [stockForm]);

  const handleStockOk = useCallback(async () => {
    let values;
    try {
      values = await stockForm.validateFields();
    } catch (e) {
      const errs = e?.errorFields;
      message.error(errs?.[0]?.errors?.[0] || '请检查表单填写');
      return;
    }
    setStockLoading(true);
    try {
      const result = await api.post(`/parts/inventory/${stockPart.id}/stock`, {
        type: stockType,
        quantity: values.quantity,
        reason: values.reason || '',
        work_order_no: values.work_order_no || '',
      });
      if (result && !result.error) {
        message.success(stockType === 'in' ? '入库成功' : '出库成功');
        setStockModalOpen(false);
        setStockPart(null);
        fetchParts();
      } else {
        message.error(result?.error || '操作失败');
      }
    } catch (e) {
      message.error(e?.response?.data?.error || e?.message || '网络异常');
    } finally {
      setStockLoading(false);
    }
  }, [stockForm, stockPart, stockType, fetchParts]);

  // 旧件回收：更换后旧件退回公司库存（区别于采购入库）
  const handleRecoverOpen = useCallback((part) => {
    setRecoverPart(part);
    recoverForm.resetFields();
    recoverForm.setFieldsValue({ quantity: 1 });
    setRecoverModalOpen(true);
  }, [recoverForm]);

  const handleRecoverOk = useCallback(async () => {
    let values;
    try {
      values = await recoverForm.validateFields();
    } catch (e) {
      const errs = e?.errorFields;
      message.error(errs?.[0]?.errors?.[0] || '请检查表单填写');
      return;
    }
    setRecoverLoading(true);
    try {
      const result = await api.post('/parts/recovery', {
        part_id: recoverPart.id,
        quantity: values.quantity,
        work_order_no: values.work_order_no || '',
        remark: values.remark || '旧件回收',
      });
      if (result && !result.error) {
        message.success(result.message || '回收成功');
        setRecoverModalOpen(false);
        setRecoverPart(null);
        fetchParts();
      } else {
        message.error(result?.error || '回收失败');
      }
    } catch (e) {
      message.error(e?.response?.data?.error || e?.message || '网络异常');
    } finally {
      setRecoverLoading(false);
    }
  }, [recoverForm, recoverPart, fetchParts]);

  const siteOptions = sites.map(s => ({ value: s.id, label: s.name || s.code }));

  const openRequestAction = useCallback((row, action) => {
    setActionRequest(row);
    setRequestAction(action);
    requestActionForm.resetFields();
    if (action === 'fulfill') {
      requestActionForm.setFieldsValue({
        supplier: row.supplier || '',
        actual_amount: row.actual_amount ?? row.estimated_amount,
        destination: 'direct_use',
      });
    }
  }, [requestActionForm]);

  const submitRequestAction = useCallback(async () => {
    let values;
    try {
      values = await requestActionForm.validateFields();
    } catch {
      return;
    }
    setActionLoading(true);
    try {
      let result;
      if (requestAction === 'issue') {
        result = await api.postStrict(`/parts/requests/${actionRequest.id}/issue`, {
          items: (actionRequest.items || []).map(item => ({ part_id: item.part_id, quantity: item.quantity })),
        });
      } else if (requestAction === 'order') {
        result = await api.postStrict(`/parts/requests/${actionRequest.id}/order`, values);
      } else {
        result = await api.postStrict(`/parts/requests/${actionRequest.id}/fulfill`, {
          ...values,
          evidence_urls: [],
        });
      }
      message.success(result.message || '履约状态已更新');
      setRequestAction(null);
      setActionRequest(null);
      fetchParts();
    } catch (error) {
      message.error(error.message || '操作失败，请检查后重试');
    } finally {
      setActionLoading(false);
    }
  }, [requestActionForm, requestAction, actionRequest, fetchParts]);

  const openRequestLedger = useCallback(async (row) => {
    setLedgerOpen(true);
    setLedgerLoading(true);
    setRequestLedger(null);
    try {
      setRequestLedger(await api.getStrict(`/parts/requests/${row.id}/ledger`));
    } catch (error) {
      message.error(error.message || '履约台账加载失败');
    } finally {
      setLedgerLoading(false);
    }
  }, []);

  const columns = [
    { title: '备件编号', dataIndex: 'part_code', key: 'part_code', width: 110,
      render: (text, r) => <Text strong style={{ color: tokens.colorPrimary }}>{text || `#${r.id}`}</Text> },
    { title: '备件名称', dataIndex: 'part_name', key: 'part_name', ellipsis: true },
    { title: '库存数量', dataIndex: 'quantity', key: 'quantity', width: 110, align: 'center',
      render: (val, r) => {
        const min = r.min_quantity || 5;
        const isLow = val != null && val < min;
        return <Text style={{ color: isLow ? tokens.colorError : tokens.colorText, fontWeight: isLow ? 600 : 400 }}>{val ?? '-'} {isLow && <Tag color="red" style={{ marginLeft: 4, fontSize: 11 }}>低库存</Tag>}</Text>;
      }},
    { title: '存放位置', dataIndex: 'location', key: 'location', width: 120,
      render: (v) => v || <Text type="secondary">未录入</Text> },
    { title: '操作', key: 'actions', width: 210,
      render: (_, r) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<EyeOutlined />} aria-label={`查看 ${r.part_name || r.part_code} 的备件详情`} onClick={() => handleView(r)}>详情</Button>
          {canWrite && <Button type="link" size="small" style={{ color: successColor }} icon={<ArrowUpOutlined />} onClick={() => handleStockOpen(r, 'in')}>入库</Button>}
          {canWrite && <Button type="link" size="small" style={{ color: warningColor }} icon={<ArrowDownOutlined />} onClick={() => handleStockOpen(r, 'out')}>出库</Button>}
          {canWrite && <Dropdown
            trigger={['click']}
            menu={{ items: [
              { key: 'edit', label: '编辑备件', icon: <EditOutlined />, onClick: () => handleEdit(r) },
              { key: 'recover', label: '旧件回收', icon: <SwapOutlined />, onClick: () => handleRecoverOpen(r) },
            ] }}
          >
            <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`管理 ${r.part_name || r.part_code}`} />
          </Dropdown>}
        </Space>
      )},
  ];

  const requestColumns = [
    { title: '需求单', dataIndex: 'request_no', width: 150, fixed: 'left' },
    { title: '备件', dataIndex: 'requested_part_name', width: 160, ellipsis: true,
      render: (value, row) => value || row.items?.[0]?.part_name || row.items?.[0]?.part_sku || '-' },
    { title: '方式', dataIndex: 'fulfillment_type', width: 110,
      render: value => ({ stock: '库存领用', local_purchase: '附近急购', vendor_order: '厂家订购' }[value] || value) },
    { title: '数量', width: 80, align: 'center', render: (_, row) => row.items?.[0]?.quantity || 0 },
    { title: '状态', dataIndex: 'status', width: 100,
      render: value => <Tag color={{ pending: 'orange', approved: 'blue', ordered: 'cyan', issued: 'green', completed: 'green', rejected: 'red' }[value]}>{({ pending: '待审批', approved: '已批准', ordered: '运输中', issued: '已领用', completed: '已完成', rejected: '已驳回' }[value] || value)}</Tag> },
    { title: '申请人', dataIndex: 'requester_name', width: 100, render: value => value || '未记录' },
    { title: '站点/工单', width: 170, ellipsis: true,
      render: (_, row) => <div><div>{row.site_name || '站点未记录'}</div>{row.work_order_no ? <Text type="secondary">{row.work_order_no}</Text> : null}</div> },
    { title: '申请/批准时间', width: 175,
      render: (_, row) => <div><div>{row.created_at || '-'}</div>{row.approved_at ? <Text type="secondary">批准：{row.approved_at}</Text> : null}</div> },
    { title: '金额', width: 100, align: 'right', render: (_, row) => row.actual_amount != null ? `¥${Number(row.actual_amount).toFixed(2)}` : (row.estimated_amount != null ? `约 ¥${Number(row.estimated_amount).toFixed(2)}` : '-') },
    { title: '当前责任人', dataIndex: 'current_owner', width: 110 },
    { title: '下一步', dataIndex: 'next_action', width: 210, ellipsis: true },
    { title: '履约操作', key: 'request_action', width: 180, fixed: 'right', render: (_, row) => (
      <Space size={0}>
        <Button type="link" size="small" onClick={() => openRequestLedger(row)}>台账</Button>
        {canWrite && row.status === 'approved' && row.fulfillment_type === 'stock' && (
          <Button type="link" size="small" onClick={() => openRequestAction(row, 'issue')}>登记领用</Button>
        )}
        {canWrite && row.status === 'approved' && row.fulfillment_type === 'vendor_order' && (
          <Button type="link" size="small" onClick={() => openRequestAction(row, 'order')}>登记下单</Button>
        )}
        {canWrite && ['approved', 'ordered'].includes(row.status) && row.fulfillment_type !== 'stock' && (
          <Button type="link" size="small" onClick={() => openRequestAction(row, 'fulfill')}>确认到货</Button>
        )}
      </Space>
    ) },
  ];

  return (
    <div className="workspace-embedded-page">
      <WorkspaceToolbar
        actions={canWrite && (
          <Space>
            <Button icon={<InboxOutlined />} onClick={() => setRequestDrawerOpen(true)}>备件需求 {partRequests.length}</Button>
            <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>批量导入</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}
              style={{ background: `linear-gradient(135deg, ${tokens.colorPrimary}, ${tokens.colorPrimaryHover})`, border: 'none' }}>
              新增备件
            </Button>
          </Space>
        )}
      >
        <Input
          aria-label="备件搜索"
          placeholder="搜索备件名称、编号..."
          prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: filterInputWidth, borderRadius: 8 }}
        />
      </WorkspaceToolbar>
      {fetchError ? (
        <Alert
          type="warning"
          showIcon
          message={parts.length > 0 ? '备件数据刷新失败，当前保留上次加载的数据' : '备件数据加载失败'}
          description={fetchError}
          action={<Button size="small" onClick={fetchParts}>重新加载</Button>}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <WorkspaceTable
            columns={columns}
            dataSource={parts}
            rowKey={(r) => r.id || r.part_code}
            loading={loading}
            emptyType={search ? 'filtered' : 'empty'}
            onRefresh={fetchParts}
          />

      <Drawer title="备件需求与履约记录" open={requestDrawerOpen} onClose={() => setRequestDrawerOpen(false)} width="min(1280px, 96vw)">
        <Text type="secondary">审批只形成授权，不会自动扣减库存。请根据“当前责任人”和“下一步”完成领用、下单或到货登记。</Text>
        <Table
          style={{ marginTop: 16 }}
          size="small"
          rowKey="id"
          columns={requestColumns}
          dataSource={partRequests}
          scroll={{ x: 1700 }}
          pagination={{ pageSize: 12, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="暂无备件需求" /> }}
        />
      </Drawer>

      <Modal
        title={{ issue: '登记现场领用', order: '登记厂家下单', fulfill: '确认采购到货' }[requestAction]}
        open={!!requestAction}
        onOk={submitRequestAction}
        onCancel={() => { setRequestAction(null); setActionRequest(null); requestActionForm.resetFields(); }}
        confirmLoading={actionLoading}
        okText="确认登记"
        destroyOnHidden
      >
        {actionRequest && (
          <Text type="secondary">{actionRequest.request_no} · {actionRequest.requested_part_name || actionRequest.items?.[0]?.part_name} · {actionRequest.site_name || '站点未记录'}</Text>
        )}
        <Form form={requestActionForm} layout="vertical" style={{ marginTop: 16 }}>
          {requestAction === 'issue' && (
            <div style={{ marginBottom: 12 }}>本次将按批准数量登记领用并扣减实际库存。库存不足时不会写入任何流水。</div>
          )}
          {requestAction === 'order' && <>
            <Form.Item name="supplier" label="供应商" rules={[{ required: true, message: '请填写供应商' }]}><Input /></Form.Item>
            <Form.Item name="tracking_no" label="物流单号"><Input placeholder="尚未取得时可留空" /></Form.Item>
          </>}
          {requestAction === 'fulfill' && <>
            <Form.Item name="supplier" label="供应商"><Input /></Form.Item>
            <Form.Item name="actual_amount" label="实际金额（元）" rules={[{ required: true, message: '请填写实际金额' }]}><Input type="number" min="0" /></Form.Item>
            <Form.Item name="receipt_no" label="票据编号" rules={[{ required: true, message: '当前网页端请填写票据编号，照片可在移动端补充' }]}><Input placeholder="发票号、收据号或采购凭证编号" /></Form.Item>
            <Form.Item name="destination" label="物资去向" rules={[{ required: true, message: '请选择物资去向' }]}>
              <Select options={[{ value: 'direct_use', label: '现场直接使用' }, { value: 'warehouse', label: '带回入库' }]} />
            </Form.Item>
            <Form.Item name="old_part_disposition" label="旧件处置"><Input placeholder="无旧件时填写“无”" /></Form.Item>
          </>}
        </Form>
      </Modal>

      <Drawer title="备件履约台账" open={ledgerOpen} onClose={() => setLedgerOpen(false)} width={680}>
        {ledgerLoading ? <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div> : requestLedger ? <>
          <Descriptions bordered size="small" column={1} styles={{ label: { width: 110 } }}>
            <Descriptions.Item label="需求单">{requestLedger.request?.request_no}</Descriptions.Item>
            <Descriptions.Item label="申请人">{requestLedger.request?.requester_name || '未记录'}</Descriptions.Item>
            <Descriptions.Item label="站点">{requestLedger.request?.site_name || '未记录'}</Descriptions.Item>
            <Descriptions.Item label="关联工单">{requestLedger.request?.work_order_no || '未关联'}</Descriptions.Item>
            <Descriptions.Item label="批准时间">{requestLedger.request?.approved_at || '尚未批准'}</Descriptions.Item>
            <Descriptions.Item label="下单时间">{requestLedger.request?.ordered_at || '尚未下单'}</Descriptions.Item>
            <Descriptions.Item label="到货时间">{requestLedger.request?.received_at || '尚未到货'}</Descriptions.Item>
          </Descriptions>
          <Title level={5} style={{ marginTop: 20 }}>事件记录</Title>
          {requestLedger.events?.length ? requestLedger.events.map((event, index) => (
            <div key={`${event.created_at}-${index}`} style={{ padding: '8px 0', borderBottom: `1px solid ${tokens.colorBorderSecondary}` }}>
              <Text strong>{({ submitted: '提交申请', approved: '审批通过', rejected: '审批驳回', ordered: '厂家下单', issued: '现场领用', fulfilled: '确认到货' }[event.event_type] || event.event_type)}</Text>
              <div><Text type="secondary">{event.created_at} · {event.operator || '系统'}</Text></div>
            </div>
          )) : <Empty description="暂无事件记录" />}
        </> : <Empty description="台账加载失败" />}
      </Drawer>

      {/* ===== View Drawer ===== */}
      <Drawer
        title="备件详情"
        open={viewOpen}
        onClose={() => { setViewOpen(false); setViewingPart(null); }}
        width={520}
        destroyOnHidden
      >
        {viewingPart && (
          <div>
            <Descriptions column={1} bordered size="small" styles={{ label: { width: 90 } }}>
              <Descriptions.Item label="备件编号">{viewingPart.part_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="备件名称">{viewingPart.part_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="规格型号">{viewingPart.spec || viewingPart.model || <Text type="secondary">未录入</Text>}</Descriptions.Item>
              <Descriptions.Item label="适用设备">
                {Array.isArray(viewingPart.device_types) && viewingPart.device_types.length
                  ? viewingPart.device_types.map(type => deviceTypeMap[type] || type).join('、')
                  : <Text type="secondary">未录入</Text>}
              </Descriptions.Item>
              <Descriptions.Item label="厂家名称">{viewingPart.manufacturer || <Text type="secondary">未录入</Text>}</Descriptions.Item>
              <Descriptions.Item label="库存数量">
                {viewingPart.quantity ?? '-'}
                {(viewingPart.quantity != null && viewingPart.min_quantity != null && viewingPart.quantity < viewingPart.min_quantity) && (
                  <Tag color="red" style={{ marginLeft: 6 }}>低库存</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="最低库存">{viewingPart.min_quantity ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="单位">{viewingPart.unit || '-'}</Descriptions.Item>
              <Descriptions.Item label="存放位置">{viewingPart.location || '-'}</Descriptions.Item>
              <Descriptions.Item label="所属站点">{viewingPart.site_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="备注">{viewingPart.remark || '-'}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{viewingPart.updated_at || '-'}</Descriptions.Item>
            </Descriptions>

            {/* Inventory Logs */}
            <div style={{ marginTop: 24 }}>
              <Text strong style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
                <SwapOutlined /> 出入库记录
              </Text>
              {viewingPart._logs && viewingPart._logs.length > 0 ? (
                <Table
                  dataSource={viewingPart._logs}
                  columns={[
                    { title: '类型', dataIndex: 'type', key: 'type', width: 70,
                      render: (v, r) => r.ref_type === 'recovery'
                        ? <Tag color="cyan">回收</Tag>
                        : <Tag color={v === 'in' ? 'green' : 'orange'}>{v === 'in' ? '入库' : '出库'}</Tag> },
                    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 60, align: 'center' },
                    { title: '事由', dataIndex: 'reason', key: 'reason', width: 220,
                      render: (v) => <TableLongText value={v} /> },
                    { title: '关联工单', dataIndex: 'work_order_no', key: 'work_order_no', width: 110,
                      render: (v) => v || '-' },
                    { title: '操作人', dataIndex: 'operator', key: 'operator', width: 80, render: (v) => v || '-' },
                    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 140, render: (v) => v || '-' },
                  ]}
                  rowKey={(r) => r.id || `${r.created_at}-${r.type}`}
                  pagination={false}
                  size="small"
                  scroll={{ x: 700, y: 240 }}
                />
              ) : (
                <Empty description="暂无出入库记录" style={{ padding: '16px 0' }} />
              )}
            </div>
          </div>
        )}
      </Drawer>

      <BulkImportModal kind="parts" open={importOpen} onClose={() => setImportOpen(false)} onImported={fetchParts} />

      {/* ===== Create / Edit Modal (basic info only) ===== */}
      <Modal
        title={editingPart ? '编辑备件信息' : '新增备件'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => { setModalOpen(false); setEditingPart(null); form.resetFields(); }}
        confirmLoading={modalLoading}
        okText={editingPart ? '保存' : '新增'}
        cancelText="取消"
        destroyOnHidden
      >
        {editingPart && (
          <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: `${warningColor}0F`, border: `1px solid ${warningColor}26` }}>
            <Text style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
              数量和存放位置不可直接修改，请通过入库/出库操作调整库存。
            </Text>
          </div>
        )}
        <Form form={form} layout="vertical" style={{ marginTop: editingPart ? 0 : 16 }}>
          <Form.Item name="part_name" label="备件名称" rules={[{ required: true, message: '请输入备件名称' }]}>
            <Input placeholder="请输入备件名称" />
          </Form.Item>
          <Form.Item name="part_code" label="备件编号">
            <Input placeholder="留空自动生成" disabled={!!editingPart} />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Input placeholder="如: 传感器、电源、通信模块" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="min_quantity" label="最低库存">
                <Input type="number" min={0} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="unit" label="单位">
                <Input placeholder="个/套/台" />
              </Form.Item>
            </Col>
          </Row>
          {!editingPart && (
            <Form.Item name="quantity" label="初始数量" rules={[{ required: true, message: '请输入初始数量' }]}>
              <Input type="number" min={0} />
            </Form.Item>
          )}
          <Form.Item name="site_id" label="存放站点">
            <Select placeholder="请选择站点" options={siteOptions} showSearch allowClear
              filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="备注信息" />
          </Form.Item>
        </Form>
      </Modal>

      {/* ===== In/Out Stock Modal ===== */}
      <Modal
        title={stockType === 'in' ? '备件入库' : '备件出库'}
        open={stockModalOpen}
        onOk={handleStockOk}
        onCancel={() => { setStockModalOpen(false); setStockPart(null); stockForm.resetFields(); }}
        confirmLoading={stockLoading}
        okText="确认"
        cancelText="取消"
        destroyOnHidden
      >
        {stockPart && (
          <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: stockType === 'in' ? `${successColor}0F` : `${warningColor}0F`, border: `1px solid ${stockType === 'in' ? `${successColor}26` : `${warningColor}26`}` }}>
            <Text style={{ fontSize: 13 }}>
              <Text strong>{stockPart.part_name}</Text>
              <Text type="secondary" style={{ marginLeft: 12 }}>当前库存: {stockPart.quantity ?? 0} {stockPart.unit || '个'}</Text>
            </Text>
          </div>
        )}
        <Form form={stockForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="quantity" label={stockType === 'in' ? '入库数量' : '出库数量'} rules={[{ required: true, message: '请输入数量' }]}>
            <Input type="number" min={1} placeholder="请输入数量" />
          </Form.Item>
          <Form.Item name="reason" label="事由">
            <Input.TextArea rows={2} placeholder={stockType === 'in' ? '如: 采购入库、退库' : '如: 工单维修领用、更换'} />
          </Form.Item>
          <Form.Item name="work_order_no" label="关联工单号">
            <Input placeholder="可选，关联工单号" />
          </Form.Item>
        </Form>
      </Modal>

      {/* ===== 旧件回收 Modal ===== */}
      <Modal
        title="旧件回收"
        open={recoverModalOpen}
        onOk={handleRecoverOk}
        onCancel={() => { setRecoverModalOpen(false); setRecoverPart(null); recoverForm.resetFields(); }}
        confirmLoading={recoverLoading}
        okText="确认回收"
        cancelText="取消"
        destroyOnHidden
      >
        {recoverPart && (
          <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: `${accentColor}0F`, border: `1px solid ${accentColor}26` }}>
            <Text style={{ fontSize: 13 }}>
              <Text strong>{recoverPart.part_name}</Text>
              <Text type="secondary" style={{ marginLeft: 12 }}>当前库存: {recoverPart.quantity ?? 0} {recoverPart.unit || '个'}</Text>
            </Text>
          </div>
        )}
        <Text type="secondary" style={{ fontSize: 12 }}>
          更换备件后旧件退回公司库存，单独记为「回收」入库，便于后续维修 / 报废 / 采购决策。
        </Text>
        <Form form={recoverForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="quantity" label="回收数量" rules={[{ required: true, message: '请输入数量' }]}>
            <Input type="number" min={1} placeholder="请输入数量" />
          </Form.Item>
          <Form.Item name="work_order_no" label="关联工单号">
            <Input placeholder="可选，关联工单号" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="如: 更换下的旧电极，待维修" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ---------- Device Recycling Tab ----------
function DeviceRecyclingTab() {
  const { tokens } = useTheme();
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState('');

  const fetchRecords = useCallback(async () => {
    setLoading(true);
    setFetchError('');
    try {
      const data = await api.getStrict('/device-recycle');
      setRecords(Array.isArray(data) ? data : (data?.records || []));
    } catch (error) {
      setFetchError(error?.message || '设备回收记录加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchRecords(); }, [fetchRecords]);

  const columns = [
    { title: '设备编码', dataIndex: 'device_code', key: 'device_code', width: 110,
      render: (text, r) => <Text strong style={{ color: tokens.colorPrimary }}>{text || `#${r.id}`}</Text> },
    { title: '设备名称', dataIndex: 'device_name', key: 'device_name', width: 125, ellipsis: true },
    { title: '设备类型', dataIndex: 'device_type', key: 'device_type', width: 110,
      render: (val) => val ? <Tag>{deviceTypeMap[val] || val}</Tag> : '-' },
    { title: '生产厂家', dataIndex: 'manufacturer', key: 'manufacturer', width: 95, ellipsis: true,
      render: (value) => value || '未录入' },
    { title: '原属站点', dataIndex: 'site_name', key: 'site_name', width: 80, render: (v) => v || '-' },
    { title: '回收原因', dataIndex: 'reason', key: 'reason', width: 220,
      render: (value) => <TableLongText value={value} /> },
    { title: '回收方式', dataIndex: 'destination', key: 'destination', width: 80,
      render: (val) => {
        const map = { repair: '维修', replace: '更换', scrap: '报废', return: '退回' };
        return <Tag color={val === 'scrap' ? 'red' : 'blue'}>{map[val] || val || '-'}</Tag>;
      }},
    { title: '回收时间', dataIndex: 'recycle_date', key: 'recycle_date', width: 105, render: (v) => v || '-' },
    { title: '操作人', dataIndex: 'operator_name', key: 'operator_name', width: 80, render: (v) => v || '-' },
  ];

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {fetchError ? (
        <Alert
          type="warning"
          showIcon
          message={records.length > 0 ? '回收记录刷新失败，当前保留上次加载的数据' : '回收记录加载失败'}
          description={fetchError}
          action={<Button size="small" onClick={fetchRecords}>重新加载</Button>}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <WorkspaceTable
            columns={columns}
            dataSource={records}
            rowKey={(r) => r.id || r.device_code}
            loading={loading}
            onRefresh={fetchRecords}
          />
    </div>
  );
}

// ---------- Operation Logs Tab ----------
function OperationLogsTab() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState('');

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setFetchError('');
    try {
      const [opLogs, dash] = await Promise.all([
        api.getStrict('/operation-logs?limit=50'),
        api.getStrict('/parts/dashboard'),
      ]);
      const opRows = Array.isArray(opLogs) ? opLogs : [];
      const invRows = (dash?.latest_operations || []).map(o => ({
        id: `inv-${o.created_at}-${o.part_id}-${o.type}`,
        created_at: o.created_at,
        operator: o.operator || '系统',
        action: o.type === 'in' ? '入库' : '出库',
        target_type: '备件',
        details: `${o.part_name || o.part_code || '备件'} ${o.type === 'in' ? '+' : '-'}${o.quantity}`,
        _type: 'inventory',
      }));
      const merged = [...opRows.map(r => ({...r, _type: 'operation'})), ...invRows];
      merged.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
      setLogs(merged.slice(0, 100));
    } catch (error) {
      setFetchError(error?.message || '操作日志加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  const actionLabel = { create: '注册', update: '更新', delete: '删除', recycle: '回收处置', approve: '审批通过', reject: '驳回', '入库': '入库', '出库': '出库' };

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 150 },
    { title: '操作人', dataIndex: 'operator', key: 'operator', width: 100,
      render: (v) => v || '-' },
    { title: '操作类型', dataIndex: 'action', key: 'action', width: 110,
      render: (v) => {
        const color = v === 'delete' ? 'red' : v === 'create' ? 'green' : v === '入库' ? 'green' : v === '出库' ? 'red' : 'blue';
        return <Tag color={color} style={{ margin: 0, borderRadius: 4, fontSize: 11 }}>{actionLabel[v] || v}</Tag>;
      }},
    { title: '目标', dataIndex: 'target_type', key: 'target_type', width: 90,
      render: (v) => v === 'device' ? '设备' : v === 'part' ? '备件' : v || '-' },
    { title: '详情', dataIndex: 'details', key: 'details', width: 460,
      render: (value) => <TableLongText value={value} /> },
  ];

  return (
    <>
      {fetchError ? (
        <Alert
          type="warning"
          showIcon
          message={logs.length > 0 ? '操作日志刷新失败，当前保留上次加载的数据' : '操作日志加载失败'}
          description={fetchError}
          action={<Button size="small" onClick={fetchLogs}>重新加载</Button>}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <WorkspaceTable columns={columns} dataSource={logs} rowKey="id" loading={loading} onRefresh={fetchLogs} />
    </>
  );
}

// ---------- Main Page ----------
export default function EquipmentPage() {
  const { isDark } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const [dashData, setDashData] = useState(null);
  const [dashError, setDashError] = useState('');

  const tabKeys = ['ledger', 'spare-parts', 'recycling', 'operation-logs'];
  const requestedTab = searchParams.get('tab');
  const activeTab = tabKeys.includes(requestedTab) ? requestedTab : 'ledger';

  useEffect(() => {
    api.getStrict('/parts/dashboard').then(data => {
      if (data && typeof data === 'object') setDashData(data);
      setDashError('');
    }).catch(error => setDashError(error?.message || '库存统计加载失败'));
  }, []);

  const changeTab = useCallback((key) => {
    const next = new URLSearchParams(searchParams);
    if (key === 'ledger') next.delete('tab');
    else next.set('tab', key);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const tabItems = [
    {
      key: 'ledger',
      label: '设备台账',
      children: <DeviceLedgerTab />,
    },
    {
      key: 'spare-parts',
      label: '备件库存',
      children: <SparePartsTab />,
    },
    {
      key: 'recycling',
      label: '设备回收',
      children: <DeviceRecyclingTab />,
    },
    {
      key: 'operation-logs',
      label: '操作日志',
      children: <OperationLogsTab />,
    },
  ];

  return (
    <WorkspacePage title="设备与物资" subtitle="维护设备台账、现场耗材和回收记录。">
      <StatusStrip items={[
        { label: '低库存预警', value: dashData ? (dashData.low_stock || 0) : '—', color: statusColors.danger[isDark ? 'dark' : 'light'] },
      ]} />
      {dashError ? <Alert type="warning" showIcon message="库存统计暂未加载" description={dashError} style={{ marginBottom: 12 }} /> : null}
      <Tabs type="line" items={tabItems} activeKey={activeTab} onChange={changeTab} />
    </WorkspacePage>
  );
}
