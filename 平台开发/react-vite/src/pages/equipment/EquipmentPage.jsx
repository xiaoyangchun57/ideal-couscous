import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  Table, Card, Input, Select, Button, Space, Tag, Tabs,
  Typography, message, Spin, Empty, Badge, Modal, Form,
  Statistic, Row, Col, Descriptions, Drawer, Divider, DatePicker,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, PlusOutlined, EyeOutlined,
  EditOutlined, DeleteOutlined, ToolOutlined, DatabaseOutlined,
  InboxOutlined, SwapOutlined, ExclamationCircleOutlined,
  CheckCircleOutlined, WarningOutlined, StopOutlined,
  ArrowUpOutlined, ArrowDownOutlined, HistoryOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import AttachmentUpload from '../../components/AttachmentUpload';
import { deviceTypeMap } from '../../services/constants';

const { Title, Text } = Typography;

// ---------- Simulated device model & manufacturer data ----------
const deviceModelMap = {
  multi_param_analyzer: 'WQ-MA900',
  ph_meter: 'PH-6100',
  do_sensor: 'DO-3050',
  turbidity_meter: 'TB-880',
  ammonia_analyzer: 'NH-4200',
  codmn_analyzer: 'CODMn-5600',
  tp_analyzer: 'TP-3100',
  tn_analyzer: 'TN-7800',
  conductivity_meter: 'EC-4500',
  submersible_pump: 'SP-200W',
  sample_float: 'SF-304',
  dtu: 'DTU-4100',
  fire_extinguisher: 'FE-ABC4',
  lighting: 'LED-SOLAR',
};

const deviceMfrMap = {
  multi_param_analyzer: { name: '哈希水质分析仪器有限公司', tel: '021-68415678' },
  ph_meter: { name: '梅特勒-托利多仪器有限公司', tel: '021-64093388' },
  do_sensor: { name: '哈希水质分析仪器有限公司', tel: '021-68415678' },
  turbidity_meter: { name: '哈希水质分析仪器有限公司', tel: '021-68415678' },
  ammonia_analyzer: { name: '聚光科技股份有限公司', tel: '0571-85012800' },
  codmn_analyzer: { name: '聚光科技股份有限公司', tel: '0571-85012800' },
  tp_analyzer: { name: '力合科技有限公司', tel: '0755-26551800' },
  tn_analyzer: { name: '力合科技有限公司', tel: '0755-26551800' },
  conductivity_meter: { name: '梅特勒-托利多仪器有限公司', tel: '021-64093388' },
  submersible_pump: { name: '上海凯泉泵业集团有限公司', tel: '021-56615566' },
  sample_float: { name: '南京水质仪器有限公司', tel: '025-84312567' },
  dtu: { name: '深圳有人物联网有限公司', tel: '0755-83556800' },
  fire_extinguisher: { name: '天广消防设备有限公司', tel: '0595-86399119' },
  lighting: { name: '太阳能照明科技有限公司', tel: '0755-26001234' },
};

// ---------- Simulated spare parts spec & manufacturer data ----------
const spareSpecMap = {
  'pH电极组件': 'PH-E610',
  '溶解氧传感器膜帽': 'DO-MEMB-K',
  '高锰酸盐反应管路': 'CODMn-FLW',
  '氨氮试剂管路': 'NH3-REAG',
  '总磷消解管': 'TP-DIGEST',
  '总氮紫外灯管': 'TN-UV-LAMP',
  '电导率电极': 'EC-CELL4',
  '蠕动泵管': 'PUMP-TUBE01',
  '采样滤网(100目)': 'FILTER-100M',
  '数据采集模块': 'DAQ-600',
  '太阳能板': 'SP-50W',
  '蓄电池': 'BAT-12V38AH',
  '通信模块': '4G-DTU-100',
  '防雷器': 'SPD-24V',
  '电缆接头': 'M12-IP68',
  '密封圈': 'OR-80',
  '电池盒': 'BK-12A',
  '浊度光源': 'TB-LAMP',
  '标准液套装': 'CAL-STD-KIT',
};

