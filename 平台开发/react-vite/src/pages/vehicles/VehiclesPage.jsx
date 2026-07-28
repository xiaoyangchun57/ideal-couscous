import { useState, useEffect, useMemo } from 'react';
import { Table, Card, Button, Space, Tag, Typography, message, Modal, Form, Input, InputNumber, Empty, Statistic, Row, Col, Tabs, Select, Upload, Drawer, Descriptions, Alert, Checkbox } from 'antd';
import { PlusOutlined, ReloadOutlined, CarOutlined, ToolOutlined, FireOutlined, HistoryOutlined, UploadOutlined, EditOutlined, SearchOutlined, FileProtectOutlined, SafetyCertificateOutlined, EyeOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { statusColors } from '../../theme/tokens';
import FilterBar from '../../components/FilterBar';
import { pageRootStyle, filterInputWidth } from '../../services/pageStyles';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';

const { Text, Title } = Typography;

const VEH_STATUS = {
  idle: { label: '可用', color: 'green' }, in_use: { label: '出车中', color: 'blue' },
  maintenance: { label: '维修中', color: 'orange' }, restricted: { label: '限制使用', color: 'red' },
  retired: { label: '已报废', color: 'default' },
};
const MAINT_TYPE = { routine: '例行保养', regular: '定期保养', major: '大修', minor: '小修', other: '其他' };
const DOC_TYPE = { insurance: '保险', annual_inspection: '年检', registration: '行驶证', driving_license: '驾驶证' };
const ENERGY_TYPE = { gasoline: '汽油', diesel: '柴油', electric: '纯电', phev: '插电混动', hybrid: '混动', other: '其他' };
const isElectric = (fuelType) => fuelType === 'electric';

export default function VehiclesPage() {
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [tab, setTab] = useState('ledger');
  const [searchText, setSearchText] = useState('');
  const [vehicles, setVehicles] = useState([]);
  const [useRecords, setUseRecords] = useState([]);
  const [maintRecords, setMaintRecords] = useState([]);
  const [refuelRecords, setRefuelRecords] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [inspections, setInspections] = useState([]);
  const [inspectionTemplate, setInspectionTemplate] = useState([]);
  const [loading, setLoading] = useState(false);
  const [vehicleForm] = Form.useForm();
  const [maintForm] = Form.useForm();
  const [refuelForm] = Form.useForm();
  const [newVehicleOpen, setNewVehicleOpen] = useState(false);
  const [maintOpen, setMaintOpen] = useState(false);
  const [refuelOpen, setRefuelOpen] = useState(false);
  const [activeVehicle, setActiveVehicle] = useState(null);
  const [editVehicleOpen, setEditVehicleOpen] = useState(false);
  const [editForm] = Form.useForm();
  const [inspectionForm] = Form.useForm();
  const [documentForm] = Form.useForm();
  const [maintPhotos, setMaintPhotos] = useState([]);
  const [refuelPhotos, setRefuelPhotos] = useState([]);
  const [submittingMaint, setSubmittingMaint] = useState(false);
  const [submittingRefuel, setSubmittingRefuel] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [inspectionOpen, setInspectionOpen] = useState(false);
  const [documentOpen, setDocumentOpen] = useState(false);
  const [vehWrapRef, vehBodyH] = useTableAutoHeight({ deps: [tab, vehicles.length, useRecords.length, maintRecords.length, refuelRecords.length, loading] });

  const load = async () => {
    setLoading(true);
    try {
      const [v, u, m, r, d, i, template] = await Promise.all([
        api.get('/vehicles') || [],
        api.get('/vehicle/use-records') || [],
        api.get('/vehicle/maintenance') || [],
        api.get('/vehicle/refueling') || [],
        api.get('/vehicle/documents') || [],
        api.get('/vehicle/inspections') || [],
        api.get('/vehicle/inspection-template') || [],
      ]);
      setVehicles(v); setUseRecords(u); setMaintRecords(m); setRefuelRecords(r);
      setDocuments(d); setInspections(i); setInspectionTemplate(template);
    } catch (e) { message.error('加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const onCreateVehicle = async () => {
    const v = await vehicleForm.validateFields();
    try { await api.post('/vehicles', v); message.success('已新增车辆'); setNewVehicleOpen(false); vehicleForm.resetFields(); load(); }
    catch (e) { message.error('失败：' + e.message); }
  };
  // 车辆照片与加油/养护记录「一起提交」：先存记录取得新 ID，再关联上传照片
  const uploadVehiclePhotos = async (photos, recordId, category) => {
    const token = localStorage.getItem('water_ops_token') || '';
    for (const p of photos) {
      try {
        const fd = new FormData();
        fd.append('file', p.file);
        fd.append('source_type', 'vehicle');
        fd.append('source_id', String(recordId));
        fd.append('category', category);
        fd.append('uploader_name', user?.name || user?.username || '运维人员');
        await fetch('/api/upload/attachment', {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: fd,
        });
      } catch (_) { /* 单张失败不阻断其余 */ }
    }
  };

  const onMaint = async () => {
    const v = await maintForm.validateFields();
    setSubmittingMaint(true);
    try {
      const res = await api.post('/vehicle/maintenance', v);
      const newId = res?.id;
      if (newId && maintPhotos.length) await uploadVehiclePhotos(maintPhotos, newId, '养护记录');
      message.success('已记录保养');
      setMaintOpen(false); maintForm.resetFields(); setMaintPhotos([]); load();
    } catch (e) { message.error('失败：' + (e?.message || e)); }
    finally { setSubmittingMaint(false); }
  };
  const onRefuel = async () => {
    const v = await refuelForm.validateFields();
    setSubmittingRefuel(true);
    try {
      const res = await api.post('/vehicle/refueling', { ...v, energy_quantity: v.energy_quantity });
      const newId = res?.id;
      if (newId && refuelPhotos.length) await uploadVehiclePhotos(refuelPhotos, newId, '车辆加油');
      message.success(isElectric(activeVehicle?.fuel_type) ? '已记录充电' : '已记录加油');
      setRefuelOpen(false); refuelForm.resetFields(); setRefuelPhotos([]); load();
    } catch (e) { message.error('失败：' + (e?.message || e)); }
    finally { setSubmittingRefuel(false); }
  };

  const openMaint = (r) => { setActiveVehicle(r); maintForm.setFieldsValue({ vehicle_id: r.id, maint_type: 'routine' }); setMaintPhotos([]); setMaintOpen(true); };
  const openRefuel = (r) => { setActiveVehicle(r); refuelForm.setFieldsValue({ vehicle_id: r.id }); setRefuelPhotos([]); setRefuelOpen(true); };

  const onEditVehicle = async () => {
    try {
      const values = await editForm.validateFields();
      await api.put(`/vehicles/${activeVehicle.id}`, {
        ...values,
        seats: values.seats == null ? 5 : Number(values.seats),
        current_mileage: values.current_mileage == null ? 0 : Number(values.current_mileage),
        next_maintenance_mileage: values.next_maintenance_mileage == null ? null : Number(values.next_maintenance_mileage),
      });
      message.success('车辆台账已更新');
      setEditVehicleOpen(false); editForm.resetFields(); load();
    } catch (e) { if (e?.message) message.error(e.message); }
  };

  const onDeleteVehicle = (vehicle) => {
    Modal.confirm({ title: `删除 ${vehicle.plate_no}？`, content: '仅从未产生申请、行程、能源补给、检查、维保或证照记录的误建车辆可永久删除。', okText: '确认删除', okButtonProps: { danger: true }, cancelText: '取消', onOk: async () => {
      const res = await api.delete(`/vehicles/${vehicle.id}`);
      if (res?.ok) { message.success('车辆已删除'); load(); } else message.error(res?.error || '删除失败');
    }});
  };

  const openDetail = (vehicle) => { setActiveVehicle(vehicle); setDetailOpen(true); };

  const openInspection = (vehicle, inspectionType = 'routine') => {
    setActiveVehicle(vehicle);
    inspectionForm.setFieldsValue({ inspection_type: inspectionType, overall_status: 'normal', abnormal_items: [], remarks: '', odometer: vehicle.current_mileage });
    setInspectionOpen(true);
  };

  const onInspection = async () => {
    try {
      const values = await inspectionForm.validateFields();
      const abnormal = values.abnormal_items || [];
      const status = values.overall_status === 'normal' && abnormal.length ? 'attention' : values.overall_status;
      const items = inspectionTemplate.map((item) => ({
        key: item.key, label: item.label, status: abnormal.includes(item.key) ? status : 'normal', remark: '',
      }));
      await api.post('/vehicle/inspections', { ...values, vehicle_id: activeVehicle.id, overall_status: status, items });
      message.success(status === 'blocked' ? '已登记不合格检查，车辆已限制使用' : '车况检查已记录');
      setInspectionOpen(false); inspectionForm.resetFields(); load();
    } catch (e) { if (e?.message) message.error(e.message); }
  };

  const openDocument = (vehicle) => {
    setActiveVehicle(vehicle); documentForm.resetFields(); setDocumentOpen(true);
  };

  const onDocument = async () => {
    try {
      const values = await documentForm.validateFields();
      await api.post('/vehicle/documents', { ...values, vehicle_id: activeVehicle.id });
      message.success('证照已登记'); setDocumentOpen(false); documentForm.resetFields(); load();
    } catch (e) { if (e?.message) message.error(e.message); }
  };

  const now = new Date();
  const thisMonth = now.getMonth();
  const thisYear = now.getFullYear();
  const isThisMonth = (d) => { const dt = new Date(d); return dt.getFullYear() === thisYear && dt.getMonth() === thisMonth; };

  const stats = useMemo(() => {
    const mileage = useRecords
      .filter(r => r.returned_at && isThisMonth(r.returned_at) && r.end_mileage && r.start_mileage)
      .reduce((s, r) => s + (r.end_mileage - r.start_mileage), 0);
    return {
      total: vehicles.length,
      inUse: vehicles.filter(v => v.status === 'in_use').length,
      unavailable: vehicles.filter(v => ['maintenance', 'restricted', 'retired'].includes(v.status)).length,
      documentDue: vehicles.filter(v => (v.document_state?.expired?.length || v.document_state?.due_soon?.length)).length,
      returnPending: useRecords.filter(r => !r.returned_at).length,
      maintThisMonth: maintRecords.filter(r => isThisMonth(r.maint_at)).length,
      refuelThisMonth: refuelRecords.filter(r => isThisMonth(r.refuel_at)).length,
      mileageThisMonth: mileage,
    };
  }, [vehicles, useRecords, maintRecords, refuelRecords]);

  const decisionRows = useMemo(() => vehicles.map((vehicle) => {
    const uses = useRecords.filter(r => r.vehicle_id === vehicle.id);
    const mileage = uses.filter(r => r.returned_at && r.end_mileage != null && r.start_mileage != null)
      .reduce((sum, r) => sum + Math.max(0, Number(r.end_mileage) - Number(r.start_mileage)), 0);
    const fuel = refuelRecords.filter(r => r.vehicle_id === vehicle.id).reduce((sum, r) => sum + Number(r.amount || 0), 0);
    const maintenance = maintRecords.filter(r => r.vehicle_id === vehicle.id);
    const maintenanceCost = maintenance.reduce((sum, r) => sum + Number(r.cost || 0), 0);
    const issues = [];
    if (vehicle.document_state?.expired?.length) issues.push('证照到期');
    else if (vehicle.document_state?.due_soon?.length) issues.push('证照临期');
    if (vehicle.status === 'restricted') issues.push('限制使用');
    if (vehicle.status === 'maintenance') issues.push('维修中');
    if (uses.some(r => !r.returned_at)) issues.push('待还车');
    if (maintenance.some(r => ['open', 'in_progress'].includes(r.maint_status))) issues.push('待维修');
    if (vehicle.next_maintenance_mileage && Number(vehicle.current_mileage || 0) >= Number(vehicle.next_maintenance_mileage)) issues.push('保养到期');
    if (uses.some(r => r.end_mileage != null && r.start_mileage != null && Number(r.end_mileage) < Number(r.start_mileage))) issues.push('里程倒退');
    return { ...vehicle, mileage, fuel, maintenanceCost, totalCost: fuel + maintenanceCost,
      costPer100: mileage > 0 ? ((fuel + maintenanceCost) / mileage * 100).toFixed(1) : null, issues };
  }), [vehicles, useRecords, maintRecords, refuelRecords]);
  const decisionCount = decisionRows.filter(r => r.issues.length).length;

  const activeDocuments = useMemo(() => documents.filter(d => d.vehicle_id === activeVehicle?.id), [documents, activeVehicle]);
  const activeInspections = useMemo(() => inspections.filter(i => i.vehicle_id === activeVehicle?.id), [inspections, activeVehicle]);

  const vehicleOptions = useMemo(() => vehicles.map(v => ({ value: v.id, label: `${v.plate_no} (${v.model || ''})` })), [vehicles]);

  // 车辆台账客户端关键词过滤（车牌号 / 负责人），不改动后端请求逻辑
  const filteredVehicles = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    if (!q) return vehicles;
    return vehicles.filter(v =>
      (v.plate_no && String(v.plate_no).toLowerCase().includes(q)) ||
      (v.model && String(v.model).toLowerCase().includes(q)) ||
      (v.manager && String(v.manager).toLowerCase().includes(q)) ||
      (v.driver && String(v.driver).toLowerCase().includes(q))
    );
  }, [vehicles, searchText]);

  const ledgerColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '车型', dataIndex: 'model', width: 100, render: v => v || '-' },
    { title: '能源', dataIndex: 'fuel_type', width: 90, render: v => ENERGY_TYPE[v] || '-' },
    { title: '归属', width: 100, render: (_, r) => r.department || '-' },
    { title: '状态', dataIndex: 'status', width: 90, render: v => { const s = VEH_STATUS[v] || {label:v||'-',color:'default'}; return <Tag color={s.color}>{s.label}</Tag>; } },
    { title: '当前里程', dataIndex: 'current_mileage', width: 110, render: v => v ? `${Math.round(v).toLocaleString()} km` : '-' },
    { title: '证照 / 车况', width: 160, render: (_, r) => {
      const doc = r.document_state || {};
      if (doc.expired?.length) return <Tag color="red">证照到期</Tag>;
      if (doc.due_soon?.length) return <Tag color="orange">30天内到期</Tag>;
      if (r.last_inspection_status === 'blocked') return <Tag color="red">检查不合格</Tag>;
      if (r.last_inspection_status === 'attention') return <Tag color="orange">车况需关注</Tag>;
      return <Tag color="green">正常</Tag>;
    }},
    { title: '下次保养', dataIndex: 'next_maintenance_mileage', width: 120, render: v => v ? `${Math.round(v).toLocaleString()} km` : '-' },
    { title: '操作', width: 270, render: (_, r) => (
      <Space size={4}>
        <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)}>详情</Button>
        {user?.role === 'admin' && (
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            editForm.setFieldsValue({
              plate_no: r.plate_no, vehicle_name: r.vehicle_name, model: r.model, seats: r.seats,
              department: r.department, fuel_type: r.fuel_type, status: r.status,
              current_mileage: r.current_mileage, next_maintenance_mileage: r.next_maintenance_mileage,
              purchase_date: r.purchase_date, insurance_expiry: r.insurance_expiry,
              annual_inspection_expiry: r.annual_inspection_expiry, registration_expiry: r.registration_expiry,
            });
            setActiveVehicle(r); setEditVehicleOpen(true);
          }}>编辑</Button>
        )}
        {user?.role === 'admin' && <Button size="small" danger icon={<DeleteOutlined />} onClick={() => onDeleteVehicle(r)}>删除</Button>}
        <Button size="small" icon={<SafetyCertificateOutlined />} onClick={() => openInspection(r)}>检查</Button>
      </Space>
    )},
  ];

  const useColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '申请人', dataIndex: 'applicant_name', width: 100, render: v => v || '-' },
    { title: '开始时间', dataIndex: 'start_at', width: 150, render: v => v || '-' },
    { title: '结束时间', dataIndex: 'returned_at', width: 150, render: v => v || '-' },
    { title: '目的地', dataIndex: 'destination', width: 140, render: v => v || '-' },
    { title: '起点里程', dataIndex: 'start_mileage', width: 100, render: v => v ? `${Math.round(v)} km` : '-' },
    { title: '终点里程', dataIndex: 'end_mileage', width: 100, render: v => v ? `${Math.round(v)} km` : '-' },
    { title: '行驶里程', width: 100, render: (_, r) => (r.start_mileage && r.end_mileage) ? `${Math.round(r.end_mileage - r.start_mileage)} km` : '-' },
    { title: '状态', width: 90, render: (_, r) => r.returned_at ? <Tag color="green">已还车</Tag> : <Tag color="blue">使用中</Tag> },
  ];

  const maintColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '保养时间', dataIndex: 'maint_at', width: 150 },
    { title: '类型', dataIndex: 'maint_type', width: 100, render: v => MAINT_TYPE[v] || v || '-' },
    { title: '当前里程', dataIndex: 'mileage_at', width: 110, render: v => v ? `${Math.round(v)} km` : '-' },
    { title: '保养项目', dataIndex: 'items', ellipsis: true, render: v => <span title={v}>{v || '-'}</span> },
    { title: '费用', dataIndex: 'cost', width: 100, render: v => v ? `¥${v}` : '-' },
    { title: '下次保养里程', dataIndex: 'next_maint_mileage', width: 120, render: v => v ? `${Math.round(v)} km` : '-' },
  ];

  const refuelColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '补给时间', dataIndex: 'refuel_at', width: 150 },
    { title: '补给量', width: 100, render: (_, r) => { const q = r.energy_quantity ?? r.liters; return q ? `${q} ${r.energy_unit || (r.vehicle_fuel_type === 'electric' ? 'kWh' : 'L')}` : '-'; } },
    { title: '金额', dataIndex: 'amount', width: 100, render: v => v ? `¥${v}` : '-' },
    { title: '里程', dataIndex: 'mileage_at', width: 110, render: v => v ? `${Math.round(v)} km` : '-' },
    { title: '备注', dataIndex: 'remark', ellipsis: true, render: v => <span title={v}>{v || '-'}</span> },
  ];

  const tableScroll = vehBodyH ? { y: vehBodyH } : undefined;

  return (
    <div style={pageRootStyle}>
      <Title level={4} style={{ margin: 0 }}>车辆管理</Title>
      <Row gutter={[12, 12]}>
        <Col flex="1"><Card styles={{ body: { padding: '12px 16px' } }}><Statistic title={<span style={{ fontSize: 12 }}>车辆总数</span>} value={stats.total} valueStyle={{ fontSize: 22, fontWeight: 600 }} prefix={<CarOutlined />} /></Card></Col>
        <Col flex="1"><Card styles={{ body: { padding: '12px 16px' } }}><Statistic title={<span style={{ fontSize: 12 }}>出车中 / 待归还</span>} value={stats.inUse} suffix={stats.returnPending ? `/ ${stats.returnPending}` : ''} valueStyle={{ color: statusColors.info[isDark ? 'dark' : 'light'], fontSize: 22, fontWeight: 600 }} prefix={<CarOutlined />} /></Card></Col>
        <Col flex="1"><Card styles={{ body: { padding: '12px 16px' } }}><Statistic title={<span style={{ fontSize: 12 }}>受限 / 维修</span>} value={stats.unavailable} valueStyle={{ color: statusColors.warning[isDark ? 'dark' : 'light'], fontSize: 22, fontWeight: 600 }} prefix={<ToolOutlined />} /></Card></Col>
        <Col flex="1"><Card styles={{ body: { padding: '12px 16px' } }}><Statistic title={<span style={{ fontSize: 12 }}>证照需关注</span>} value={stats.documentDue} valueStyle={{ color: statusColors.purple[isDark ? 'dark' : 'light'], fontSize: 22, fontWeight: 600 }} prefix={<FileProtectOutlined />} /></Card></Col>
        <Col flex="1"><Card styles={{ body: { padding: '12px 16px' } }}><Statistic title={<span style={{ fontSize: 12 }}>本月行驶里程</span>} value={stats.mileageThisMonth} suffix="km" valueStyle={{ color: statusColors.success[isDark ? 'dark' : 'light'], fontSize: 22, fontWeight: 600 }} /></Card></Col>
      </Row>
      <div ref={vehWrapRef} style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <Tabs activeKey={tab} onChange={setTab} style={{ flex: 1 }} className="vehicles-tabs" items={[
        { key: 'ledger', label: <span><CarOutlined /> 车辆台账</span>, children: (
          <Card styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <FilterBar
              style={{ margin: 12 }}
              extra={(
                <Space>
                  <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
                  {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewVehicleOpen(true)}>新增车辆</Button>}
                </Space>
              )}
            >
              <Input
                placeholder="搜索车牌号 / 负责人..."
                prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
                allowClear
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                style={{ width: filterInputWidth, borderRadius: 8 }}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>优先处理受限车辆、证照临期与待归还记录</Text>
            </FilterBar>
            <Table rowKey="id" dataSource={filteredVehicles} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={ledgerColumns} locale={{ emptyText: <Empty description="暂无车辆" /> }} />
          </Card>
        )},
        { key: 'history', label: <span><HistoryOutlined /> 运行履历</span>, children: (
          <Tabs size="small" tabBarStyle={{ margin: '0 12px' }} items={[
        { key: 'use', label: '使用记录', children: (
          <Card styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">来自巡检计划 / 工单的出车还车记录</Text>
              <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
            </div>
            <Table rowKey="id" dataSource={useRecords} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={useColumns} locale={{ emptyText: <Empty description="暂无使用记录" /> }} />
          </Card>
        )},
        { key: 'maint', label: '保养记录', children: (
          <Card styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">车辆保养历史与下次保养里程</Text>
              {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={() => { setActiveVehicle(null); maintForm.resetFields(); setMaintOpen(true); }}>记录保养</Button>}
            </div>
            <Table rowKey="id" dataSource={maintRecords} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={maintColumns} locale={{ emptyText: <Empty description="暂无保养记录" /> }} />
          </Card>
        )},
        { key: 'refuel', label: '加油记录', children: (
          <Card styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">车辆加油历史与费用统计</Text>
              {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={() => { setActiveVehicle(null); refuelForm.resetFields(); setRefuelPhotos([]); setRefuelOpen(true); }}>记录加油</Button>}
            </div>
            <Table rowKey="id" dataSource={refuelRecords} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={refuelColumns} locale={{ emptyText: <Empty description="暂无加油记录" /> }} />
          </Card>
        )},
          ]} />
        )},
        { key: 'decision', label: <span><SafetyCertificateOutlined /> 决策待办{decisionCount ? ` (${decisionCount})` : ''}</span>, children: (
          <Card styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">汇总费用、里程和未闭环事项，优先处理证照到期、受限、待维修和待还车车辆。</Text>
              <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
            </div>
            <Table rowKey="id" dataSource={decisionRows} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={[
                { title: '车辆', width: 140, render: (_, r) => <span>{r.plate_no}<br /><Text type="secondary" style={{ fontSize: 12 }}>{r.model || '-'}</Text></span> },
                { title: '待决策事项', width: 220, render: (_, r) => r.issues.length ? <Space size={[4, 4]} wrap>{r.issues.map(issue => <Tag color={issue === '证照临期' ? 'orange' : 'red'} key={issue}>{issue}</Tag>)}</Space> : <Tag color="green">无待办</Tag> },
                { title: '累计行驶', dataIndex: 'mileage', width: 110, render: v => `${Math.round(v)} km` },
                { title: '加油 / 维修', width: 130, render: (_, r) => `¥${r.fuel.toFixed(0)} / ¥${r.maintenanceCost.toFixed(0)}` },
                { title: '综合费用', dataIndex: 'totalCost', width: 100, render: v => `¥${v.toFixed(0)}` },
                { title: '每百公里成本', dataIndex: 'costPer100', width: 120, render: v => v == null ? '-' : `¥${v}` },
                { title: '操作', width: 90, render: (_, r) => <Button size="small" onClick={() => openDetail(r)}>查看</Button> },
              ]} />
          </Card>
        )},
      ]} />
      </div>

      <Drawer open={detailOpen} onClose={() => setDetailOpen(false)} width={640}
        title={activeVehicle ? `${activeVehicle.plate_no} · 车辆档案` : '车辆档案'}>
        {activeVehicle && <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {!activeVehicle.dispatchable && <Alert type="error" showIcon message={`当前不可出车：${activeVehicle.dispatch_block_reason || '车辆状态受限'}`} />}
          {activeVehicle.document_state?.due_soon?.length > 0 && <Alert type="warning" showIcon message={`30天内到期：${activeVehicle.document_state.due_soon.map(t => DOC_TYPE[t] || t).join('、')}`} />}
          <Descriptions size="small" bordered column={2}>
            <Descriptions.Item label="状态"><Tag color={(VEH_STATUS[activeVehicle.status] || {}).color}>{(VEH_STATUS[activeVehicle.status] || {}).label || activeVehicle.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="当前里程">{Math.round(activeVehicle.current_mileage || 0).toLocaleString()} km</Descriptions.Item>
            <Descriptions.Item label="车型">{activeVehicle.model || '-'}</Descriptions.Item>
            <Descriptions.Item label="归属">{activeVehicle.department || '-'}</Descriptions.Item>
            <Descriptions.Item label="下次保养">{activeVehicle.next_maintenance_mileage ? `${Math.round(activeVehicle.next_maintenance_mileage).toLocaleString()} km` : '-'}</Descriptions.Item>
            <Descriptions.Item label="最近检查">{activeVehicle.last_inspection_at || '-'}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button icon={<SafetyCertificateOutlined />} onClick={() => openInspection(activeVehicle)}>登记车况检查</Button>
            <Button icon={<FireOutlined />} onClick={() => openRefuel(activeVehicle)}>记录加油</Button>
            {canWrite && <Button icon={<ToolOutlined />} onClick={() => openMaint(activeVehicle)}>登记维保</Button>}
            {canWrite && <Button icon={<FileProtectOutlined />} onClick={() => openDocument(activeVehicle)}>登记证照</Button>}
          </Space>
          <div>
            <Text strong>证照有效期</Text>
            <Table size="small" style={{ marginTop: 8 }} pagination={false} rowKey="id" dataSource={activeDocuments}
              columns={[
                { title: '类型', dataIndex: 'document_type', render: v => DOC_TYPE[v] || v },
                { title: '有效期至', dataIndex: 'valid_until', render: (v) => v || '-' },
                { title: '编号', dataIndex: 'document_no', render: v => v || '-' },
              ]} locale={{ emptyText: '暂未登记证照' }} />
          </div>
          <div>
            <Text strong>最近车况检查</Text>
            <Table size="small" style={{ marginTop: 8 }} pagination={false} rowKey="id" dataSource={activeInspections.slice(0, 5)}
              columns={[
                { title: '时间', dataIndex: 'inspection_date', width: 160 },
                { title: '环节', dataIndex: 'inspection_type', render: v => ({ dispatch: '出车前', return: '还车', routine: '日常' }[v] || v) },
                { title: '结论', dataIndex: 'overall_status', render: v => <Tag color={{ normal: 'green', attention: 'orange', blocked: 'red' }[v]}>{{ normal: '正常', attention: '需关注', blocked: '不合格' }[v] || v}</Tag> },
                { title: '检查人', dataIndex: 'inspector_name', render: v => v || '-' },
              ]} locale={{ emptyText: '暂无检查记录' }} />
          </div>
        </Space>}
      </Drawer>

      <Modal open={inspectionOpen} onCancel={() => { setInspectionOpen(false); inspectionForm.resetFields(); }} onOk={onInspection}
        title={activeVehicle ? `车况检查 - ${activeVehicle.plate_no}` : '车况检查'} okText="提交检查" cancelText="取消" destroyOnClose>
        <Form form={inspectionForm} layout="vertical">
          <Form.Item name="inspection_type" label="检查环节" rules={[{ required: true }]}>
            <Select options={[{ value: 'routine', label: '日常检查' }, { value: 'dispatch', label: '出车前检查' }, { value: 'return', label: '还车检查' }]} />
          </Form.Item>
          <Form.Item name="odometer" label="检查时里程"><InputNumber min={0} style={{ width: '100%' }} suffix="km" /></Form.Item>
          <Form.Item name="overall_status" label="检查结论" rules={[{ required: true }]}>
            <Select options={[{ value: 'normal', label: '正常' }, { value: 'attention', label: '需关注（仍可出车）' }, { value: 'blocked', label: '不合格（限制使用）' }]} />
          </Form.Item>
          <Form.Item name="abnormal_items" label="异常项（未勾选项默认正常）">
            <Checkbox.Group options={inspectionTemplate.map(item => ({ label: item.label, value: item.key }))} />
          </Form.Item>
          <Form.Item name="remarks" label="异常说明"><Input.TextArea rows={3} placeholder="仅有异常时填写，如：前轮需补气、右后视镜破损" /></Form.Item>
        </Form>
      </Modal>

      <Modal open={documentOpen} onCancel={() => { setDocumentOpen(false); documentForm.resetFields(); }} onOk={onDocument}
        title={activeVehicle ? `登记证照 - ${activeVehicle.plate_no}` : '登记证照'} okText="保存" cancelText="取消" destroyOnClose>
        <Form form={documentForm} layout="vertical">
          <Form.Item name="document_type" label="证照类型" rules={[{ required: true }]}><Select options={Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="valid_until" label="有效期至" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="document_no" label="证照编号"><Input /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal open={newVehicleOpen} onCancel={() => setNewVehicleOpen(false)} onOk={onCreateVehicle} title="新增车辆" okText="保存" cancelText="取消" destroyOnClose>
        <Form form={vehicleForm} layout="vertical">
          <Form.Item name="plate_no" label="车牌号" rules={[{ required: true }]}><Input placeholder="如 赣A12345" /></Form.Item>
          <Form.Item name="model" label="车型"><Input placeholder="如 SUV/皮卡" /></Form.Item>
          <Form.Item name="seats" label="座位数" initialValue={5}><InputNumber style={{ width: '100%' }} min={2} max={20} /></Form.Item>
          <Form.Item name="department" label="归属部门"><Input /></Form.Item>
          <Form.Item name="fuel_type" label="能源类型"><Select allowClear options={Object.entries(ENERGY_TYPE).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="insurance_expiry" label="保险到期日"><Input type="date" /></Form.Item>
          <Form.Item name="annual_inspection_expiry" label="年检到期日"><Input type="date" /></Form.Item>
        </Form>
      </Modal>

      <Modal open={maintOpen} onCancel={() => { setMaintOpen(false); maintForm.resetFields(); setMaintPhotos([]); }} onOk={onMaint} confirmLoading={submittingMaint} title={activeVehicle ? `记录保养 - ${activeVehicle.plate_no}` : '记录保养'} okText="保存" cancelText="取消" destroyOnClose>
        <Form form={maintForm} layout="vertical">
          <Form.Item name="vehicle_id" label="车辆" rules={[{ required: true }]}>
            <Select options={vehicleOptions} disabled={!!activeVehicle} placeholder="请选择车辆" />
          </Form.Item>
          <Form.Item name="maint_type" label="类型" initialValue="routine" rules={[{ required: true }]}>
            <Select options={Object.entries(MAINT_TYPE).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="mileage_at" label="当前里程"><InputNumber style={{ width: '100%' }} min={0} /></Form.Item>
          <Form.Item name="items" label="保养项目"><Input.TextArea rows={2} placeholder="如 更换机油、机滤" /></Form.Item>
          <Form.Item name="cost" label="费用"><InputNumber style={{ width: '100%' }} min={0} step={0.01} prefix="¥" /></Form.Item>
          <Form.Item name="next_maint_mileage" label="下次保养里程"><InputNumber style={{ width: '100%' }} min={0} /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item label="保养照片" style={{ marginBottom: 8 }}>
            <Upload
              listType="picture-card"
              multiple
              accept=".png,.jpg,.jpeg,.webp,.gif"
              fileList={maintPhotos.map(p => ({ uid: p.uid, name: p.file.name, status: 'done', url: p.url }))}
              beforeUpload={(file) => {
                const uid = `m-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
                const url = (typeof URL !== 'undefined' && URL.createObjectURL) ? URL.createObjectURL(file) : '';
                setMaintPhotos(prev => [...prev, { uid, file, url }]);
                return false;
              }}
              onRemove={(file) => { setMaintPhotos(prev => prev.filter(p => p.uid !== file.uid)); }}
            >
              <div><UploadOutlined /><div style={{ marginTop: 4 }}>添加照片</div></div>
            </Upload>
          </Form.Item>
        </Form>
      </Modal>

      <Modal open={refuelOpen} onCancel={() => { setRefuelOpen(false); refuelForm.resetFields(); setRefuelPhotos([]); }} onOk={onRefuel} confirmLoading={submittingRefuel} title={activeVehicle ? `记录${isElectric(activeVehicle.fuel_type) ? '充电' : '加油'} - ${activeVehicle.plate_no}` : '记录能源补给'} okText="保存" cancelText="取消" destroyOnClose>
        <Form form={refuelForm} layout="vertical">
          <Form.Item name="vehicle_id" label="车辆" rules={[{ required: true }]}>
            <Select options={vehicleOptions} disabled={!!activeVehicle} placeholder="请选择车辆" />
          </Form.Item>
          <Form.Item name="energy_quantity" label={isElectric(activeVehicle?.fuel_type) ? '充电量（kWh）' : '加油量（L）'} rules={[{ required: true }]}><InputNumber style={{ width: '100%' }} step={0.1} min={0} suffix={isElectric(activeVehicle?.fuel_type) ? 'kWh' : 'L'} /></Form.Item>
          <Form.Item name="amount" label="金额（元）"><InputNumber style={{ width: '100%' }} step={0.1} min={0} prefix="¥" /></Form.Item>
          <Form.Item name="unit_price" label={isElectric(activeVehicle?.fuel_type) ? '单价（元/kWh）' : '单价（元/L）'}><InputNumber style={{ width: '100%' }} step={0.01} min={0} prefix="¥" /></Form.Item>
          <Form.Item name="mileage_at" label="当前里程"><InputNumber style={{ width: '100%' }} min={0} suffix="km" /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item label="加油照片" style={{ marginBottom: 8 }}>
            <Upload
              listType="picture-card"
              multiple
              accept=".png,.jpg,.jpeg,.webp,.gif"
              fileList={refuelPhotos.map(p => ({ uid: p.uid, name: p.file.name, status: 'done', url: p.url }))}
              beforeUpload={(file) => {
                const uid = `r-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
                const url = (typeof URL !== 'undefined' && URL.createObjectURL) ? URL.createObjectURL(file) : '';
                setRefuelPhotos(prev => [...prev, { uid, file, url }]);
                return false;
              }}
              onRemove={(file) => { setRefuelPhotos(prev => prev.filter(p => p.uid !== file.uid)); }}
            >
              <div><UploadOutlined /><div style={{ marginTop: 4 }}>添加照片</div></div>
            </Upload>
          </Form.Item>
        </Form>
      </Modal>

      <Modal open={editVehicleOpen} onCancel={() => { setEditVehicleOpen(false); editForm.resetFields(); }}
        onOk={onEditVehicle} title={activeVehicle ? `编辑车辆 - ${activeVehicle.plate_no}` : '编辑车辆'}
        okText="保存" cancelText="取消" destroyOnClose>
        <Form form={editForm} layout="vertical">
          <Form.Item name="plate_no" label="车牌号" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="vehicle_name" label="车辆名称"><Input /></Form.Item>
          <Form.Item name="model" label="车型"><Input /></Form.Item>
          <Form.Item name="seats" label="座位数"><InputNumber min={1} max={99} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="department" label="归属部门"><Input /></Form.Item>
          <Form.Item name="fuel_type" label="能源类型"><Select options={Object.entries(ENERGY_TYPE).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="status" label="车辆状态"><Select options={Object.entries(VEH_STATUS).map(([value, item]) => ({ value, label: item.label }))} /></Form.Item>
          <Form.Item name="current_mileage" label="当前里程（km）"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="next_maintenance_mileage" label="下次保养里程（km）"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="purchase_date" label="购置日期"><Input type="date" /></Form.Item>
          <Form.Item name="insurance_expiry" label="保险到期日"><Input type="date" /></Form.Item>
          <Form.Item name="annual_inspection_expiry" label="年检到期日"><Input type="date" /></Form.Item>
          <Form.Item name="registration_expiry" label="行驶证有效期"><Input type="date" /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
