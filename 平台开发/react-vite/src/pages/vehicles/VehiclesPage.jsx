import { useState, useEffect, useMemo } from 'react';
import { Table, Card, Button, Space, Tag, Typography, message, Modal, Form, Input, InputNumber, Empty, Statistic, Row, Col, Tabs, Select, Upload } from 'antd';
import { PlusOutlined, ReloadOutlined, CarOutlined, ToolOutlined, FireOutlined, HistoryOutlined, UploadOutlined, EditOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';

const { Text, Title } = Typography;

const VEH_STATUS = { idle: { label: '空闲', color: 'green' }, in_use: { label: '使用中', color: 'blue' }, maintenance: { label: '维修中', color: 'orange' } };
const MAINT_TYPE = { routine: '例行保养', regular: '定期保养', major: '大修', minor: '小修', other: '其他' };

export default function VehiclesPage() {
  const { themeConfig } = useTheme();
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [tab, setTab] = useState('ledger');
  const [vehicles, setVehicles] = useState([]);
  const [useRecords, setUseRecords] = useState([]);
  const [maintRecords, setMaintRecords] = useState([]);
  const [refuelRecords, setRefuelRecords] = useState([]);
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
  const [maintPhotos, setMaintPhotos] = useState([]);
  const [refuelPhotos, setRefuelPhotos] = useState([]);
  const [submittingMaint, setSubmittingMaint] = useState(false);
  const [submittingRefuel, setSubmittingRefuel] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [v, u, m, r] = await Promise.all([
        api.get('/vehicles') || [],
        api.get('/vehicle/use-records') || [],
        api.get('/vehicle/maintenance') || [],
        api.get('/vehicle/refueling') || [],
      ]);
      setVehicles(v); setUseRecords(u); setMaintRecords(m); setRefuelRecords(r);
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
      const res = await api.post('/vehicle/refueling', v);
      const newId = res?.id;
      if (newId && refuelPhotos.length) await uploadVehiclePhotos(refuelPhotos, newId, '车辆加油');
      message.success('已记录加油');
      setRefuelOpen(false); refuelForm.resetFields(); setRefuelPhotos([]); load();
    } catch (e) { message.error('失败：' + (e?.message || e)); }
    finally { setSubmittingRefuel(false); }
  };

  const openMaint = (r) => { setActiveVehicle(r); maintForm.setFieldsValue({ vehicle_id: r.id, maint_type: 'routine' }); setMaintPhotos([]); setMaintOpen(true); };
  const openRefuel = (r) => { setActiveVehicle(r); refuelForm.setFieldsValue({ vehicle_id: r.id }); setRefuelPhotos([]); setRefuelOpen(true); };

  const onEditVehicle = async () => {
    try {
      const values = await editForm.validateFields();
      const mileage = values.next_maint_mileage ? Number(values.next_maint_mileage) : null;
      await api.put(`/vehicles/${activeVehicle.id}`, { next_maintenance_mileage: mileage });
      message.success('保养里程已更新');
      setEditVehicleOpen(false); editForm.resetFields(); load();
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
      maintThisMonth: maintRecords.filter(r => isThisMonth(r.maint_at)).length,
      refuelThisMonth: refuelRecords.filter(r => isThisMonth(r.refuel_at)).length,
      mileageThisMonth: mileage,
    };
  }, [vehicles, useRecords, maintRecords, refuelRecords]);

  const vehicleOptions = useMemo(() => vehicles.map(v => ({ value: v.id, label: `${v.plate_no} (${v.model || ''})` })), [vehicles]);

  const ledgerColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '车型', dataIndex: 'model', width: 100, render: v => v || '-' },
    { title: '座位', dataIndex: 'seats', width: 60 },
    { title: '状态', dataIndex: 'status', width: 90, render: v => { const s = VEH_STATUS[v] || {label:v||'-',color:'default'}; return <Tag color={s.color}>{s.label}</Tag>; } },
    { title: '当前里程', dataIndex: 'current_mileage', width: 110, render: v => v ? `${Math.round(v).toLocaleString()} km` : '-' },
    { title: '下次保养里程', dataIndex: 'next_maintenance_mileage', width: 120, render: v => v ? `${Math.round(v).toLocaleString()} km` : '-' },
    { title: '操作', width: 200, render: (_, r) => (
      <Space size={4}>
        {user?.role === 'admin' && (
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            let nextMileage = r.next_maintenance_mileage != null ? String(Math.round(r.next_maintenance_mileage)) : '';
            editForm.setFieldsValue({ next_maint_mileage: nextMileage });
            setActiveVehicle(r); setEditVehicleOpen(true);
          }}>编辑</Button>
        )}
        <Button size="small" icon={<FireOutlined />} onClick={() => openRefuel(r)}>加油</Button>
        <Button size="small" icon={<ToolOutlined />} onClick={() => openMaint(r)}>保养</Button>
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
    { title: '保养项目', dataIndex: 'items', ellipsis: true, render: v => v || '-' },
    { title: '费用', dataIndex: 'cost', width: 100, render: v => v ? `¥${v}` : '-' },
    { title: '下次保养里程', dataIndex: 'next_maint_mileage', width: 120, render: v => v ? `${Math.round(v)} km` : '-' },
  ];

  const refuelColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '加油时间', dataIndex: 'refuel_at', width: 150 },
    { title: '加油量', dataIndex: 'liters', width: 100, render: v => v ? `${v} L` : '-' },
    { title: '金额', dataIndex: 'amount', width: 100, render: v => v ? `¥${v}` : '-' },
    { title: '里程', dataIndex: 'mileage_at', width: 110, render: v => v ? `${Math.round(v)} km` : '-' },
    { title: '备注', dataIndex: 'remark', ellipsis: true, render: v => v || '-' },
  ];

  const tableScroll = { y: 'calc(100vh - 420px)' };

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 24 }}>
      <Title level={4} style={{ margin: 0 }}>车辆管理</Title>
      <Row gutter={12}>
        <Col flex="1"><Card bodyStyle={{ padding: '12px 16px' }}><Statistic title={<span style={{ fontSize: 12 }}>车辆总数</span>} value={stats.total} valueStyle={{ fontSize: 22, fontWeight: 600 }} prefix={<CarOutlined />} /></Card></Col>
        <Col flex="1"><Card bodyStyle={{ padding: '12px 16px' }}><Statistic title={<span style={{ fontSize: 12 }}>使用中</span>} value={stats.inUse} valueStyle={{ color: '#1890ff', fontSize: 22, fontWeight: 600 }} prefix={<CarOutlined />} /></Card></Col>
        <Col flex="1"><Card bodyStyle={{ padding: '12px 16px' }}><Statistic title={<span style={{ fontSize: 12 }}>本月行驶里程</span>} value={stats.mileageThisMonth} suffix="km" valueStyle={{ color: '#52c41a', fontSize: 22, fontWeight: 600 }} /></Card></Col>
        <Col flex="1"><Card bodyStyle={{ padding: '12px 16px' }}><Statistic title={<span style={{ fontSize: 12 }}>本月加油</span>} value={stats.refuelThisMonth} suffix="次" valueStyle={{ color: '#fa8c16', fontSize: 22, fontWeight: 600 }} prefix={<FireOutlined />} /></Card></Col>
        <Col flex="1"><Card bodyStyle={{ padding: '12px 16px' }}><Statistic title={<span style={{ fontSize: 12 }}>本月保养</span>} value={stats.maintThisMonth} suffix="次" valueStyle={{ color: '#722ed1', fontSize: 22, fontWeight: 600 }} prefix={<ToolOutlined />} /></Card></Col>
      </Row>
      <Tabs activeKey={tab} onChange={setTab} style={{ flex: 1 }} className="vehicles-tabs" items={[
        { key: 'ledger', label: <span><CarOutlined /> 车辆台账</span>, children: (
          <Card bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">台账中可直接记录加油 / 保养</Text>
              <Space>
                <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
                {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewVehicleOpen(true)}>新增车辆</Button>}
              </Space>
            </div>
            <Table rowKey="id" dataSource={vehicles} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={ledgerColumns} locale={{ emptyText: <Empty description="暂无车辆" /> }} />
          </Card>
        )},
        { key: 'use', label: <span><HistoryOutlined /> 使用记录</span>, children: (
          <Card bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">来自巡检计划 / 工单的出车还车记录</Text>
              <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
            </div>
            <Table rowKey="id" dataSource={useRecords} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={useColumns} locale={{ emptyText: <Empty description="暂无使用记录" /> }} />
          </Card>
        )},
        { key: 'maint', label: <span><ToolOutlined /> 保养记录</span>, children: (
          <Card bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            style={{ borderRadius: 12, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text type="secondary">车辆保养历史与下次保养里程</Text>
              {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={() => { setActiveVehicle(null); maintForm.resetFields(); setMaintOpen(true); }}>记录保养</Button>}
            </div>
            <Table rowKey="id" dataSource={maintRecords} loading={loading} size="small" pagination={false} scroll={tableScroll}
              columns={maintColumns} locale={{ emptyText: <Empty description="暂无保养记录" /> }} />
          </Card>
        )},
        { key: 'refuel', label: <span><FireOutlined /> 加油记录</span>, children: (
          <Card bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
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

      <Modal open={newVehicleOpen} onCancel={() => setNewVehicleOpen(false)} onOk={onCreateVehicle} title="新增车辆" okText="保存" cancelText="取消" destroyOnClose>
        <Form form={vehicleForm} layout="vertical">
          <Form.Item name="plate_no" label="车牌号" rules={[{ required: true }]}><Input placeholder="如 赣A12345" /></Form.Item>
          <Form.Item name="model" label="车型"><Input placeholder="如 SUV/皮卡" /></Form.Item>
          <Form.Item name="seats" label="座位数" initialValue={5}><InputNumber style={{ width: '100%' }} min={2} max={20} /></Form.Item>
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

      <Modal open={refuelOpen} onCancel={() => { setRefuelOpen(false); refuelForm.resetFields(); setRefuelPhotos([]); }} onOk={onRefuel} confirmLoading={submittingRefuel} title={activeVehicle ? `记录加油 - ${activeVehicle.plate_no}` : '记录加油'} okText="保存" cancelText="取消" destroyOnClose>
        <Form form={refuelForm} layout="vertical">
          <Form.Item name="vehicle_id" label="车辆" rules={[{ required: true }]}>
            <Select options={vehicleOptions} disabled={!!activeVehicle} placeholder="请选择车辆" />
          </Form.Item>
          <Form.Item name="liters" label="加油量（升）" rules={[{ required: true }]}><InputNumber style={{ width: '100%' }} step={0.1} min={0} suffix="L" /></Form.Item>
          <Form.Item name="amount" label="金额（元）"><InputNumber style={{ width: '100%' }} step={0.1} min={0} prefix="¥" /></Form.Item>
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
        onOk={onEditVehicle} title={activeVehicle ? `编辑保养里程 - ${activeVehicle.plate_no}` : '编辑'}
        okText="保存" cancelText="取消" destroyOnClose>
        <Form form={editForm} layout="vertical">
          <Form.Item label="车牌号">
            <Input value={activeVehicle?.plate_no || ''} disabled />
          </Form.Item>
          <Form.Item name="next_maint_mileage" label="下次保养里程（km）" rules={[{ type: 'string', pattern: /^\d+$/, message: '请输入整数' }]}>
            <Input placeholder="请输入下次保养的里程数" style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