// 实际备件名称→规格型号映射（匹配数据库中的备件名称）
const partSpecMap = {
  '数据采集终端RTU': 'RTU-600',
  'pH复合电极': 'PH-E610',
  '溶解氧膜头': 'DO-MEMB-K',
  '高锰酸盐反应管': 'CODMn-FLW',
  '氨氮试剂泵管': 'NH3-REAG',
  '总磷消解管': 'TP-DIGEST',
  '总氮紫外灯管': 'TN-UV-LAMP',
  '电导率电极': 'EC-CELL4',
  'GPRS通信模块': '4G-DTU-100',
  '温湿度传感器': 'TH-100',
  '蓄电池(12V)': 'BAT-12V38AH',
  '防雷模块': 'SPD-24V',
  '信号电缆(10m)': 'CABLE-10M',
  '蠕动泵管': 'PUMP-TUBE01',
  '标准校准液': 'CAL-STD-KIT',
};

// 实际备件名称→适用设备类型映射
const partDeviceMap = {
  '数据采集终端RTU': ['dtu'],
  'pH复合电极': ['ph_meter', 'multi_param_analyzer'],
  '溶解氧膜头': ['do_sensor', 'multi_param_analyzer'],
  '高锰酸盐反应管': ['codmn_analyzer', 'multi_param_analyzer'],
  '氨氮试剂泵管': ['ammonia_analyzer', 'multi_param_analyzer'],
  '总磷消解管': ['tp_analyzer'],
  '总氮紫外灯管': ['tn_analyzer'],
  '电导率电极': ['conductivity_meter', 'multi_param_analyzer'],
  'GPRS通信模块': ['dtu'],
  '温湿度传感器': ['dtu'],
  '蓄电池(12V)': ['dtu', 'multi_param_analyzer', 'ph_meter'],
  '蠕动泵管': ['submersible_pump', 'ammonia_analyzer', 'codmn_analyzer'],
  '防雷模块': ['dtu', 'multi_param_analyzer', 'ph_meter'],
  '信号电缆(10m)': ['dtu', 'multi_param_analyzer', 'ph_meter', 'do_sensor'],
  '标准校准液': ['ph_meter', 'do_sensor', 'turbidity_meter', 'conductivity_meter'],
};

// 实际备件名称→存放位置映射
const partLocationMap = {
  '数据采集终端RTU': 'A区-柜1-层2',
  '风速风向仪': 'B区-柜3-层1',
  '不锈钢水位计支架': 'C区-架2-层1',
  '太阳能板(20W)': 'D区-架1-层3',
  'GPRS通信模块': 'A区-柜2-层1',
  '温湿度传感器': 'B区-柜1-层2',
  '蓄电池(12V)': 'D区-架2-层1',
  '雨量筒翻斗': 'C区-柜1-层1',
  '防雷模块': 'A区-柜3-层3',
  '信号电缆(10m)': 'D区-架3-层2',
  '水位计传感器': 'B区-柜2-层1',
  '水位计密封圈': 'C区-柜2-层2',
};

const spareMfrMap = {
  '翻斗雨量计核心组件': { name: '南京水文仪器有限公司', tel: '025-84312567' },
  '雷达水位计探头': { name: '成都测测科技有限公司', tel: '028-85193456' },
  '压力传感器': { name: '南京水文仪器有限公司', tel: '025-84312567' },
  '流速仪转子': { name: '重庆水文仪器厂', tel: '023-65120123' },
  '数据采集模块': { name: '北京华水科技有限公司', tel: '010-62351234' },
  '太阳能板': { name: '杭州水文智能设备有限公司', tel: '0571-88256789' },
  '蓄电池': { name: '杭州水文智能设备有限公司', tel: '0571-88256789' },
  '通信模块': { name: '武汉长江水文科技有限公司', tel: '027-86771234' },
  '防雷器': { name: '北京华水科技有限公司', tel: '010-62351234' },
  '电缆接头': { name: '南京水文仪器有限公司', tel: '025-84312567' },
  '密封圈': { name: '重庆水文仪器厂', tel: '023-65120123' },
  '电池盒': { name: '杭州水文智能设备有限公司', tel: '0571-88256789' },
  '雨量传感器': { name: '武汉长江水文科技有限公司', tel: '027-86771234' },
  '土壤水分探头': { name: '托普云农科技股份有限公司', tel: '0571-86823567' },
  '百叶箱配件': { name: '杭州水文智能设备有限公司', tel: '0571-88256789' },
};

