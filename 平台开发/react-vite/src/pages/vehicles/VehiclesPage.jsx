import { useState, useEffect, useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Table, Button, Space, Tag, Typography, App as AntApp, Modal, Form, Input, InputNumber, Tabs, Segmented, Select, Upload, Drawer, Descriptions, Alert, Checkbox, Tooltip } from 'antd';
import { PlusOutlined, ReloadOutlined, ToolOutlined, FireOutlined, UploadOutlined, EditOutlined, SearchOutlined, FileProtectOutlined, SafetyCertificateOutlined, EyeOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { statusColors } from '../../theme/tokens';
import WorkspacePage, { TableLongText, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';
import { filterInputWidth } from '../../services/pageStyles';

const { Text } = Typography;

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
  const { message, modal } = AntApp.useApp();
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const canWrite = (user?.roles || [user?.role]).includes('admin');
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab');
  const tab = ['ledger', 'history', 'decision'].includes(requestedTab) ? requestedTab : 'ledger';
  const requestedHistoryView = searchParams.get('history');
  const historyView = ['use', 'maint', 'refuel'].includes(requestedHistoryView) ? requestedHistoryView : 'use';
  const searchText = searchParams.get('q') || '';
  const [vehicles, setVehicles] = useState([]);
  const [useRecords, setUseRecords] = useState([]);
  const [maintRecords, setMaintRecords] = useState([]);
  const [refuelRecords, setRefuelRecords] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [inspections, setInspections] = useState([]);
  const [inspectionTemplate, setInspectionTemplate] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
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
  const [submittingVehicle, setSubmittingVehicle] = useState(false);
  const [pendingMaintRecordId, setPendingMaintRecordId] = useState(null);
  const [pendingRefuelRecordId, setPendingRefuelRecordId] = useState(null);
  const [maintUploadNotice, setMaintUploadNotice] = useState('');
  const [refuelUploadNotice, setRefuelUploadNotice] = useState('');
  const [detailOpen, setDetailOpen] = useState(false);
  const [inspectionOpen, setInspectionOpen] = useState(false);
  const [documentOpen, setDocumentOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    const sources = [
      ['车辆台账', '/vehicles', setVehicles],
      ['使用记录', '/vehicle/use-records', setUseRecords],
      ['保养记录', '/vehicle/maintenance', setMaintRecords],
      ['能源补给', '/vehicle/refueling', setRefuelRecords],
      ['车辆证照', '/vehicle/documents', setDocuments],
      ['车况检查', '/vehicle/inspections', setInspections],
      ['检查模板', '/vehicle/inspection-template', setInspectionTemplate],
    ];
    const results = await Promise.allSettled(sources.map(([, url]) => api.getStrict(url)));
    const failures = [];
    results.forEach((result, index) => {
      const [label, , setter] = sources[index];
      if (result.status === 'fulfilled' && Array.isArray(result.value)) setter(result.value);
      else failures.push(`${label}：${result.reason?.message || '返回格式异常'}`);
    });
    if (failures.length) setLoadError(failures.join('；'));
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const updateQuery = useCallback((updates) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') next.delete(key);
      else next.set(key, String(value));
    });
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const onCreateVehicle = async () => {
    if (submittingVehicle) return;
    const v = await vehicleForm.validateFields();
    setSubmittingVehicle(true);
    try { await api.postStrict('/vehicles', v); message.success('已新增车辆'); setNewVehicleOpen(false); vehicleForm.resetFields(); load(); }
    catch (e) { message.error('失败：' + e.message); }
    finally { setSubmittingVehicle(false); }
  };
  // 车辆照片与加油/养护记录「一起提交」：先存记录取得新 ID，再关联上传照片
  const uploadVehiclePhotos = async (photos, recordId, category) => {
    const results = await Promise.allSettled(photos.map(async (photo) => {
        const file = photo.file?.originFileObj || photo.file || photo.originFileObj || photo;
        if (!file || typeof file.name !== 'string') {
          throw new Error('照片文件已失效，请移除后重新选择');
        }
        const fd = new FormData();
        fd.append('file', file);
        fd.append('source_type', 'vehicle');
        fd.append('source_id', String(recordId));
        fd.append('category', category);
        fd.append('uploader_name', user?.real_name || user?.name || user?.username || '运维人员');
        await api.postFormStrict('/upload/attachment', fd);
        return photo;
    }));
    return {
      succeeded: photos.filter((_, index) => results[index].status === 'fulfilled'),
      failed: photos.filter((_, index) => results[index].status === 'rejected'),
      errors: results.filter(result => result.status === 'rejected').map(result => result.reason?.message || '照片上传失败'),
    };
  };

  const onMaint = async () => {
    if (submittingMaint) return;
    setSubmittingMaint(true);
    try {
      let recordId = pendingMaintRecordId;
      if (!recordId) {
        const values = await maintForm.validateFields();
        const result = await api.postStrict('/vehicle/maintenance', {
          ...values,
          evidence_expected_count: maintPhotos.length,
        });
        recordId = result.id;
        setPendingMaintRecordId(recordId);
      }
      if (maintPhotos.length) {
        const upload = await uploadVehiclePhotos(maintPhotos, recordId, '养护记录');
        await api.putStrict(`/vehicle/maintenance/${recordId}/evidence-status`, {});
        if (upload.failed.length) {
          setMaintPhotos(upload.failed);
          setMaintUploadNotice(`保养记录已保存；${upload.succeeded.length} 张照片上传成功，${upload.failed.length} 张失败：${upload.errors[0]}。请点击“重试上传”。`);
          message.warning('保养记录已保存，但仍有照片上传失败');
          await load();
          return;
        }
      }
      message.success(`已记录保养${maintPhotos.length ? `，并上传 ${maintPhotos.length} 张照片` : ''}`);
      setMaintOpen(false); maintForm.resetFields(); setMaintPhotos([]); load();
      setPendingMaintRecordId(null); setMaintUploadNotice('');
    } catch (e) {
      const reason = e?.message || String(e);
      if (maintPhotos.length) setMaintUploadNotice(`提交失败：${reason}。已选照片仍保留，请修正后重试。`);
      message.error('失败：' + reason);
    }
    finally { setSubmittingMaint(false); }
  };
  const onRefuel = async () => {
    if (submittingRefuel) return;
    setSubmittingRefuel(true);
    try {
      let recordId = pendingRefuelRecordId;
      if (!recordId) {
        const values = await refuelForm.validateFields();
        const result = await api.postStrict('/vehicle/refueling', {
          ...values,
          energy_quantity: values.energy_quantity,
          evidence_expected_count: refuelPhotos.length,
        });
        recordId = result.id;
        setPendingRefuelRecordId(recordId);
      }
      if (refuelPhotos.length) {
        const upload = await uploadVehiclePhotos(refuelPhotos, recordId, '车辆加油');
        await api.putStrict(`/vehicle/refueling/${recordId}/evidence-status`, {});
        if (upload.failed.length) {
          setRefuelPhotos(upload.failed);
          setRefuelUploadNotice(`能源记录已保存；${upload.succeeded.length} 张照片上传成功，${upload.failed.length} 张失败：${upload.errors[0]}。请点击“重试上传”。`);
          message.warning('能源记录已保存，但仍有照片上传失败');
          await load();
          return;
        }
      }
      message.success(`${isElectric(activeVehicle?.fuel_type) ? '已记录充电' : '已记录加油'}${refuelPhotos.length ? `，并上传 ${refuelPhotos.length} 张照片` : ''}`);
      setRefuelOpen(false); refuelForm.resetFields(); setRefuelPhotos([]); load();
      setPendingRefuelRecordId(null); setRefuelUploadNotice('');
    } catch (e) {
      const reason = e?.message || String(e);
      if (refuelPhotos.length) setRefuelUploadNotice(`提交失败：${reason}。已选照片仍保留，请修正后重试。`);
      message.error('失败：' + reason);
    }
    finally { setSubmittingRefuel(false); }
  };

  const openMaint = (r) => {
    const mileage = Number(r.current_mileage || 0);
    setActiveVehicle(r);
    maintForm.setFieldsValue({
      vehicle_id: r.id,
      maint_type: 'routine',
      mileage_at: mileage,
      next_maint_mileage: mileage + 5000,
    });
    setMaintPhotos([]); setPendingMaintRecordId(null); setMaintUploadNotice(''); setMaintOpen(true);
  };
  const openRefuel = (r) => {
    setActiveVehicle(r);
    refuelForm.setFieldsValue({ vehicle_id: r.id, mileage_at: Number(r.current_mileage || 0) });
    setRefuelPhotos([]); setPendingRefuelRecordId(null); setRefuelUploadNotice(''); setRefuelOpen(true);
  };

  const selectMaintenanceVehicle = (vehicleId) => {
    const vehicle = vehicles.find(item => item.id === vehicleId) || null;
    const mileage = Number(vehicle?.current_mileage || 0);
    setActiveVehicle(vehicle);
    maintForm.setFieldsValue({
      vehicle_id: vehicleId,
      mileage_at: mileage,
      next_maint_mileage: mileage + 5000,
    });
  };

  const selectRefuelingVehicle = (vehicleId) => {
    const vehicle = vehicles.find(item => item.id === vehicleId) || null;
    setActiveVehicle(vehicle);
    refuelForm.setFieldsValue({ vehicle_id: vehicleId, mileage_at: Number(vehicle?.current_mileage || 0) });
  };

  const supplementVehicleEvidence = async (file, record, type) => {
    const category = type === 'maintenance' ? '养护记录' : '车辆加油';
    try {
      const upload = await uploadVehiclePhotos([{ uid: `retry-${Date.now()}`, file, url: '' }], record.id, category);
      if (upload.failed.length) throw new Error('照片上传失败，请检查网络后重试');
      const status = await api.putStrict(`/vehicle/${type}/${record.id}/evidence-status`, {});
      message.success(status.evidence_status === 'complete' ? '照片已补齐' : `已补传 1 张，仍需补充 ${Math.max(0, status.expected - status.uploaded)} 张`);
      await load();
    } catch (error) {
      message.error(error.message || '照片补传失败');
    }
    return false;
  };

  const onEditVehicle = async () => {
    try {
      const values = await editForm.validateFields();
      const editableValues = Object.fromEntries(
        Object.entries(values).filter(([key]) => !['current_mileage', 'next_maintenance_mileage'].includes(key)),
      );
      await api.putStrict(`/vehicles/${activeVehicle.id}`, {
        ...editableValues,
        seats: values.seats == null ? 5 : Number(values.seats),
      });
      message.success('车辆台账已更新');
      setEditVehicleOpen(false); editForm.resetFields(); load();
    } catch (e) { if (e?.message) message.error(e.message); }
  };

  const onDeleteVehicle = (vehicle) => {
    modal.confirm({ title: `删除 ${vehicle.plate_no}？`, content: '仅从未产生申请、行程、能源补给、检查、维保或证照记录的误建车辆可永久删除。', okText: '确认删除', okButtonProps: { danger: true }, cancelText: '取消', onOk: async () => {
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
      await api.postStrict('/vehicle/inspections', { ...values, vehicle_id: activeVehicle.id, overall_status: status, items });
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
      await api.postStrict('/vehicle/documents', { ...values, vehicle_id: activeVehicle.id });
      message.success('证照已登记'); setDocumentOpen(false); documentForm.resetFields(); load();
    } catch (e) { if (e?.message) message.error(e.message); }
  };

  const now = new Date();
  const thisMonth = now.getMonth();
  const thisYear = now.getFullYear();
  const isThisMonth = (d) => { const dt = new Date(d); return dt.getFullYear() === thisYear && dt.getMonth() === thisMonth; };

  const stats = useMemo(() => {
    const mileage = useRecords
      .filter(r => r.returned_at && isThisMonth(r.returned_at) && r.end_mileage != null && r.start_mileage != null)
      .reduce((s, r) => s + (r.end_mileage - r.start_mileage), 0);
    return {
      total: vehicles.length,
      inUse: vehicles.filter(v => v.status === 'in_use').length,
      unavailable: vehicles.filter(v => !v.dispatchable).length,
      documentDue: vehicles.filter(v => (v.document_state?.expired?.length || v.document_state?.due_soon?.length)).length,
      returnPending: useRecords.filter(r => !r.returned_at).length,
      maintThisMonth: maintRecords.filter(r => isThisMonth(r.maint_at)).length,
      refuelThisMonth: refuelRecords.filter(r => isThisMonth(r.refuel_at)).length,
      mileageThisMonth: mileage,
    };
  }, [vehicles, useRecords, maintRecords, refuelRecords]);

  const decisionRows = useMemo(() => vehicles.map((vehicle) => {
    const uses = useRecords.filter(r => r.vehicle_id === vehicle.id);
    const registeredTripMileage = uses.filter(r => r.returned_at && r.end_mileage != null && r.start_mileage != null)
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
    const currentMileage = Number(vehicle.current_mileage || 0);
    const nextMaintenanceMileage = Number(vehicle.next_maintenance_mileage || 0);
    if (nextMaintenanceMileage && currentMileage >= nextMaintenanceMileage) {
      const overdueMileage = Math.round(currentMileage - nextMaintenanceMileage);
      issues.push(overdueMileage > 0 ? `保养已超 ${overdueMileage.toLocaleString()} km` : '保养已到期');
    }
    if (uses.some(r => r.end_mileage != null && r.start_mileage != null && Number(r.end_mileage) < Number(r.start_mileage))) issues.push('里程倒退');
    return { ...vehicle, registeredTripMileage, fuel, maintenanceCost, totalCost: fuel + maintenanceCost,
      costPer100: registeredTripMileage > 0 ? ((fuel + maintenanceCost) / registeredTripMileage * 100).toFixed(1) : null, issues };
  }), [vehicles, useRecords, maintRecords, refuelRecords]);
  const decisionCount = decisionRows.filter(r => r.issues.length).length;
  const activeOperationalIssues = useMemo(() => {
    const issues = decisionRows.find(row => row.id === activeVehicle?.id)?.issues || [];
    return issues.filter(issue => !['证照到期', '证照临期', '限制使用', '维修中'].includes(issue));
  }, [decisionRows, activeVehicle]);

  const activeDocuments = useMemo(() => {
    const records = documents.filter(document => document.vehicle_id === activeVehicle?.id);
    if (!activeVehicle) return records;
    const masterDates = [
      ['insurance', activeVehicle.insurance_expiry],
      ['annual_inspection', activeVehicle.annual_inspection_expiry],
      ['registration', activeVehicle.registration_expiry],
    ];
    const masterRecords = masterDates
      .filter(([type, validUntil]) => validUntil && !records.some(record => record.document_type === type))
      .map(([type, validUntil]) => ({
        id: `vehicle-master-${type}`,
        vehicle_id: activeVehicle.id,
        document_type: type,
        valid_until: validUntil,
        document_no: '',
      }));
    return [...records, ...masterRecords];
  }, [documents, activeVehicle]);
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
    { title: '状态', width: 110, render: (_, r) => {
      if (!r.dispatchable) return <Tooltip title={r.dispatch_block_reason || '车辆当前不可出车'}><Tag color="red">不可出车</Tag></Tooltip>;
      const s = VEH_STATUS[r.status] || { label: r.status || '-', color: 'default' };
      return <Tag color={s.color}>{s.label}</Tag>;
    } },
    { title: '当前里程', dataIndex: 'current_mileage', width: 110, render: v => v != null ? `${Math.round(v).toLocaleString()} km` : '-' },
    { title: '证照 / 车况', width: 160, render: (_, r) => {
      const doc = r.document_state || {};
      if (doc.expired?.length) return <Tag color="red">证照到期</Tag>;
      if (doc.due_soon?.length) return <Tag color="orange">30天内到期</Tag>;
      if (r.last_inspection_status === 'blocked') return <Tag color="red">检查不合格</Tag>;
      if (r.last_inspection_status === 'attention') return <Tag color="orange">车况需关注</Tag>;
      return <Tag color="green">正常</Tag>;
    }},
    { title: '下次保养', dataIndex: 'next_maintenance_mileage', width: 120, render: v => v != null ? `${Math.round(v).toLocaleString()} km` : '-' },
    { title: '操作', width: 130, render: (_, r) => (
      <Space size={4}>
        <Tooltip title="查看车辆档案"><Button size="small" aria-label={`查看 ${r.plate_no} 的车辆档案`} icon={<EyeOutlined />} onClick={() => openDetail(r)} /></Tooltip>
        {canWrite && (
          <Tooltip title="编辑车辆"><Button size="small" aria-label={`编辑车辆 ${r.plate_no}`} icon={<EditOutlined />} onClick={() => {
            editForm.setFieldsValue({
              plate_no: r.plate_no, vehicle_name: r.vehicle_name, model: r.model, seats: r.seats,
              department: r.department, fuel_type: r.fuel_type, status: r.status,
              current_mileage: r.current_mileage, next_maintenance_mileage: r.next_maintenance_mileage,
              purchase_date: r.purchase_date, insurance_expiry: r.insurance_expiry,
              annual_inspection_expiry: r.annual_inspection_expiry, registration_expiry: r.registration_expiry,
            });
            setActiveVehicle(r); setEditVehicleOpen(true);
          }} /></Tooltip>
        )}
        {canWrite && <Tooltip title="删除车辆"><Button size="small" danger aria-label={`删除车辆 ${r.plate_no}`} icon={<DeleteOutlined />} onClick={() => onDeleteVehicle(r)} /></Tooltip>}
        <Tooltip title="登记车况检查"><Button size="small" aria-label={`登记 ${r.plate_no} 的车况检查`} icon={<SafetyCertificateOutlined />} onClick={() => openInspection(r)} /></Tooltip>
      </Space>
    )},
  ];

  const useColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '申请人', dataIndex: 'applicant_name', width: 100, render: v => v || '-' },
    { title: '开始时间', dataIndex: 'start_at', width: 150, render: v => v || '-' },
    { title: '结束时间', dataIndex: 'returned_at', width: 150, render: v => v || '-' },
    { title: '目的地', dataIndex: 'destination', width: 140, render: v => v || '-' },
    { title: '起点里程', dataIndex: 'start_mileage', width: 100, render: v => v != null ? `${Math.round(v)} km` : '-' },
    { title: '终点里程', dataIndex: 'end_mileage', width: 100, render: v => v != null ? `${Math.round(v)} km` : '-' },
    { title: '行驶里程', width: 100, render: (_, r) => (r.start_mileage != null && r.end_mileage != null) ? `${Math.round(r.end_mileage - r.start_mileage)} km` : '-' },
    { title: '状态', width: 90, render: (_, r) => r.returned_at ? <Tag color="green">已还车</Tag> : <Tag color="blue">使用中</Tag> },
  ];

  const maintColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '保养时间', dataIndex: 'maint_at', width: 150 },
    { title: '类型', dataIndex: 'maint_type', width: 100, render: v => MAINT_TYPE[v] || v || '-' },
    { title: '当前里程', dataIndex: 'mileage_at', width: 110, render: v => v != null ? `${Math.round(v)} km` : '-' },
    { title: '保养项目', dataIndex: 'items', width: 260, render: v => <TableLongText value={v} /> },
    { title: '费用', dataIndex: 'cost', width: 100, render: v => v ? `¥${v}` : '-' },
    { title: '下次保养里程', dataIndex: 'next_maint_mileage', width: 120, render: v => v != null ? `${Math.round(v)} km` : '-' },
    { title: '影像', width: 130, render: (_, r) => {
      if (r.evidence_status === 'not_required' || !r.evidence_expected_count) return <Text type="secondary">未要求</Text>;
      if (r.evidence_status === 'complete') return <Tag color="green">已完整</Tag>;
      return <Space size={4}><Tag color="orange">{r.evidence_status === 'partial' ? '部分成功' : '待补传'}</Tag><Upload showUploadList={false} multiple beforeUpload={(file) => supplementVehicleEvidence(file, r, 'maintenance')}><Button size="small">补传</Button></Upload></Space>;
    } },
  ];

  const refuelColumns = [
    { title: '车牌', dataIndex: 'plate_no', width: 110 },
    { title: '补给时间', dataIndex: 'refuel_at', width: 150 },
    { title: '补给量', width: 100, render: (_, r) => { const q = r.energy_quantity ?? r.liters; return q ? `${q} ${r.energy_unit || (r.vehicle_fuel_type === 'electric' ? 'kWh' : 'L')}` : '-'; } },
    { title: '金额', dataIndex: 'amount', width: 100, render: v => v ? `¥${v}` : '-' },
    { title: '里程', dataIndex: 'mileage_at', width: 110, render: v => v != null ? `${Math.round(v)} km` : '-' },
    { title: '备注', dataIndex: 'remark', width: 260, render: v => <TableLongText value={v} /> },
    { title: '影像', width: 130, render: (_, r) => {
      if (r.evidence_status === 'not_required' || !r.evidence_expected_count) return <Text type="secondary">未要求</Text>;
      if (r.evidence_status === 'complete') return <Tag color="green">已完整</Tag>;
      return <Space size={4}><Tag color="orange">{r.evidence_status === 'partial' ? '部分成功' : '待补传'}</Tag><Upload showUploadList={false} multiple beforeUpload={(file) => supplementVehicleEvidence(file, r, 'refueling')}><Button size="small">补传</Button></Upload></Space>;
    } },
  ];

  const historyData = historyView === 'use' ? useRecords : historyView === 'maint' ? maintRecords : refuelRecords;
  const historyColumns = historyView === 'use' ? useColumns : historyView === 'maint' ? maintColumns : refuelColumns;
  const historyAction = historyView === 'maint' && canWrite
    ? <Button type="primary" icon={<PlusOutlined />} onClick={() => { setActiveVehicle(null); maintForm.resetFields(); setMaintOpen(true); }}>记录保养</Button>
    : historyView === 'refuel' && canWrite
      ? <Button type="primary" icon={<PlusOutlined />} onClick={() => { setActiveVehicle(null); refuelForm.resetFields(); setRefuelPhotos([]); setRefuelOpen(true); }}>记录补给</Button>
      : null;

  return (
    <WorkspacePage
      title="车辆"
      subtitle="集中查看车辆台账、出车履历和待处理风险。"
      primaryAction={canWrite ? <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewVehicleOpen(true)}>新增车辆</Button> : null}
      statusItems={[
        { label: '待处理', value: decisionCount, color: tokens.colorWarning },
        { label: '出车中', value: stats.inUse, color: statusColors.info[isDark ? 'dark' : 'light'] },
        { label: '待归还', value: stats.returnPending, color: tokens.colorWarning },
        { label: '受限或维修', value: stats.unavailable, color: tokens.colorError },
        { label: '证照需关注', value: stats.documentDue, color: tokens.colorWarning },
      ]}
    >
      {loadError ? (
        <Alert
          type="warning"
          showIcon
          message={vehicles.length > 0 ? '部分车辆数据刷新失败，当前保留上次加载的数据' : '车辆数据加载失败'}
          description={loadError}
          action={<Button size="small" onClick={load}>重新加载</Button>}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <Tabs activeKey={tab} onChange={(key) => updateQuery({ tab: key === 'ledger' ? '' : key })} type="line" items={[
        {
          key: 'ledger', label: '台账', children: <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <WorkspaceToolbar actions={<Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}>
              <Input aria-label="车辆搜索" placeholder="搜索车牌号 / 负责人" prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
                allowClear value={searchText} onChange={(e) => updateQuery({ q: e.target.value })} style={{ width: filterInputWidth }} />
            </WorkspaceToolbar>
            <WorkspaceTable rowKey="id" dataSource={filteredVehicles} loading={loading} columns={ledgerColumns}
              emptyType={searchText ? 'filtered' : 'empty'} onRefresh={load} />
          </div>,
        },
        {
          key: 'history', label: '履历', children: <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Segmented value={historyView} onChange={(value) => updateQuery({ history: value === 'use' ? '' : value })} options={[
              { value: 'use', label: '使用' }, { value: 'maint', label: '保养' }, { value: 'refuel', label: '补给' },
            ]} />
            <WorkspaceToolbar actions={<Space><Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>{historyAction}</Space>}>
              <Text type="secondary">{historyView === 'use' ? '来自巡检计划和工单的出车、还车记录。' : historyView === 'maint' ? '车辆保养历史与下次保养里程。' : '车辆加油、充电和费用记录。'}</Text>
            </WorkspaceToolbar>
            <WorkspaceTable rowKey="id" dataSource={historyData} loading={loading} columns={historyColumns} emptyType="empty" onRefresh={load} />
          </div>,
        },
        {
          key: 'decision', label: decisionCount ? `待办 ${decisionCount}` : '待办', children: <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <WorkspaceToolbar actions={<Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}>
              <Text type="secondary">仅列出需要处理的证照、车况、维修和归还事项。</Text>
            </WorkspaceToolbar>
            <WorkspaceTable rowKey="id" dataSource={decisionRows.filter(row => row.issues.length)} loading={loading} emptyType="empty" onRefresh={load}
              columns={[
                { title: '车辆', width: 140, render: (_, r) => <span>{r.plate_no}<br /><Text type="secondary" style={{ fontSize: 12 }}>{r.model || '-'}</Text></span> },
                { title: '待处理事项', width: 220, render: (_, r) => <Space size={[4, 4]} wrap>{r.issues.map(issue => <Tag color={issue === '证照临期' ? 'orange' : 'red'} key={issue}>{issue}</Tag>)}</Space> },
                { title: <Tooltip title="仅统计系统内已完成并归还的行程">已登记行程</Tooltip>, dataIndex: 'registeredTripMileage', width: 120, render: v => `${Math.round(v)} km` },
                { title: '加油 / 维修', width: 130, render: (_, r) => `¥${r.fuel.toFixed(0)} / ¥${r.maintenanceCost.toFixed(0)}` },
                { title: '综合费用', dataIndex: 'totalCost', width: 100, render: v => `¥${v.toFixed(0)}` },
                { title: '操作', width: 90, render: (_, r) => <Button size="small" aria-label={`查看 ${r.plate_no} 的待办详情`} onClick={() => openDetail(r)}>详情</Button> },
              ]} />
          </div>,
        },
      ]} />

      <Drawer open={detailOpen} onClose={() => setDetailOpen(false)} width={640}
        title={activeVehicle ? `${activeVehicle.plate_no} · 车辆档案` : '车辆档案'}>
        {activeVehicle && <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {!activeVehicle.dispatchable && <Alert type="error" showIcon message={`当前不可出车：${activeVehicle.dispatch_block_reason || '车辆状态受限'}`} />}
          {activeVehicle.document_state?.due_soon?.length > 0 && <Alert type="warning" showIcon message={`30天内到期：${activeVehicle.document_state.due_soon.map(t => DOC_TYPE[t] || t).join('、')}`} />}
          {activeOperationalIssues.length > 0 && <Alert type="warning" showIcon message={`待处理：${activeOperationalIssues.join('、')}`} />}
          <Descriptions size="small" bordered column={2}>
            <Descriptions.Item label="状态">{activeVehicle.dispatchable
              ? <Tag color={(VEH_STATUS[activeVehicle.status] || {}).color}>{(VEH_STATUS[activeVehicle.status] || {}).label || activeVehicle.status}</Tag>
              : <Tag color="red">不可出车</Tag>}</Descriptions.Item>
            <Descriptions.Item label="当前里程">{Math.round(activeVehicle.current_mileage || 0).toLocaleString()} km</Descriptions.Item>
            <Descriptions.Item label="车型">{activeVehicle.model || '-'}</Descriptions.Item>
            <Descriptions.Item label="归属">{activeVehicle.department || '-'}</Descriptions.Item>
            <Descriptions.Item label="下次保养">{activeVehicle.next_maintenance_mileage ? `${Math.round(activeVehicle.next_maintenance_mileage).toLocaleString()} km` : '-'}</Descriptions.Item>
            <Descriptions.Item label="最近检查">{activeVehicle.last_inspection_at || '-'}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button icon={<SafetyCertificateOutlined />} onClick={() => openInspection(activeVehicle)}>登记车况检查</Button>
            <Button icon={<FireOutlined />} onClick={() => openRefuel(activeVehicle)}>
              {isElectric(activeVehicle.fuel_type) ? '记录充电' : '记录加油'}
            </Button>
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
        title={activeVehicle ? `车况检查 - ${activeVehicle.plate_no}` : '车况检查'} okText="提交检查" cancelText="取消" destroyOnHidden>
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
        title={activeVehicle ? `登记证照 - ${activeVehicle.plate_no}` : '登记证照'} okText="保存" cancelText="取消" destroyOnHidden>
        <Form form={documentForm} layout="vertical">
          <Form.Item name="document_type" label="证照类型" rules={[{ required: true }]}><Select options={Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="valid_until" label="有效期至" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="document_no" label="证照编号"><Input /></Form.Item>
          <Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal open={newVehicleOpen} onCancel={() => setNewVehicleOpen(false)} onOk={onCreateVehicle} confirmLoading={submittingVehicle} title="新增车辆" okText="保存" cancelText="取消" destroyOnHidden>
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

      <Modal open={maintOpen} onCancel={() => {
        if (pendingMaintRecordId && maintPhotos.length) { message.warning('记录已保存，仍有照片待补传；请重试上传或移除失败照片后关闭'); return; }
        setMaintOpen(false); maintForm.resetFields(); setMaintPhotos([]); setPendingMaintRecordId(null); setMaintUploadNotice('');
      }} onOk={onMaint} confirmLoading={submittingMaint} title={activeVehicle ? `记录保养 - ${activeVehicle.plate_no}` : '记录保养'} okText={pendingMaintRecordId ? '重试上传' : '保存'} cancelText="取消" destroyOnHidden>
        <Form form={maintForm} layout="vertical">
          {maintUploadNotice && <Alert type="warning" showIcon message={maintUploadNotice} style={{ marginBottom: 16 }} />}
          <Form.Item name="vehicle_id" label="车辆" rules={[{ required: true }]}>
            <Select options={vehicleOptions} disabled={!!activeVehicle} placeholder="请选择车辆" onChange={selectMaintenanceVehicle} />
          </Form.Item>
          <Form.Item name="maint_type" label="类型" initialValue="routine" rules={[{ required: true }]}>
            <Select options={Object.entries(MAINT_TYPE).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="mileage_at" label="当前里程"><InputNumber style={{ width: '100%' }} min={0} disabled suffix="km" /></Form.Item>
          <Form.Item name="items" label="保养项目"><Input.TextArea rows={2} placeholder="如 更换机油、机滤" /></Form.Item>
          <Form.Item name="cost" label="费用"><InputNumber style={{ width: '100%' }} min={0} step={0.01} prefix="¥" /></Form.Item>
          <Form.Item name="next_maint_mileage" label="下次保养里程（当前里程 + 5000 km）"><InputNumber style={{ width: '100%' }} min={0} disabled suffix="km" /></Form.Item>
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

      <Modal open={refuelOpen} onCancel={() => {
        if (pendingRefuelRecordId && refuelPhotos.length) { message.warning('记录已保存，仍有照片待补传；请重试上传或移除失败照片后关闭'); return; }
        setRefuelOpen(false); refuelForm.resetFields(); setRefuelPhotos([]); setPendingRefuelRecordId(null); setRefuelUploadNotice('');
      }} onOk={onRefuel} confirmLoading={submittingRefuel} title={activeVehicle ? `记录${isElectric(activeVehicle.fuel_type) ? '充电' : '加油'} - ${activeVehicle.plate_no}` : '记录能源补给'} okText={pendingRefuelRecordId ? '重试上传' : '保存'} cancelText="取消" destroyOnHidden>
        <Form form={refuelForm} layout="vertical">
          {refuelUploadNotice && <Alert type="warning" showIcon message={refuelUploadNotice} style={{ marginBottom: 16 }} />}
          <Form.Item name="vehicle_id" label="车辆" rules={[{ required: true }]}>
            <Select options={vehicleOptions} disabled={!!activeVehicle} placeholder="请选择车辆" onChange={selectRefuelingVehicle} />
          </Form.Item>
          <Form.Item name="energy_quantity" label={isElectric(activeVehicle?.fuel_type) ? '充电量（kWh）' : '加油量（L）'} rules={[{ required: true }]}><InputNumber style={{ width: '100%' }} step={0.1} min={0} suffix={isElectric(activeVehicle?.fuel_type) ? 'kWh' : 'L'} /></Form.Item>
          <Form.Item name="amount" label="金额（元）"><InputNumber style={{ width: '100%' }} step={0.1} min={0} prefix="¥" /></Form.Item>
          <Form.Item name="unit_price" label={isElectric(activeVehicle?.fuel_type) ? '单价（元/kWh）' : '单价（元/L）'}><InputNumber style={{ width: '100%' }} step={0.01} min={0} prefix="¥" /></Form.Item>
          <Form.Item name="mileage_at" label="当前里程"><InputNumber style={{ width: '100%' }} min={0} disabled suffix="km" /></Form.Item>
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
        okText="保存" cancelText="取消" destroyOnHidden>
        <Form form={editForm} layout="vertical">
          <Form.Item name="plate_no" label="车牌号" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="vehicle_name" label="车辆名称"><Input /></Form.Item>
          <Form.Item name="model" label="车型"><Input /></Form.Item>
          <Form.Item name="seats" label="座位数"><InputNumber min={1} max={99} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="department" label="归属部门"><Input /></Form.Item>
          <Form.Item name="fuel_type" label="能源类型"><Select options={Object.entries(ENERGY_TYPE).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="status" label="车辆状态"><Select options={Object.entries(VEH_STATUS).map(([value, item]) => ({ value, label: item.label }))} /></Form.Item>
          <Form.Item name="current_mileage" label="当前里程（系统记录）"><InputNumber min={0} style={{ width: '100%' }} disabled /></Form.Item>
          <Form.Item name="next_maintenance_mileage" label="下次保养里程（系统计算）"><InputNumber min={0} style={{ width: '100%' }} disabled /></Form.Item>
          <Form.Item name="purchase_date" label="购置日期"><Input type="date" /></Form.Item>
          <Form.Item name="insurance_expiry" label="保险到期日"><Input type="date" /></Form.Item>
          <Form.Item name="annual_inspection_expiry" label="年检到期日"><Input type="date" /></Form.Item>
          <Form.Item name="registration_expiry" label="行驶证有效期"><Input type="date" /></Form.Item>
        </Form>
      </Modal>
    </WorkspacePage>
  );
}
