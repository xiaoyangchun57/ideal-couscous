import { useState, useEffect, useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Table, Card, Input, Select, Button, Space, Tag, Badge,
  Modal, Descriptions, Tabs, Typography, message, Spin, Empty, Row, Col,
  Upload, Timeline, Divider, Form, InputNumber, Popconfirm, DatePicker, Image, Radio,
} from 'antd';
import {
  SearchOutlined, FileSearchOutlined, EnvironmentOutlined,
  ReloadOutlined, FilterOutlined, DownloadOutlined, UploadOutlined,
  FileTextOutlined, CloudServerOutlined, ApiOutlined, InboxOutlined,
  PlusOutlined, DeleteOutlined, ExperimentOutlined, CheckCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { stationTypeMap } from '../../services/constants';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import { getThresholds, classifyMetric, THRESHOLD_COLORS } from '../../services/thresholds';
import ArchiveTrendPanel from './components/ArchiveTrendPanel';
import dayjs from 'dayjs';

const { Text, Title } = Typography;

// ---------------------------------------------------------------------------
// Station type → Tag color mapping
// ---------------------------------------------------------------------------
const typeColorMap = {
  water_quality: 'blue',
  manual_station: 'cyan',
  drinking_source: 'green',
  cross_boundary: 'orange',
  groundwater: 'purple',
  station_yard: 'gold',
};

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------
const tagStyle = { borderRadius: 4, fontSize: 11 };

const statusConfig = {
  normal: { color: 'green', text: '在线' },
  online: { color: 'green', text: '在线' },
  offline: { color: 'red', text: '离线' },
  maintenance: { color: 'orange', text: '维护中' },
};

function getStatusCfg(status) {
  return statusConfig[status] || { color: 'default', text: status || '未知' };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function SitesPage() {
  const { tokens } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();

  // ---- data state ----
  const [sites, setSites] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState(null);
  const [listWrapRef, listH] = useTableAutoHeight({ headerOffset: 40 });

  // ---- filter state ----
  const [searchText, setSearchText] = useState('');
  const [typeFilter, setTypeFilter] = useState(undefined);
  const [districtFilter, setDistrictFilter] = useState(undefined);
  const [managerFilter, setManagerFilter] = useState(undefined);

  // ---- archive modal ----
  const [archiveModalOpen, setArchiveModalOpen] = useState(false);
  const [archiveData, setArchiveData] = useState(null);
  const [reagentInventory, setReagentInventory] = useState([]);
  const [reagentUpdOpen, setReagentUpdOpen] = useState(false);
  const [reagentUpdTarget, setReagentUpdTarget] = useState(null);
  const [reagentUpdForm, setReagentUpdForm] = useState({ placed_at: null, expected_duration_days: '', new_qty: '' });
  const [reagentModalMode, setReagentModalMode] = useState('edit'); // 'create' | 'edit'
  const [reagentMaster, setReagentMaster] = useState([]); // 试剂主数据目录（用于新增下拉）
  const [reagentSubmitting, setReagentSubmitting] = useState(false);
  // 试剂质控（更换后跑标样）
  const [qcOpen, setQcOpen] = useState(false);
  const [qcTarget, setQcTarget] = useState(null);
  const [qcForm, setQcForm] = useState({ standard_value: '', measured_value: '', passed: true, fail_action: 'calibrate', remark: '' });
  const [qcSubmitting, setQcSubmitting] = useState(false);
  const [archiveSiteId, setArchiveSiteId] = useState(null);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [selectedTrendParam, setSelectedTrendParam] = useState('ph');
  const [thresholds, setThresholds] = useState([]);

  // ---- data import modal ----
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importTab, setImportTab] = useState('file');
  const [importLoading, setImportLoading] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [dataSources, setDataSources] = useState([]);
  const [dsForm] = Form.useForm();
  const [dsModalOpen, setDsModalOpen] = useState(false);
  const [dsLoading, setDsLoading] = useState(false);
  const [testingDs, setTestingDs] = useState(null);

  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  // ========================================================================
  // Fetch all sites
  // ========================================================================
  const fetchSites = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const data = await api.get('/sites');
      if (data && Array.isArray(data)) {
        setSites(data);
      } else if (data && Array.isArray(data.data)) {
        // handle { data: [...] } wrapper
        setSites(data.data);
      } else {
        setSites([]);
        setFetchError('无法获取站点数据，请稍后重试');
      }
    } catch (err) {
      console.error('Failed to fetch sites:', err);
      setFetchError('网络异常，无法加载站点列表');
      message.error('加载站点列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSites();
  }, [fetchSites]);

  // 加载阈值配置（色阶数据源）
  useEffect(() => {
    getThresholds().then((data) => setThresholds(Array.isArray(data) ? data : [])).catch(() => setThresholds([]));
  }, []);

  // ========================================================================
  // Derived option lists for filter dropdowns
  // ========================================================================
  // Normalize district names: strip "江西" prefix, merge county/district variants
  const normalizeDistrict = (addr) => {
    if (!addr) return '';
    let d = addr.replace(/^江西/, '');
    // Merge known variants
    const mergeMap = { '新建县': '新建区', '红谷滩新区': '红谷滩区' };
    if (mergeMap[d]) d = mergeMap[d];
    return d;
  };

  // Extract district-level prefix from full address (e.g. "新建区某某村" → "新建区")
  const extractDistrict = (addr) => {
    if (!addr) return '';
    for (let i = 0; i < addr.length; i++) {
      if ('区县市'.includes(addr[i]) && i > 0 && i < addr.length - 1) {
        return normalizeDistrict(addr.slice(0, i + 1));
      }
    }
    return normalizeDistrict(addr);
  };

  const districtOptions = useMemo(() => {
    const set = new Set(sites.map((s) => extractDistrict(s.district)).filter(Boolean));
    return [...set].sort().map((d) => ({ label: d, value: d }));
  }, [sites]);

  const managerOptions = useMemo(() => {
    const set = new Set(sites.map((s) => s.manager).filter(Boolean));
    return [...set].sort().map((m) => ({ label: m, value: m }));
  }, [sites]);

  const typeOptions = useMemo(
    () =>
      Object.entries(stationTypeMap).map(([value, label]) => ({
        label,
        value,
      })),
    [],
  );

  // ========================================================================
  // Client-side filtering + sorting (abnormal sites first)
  // ========================================================================
  const statusPriority = { offline: 0, maintenance: 1, normal: 2, online: 2 };
  const filteredSites = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    const result = sites.filter((site) => {
      if (keyword) {
        const nameMatch = (site.name || '').toLowerCase().includes(keyword);
        const codeMatch = (site.code || '').toLowerCase().includes(keyword);
        if (!nameMatch && !codeMatch) return false;
      }
      if (typeFilter && site.type !== typeFilter) return false;
      if (districtFilter && extractDistrict(site.district) !== districtFilter) return false;
      if (managerFilter && site.manager !== managerFilter) return false;
      return true;
    });
    // Sort: abnormal (offline/maintenance) first, then by name
    result.sort((a, b) => {
      const pa = statusPriority[a.status] ?? 2;
      const pb = statusPriority[b.status] ?? 2;
      if (pa !== pb) return pa - pb;
      return (a.name || '').localeCompare(b.name || '', 'zh');
    });
    return result;
  }, [sites, searchText, typeFilter, districtFilter, managerFilter]);

  // ========================================================================
  // Archive modal handler
  // ========================================================================
  const openArchive = useCallback(async (siteId) => {
    setArchiveSiteId(siteId);
    setArchiveModalOpen(true);
    setArchiveLoading(true);
    setArchiveData(null);
    setReagentInventory([]);
    try {
      const [data, inv] = await Promise.all([
        api.get(`/sites/${siteId}/archive`),
        api.get(`/reagent-inventory/${siteId}`).catch(() => []),
      ]);
      if (data) {
        setArchiveData(data);
      } else {
        message.warning('未获取到该站点的档案信息');
      }
      if (Array.isArray(inv)) setReagentInventory(inv);
    } catch (err) {
      console.error('Failed to fetch archive:', err);
      message.error('加载站点档案失败');
    } finally {
      setArchiveLoading(false);
    }
  }, []);

  const closeArchive = useCallback(() => {
    setArchiveModalOpen(false);
    setArchiveData(null);
    setReagentInventory([]);
    setReagentUpdOpen(false);
    setReagentUpdTarget(null);
  }, []);

  // 试剂库存：新增 / 编辑（更换时间 + 可用天数 + 余量）
  const openReagentUpd = (row) => {
    setReagentModalMode('edit');
    setReagentUpdTarget(row);
    setReagentUpdForm({
      placed_at: row.last_replaced_at ? row.last_replaced_at.slice(0, 10) : null,
      expected_duration_days: row.expected_duration_days ?? '',
      new_qty: row.current_qty ?? '',
    });
    setReagentUpdOpen(true);
  };
  const openReagentCreate = async () => {
    setReagentModalMode('create');
    setReagentUpdTarget(null);
    setReagentUpdForm({ reagent_id: undefined, placed_at: null, expected_duration_days: '', new_qty: '' });
    setReagentUpdOpen(true);
    try {
      const masters = await api.get('/reagents');
      const owned = new Set(reagentInventory.map((r) => r.reagent_id));
      setReagentMaster(Array.isArray(masters) ? masters.filter((m) => !owned.has(m.id)) : []);
    } catch {
      setReagentMaster([]);
    }
  };
  const submitReagentUpd = async () => {
    if (reagentModalMode === 'create') {
      if (!reagentUpdForm.reagent_id) { message.warning('请选择试剂'); return; }
      if (!reagentUpdForm.expected_duration_days && reagentUpdForm.expected_duration_days !== 0) {
        message.warning('请填写可用天数'); return;
      }
      setReagentSubmitting(true);
      try {
        await api.post('/reagent-inventory', {
          site_id: archiveSiteId,
          reagent_id: reagentUpdForm.reagent_id,
          current_qty: reagentUpdForm.new_qty === '' ? null : Number(reagentUpdForm.new_qty),
          last_replaced_at: reagentUpdForm.placed_at ? `${reagentUpdForm.placed_at} 00:00:00` : undefined,
          expected_duration_days: Number(reagentUpdForm.expected_duration_days),
        });
        message.success('已新增试剂库存');
        setReagentUpdOpen(false);
        const inv = await api.get(`/reagent-inventory/${archiveSiteId}`);
        if (Array.isArray(inv)) setReagentInventory(inv);
      } catch (e) {
        message.error(e?.response?.data?.error || '新增失败');
      } finally { setReagentSubmitting(false); }
      return;
    }
    // 编辑模式：复用更换接口（写入更换记录并刷新剩余天数）
    if (!reagentUpdTarget) return;
    if (!reagentUpdForm.expected_duration_days && reagentUpdForm.expected_duration_days !== 0) {
      message.warning('请填写可用天数'); return;
    }
    try {
      await api.post('/reagent-inventory/replacement', {
        site_id: reagentUpdTarget.site_id,
        reagent_id: reagentUpdTarget.reagent_id,
        new_qty: reagentUpdForm.new_qty === '' ? null : Number(reagentUpdForm.new_qty),
        expected_duration_days: Number(reagentUpdForm.expected_duration_days),
        placed_at: reagentUpdForm.placed_at
          ? `${reagentUpdForm.placed_at} 00:00:00`
          : undefined,
        operator: archiveData?.manager || '运维人员',
      });
      message.success('已保存');
      setReagentUpdOpen(false);
      const inv = await api.get(`/reagent-inventory/${reagentUpdTarget.site_id}`);
      if (Array.isArray(inv)) setReagentInventory(inv);
    } catch (e) {
      message.error('保存失败');
    }
  };
  const deleteReagent = async (row) => {
    try {
      await api.delete(`/reagent-inventory/${row.site_id}/${row.reagent_id}`);
      message.success('已删除');
      const inv = await api.get(`/reagent-inventory/${row.site_id}`);
      if (Array.isArray(inv)) setReagentInventory(inv);
    } catch {
      message.error('删除失败');
    }
  };

  // 试剂质控：更换后跑标样，通过才算更换完成
  const openQc = (row) => {
    setQcTarget(row);
    setQcForm({ standard_value: '', measured_value: '', passed: true, fail_action: 'calibrate', remark: '' });
    setQcOpen(true);
  };
  const submitQc = async () => {
    if (!qcTarget) return;
    if (qcForm.standard_value === '' || qcForm.measured_value === '') {
      message.warning('请填写标样值与实测值'); return;
    }
    setQcSubmitting(true);
    try {
      const res = await api.post('/reagent-qc', {
        site_id: qcTarget.site_id,
        reagent_id: qcTarget.reagent_id,
        standard_value: Number(qcForm.standard_value),
        measured_value: Number(qcForm.measured_value),
        passed: qcForm.passed ? 1 : 0,
        fail_action: qcForm.passed ? '' : qcForm.fail_action,
        remark: qcForm.remark,
      });
      if (res?.error) { message.error(res.error); return; }
      message.success(qcForm.passed ? '质控通过，更换完成' : '已记录质控不通过，请跟进处理');
      setQcOpen(false);
      const inv = await api.get(`/reagent-inventory/${qcTarget.site_id}`);
      if (Array.isArray(inv)) setReagentInventory(inv);
    } catch (e) {
      message.error(e?.response?.data?.error || '质控提交失败');
    } finally { setQcSubmitting(false); }
  };

  // Auto-open archive modal when navigated from cockpit with ?archive=siteId
  useEffect(() => {
    const archiveId = searchParams.get('archive');
    if (archiveId) {
      const timer = setTimeout(() => {
        openArchive(archiveId);
        setSearchParams({}, { replace: true });
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [searchParams, openArchive, setSearchParams]);

  // ========================================================================
  // Reset filters
  // ========================================================================
  const resetFilters = useCallback(() => {
    setSearchText('');
    setTypeFilter(undefined);
    setDistrictFilter(undefined);
    setManagerFilter(undefined);
  }, []);

  // ========================================================================
  // Table columns
  // ========================================================================
  const columns = useMemo(
    () => [
      {
        title: '站点编码',
        dataIndex: 'code',
        key: 'code',
        width: 140,
        ellipsis: true,
        sorter: (a, b) => (a.code || '').localeCompare(b.code || ''),
        render: (text) => text || '-',
      },
      {
        title: '站点名称',
        dataIndex: 'name',
        key: 'name',
        width: 180,
        ellipsis: true,
        sorter: (a, b) => (a.name || '').localeCompare(b.name || ''),
        render: (text) => <Text strong>{text}</Text>,
      },
      {
        title: '区县/地址',
        key: 'location',
        width: 240,
        ellipsis: true,
        render: (_, record) => (
          <Space size={4} align="start">
            <EnvironmentOutlined style={{ color: tokens.colorTextTertiary, marginTop: 4 }} />
            <span>
              <Text type="secondary">{record.district}</Text>
              {record.address && (
                <>
                  <Text type="secondary"> · </Text>
                  <Text>{record.address}</Text>
                </>
              )}
            </span>
          </Space>
        ),
      },
      {
        title: '站点类型',
        dataIndex: 'type',
        key: 'type',
        width: 120,
        filters: Object.entries(stationTypeMap).map(([value, text]) => ({
          text,
          value,
        })),
        onFilter: (value, record) => record.type === value,
        render: (type) => {
          const label = stationTypeMap[type] || type;
          const color = typeColorMap[type] || 'default';
          return <Tag color={color} style={tagStyle}>{label}</Tag>;
        },
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 100,
        render: (status) => {
          const cfg = getStatusCfg(status);
          return <Badge color={cfg.color} text={cfg.text} />;
        },
      },
      {
        title: '负责人',
        dataIndex: 'manager',
        key: 'manager',
        width: 110,
        ellipsis: true,
      },
      {
        title: '操作',
        key: 'actions',
        width: 100,
        fixed: 'right',
        render: (_, record) => (
          <Button
            type="link"
            size="small"
            icon={<FileSearchOutlined />}
            onClick={() => openArchive(record.id)}
          >
            档案
          </Button>
        ),
      },
    ],
    [tokens, openArchive],
  );

  // ========================================================================
  // Active filter count (for badge on reset button)
  // ========================================================================
  const activeFilterCount = useMemo(
    () => [typeFilter, districtFilter, managerFilter].filter(Boolean).length + (searchText ? 1 : 0),
    [typeFilter, districtFilter, managerFilter, searchText],
  );

  // ========================================================================
  // Archive modal content
  // ========================================================================
  const renderArchiveContent = () => {
    if (archiveLoading) {
      return (
        <div style={{ textAlign: 'center', padding: '48px 0' }}>
          <Spin size="large" tip="加载档案数据..." />
        </div>
      );
    }
    if (!archiveData) {
      return <Empty description="暂无档案数据" />;
    }

    const {
      name, code, id, type, district, address, manager, status,
      lat, lng, build_date, elevation, equipment, history_records,
      description: siteDesc, contact, area, basin,
      fault_records, replacement_records, inspection_records, calibration_reports,
    } = archiveData;

    const basicItems = [
      { key: 'code', label: '站点编码', children: code },
      { key: 'name', label: '站点名称', children: name },
      {
        key: 'type',
        label: '站点类型',
        children: <Tag color={typeColorMap[type] || 'default'} style={tagStyle}>{stationTypeMap[type] || type}</Tag>,
      },
      {
        key: 'status',
        label: '运行状态',
        children: <Badge {...getStatusCfg(status)} />,
      },
      { key: 'district', label: '所属区县', children: extractDistrict(district) || '-' },
      { key: 'address', label: '详细地址', children: address || '-', span: 2 },
      { key: 'basin', label: '所属流域', children: basin || '-' },
      { key: 'elevation', label: '海拔高程', children: elevation ? `${elevation}m` : '-' },
      {
        key: 'coordinates',
        label: '经纬度',
        children: lat && lng ? `${Number(lat).toFixed(6)}, ${Number(lng).toFixed(6)}` : '-',
      },
      { key: 'build_date', label: '建站日期', children: build_date || '-' },
      { key: 'manager', label: '负责人', children: manager || '-' },
    ];

    const tabItems = [
      {
        key: 'basic',
        label: '基本信息',
        children: (
          <div>
            <Descriptions bordered size="small" column={2} items={basicItems} labelStyle={{ width: 90, minWidth: 90 }} contentStyle={{ width: 'auto' }} />
            <Divider style={{ margin: '20px 0 16px' }} />
            <ArchiveTrendPanel
              code={code}
              selectedKey={selectedTrendParam}
              onSelectedKeyChange={setSelectedTrendParam}
              tokens={tokens}
              thresholds={thresholds}
              classifyMetric={classifyMetric}
              tagStyle={tagStyle}
            />
          </div>
        ),
      },
    ];

    // Equipment list - always show tab
    const equipmentList = equipment || [];
    const eqColumns = [
      { title: '设备名称', dataIndex: 'device_name', key: 'device_name', render: (v) => v || '-' },
      { title: '设备型号', dataIndex: 'device_model', key: 'device_model', render: (v) => v || '-' },
      { title: '生产厂家', dataIndex: 'manufacturer', key: 'manufacturer', render: (v) => v || '-' },
      { title: '安装日期', dataIndex: 'install_date', key: 'install_date', render: (v) => v || '-' },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        render: (s) => {
          const cfg = getStatusCfg(s);
          return <Badge color={cfg.color} text={cfg.text} />;
        },
      },
    ];
    tabItems.push({
      key: 'equipment',
      label: `设备清单${equipmentList.length > 0 ? ` (${equipmentList.length})` : ''}`,
      children: equipmentList.length > 0 ? (
        <Table dataSource={equipmentList} columns={eqColumns} rowKey={(r) => r.id || r.name} pagination={false} size="small" />
      ) : (
        <Empty description="暂无设备信息" style={{ padding: '32px 0' }} />
      ),
    });

    // Fault records - always show tab
    const faultRecords = fault_records || [];
    tabItems.push({
      key: 'faults',
      label: `故障记录${faultRecords.length > 0 ? ` (${faultRecords.length})` : ''}`,
      children: faultRecords.length > 0 ? (
        <Timeline
          items={faultRecords.map((r) => ({
            color: r.severity === 'high' ? 'red' : r.severity === 'medium' ? 'orange' : 'blue',
            children: (
              <div>
                <div style={{ fontWeight: 500 }}>{r.title || r.event}</div>
                <div style={{ fontSize: 12, color: tokens.colorTextSecondary, marginTop: 4 }}>{r.description || r.detail}</div>
                <div style={{ fontSize: 11, color: tokens.colorTextTertiary, marginTop: 4 }}>
                  {r.date} · {r.operator || '未知'}
                </div>
              </div>
            ),
          }))}
        />
      ) : (
        <Empty description="暂无故障记录" style={{ padding: '32px 0' }} />
      ),
    });

    // Equipment replacement records - always show tab
    const replacementRecords = replacement_records || [];
    tabItems.push({
      key: 'replacement',
      label: `设备更换${replacementRecords.length > 0 ? ` (${replacementRecords.length})` : ''}`,
      children: replacementRecords.length > 0 ? (
        <Table
          dataSource={replacementRecords}
          columns={[
            { title: '日期', dataIndex: 'date', key: 'date', width: 120 },
            { title: '旧设备', dataIndex: 'old_equipment', key: 'old_equipment' },
            { title: '新设备', dataIndex: 'new_equipment', key: 'new_equipment' },
            { title: '原因', dataIndex: 'reason', key: 'reason' },
            { title: '操作人', dataIndex: 'operator', key: 'operator', width: 100 },
          ]}
          rowKey={(r, i) => r.id || `${r.date}-${i}`}
          pagination={false}
          scroll={{ y: 180 }}
          size="small"
        />
      ) : (
        <Empty description="暂无设备更换记录" style={{ padding: '32px 0' }} />
      ),
    });

    // Inspection records - always show tab
    const inspectionRecords = inspection_records || [];
    tabItems.push({
      key: 'inspection',
      label: `巡检记录${inspectionRecords.length > 0 ? ` (${inspectionRecords.length})` : ''}`,
      children: inspectionRecords.length > 0 ? (
        <Table
          dataSource={inspectionRecords}
          columns={[
            { title: '巡检日期', dataIndex: 'date', key: 'date', width: 120 },
            { title: '巡检类型', dataIndex: 'type', key: 'type', width: 100 },
            { title: '巡检结果', dataIndex: 'result', key: 'result' },
            { title: '发现问题', dataIndex: 'issues', key: 'issues' },
            { title: '巡检人', dataIndex: 'inspector', key: 'inspector', width: 100 },
          ]}
          rowKey={(r, i) => r.id || `${r.date}-${i}`}
          pagination={false}
          scroll={{ y: 180 }}
          size="small"
        />
      ) : (
        <Empty description="暂无巡检记录" style={{ padding: '32px 0' }} />
      ),
    });

    // Calibration reports with file upload
    tabItems.push({
      key: 'calibration',
      label: '校准报告',
      children: (
        <div>
          {calibration_reports && Array.isArray(calibration_reports) && calibration_reports.length > 0 ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(168px, 1fr))', gap: 16, padding: 4 }}>
              <Image.PreviewGroup>
                {calibration_reports.map((cal, i) => {
                  const isImage = cal.file && cal.file.url && cal.file.url !== '#'
                    && /\.(png|jpe?g|gif|webp|bmp)$/i.test(cal.file.url);
                  return (
                    <div key={cal.id || i} style={{
                      background: tokens.colorBgContainer, borderRadius: tokens.borderRadius,
                      border: `1px solid ${tokens.colorBorderSecondary}`, overflow: 'hidden',
                      transition: 'box-shadow .2s, transform .2s',
                    }}>
                      <div style={{ height: 132, background: tokens.colorFillSecondary, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        {isImage ? (
                          <Image
                            src={cal.file.url}
                            alt={cal.file.name || '校准照片'}
                            style={{ width: '100%', height: 132, objectFit: 'cover' }}
                            fallback="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100' height='100'%3E%3Crect width='100' height='100' fill='%23f0f0f0'/%3E%3C/svg%3E"
                          />
                        ) : (
                          <div style={{ textAlign: 'center', color: tokens.colorTextTertiary }}>
                            <FileTextOutlined style={{ fontSize: 30 }} />
                            <div style={{ fontSize: 12, marginTop: 6 }}>{cal.file?.name || '暂无照片'}</div>
                          </div>
                        )}
                      </div>
                      <div style={{ padding: '8px 10px' }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: tokens.colorTextHeading, marginBottom: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {cal.type || '校准记录'}
                        </div>
                        <div style={{ fontSize: 12, color: tokens.colorTextDescription, lineHeight: 1.7 }}>
                          <div>日期：{cal.date || '—'}</div>
                          <div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>结果：{cal.result || '—'}</div>
                          {cal.valid_until ? <div>有效期至：{cal.valid_until}</div> : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </Image.PreviewGroup>
            </div>
          ) : (
            <Empty description="暂无校准记录" style={{ padding: '20px 0' }} />
          )}
          <Divider style={{ margin: '16px 0' }} />
          <div style={{ textAlign: 'center' }}>
            <Upload
              action="/api/sites/archive/upload-calibration"
              data={{ site_id: archiveData?.id }}
              listType="text"
              accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.doc,.docx,.xls,.xlsx"
              onChange={(info) => {
                if (info.file.status === 'done') {
                  message.success('附件上传成功');
                  if (archiveData?.id) openArchive(archiveData.id);
                } else if (info.file.status === 'error') {
                  message.error('上传失败，请重试');
                }
              }}
            >
              <Button icon={<UploadOutlined />}>上传附件</Button>
            </Upload>
          </div>
        </div>
      ),
    });

    // Reagent inventory tab - 一线主入口：新增 / 编辑 / 删除站点试剂库存
    const reagentColumns = [
      { title: '试剂名称', dataIndex: 'reagent_name', key: 'reagent_name', width: 140 },
      { title: '生产厂家', dataIndex: 'manufacturer', key: 'manufacturer', width: 140, render: (v) => v || '—' },
      {
        title: '更换时间', dataIndex: 'last_replaced_at', key: 'last_replaced_at', width: 130,
        render: (v) => (v ? v.slice(0, 10) : <Text type="secondary">未设置</Text>),
      },
      {
        title: '剩余可用天数', dataIndex: 'remaining_days', key: 'remaining_days', width: 150,
        render: (v) => v == null
          ? <Text type="secondary">未设置</Text>
          : <Text strong style={{ color: v <= 0 ? '#ff4d4f' : v <= 7 ? '#faad14' : '#52c41a' }}>{v} 天</Text>,
      },
      {
        title: '质控状态', dataIndex: 'qc_status', key: 'qc_status', width: 100,
        render: (v) => v === 'pending'
          ? <Tag color="orange">待质控</Tag>
          : v === 'failed'
            ? <Tag color="red">不通过</Tag>
            : <Tag color="green">已通过</Tag>,
      },
      {
        title: '操作', key: 'op', width: 190,
        render: (_, r) => (
          <Space size={0}>
            {(r.qc_status === 'pending' || r.qc_status === 'failed') && (
              <Button size="small" type="link" onClick={() => openQc(r)}>质控</Button>
            )}
            <Button size="small" type="link" onClick={() => openReagentUpd(r)}>编辑</Button>
            <Popconfirm
              title="确认删除该试剂库存？"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteReagent(r)}
            >
              <Button size="small" type="link" danger>删除</Button>
            </Popconfirm>
          </Space>
        ),
      },
    ];
    tabItems.push({
      key: 'reagent',
      label: `试剂库存${reagentInventory.length > 0 ? ` (${reagentInventory.length})` : ''}`,
      children: (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <Text type="secondary">
              <ExperimentOutlined style={{ color: '#1890ff', marginRight: 6 }} />
              可新增 / 编辑 / 删除本站点试剂库存，更换时间与可用天数用于推算剩余可用天数
            </Text>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={openReagentCreate}>新增试剂</Button>
          </div>
          {reagentInventory.length > 0 ? (
            <Table
              dataSource={reagentInventory}
              columns={reagentColumns}
              rowKey={(r) => r.id}
              pagination={false}
              size="small"
              scroll={{ y: 240 }}
            />
          ) : (
            <Empty description="该站点暂无试剂库存信息，点「新增试剂」添加" style={{ padding: '32px 0' }} />
          )}
          <Modal
            title={reagentModalMode === 'create' ? '新增试剂库存' : `编辑试剂 · ${reagentUpdTarget?.reagent_name || ''}`}
            open={reagentUpdOpen}
            onOk={submitReagentUpd}
            onCancel={() => setReagentUpdOpen(false)}
            okText="保存"
            cancelText="取消"
            confirmLoading={reagentSubmitting}
            destroyOnClose
          >
            <Form layout="vertical" style={{ marginTop: 12 }}>
              {reagentModalMode === 'create' ? (
                <Form.Item label="试剂" required>
                  <Select
                    placeholder="选择试剂（来自试剂主数据目录）"
                    value={reagentUpdForm.reagent_id}
                    onChange={(id) => setReagentUpdForm((f) => ({ ...f, reagent_id: id }))}
                    options={reagentMaster.map((m) => ({ value: m.id, label: `${m.name}${m.manufacturer ? '（' + m.manufacturer + '）' : ''}` }))}
                    showSearch
                    optionFilterProp="label"
                    notFoundContent={reagentMaster.length === 0 ? '该站点已包含全部试剂目录' : null}
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              ) : (
                <>
                  <Form.Item label="试剂名称">
                    <Input value={reagentUpdTarget?.reagent_name || ''} disabled />
                  </Form.Item>
                  <Form.Item label="厂家">
                    <Input value={reagentUpdTarget?.manufacturer || '—'} disabled />
                  </Form.Item>
                </>
              )}
              <Form.Item label="更换时间" required={reagentModalMode === 'create'}>
                <DatePicker
                  value={reagentUpdForm.placed_at ? dayjs(reagentUpdForm.placed_at) : null}
                  onChange={(d) => setReagentUpdForm((f) => ({ ...f, placed_at: d ? d.format('YYYY-MM-DD') : null }))}
                  style={{ width: '100%' }}
                />
              </Form.Item>
              <Form.Item label="可用天数（经验估算）" required>
                <InputNumber
                  min={0} max={365} value={reagentUpdForm.expected_duration_days}
                  onChange={(v) => setReagentUpdForm((f) => ({ ...f, expected_duration_days: v }))}
                  addonAfter="天" style={{ width: '100%' }} placeholder="如 30"
                />
              </Form.Item>
              <Form.Item label="当前余量（可选）">
                <InputNumber
                  min={0} step={0.1} value={reagentUpdForm.new_qty}
                  onChange={(v) => setReagentUpdForm((f) => ({ ...f, new_qty: v }))}
                  style={{ width: '100%' }} placeholder="不填则不修改余量"
                />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12 }}>
                保存后系统按「更换时间 + 可用天数」推算剩余使用天数，低于临期阈值（默认 7 天）时高亮提醒。
              </Text>
            </Form>
          </Modal>
          <Modal
            title={`试剂质控 · ${qcTarget?.reagent_name || ''}`}
            open={qcOpen}
            onOk={submitQc}
            onCancel={() => setQcOpen(false)}
            okText="提交质控结果"
            cancelText="取消"
            confirmLoading={qcSubmitting}
            destroyOnClose
          >
            <Text type="secondary" style={{ fontSize: 12 }}>
              更换试剂后须跑标样验证，质控通过才算更换完成；不通过需校准或报修。
            </Text>
            <Form layout="vertical" style={{ marginTop: 12 }}>
              <Form.Item label="标样值（标准浓度）" required>
                <InputNumber
                  step={0.01} value={qcForm.standard_value}
                  onChange={(v) => setQcForm((f) => ({ ...f, standard_value: v }))}
                  style={{ width: '100%' }} placeholder="标样证书浓度值"
                />
              </Form.Item>
              <Form.Item label="实测值（仪器读数）" required>
                <InputNumber
                  step={0.01} value={qcForm.measured_value}
                  onChange={(v) => setQcForm((f) => ({ ...f, measured_value: v }))}
                  style={{ width: '100%' }} placeholder="标样上机实测值"
                />
              </Form.Item>
              <Form.Item label="质控结论" required>
                <Radio.Group
                  value={qcForm.passed}
                  onChange={(e) => setQcForm((f) => ({ ...f, passed: e.target.value }))}
                >
                  <Radio value={true}>通过</Radio>
                  <Radio value={false}>不通过</Radio>
                </Radio.Group>
              </Form.Item>
              {!qcForm.passed && (
                <Form.Item label="处置动作" required>
                  <Select
                    value={qcForm.fail_action}
                    onChange={(v) => setQcForm((f) => ({ ...f, fail_action: v }))}
                    options={[
                      { value: 'calibrate', label: '校准后复测' },
                      { value: 'repair', label: '报修' },
                    ]}
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              )}
              <Form.Item label="备注（选填）">
                <Input
                  value={qcForm.remark}
                  onChange={(e) => setQcForm((f) => ({ ...f, remark: e.target.value }))}
                  placeholder="如标样批号、复测情况"
                />
              </Form.Item>
            </Form>
          </Modal>
        </>
      ),
    });

    return <Tabs items={tabItems} defaultActiveKey="basic" size="small" />;
  };

  // ---- Data import handlers ----
  const fetchDataSources = useCallback(async () => {
    try {
      const data = await api.get('/sites/data-sources');
      setDataSources(Array.isArray(data) ? data : []);
    } catch { setDataSources([]); }
  }, []);

  const handleImportFile = async (file) => {
    setImportLoading(true);
    setImportResult(null);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch('/api/sites/import', {
        method: 'POST',
        headers: { Authorization: `Bearer ${localStorage.getItem('water_ops_token') || ''}` },
        body: formData,
      });
      const data = await res.json();
      setImportResult(data);
      if (data.imported > 0) {
        message.success(`成功导入 ${data.imported} 个站点`);
        fetchSites();
      }
      if (data.failed > 0) {
        message.warning(`${data.failed} 条记录导入失败`);
      }
    } catch (e) {
      message.error('导入失败');
      setImportResult({ error: String(e) });
    } finally {
      setImportLoading(false);
    }
    return false; // prevent auto upload
  };

  const handleAddDataSource = async () => {
    try {
      const values = await dsForm.validateFields();
      setDsLoading(true);
      const result = await api.post('/sites/data-sources', values);
      if (result && !result.error) {
        message.success('数据源已添加');
        setDsModalOpen(false);
        dsForm.resetFields();
        fetchDataSources();
      } else {
        message.error(result?.error || '添加失败');
      }
    } catch { /* validation error */ }
    setDsLoading(false);
  };

  const handleTestDs = async (ds) => {
    setTestingDs(ds.id);
    try {
      const result = await api.post(`/sites/data-sources/${ds.id}/test`, {});
      if (result?.success) {
        message.success(result.message || '连接成功');
      } else {
        message.error(result?.error || '连接失败');
      }
    } catch { message.error('测试失败'); }
    setTestingDs(null);
  };

  const handleDeleteDs = async (id) => {
    const result = await api.delete(`/sites/data-sources/${id}`);
    if (result && !result.error) {
      message.success('数据源已删除');
      fetchDataSources();
    } else {
      message.error('删除失败');
    }
  };

  // ========================================================================
  // Render
  // ========================================================================
  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 24 }}>
      {/* ---- Page Header ---- */}
      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, flexShrink: 0 }}>
        <Title level={4} style={{ margin: 0, color: tokens.colorText }}>站点管理</Title>
        <Button
          icon={<CloudServerOutlined />}
          onClick={() => { setImportModalOpen(true); setImportResult(null); fetchDataSources(); }}
          disabled={!isAdmin}
          title={!isAdmin ? '仅管理员可配置数据接入' : undefined}
          style={{
            background: 'linear-gradient(135deg, #1890ff, #00c9a7)',
            border: 'none', color: '#fff', fontWeight: 500,
          }}
        >
          数据接入
        </Button>
      </div>

      {/* ---- Filter Bar ---- */}
      <div style={{
        padding: '8px 0', borderTop: `1px solid ${tokens.colorBorder}`,
        borderBottom: `1px solid ${tokens.colorBorder}`, flexShrink: 0,
      }}>
        <Row gutter={[12, 12]} align="middle">
          <Col flex="auto">
            <Space wrap size={12}>
              <Input
                placeholder="搜索站点名称 / 编码"
                prefix={<SearchOutlined style={{ color: tokens.colorTextQuaternary }} />}
                allowClear
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                style={{ width: 260 }}
              />

              <Select
                placeholder="站点类型"
                allowClear
                value={typeFilter}
                onChange={setTypeFilter}
                options={typeOptions}
                style={{ width: 140 }}
              />

              <Select
                placeholder="所属区县"
                allowClear
                showSearch
                optionFilterProp="label"
                value={districtFilter}
                onChange={setDistrictFilter}
                options={districtOptions}
                style={{ width: 150 }}
              />

              <Select
                placeholder="负责人"
                allowClear
                showSearch
                optionFilterProp="label"
                value={managerFilter}
                onChange={setManagerFilter}
                options={managerOptions}
                style={{ width: 140 }}
              />
              {(searchText || typeFilter || districtFilter || managerFilter) && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  已筛选 {filteredSites.length} 条结果
                </Text>
              )}
            </Space>
          </Col>

          <Col>
            <Space>
              <Button icon={<ReloadOutlined />} onClick={resetFilters}>
                重置
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={fetchSites}
                loading={loading}
              >
                刷新
              </Button>
            </Space>
          </Col>
        </Row>
      </div>

      {/* ---- Data Table ---- */}
      <Card
        size="small"
        style={{ marginTop: 12, flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }}
        styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 0 } }}
      >
        {fetchError && !loading ? (
          <Empty
            description={fetchError}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Button type="primary" onClick={fetchSites}>
              重新加载
            </Button>
          </Empty>
        ) : (
          <div ref={listWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <Table
              dataSource={filteredSites}
              columns={columns}
              rowKey="id"
              size="small"
              loading={loading}
              pagination={false}
              scroll={{ y: 'calc(100vh - 380px)' }}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      activeFilterCount > 0
                        ? '没有符合条件的站点'
                        : '暂无站点数据'
                    }
                  />
                ),
              }}
            />
          </div>
        )}
      </Card>

      {/* ---- Archive Modal ---- */}
      <Modal
        title={
          <Space>
            <FileSearchOutlined />
            <span>站点档案</span>
            {archiveData?.name && (
              <Tag color="processing" style={tagStyle}>{archiveData.name}</Tag>
            )}
          </Space>
        }
        open={archiveModalOpen}
        onCancel={closeArchive}
        footer={[
          <Button key="export" icon={<DownloadOutlined />} onClick={() => {
            if (!archiveData) return;
            const exportData = {
              站点名称: archiveData.name,
              站点编码: archiveData.code,
              站点类型: stationTypeMap[archiveData.type] || archiveData.type,
              所属区县: archiveData.district,
              详细地址: archiveData.address,
              负责人: archiveData.manager,
              运行状态: archiveData.status,
              经纬度: archiveData.lat && archiveData.lng ? `${archiveData.lat}, ${archiveData.lng}` : '',
              建站日期: archiveData.build_date,
              设备清单: (archiveData.equipment || []).map(e => ({
                设备编码: e.device_code, 设备名称: e.device_name, 设备类型: e.device_type, 状态: e.status
              })),
              故障记录: (archiveData.fault_records || []).map(r => ({
                时间: r.time || r.created_at, 描述: r.description || r.event_type, 状态: r.status
              })),
              更换记录: (archiveData.replacement_records || []).map(r => ({
                时间: r.time || r.created_at, 设备: r.device_name, 描述: r.description
              })),
              巡检记录: (archiveData.inspection_records || []).map(r => ({
                计划: r.plan_name, 频次: r.frequency, 状态: r.status
              })),
            };
            const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `站点档案_${archiveData.name || archiveData.code || 'export'}.json`;
            a.click();
            URL.revokeObjectURL(url);
            message.success('档案已导出');
          }}>
            导出档案
          </Button>,
          <Button key="close" onClick={closeArchive}>
            关闭
          </Button>,
        ]}
        width={880}
        destroyOnClose
      >
        {renderArchiveContent()}
      </Modal>

      {/* ===== Data Import Modal ===== */}
      <Modal
        title={<span><CloudServerOutlined style={{ marginRight: 8, color: '#1890ff' }} />数据接入</span>}
        open={importModalOpen}
        onCancel={() => setImportModalOpen(false)}
        footer={[<Button key="close" onClick={() => setImportModalOpen(false)}>关闭</Button>]}
        width={720}
        destroyOnClose
      >
        <Tabs
          activeKey={importTab}
          onChange={setImportTab}
          items={[
            {
              key: 'file',
              label: <span><UploadOutlined /> 文件导入</span>,
              children: (
                <div style={{ padding: '16px 0' }}>
                  <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 8, background: tokens.colorBgTextHover }}>
                    <Text style={{ fontSize: 13 }}>
                      支持 CSV 格式批量导入站点，必填字段：code（编码）、name（名称）、type（类型）。
                      <a onClick={() => window.open('/api/sites/template')} style={{ marginLeft: 8 }}>下载导入模板</a>
                    </Text>
                  </div>
                  <Upload.Dragger
                    accept=".csv"
                    showUploadList={false}
                    beforeUpload={handleImportFile}
                    disabled={importLoading}
                    style={{ borderRadius: 12 }}
                  >
                    <p style={{ fontSize: 36, color: tokens.colorPrimary, marginBottom: 8 }}><InboxOutlined /></p>
                    <p style={{ fontSize: 15, fontWeight: 500 }}>点击或拖拽 CSV 文件到此区域</p>
                    <p style={{ fontSize: 13, color: tokens.colorTextSecondary }}>支持 .csv 格式，UTF-8 编码</p>
                  </Upload.Dragger>
                  {importLoading && <div style={{ textAlign: 'center', padding: 16 }}><Spin /> <Text style={{ marginLeft: 8 }}>正在导入...</Text></div>}
                  {importResult && !importResult.error && (
                    <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 8, background: importResult.imported > 0 ? 'rgba(82,196,26,0.08)' : 'rgba(250,173,20,0.08)', border: `1px solid ${importResult.imported > 0 ? '#b7eb8f' : '#ffe58f'}` }}>
                      <div style={{ fontWeight: 500, marginBottom: 4 }}>
                        <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 6 }} />
                        导入完成：成功 {importResult.imported} 条，失败 {importResult.failed} 条
                      </div>
                      {importResult.errors?.length > 0 && (
                        <div style={{ fontSize: 12, color: tokens.colorTextSecondary, marginTop: 4 }}>
                          {importResult.errors.map((e, i) => <div key={i}>{e}</div>)}
                        </div>
                      )}
                    </div>
                  )}
                  {importResult?.error && (
                    <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 8, background: 'rgba(255,77,79,0.08)', border: '1px solid #ffa39e' }}>
                      <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: 6 }} />
                      <Text type="danger">{importResult.error}</Text>
                    </div>
                  )}
                </div>
              ),
            },
            {
              key: 'api',
              label: <span><ApiOutlined /> 数据源配置</span>,
              children: (
                <div style={{ padding: '16px 0' }}>
                  <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Text style={{ fontSize: 13, color: tokens.colorTextSecondary }}>配置外部数据源，实现站点数据自动接入</Text>
                    <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => { dsForm.resetFields(); setDsModalOpen(true); }}>
                      添加数据源
                    </Button>
                  </div>
                  {dataSources.length === 0 ? (
                    <Empty description="暂无数据源配置" style={{ padding: '24px 0' }} />
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {dataSources.map((ds) => (
                        <div key={ds.id} style={{
                          padding: '12px 16px', borderRadius: 12,
                          border: `1px solid ${tokens.colorBorder}`,
                          background: tokens.colorBgContainer,
                        }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <div>
                              <Text strong style={{ fontSize: 14 }}>{ds.name}</Text>
                              <Tag color="blue" style={{ borderRadius: 4, marginLeft: 8, fontSize: 11 }}>{ds.protocol || 'HTTP'}</Tag>
                              <Tag color={ds.status === 'active' ? 'green' : 'default'} style={{ borderRadius: 4, fontSize: 11 }}>
                                {ds.status === 'active' ? '运行中' : '未启用'}
                              </Tag>
                            </div>
                            <Space size={4}>
                              <Button type="link" size="small" icon={<ExperimentOutlined />}
                                loading={testingDs === ds.id} onClick={() => handleTestDs(ds)}>测试</Button>
                              <Popconfirm title="确认删除此数据源？" onConfirm={() => handleDeleteDs(ds.id)} okText="删除" cancelText="取消">
                                <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
                              </Popconfirm>
                            </Space>
                          </div>
                          <div style={{ marginTop: 6, fontSize: 12, color: tokens.colorTextSecondary }}>
                            <Text copyable style={{ fontSize: 12 }}>{ds.url}</Text>
                            {ds.last_sync && <Text style={{ marginLeft: 12, fontSize: 12 }}>上次同步: {ds.last_sync}</Text>}
                            {ds.sync_interval && <Text style={{ marginLeft: 12, fontSize: 12 }}>间隔: {ds.sync_interval}分钟</Text>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ),
            },
          ]}
        />
      </Modal>

      {/* ===== Add Data Source Modal ===== */}
      <Modal
        title="添加数据源"
        open={dsModalOpen}
        onOk={handleAddDataSource}
        onCancel={() => { setDsModalOpen(false); dsForm.resetFields(); }}
        confirmLoading={dsLoading}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={dsForm} layout="vertical" style={{ marginTop: 16 }} initialValues={{ source_type: 'api', protocol: 'HTTP', auth_type: 'none', sync_interval: 60 }}>
          <Form.Item name="name" label="数据源名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如: 省水文局数据接口" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="source_type" label="接入类型">
                <Select options={[
                  { value: 'api', label: 'REST API' },
                  { value: 'mqtt', label: 'MQTT' },
                  { value: 'ftp', label: 'FTP/SFTP' },
                  { value: 'database', label: '数据库直连' },
                ]} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="protocol" label="协议">
                <Select options={[
                  { value: 'HTTP', label: 'HTTP/HTTPS' },
                  { value: 'TCP', label: 'TCP' },
                  { value: 'UDP', label: 'UDP' },
                  { value: 'MQTT', label: 'MQTT' },
                ]} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="url" label="接口地址" rules={[{ required: true, message: '请输入URL' }]}>
            <Input placeholder="https://api.example.com/water/data" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="auth_type" label="认证方式">
                <Select options={[
                  { value: 'none', label: '无认证' },
                  { value: 'token', label: 'Token' },
                  { value: 'basic', label: 'Basic Auth' },
                  { value: 'apikey', label: 'API Key' },
                ]} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="sync_interval" label="同步间隔(分钟)">
                <InputNumber min={1} max={1440} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="可选备注信息" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