// ---------- Device Ledger Tab ----------
function DeviceLedgerTab() {
  const { tokens } = useTheme();
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [searchParams] = useSearchParams();
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [typeFilter, setTypeFilter] = useState(undefined);

  // View / Create / Edit state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingDevice, setViewingDevice] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDevice, setEditingDevice] = useState(null);
  const [modalLoading, setModalLoading] = useState(false);
  const [sites, setSites] = useState([]);
  const [form] = Form.useForm();

  const fetchDevices = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (typeFilter) params.set('type', typeFilter);
      const data = await api.get(`/devices?${params.toString()}`);
      setDevices(Array.isArray(data) ? data : (data?.devices || []));
    } catch {
      message.error('加载设备列表失败');
      setDevices([]);
    } finally {
      setLoading(false);
    }
  }, [search, typeFilter]);

  useEffect(() => { fetchDevices(); }, [fetchDevices]);

  // Fetch sites for form dropdown
  useEffect(() => {
    api.get('/sites').then(data => {
      const list = Array.isArray(data) ? data : (data?.sites || []);
      setSites(list);
    }).catch(() => {});
  }, []);

  const handleReset = () => {
    setSearch('');
    setTypeFilter(undefined);
  };

  // ---- View detail ----
  const handleView = useCallback(async (record) => {
    setViewingDevice(record);
    setViewOpen(true);
    setViewLoading(true);
    try {
      const data = await api.get(`/devices/${record.id}`);
      if (data && data.device) {
        setViewingDevice(data.device);
        setViewingDevice(prev => ({ ...data.device, _logs: data.logs || [], _op_logs: data.operation_logs || [] }));
      }
    } catch { /* ignore, use basic info */ }
    setViewLoading(false);
  }, []);

  // ---- Create ----
  const handleCreate = useCallback(() => {
    setEditingDevice(null);
    form.resetFields();
    setModalOpen(true);
  }, [form]);

  // ---- 申请设备回收（独立事件） ----
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
      const result = await api.post('/device-recycle', {
        device_id: recycleDevice.id,
        reason: values.reason || '',
        destination: values.destination || 'scrap',
        operator: user?.username || user?.name || '',
        recycle_date: values.recycle_date ? values.recycle_date.format('YYYY-MM-DD') : '',
      });
      if (result && !result.error) {
        message.success('已提交设备回收申请');
        setRecycleOpen(false);
        setRecycleDevice(null);
      } else {
        message.error(result?.error || '提交失败');
      }
    } catch (e) {
      message.error(e?.response?.data?.error || e?.message || '提交失败');
    } finally {
      setRecycleLoading(false);
    }
  }, [recycleForm, recycleDevice, user]);

  // ---- Edit (relocation only) ----
  const handleEdit = useCallback((record) => {
    setEditingDevice(record);
    form.setFieldsValue({
      device_code: record.device_code,
      device_name: record.device_name,
      device_type: record.device_type,
      site_id: record.site_id,
    });
    setModalOpen(true);
  }, [form]);

  // ---- Modal submit ----
  const handleModalOk = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setModalLoading(true);
      if (editingDevice) {
        // Only submit site_id for relocation
        const result = await api.put(`/devices/${editingDevice.id}`, { site_id: values.site_id });
        if (result && !result.error) {
          message.success('设备移站成功');
          setModalOpen(false);
          fetchDevices();
        } else {
          message.error(result?.error || '移站失败');
        }
      } else {
        const result = await api.post('/devices', values);
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
    },
    {
      title: '所属站点',
      dataIndex: 'site_name',
      key: 'site_name',
      width: 150,
      ellipsis: true,
      render: (text) => text || '-',
    },
    {
      title: '设备型号',
      dataIndex: 'device_model',
      key: 'device_model',
      width: 120,
      ellipsis: true,
      render: (val, record) => {
        const model = val || deviceModelMap[record.device_type] || '';
        return <Text style={{ fontSize: 13 }}>{model || '-'}</Text>;
      },
    },
    {
      title: '生产厂家',
      dataIndex: 'manufacturer',
      key: 'manufacturer',
      width: 150,
      ellipsis: true,
      render: (val, record) => {
        const mfrObj = deviceMfrMap[record.device_type];
        const mfr = val || (mfrObj ? mfrObj.name : '') || '';
        return <Text style={{ fontSize: 13 }}>{mfr || '-'}</Text>;
      },
    },
    {
      title: '安装日期',
      dataIndex: 'install_date',
      key: 'install_date',
      width: 110,
      render: (val) => <Text style={{ fontSize: 13 }}>{val || '-'}</Text>,
    },
    {
      title: '最后数据',
      dataIndex: 'last_data_time',
      key: 'last_data_time',
      width: 160,
      render: (text) => text ? (
        <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>{text}</Text>
      ) : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 260,
      fixed: 'right',
      render: (_, record) => (
        <Space size={2}>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => handleView(record)}>
            详情
          </Button>
          {canWrite && (
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
              编辑
            </Button>
          )}
          {canWrite && (
            <Button type="link" size="small" icon={<SwapOutlined />} onClick={() => handleRecycleOpen(record)}>
              申请回收
            </Button>
          )}
          {canWrite && (
            <Button type="link" size="small" danger icon={<DeleteOutlined />}
              onClick={() => {
                Modal.confirm({
                  title: '确认删除',
                  icon: <ExclamationCircleOutlined />,
                  content: `确认从台账移除设备「${record.device_name || record.device_code}」？该设备记录将被永久删除，不会进入设备回收列表。`,
                  okText: '删除',
                  okType: 'danger',
                  cancelText: '取消',
                  onOk: async () => {
                    try {
                      const result = await api.delete(`/devices/${record.id}`);
                      if (result && !result.error) {
                        message.success('设备已删除');
                        fetchDevices();
                      } else {
                        message.error(result?.error || '删除失败');
                      }
                    } catch (e) {
                      message.error(e?.message || '删除失败');
                    }
                  },
                });
              }}>
              删除
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div style={{ flexShrink: 0, marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <Space wrap size={12}>
          <Input
            placeholder="搜索设备编码、名称..."
            prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
            allowClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 250, borderRadius: 8 }}
          />
          <Select placeholder="设备类型" allowClear value={typeFilter} onChange={setTypeFilter}
            style={{ width: 160 }} options={typeOptions} showSearch
            filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())} />
          <Button icon={<ReloadOutlined />} onClick={handleReset}>重置</Button>
          {(search || typeFilter) && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              已筛选 {devices.length} 条结果
            </Text>
          )}
        </Space>
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}
            style={{ background: `linear-gradient(135deg, ${tokens.colorPrimary}, ${tokens.colorPrimaryHover})`, border: 'none' }}>
            注册设备
          </Button>
        )}
      </div>
      <Table
        columns={columns}
        dataSource={devices}
        rowKey={(r) => r.id || r.code || r.device_code}
        loading={loading}
        pagination={false}
        scroll={{ x: 1250, y: 'calc(100vh - 380px)' }}
        locale={{ emptyText: <Empty description="暂无设备数据" /> }}
        size="middle"
      />

      {/* ===== View Drawer ===== */}
      <Drawer
        title="设备详情"
        open={viewOpen}
        onClose={() => { setViewOpen(false); setViewingDevice(null); }}
        width={520}
        destroyOnClose
      >
        {viewLoading ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        ) : viewingDevice ? (
          <div>
            <Descriptions column={1} bordered size="small" labelStyle={{ width: 100 }}>
              <Descriptions.Item label="设备编码">{viewingDevice.device_code || viewingDevice.code || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备名称">{viewingDevice.device_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备类型">{deviceTypeMap[viewingDevice.device_type] || viewingDevice.device_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备型号">{viewingDevice.device_model || deviceModelMap[viewingDevice.device_type] || '-'}</Descriptions.Item>
              <Descriptions.Item label="生产厂家">{viewingDevice.manufacturer || deviceMfrMap[viewingDevice.device_type]?.name || '-'}</Descriptions.Item>
              <Descriptions.Item label="安装日期">{viewingDevice.install_date || '-'}</Descriptions.Item>
              <Descriptions.Item label="所属站点">{viewingDevice.site_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="最后数据时间">{viewingDevice.last_data_time || '-'}</Descriptions.Item>
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
              const actionLabel = { create: '注册', update: '更新', delete: '删除' };
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
            {viewingDevice && (
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

      {/* ===== Create / Edit Modal ===== */}
      <Modal
        title={editingDevice ? '设备移站' : '注册设备'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => { setModalOpen(false); setEditingDevice(null); form.resetFields(); }}
        confirmLoading={modalLoading}
        okText={editingDevice ? '确认移站' : '注册'}
        cancelText="取消"
        destroyOnClose
      >
        {editingDevice && (
          <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 8, background: 'rgba(24,144,255,0.06)', border: '1px solid rgba(24,144,255,0.15)' }}>
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
          {editingDevice ? (
            <div style={{ padding: '8px 0' }}>
              <Text style={{ fontSize: 12, color: tokens.colorTextTertiary }}>
                设备基础信息不可更改，仅可调整所属站点
              </Text>
            </div>
          ) : null}
        </Form>
      </Modal>

      {/* ===== 设备回收申请 Modal（独立事件） ===== */}
      <Modal
        title="设备回收申请"
        open={recycleOpen}
        onOk={handleRecycleOk}
        onCancel={() => { setRecycleOpen(false); setRecycleDevice(null); recycleForm.resetFields(); }}
        confirmLoading={recycleLoading}
        okText="提交申请"
        cancelText="取消"
        destroyOnClose
      >
        {recycleDevice && (
          <div style={{ marginBottom: 12, padding: '10px 14px', borderRadius: 8, background: 'rgba(82,196,26,0.06)', border: '1px solid rgba(82,196,26,0.15)' }}>
            <Text style={{ fontSize: 13 }}>
              设备：<Text strong>{recycleDevice.device_name}</Text>（<Text type="secondary">{recycleDevice.device_code}</Text>）
            </Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              操作人：{user?.username || user?.name || '当前登录用户'}（提交后可在「设备回收」中查看审核）
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
  const { tokens } = useTheme();
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [parts, setParts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  // View / Create / Edit state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingPart, setViewingPart] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPart, setEditingPart] = useState(null);
  const [modalLoading, setModalLoading] = useState(false);
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
    try {
      const params = search ? `?search=${encodeURIComponent(search)}` : '';
      const data = await api.get(`/parts/inventory${params}`);
      setParts(Array.isArray(data) ? data : (data?.parts || []));
    } catch {
      message.error('加载备件数据失败');
      setParts([]);
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => { fetchParts(); }, [fetchParts]);

  useEffect(() => {
    api.get('/sites').then(data => {
      const list = Array.isArray(data) ? data : (data?.sites || []);
      setSites(list);
    }).catch(() => {});
  }, []);

  const handleView = useCallback(async (record) => {
    setViewingPart(record);
    setViewOpen(true);
    // Fetch inventory logs for this part
    try {
      const logs = await api.get(`/parts/inventory/${record.id}/logs`);
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

  const columns = [
    { title: '备件编号', dataIndex: 'part_code', key: 'part_code', width: 110,
      render: (text, r) => <Text strong style={{ color: tokens.colorPrimary }}>{text || `#${r.id}`}</Text> },
    { title: '备件名称', dataIndex: 'part_name', key: 'part_name', width: 140, ellipsis: true },
    { title: '规格型号', dataIndex: 'spec', key: 'spec', width: 130, ellipsis: true,
      render: (v, r) => v || partSpecMap[r.part_name] || spareSpecMap[r.part_name] || '-' },
    { title: '生产厂家', dataIndex: 'manufacturer', key: 'manufacturer', width: 100, ellipsis: true,
      render: (v, r) => {
        const m = spareMfrMap[r.part_name];
        const name = v && !/^[A-Za-z]/.test(v) ? v : (m ? m.name : (v || '-'));
        return <span style={{ fontSize: 12 }}>{name}</span>;
      }},
    { title: '库存数量', dataIndex: 'quantity', key: 'quantity', width: 110, align: 'center',
      render: (val, r) => {
        const min = r.min_quantity || 5;
        const isLow = val != null && val < min;
        return <Text style={{ color: isLow ? tokens.colorError : tokens.colorText, fontWeight: isLow ? 600 : 400 }}>{val ?? '-'} {isLow && <Tag color="red" style={{ marginLeft: 4, fontSize: 11 }}>低库存</Tag>}</Text>;
      }},
    { title: '存放位置', dataIndex: 'location', key: 'location', width: 120,
      render: (v, r) => v || partLocationMap[r.part_name] || '-' },
    { title: '适用设备', dataIndex: 'device_types', key: 'device_types', width: 160,
      render: (val, r) => {
        const devices = Array.isArray(val) ? val : (partDeviceMap[r.part_name] || []);
        return devices.length > 0
          ? <Space size={4} wrap>{devices.map(t => <Tag key={t} style={{ fontSize: 11 }}>{deviceTypeMap[t] || t}</Tag>)}</Space>
          : '-';
      }},
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 150, render: (v) => v || '-' },
    { title: '操作', key: 'actions', width: 270,
      render: (_, r) => (
        <Space size={0} wrap>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => handleView(r)}>详情</Button>
          {canWrite && <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)}>编辑</Button>}
          {canWrite && <Button type="link" size="small" style={{ color: '#52c41a' }} icon={<ArrowUpOutlined />} onClick={() => handleStockOpen(r, 'in')}>入库</Button>}
          {canWrite && <Button type="link" size="small" style={{ color: '#fa8c16' }} icon={<ArrowDownOutlined />} onClick={() => handleStockOpen(r, 'out')}>出库</Button>}
          {canWrite && <Button type="link" size="small" style={{ color: '#13c2c2' }} icon={<SwapOutlined />} onClick={() => handleRecoverOpen(r)}>回收</Button>}
        </Space>
      )},
  ];

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div style={{ flexShrink: 0, marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <Input
          placeholder="搜索备件名称、编号..."
          prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 260, borderRadius: 8 }}
        />
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}
            style={{ background: `linear-gradient(135deg, ${tokens.colorPrimary}, ${tokens.colorPrimaryHover})`, border: 'none' }}>
            新增备件
          </Button>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 280px)', minHeight: 400 }}>
        <style>{`.hide-scrollbar::-webkit-scrollbar{display:none}.hide-scrollbar{-ms-overflow-style:none;scrollbar-width:none}`}</style>
        <div className="hide-scrollbar" style={{ flex: 1, overflow: 'auto' }}>
          <Table
            columns={columns}
            dataSource={parts}
            rowKey={(r) => r.id || r.part_code}
            loading={loading}
            pagination={false}
            scroll={{ x: 1250, y: 'calc(100vh - 380px)' }}
            locale={{ emptyText: <Empty description="暂无备件数据" /> }}
            size="middle"
          />
        </div>
      </div>

      {/* ===== View Drawer ===== */}
      <Drawer
        title="备件详情"
        open={viewOpen}
        onClose={() => { setViewOpen(false); setViewingPart(null); }}
        width={520}
        destroyOnClose
      >
        {viewingPart && (
          <div>
            <Descriptions column={1} bordered size="small" labelStyle={{ width: 90 }}>
              <Descriptions.Item label="备件编号">{viewingPart.part_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="备件名称">{viewingPart.part_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="规格型号">{viewingPart.spec || spareSpecMap[viewingPart.part_name] || viewingPart.category || '-'}</Descriptions.Item>
              {(() => {
                const mfr = spareMfrMap[viewingPart.part_name];
                return mfr ? (
                  <>
                    <Descriptions.Item label="厂家名称">{mfr.name}</Descriptions.Item>
                    <Descriptions.Item label="厂家电话">{mfr.tel}</Descriptions.Item>
                  </>
                ) : null;
              })()}
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
                    { title: '事由', dataIndex: 'reason', key: 'reason', ellipsis: true, render: (v) => v || '-' },
                    { title: '关联工单', dataIndex: 'work_order_no', key: 'work_order_no', width: 110,
                      render: (v) => v || '-' },
                    { title: '操作人', dataIndex: 'operator', key: 'operator', width: 80, render: (v) => v || '-' },
                    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 140, render: (v) => v || '-' },
                  ]}
                  rowKey={(r) => r.id || `${r.created_at}-${r.type}`}
                  pagination={false}
                  size="small"
                  scroll={{ y: 200 }}
                />
              ) : (
                <Empty description="暂无出入库记录" style={{ padding: '16px 0' }} />
              )}
            </div>
          </div>
        )}
      </Drawer>

      {/* ===== Create / Edit Modal (basic info only) ===== */}
      <Modal
        title={editingPart ? '编辑备件信息' : '新增备件'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => { setModalOpen(false); setEditingPart(null); form.resetFields(); }}
        confirmLoading={modalLoading}
        okText={editingPart ? '保存' : '新增'}
        cancelText="取消"
        destroyOnClose
      >
        {editingPart && (
          <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: 'rgba(250,140,22,0.06)', border: '1px solid rgba(250,140,22,0.15)' }}>
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
        destroyOnClose
      >
        {stockPart && (
          <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: stockType === 'in' ? 'rgba(82,196,26,0.06)' : 'rgba(250,140,22,0.06)', border: `1px solid ${stockType === 'in' ? 'rgba(82,196,26,0.15)' : 'rgba(250,140,22,0.15)'}` }}>
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
        destroyOnClose
      >
        {recoverPart && (
          <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: 'rgba(19,194,194,0.06)', border: '1px solid rgba(19,194,194,0.15)' }}>
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

  const fetchRecords = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get('/device-recycle');
      setRecords(Array.isArray(data) ? data : (data?.records || []));
    } catch {
      message.error('加载回收记录失败');
      setRecords([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchRecords(); }, [fetchRecords]);

  const columns = [
    { title: '设备编码', dataIndex: 'device_code', key: 'device_code', width: 130,
      render: (text, r) => <Text strong style={{ color: tokens.colorPrimary }}>{text || `#${r.id}`}</Text> },
    { title: '设备名称', dataIndex: 'device_name', key: 'device_name', ellipsis: true },
    { title: '设备类型', dataIndex: 'device_type', key: 'device_type', width: 120,
      render: (val) => val ? <Tag>{deviceTypeMap[val] || val}</Tag> : '-' },
    { title: '生产厂家', key: 'manufacturer', width: 140, ellipsis: true,
      render: (v, r) => { const m = deviceMfrMap[r.device_type]; return m ? m.name : '-'; } },
    { title: '原属站点', dataIndex: 'site_name', key: 'site_name', width: 140, render: (v) => v || '-' },
    { title: '回收原因', dataIndex: 'reason', key: 'reason', width: 140, ellipsis: true, render: (v) => v || '-' },
    { title: '回收方式', dataIndex: 'destination', key: 'destination', width: 100,
      render: (val) => {
        const map = { repair: '维修', replace: '更换', scrap: '报废', return: '退回' };
        return <Tag color={val === 'scrap' ? 'red' : 'blue'}>{map[val] || val || '-'}</Tag>;
      }},
    { title: '回收时间', dataIndex: 'recycle_date', key: 'recycle_date', width: 160, render: (v) => v || '-' },
    { title: '操作人', dataIndex: 'operator', key: 'operator', width: 100, render: (v) => v || '-' },
  ];

  return (
    <div>
      <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 280px)', minHeight: 400 }}>
        <style>{`.hide-scrollbar::-webkit-scrollbar{display:none}.hide-scrollbar{-ms-overflow-style:none;scrollbar-width:none}`}</style>
        <div className="hide-scrollbar" style={{ flex: 1, overflow: 'auto' }}>
          <Table
            columns={columns}
            dataSource={records}
            rowKey={(r) => r.id || r.device_code}
            loading={loading}
            pagination={false}
            locale={{ emptyText: <Empty description="暂无回收记录" /> }}
            size="middle"
          />
        </div>
      </div>
    </div>
  );
}

// ---------- Operation Logs Tab ----------
function OperationLogsTab() {
  const { tokens } = useTheme();
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.get('/operation-logs?limit=50'),
      api.get('/parts/dashboard'),
    ]).then(([opLogs, dash]) => {
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
    }).catch(() => setLogs([])).finally(() => setLoading(false));
  }, []);

  const actionLabel = { create: '注册', update: '更新', delete: '删除', approve: '审批通过', reject: '驳回', '入库': '入库', '出库': '出库' };

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: '20%' },
    { title: '操作人', dataIndex: 'operator', key: 'operator', width: '20%', align: 'center',
      render: (v) => v || '-' },
    { title: '操作类型', dataIndex: 'action', key: 'action', width: '20%', align: 'center',
      render: (v, r) => {
        const color = v === 'delete' ? 'red' : v === 'create' ? 'green' : v === '入库' ? 'green' : v === '出库' ? 'red' : 'blue';
        return <Tag color={color} style={{ margin: 0, borderRadius: 4, fontSize: 11 }}>{actionLabel[v] || v}</Tag>;
      }},
    { title: '目标', dataIndex: 'target_type', key: 'target_type', width: '20%', align: 'center',
      render: (v) => v === 'device' ? '设备' : v === 'part' ? '备件' : v || '-' },
    { title: '详情', dataIndex: 'details', key: 'details', width: '20%', ellipsis: true },
  ];

  return (
    <Card bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
      style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      <div style={{ padding: '12px 16px', flexShrink: 0, borderBottom: `1px solid ${tokens.colorBorderSecondary}` }}>
        <Text type="secondary">设备操作与备件出入库记录（按时间倒序）</Text>
      </div>
      <Table columns={columns} dataSource={logs} rowKey="id" loading={loading} size="middle"
        scroll={{ y: 'calc(100vh - 400px)' }}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无操作记录" /> }} />
    </Card>
  );
}

// ---------- Main Page ----------
export default function EquipmentPage() {
  const { tokens } = useTheme();
  const [dashData, setDashData] = useState(null);
  const [dashLoading, setDashLoading] = useState(false);

  useEffect(() => {
    setDashLoading(true);
    api.get('/parts/dashboard').then(data => {
      if (data && typeof data === 'object') setDashData(data);
    }).catch(() => {}).finally(() => setDashLoading(false));
  }, []);

  const tabItems = [
    {
      key: 'ledger',
      label: <span><DatabaseOutlined /> 设备台账</span>,
      children: <DeviceLedgerTab />,
    },
    {
      key: 'spare-parts',
      label: <span><InboxOutlined /> 备件库存</span>,
      children: <SparePartsTab />,
    },
    {
      key: 'recycling',
      label: <span><SwapOutlined /> 设备回收</span>,
      children: <DeviceRecyclingTab />,
    },
    {
      key: 'operation-logs',
      label: <span><HistoryOutlined /> 操作日志</span>,
      children: <OperationLogsTab />,
    },
  ];

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 24 }}>
      <Title level={4} style={{ margin: '0 0 16px', color: tokens.colorText }}>设备管理</Title>
      {/* 统计 */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col xs={12} xs={6} lg={3}>
          <Card bodyStyle={{ padding: '12px 16px' }} hoverable>
            <Statistic title="设备总数" value={dashData?.device_count || 0} valueStyle={{ fontSize: 22, fontWeight: 600 }} prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
        <Col xs={12} xs={6} lg={3}>
          <Card bodyStyle={{ padding: '12px 16px' }} hoverable>
            <Statistic title="备件种类" value={dashData?.total_parts || 0} valueStyle={{ fontSize: 22, fontWeight: 600 }} prefix={<InboxOutlined />} />
          </Card>
        </Col>
        <Col xs={12} xs={6} lg={3}>
          <Card bodyStyle={{ padding: '12px 16px' }} hoverable>
            <Statistic title="低库存预警" value={dashData?.low_stock || 0}
              valueStyle={{ color: (dashData?.low_stock || 0) > 0 ? '#ff4d4f' : '#52c41a', fontSize: 22, fontWeight: 600 }}
              prefix={<ExclamationCircleOutlined />} />
          </Card>
        </Col>
      </Row>
      <Card style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }} bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <Tabs items={tabItems} style={{ flex: 1, display: 'flex', flexDirection: 'column', marginTop: -8 }}
          tabBarStyle={{ flexShrink: 0 }} />
      </Card>
    </div>
  );
}
