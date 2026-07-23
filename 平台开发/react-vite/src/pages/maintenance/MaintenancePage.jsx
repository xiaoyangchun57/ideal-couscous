import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import dayjs from 'dayjs';
import {
  Table, Card, Input, Select, Button, Space, Tag, Tabs,
  Typography, message, Spin, Empty, Form, Modal, Badge, Result,
  Descriptions, Drawer, DatePicker, Row, Col, Checkbox, Progress,
  Popconfirm, Switch, InputNumber, Statistic, Tooltip, Divider, Alert, List,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, PlusOutlined, EyeOutlined,
  EditOutlined, DeleteOutlined, SettingOutlined, FileTextOutlined,
  ScheduleOutlined, CheckCircleOutlined, ClockCircleOutlined,
  CalendarOutlined, ThunderboltOutlined,
  StopOutlined, AlertOutlined, CameraOutlined,
  CheckOutlined, CloseOutlined, EnvironmentOutlined,
  ToolOutlined, ShoppingCartOutlined, ExclamationCircleOutlined,
  DashboardOutlined, CarOutlined, WarningOutlined, StarOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { stationTypeMap, metricMap, alertLevelLabel, alertStatusMap, alertLevelColor, inspectionItemResultMap } from '../../services/constants';

const { Title, Text } = Typography;
const { TextArea } = Input;

// ===== 常量映射 =====
const categoryOptions = [
  '水位观测', '雨量监测', '蒸发监测', '站院环境', '设施设备',
  '安全检查', '发电机', '缆道系统', '断面环境', '墒情监测', '安全防护', '自定义',
];
// 检查项分类映射（兼容英文旧数据）
const itemCategoryMap = {
  'station_env': '站房环境', 'equipment_ops': '设备运维', 'qaqc_calibration': '质控校准', 'log_books': '台账登记',
  'environment': '环境', 'equipment': '设备', 'quality': '质控',
  '站房环境': '站房环境', '设备运维': '设备运维', '质控校准': '质控校准', '台账登记': '台账登记',
  '水位观测': '水位观测', '雨量监测': '雨量监测', '蒸发监测': '蒸发监测',
  '站院环境': '站院环境', '设施设备': '设施设备', '安全检查': '安全检查',
  '发电机': '发电机', '缆道系统': '缆道系统', '断面环境': '断面环境',
  '墒情监测': '墒情监测', '安全防护': '安全防护', '水质监测': '水质监测',
};
const frequencyOptions = [
  { value: 'daily', label: '每日' },
  { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' },
  { value: 'quarterly', label: '每季度' },
  { value: 'semi_annual', label: '每半年' },
  { value: 'annual', label: '每年' },
];
const frequencyLabelMap = { daily: '每日', weekly: '每周', monthly: '每月', quarterly: '每季度', semi_annual: '每半年', annual: '每年' };
// 周期显示映射（计划列表用简写）
const periodLabelMap = { daily: '日检', weekly: '周检', monthly: '月检', quarterly: '季度检', semi_annual: '半年检', annual: '年检', once: '单次' };
const freqLevelMap = { high: '高频', mid: '中频', low: '低频', annual: '年度', daily: '每日', weekly: '每周', monthly: '每月' };
const freqLevelColor = { high: 'red', mid: 'orange', low: 'blue', annual: 'green', daily: 'purple', weekly: 'cyan', monthly: 'geekblue' };
const siteTypeLabelMap = {
  water_quality: '水质自动站', manual_station: '水质手动站', drinking_source: '饮用水源站',
  cross_boundary: '跨界断面站', groundwater: '地下水站',
  station_yard: '站院',
};
const statusColorMap = {
  draft: 'default', submitted: 'blue', active: 'green', rejected: 'red', completed: 'default',
};
const statusLabelMap = {
  draft: '草稿', submitted: '待审核', active: '执行中', rejected: '已驳回', completed: '已完成',
};

// ==================== PlanTab: 巡检计划（重构版） ====================
function PlanTab({ tokens, planTabAction, onPlanTabActionConsumed }) {
  const { user } = useAuth();
  const currentUserId = user?.id;

  // 计划列表高度：动态测量背景框，避免写死 calc 导致溢出
  const [planWrapRef, planH] = useTableAutoHeight({ headerOffset: 40 });

  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [assigneeFilter, setAssigneeFilter] = useState('');
  const [periodFilter, setPeriodFilter] = useState('');
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [planDetail, setPlanDetail] = useState(null);
  // 统计
  const [statsLoading, setStatsLoading] = useState(false);
  // 生成
  const [generating, setGenerating] = useState(false);
  const [remindDays, setRemindDays] = useState(1);
  const [resultModalOpen, setResultModalOpen] = useState(false);
  const [generateResult, setGenerateResult] = useState(null);
  // 审批
  const [approveLoading, setApproveLoading] = useState(null);
  // 手动创建
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [allSites, setAllSites] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  // 备件预申报
  const [partsModalOpen, setPartsModalOpen] = useState(false);
  const [partsInventory, setPartsInventory] = useState([]);
  // 异常上报弹窗（原执行结果弹窗改为仅异常用）
  const [abnormalModalOpen, setAbnormalModalOpen] = useState(false);
  const [currentItem, setCurrentItem] = useState(null);
  // A5 驳回原因弹窗
  const [rejectTarget, setRejectTarget] = useState(null);
  const [rejectReason, setRejectReason] = useState('');
  // 签到
  const [checkingIn, setCheckingIn] = useState(false);
  // 站点告警详情弹窗
  const [siteAlertsModal, setSiteAlertsModal] = useState({ open: false, site: null, alerts: [] });
  const [siteAlertsLoading, setSiteAlertsLoading] = useState(false);
  // 智能生成（预览→确认窗口）
  const [smartModalOpen, setSmartModalOpen] = useState(false);
  const [smartData, setSmartData] = useState(null);
  const [smartLoading, setSmartLoading] = useState(false);
  const [selSites, setSelSites] = useState({});
  const [selItems, setSelItems] = useState({});
  const [smartVehicle, setSmartVehicle] = useState(undefined);
  const [smartName, setSmartName] = useState('');
  const [smartPeriod, setSmartPeriod] = useState('weekly');
  // 收藏与复用
  const [favModalOpen, setFavModalOpen] = useState(false);
  const [favorites, setFavorites] = useState([]);
  const [favLoading, setFavLoading] = useState(false);
  const [favApplying, setFavApplying] = useState(null);


  const loadPlans = useCallback(async () => {
    setLoading(true);
    try {
      const q = new URLSearchParams();
      if (statusFilter) q.set('status', statusFilter);
      if (assigneeFilter) q.set('assignee_id', assigneeFilter);
      if (periodFilter) q.set('period', periodFilter);
      const qs = q.toString();
      const data = await api.get(`/inspection-v2/plans${qs ? '?' + qs : ''}`);
      setPlans(data);
    } catch { message.error('加载计划失败'); }
    setLoading(false);
  }, [statusFilter, assigneeFilter, periodFilter]);

  useEffect(() => {
    loadPlans();
    api.get('/sites').then(s => setAllSites(Array.isArray(s) ? s : [])).catch(() => {});
    api.get('/vehicles').then(v => setVehicles(Array.isArray(v) ? v : [])).catch(() => {});
    api.get('/parts/inventory').then(p => setPartsInventory(Array.isArray(p) ? p : [])).catch(() => {});
  }, [loadPlans]);

  // 跨 tab 动作：如态势看板点击「立即生成」切回计划 tab 后自动唤起智能生成
  useEffect(() => {
    if (planTabAction === 'smart') {
      handleSmartGenerate();
      onPlanTabActionConsumed?.();
    }
  }, [planTabAction, onPlanTabActionConsumed]);

  // 加载站点近30天告警数（已废弃，改用 detail.site_groups 中的 alert_count_30d 字段）
  const loadSiteAlerts = async (plan) => {};

  const handleViewDetail = async (plan) => {
    setSelectedPlan(plan);
    try {
      const data = await api.get(`/inspection-v2/plans/${plan.id}`);
      setPlanDetail(data);
      setDetailOpen(true);
      loadSiteAlerts(data);
    } catch { message.error('加载详情失败'); }
  };

  const handleDelete = async (id) => {
    await api.delete(`/inspection-v2/plans/${id}`);
    message.success('已删除');
    loadPlans();
  };

  const handleSubmitItem = async (planId, itemId, result, actualPhotos) => {
    try {
      const payload = { result };
      if (actualPhotos != null) payload.actual_photos = actualPhotos;
      await api.put(`/inspection-v2/plans/${planId}/items/${itemId}`, payload);
      const data = await api.get(`/inspection-v2/plans/${planId}`);
      setPlanDetail(data);
      loadPlans();
      message.success('已提交');
    } catch { message.error('提交失败'); }
  };

  const handleSmartGenerate = async () => {
    setSmartLoading(true);
    try {
      const data = await api.post('/inspection-v2/plans/smart-preview', { remind_days: remindDays });
      setSmartData(data);
      const sMap = {}; const iMap = {};
      (data.due_sites || []).forEach(s => {
        sMap[s.site_id] = true;
        (s.schedules || []).forEach(sch => { iMap[`${s.site_id}:${sch.schedule_id}`] = true; });
      });
      setSelSites(sMap);
      setSelItems(iMap);
      const v0 = data.available_vehicles && data.available_vehicles[0];
      setSmartVehicle(v0 ? v0.id : undefined);
      const sug = data.suggested && data.suggested[0];
      setSmartPeriod(sug ? sug.period : 'weekly');
      setSmartName(sug ? `${user?.real_name || '我'}·${sug.period_label}-${data.date}` : `智能生成-${data.date}`);
      setSmartModalOpen(true);
    } catch { message.error('智能生成预览失败'); }
    setSmartLoading(false);
  };

  const handleSmartConfirm = async () => {
    if (!smartData) return;
    const siteItems = [];
    (smartData.due_sites || []).forEach(s => {
      if (!selSites[s.site_id]) return;
      const items = (s.schedules || [])
        .filter(sch => selItems[`${s.site_id}:${sch.schedule_id}`])
        .map(sch => ({
          schedule_id: sch.schedule_id, item_name: sch.item_name,
          category: sch.category, frequency: sch.frequency,
        }));
      if (items.length > 0) siteItems.push({ site_id: s.site_id, items });
    });
    if (siteItems.length === 0) { message.warning('请至少选择一个检查项'); return; }
    if (!smartName.trim()) { message.warning('请填写计划名称'); return; }
    setSmartLoading(true);
    try {
      const res = await api.post('/inspection-v2/plans/confirm', {
        plan_name: smartName.trim(),
        assignee: user?.real_name || '',
        assignee_id: currentUserId,
        period: smartPeriod,
        vehicle_id: smartVehicle || null,
        site_items: siteItems,
      });
      message.success(`已生成草稿计划，含 ${res.total_items} 个检查项`);
      setSmartModalOpen(false);
      loadPlans();
    } catch (e) { message.error('生成失败：' + (e?.error || e?.message || '')); }
    setSmartLoading(false);
  };

  // 收藏与复用
  const loadFavorites = useCallback(async () => {
    try {
      const data = await api.get('/inspection-v2/favorites');
      setFavorites(Array.isArray(data) ? data : []);
    } catch { /* ignore */ }
  }, []);
  const handleOpenFavorites = async () => {
    setFavLoading(true);
    await loadFavorites();
    setFavModalOpen(true);
    setFavLoading(false);
  };
  const handleAddFavorite = async (plan) => {
    try {
      await api.post('/inspection-v2/favorites', { plan_id: plan.id, name: plan.plan_name });
      message.success('已收藏到「我的计划」');
      if (favModalOpen) loadFavorites();
    } catch (e) { message.error('收藏失败：' + (e?.error || e?.message || '')); }
  };
  const handleDeleteFavorite = async (fid) => {
    try {
      await api.delete(`/inspection-v2/favorites/${fid}`);
      message.success('已删除收藏');
      loadFavorites();
    } catch { message.error('删除失败'); }
  };
  const handleApplyFavorite = async (fid) => {
    setFavApplying(fid);
    try {
      const res = await api.post(`/inspection-v2/favorites/${fid}/apply`);
      message.success(`已基于收藏生成草稿计划，含 ${res.total_items} 个检查项`);
      setFavModalOpen(false);
      loadPlans();
    } catch (e) { message.error('复用失败：' + (e?.error || e?.message || '')); }
    setFavApplying(null);
  };

  // 审批操作
  const handleApprove = async (id, action) => {
    setApproveLoading(id);
    try {
      if (action === 'submit') {
        await api.post(`/inspection-v2/plans/${id}/submit`);
      } else {
        await api.post(`/inspection-v2/plans/${id}/approve`, { action, approver_id: currentUserId || 1 });
      }
      message.success(action === 'submit' ? '已提交审批' : action === 'approve' ? '已批准' : '已驳回');
      loadPlans();
    } catch (e) { message.error('操作失败：' + (e.message || '')); }
    setApproveLoading(null);
  };

  // 手动创建
  const handleCreatePlan = async () => {
    try {
      const values = await createForm.validateFields();
      await api.post('/inspection-v2/plans/manual', values);
      message.success('计划已创建');
      setCreateOpen(false);
      createForm.resetFields();
      loadPlans();
    } catch (e) {
      if (e.errorFields) return;
      message.error('创建失败：' + (e.message || ''));
    }
  };

  // 检查项操作：直接正常/异常
  const handleItemNormal = async (item) => {
    if (!planDetail) return;
    await handleSubmitItem(planDetail.id, item.id, 'normal', item.actual_photos || 0);
  };

  const handleOpenAbnormalModal = (item) => {
    setCurrentItem(item);
    setAbnormalModalOpen(true);
  };

  const handleSubmitAbnormal = async () => {
    if (!currentItem || !planDetail) return;
    await handleSubmitItem(planDetail.id, currentItem.id, 'abnormal', currentItem.actual_photos || 0);
    setAbnormalModalOpen(false);
    setCurrentItem(null);
  };

  // 备件预申报（修正：调用真实接口 parts-request，并将表单 parts_id 转为后端需要的 part_sku）
  const handlePartsPredeclare = async (values) => {
    try {
      const items = (values.items || []).map(it => {
        const part = (partsInventory || []).find(p => p.id === it.parts_id);
        return { part_sku: part ? (part.part_code || String(part.id)) : String(it.parts_id), quantity: it.quantity };
      }).filter(it => it.part_sku);
      if (items.length === 0) { message.warning('请至少选择一个备件'); return; }
      await api.post(`/inspection-v2/plans/${planDetail.id}/parts-request`, { items, requester_id: currentUserId || 0 });
      message.success('备件已预申报');
      setPartsModalOpen(false);
    } catch (e) { message.error('预申报失败：' + (e?.error || e?.message || '')); }
  };

  // 检查项现场照片上传（A4：满足拍照要求后方可判正常）
  const handleUploadItemPhoto = async (item, file) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('source_type', 'inspection_item');
    fd.append('source_id', String(item.id));
    try {
      const token = (() => { try { return localStorage.getItem('water_ops_token') || ''; } catch { return ''; } })();
      const res = await fetch('/api/upload', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body: fd });
      const j = await res.json();
      if (j && j.url) {
        let urls = [];
        try { urls = item.photo_urls ? JSON.parse(item.photo_urls) : []; } catch { urls = []; }
        urls.push(j.url);
        const act = (item.actual_photos || 0) + 1;
        await api.put(`/inspection-v2/plans/${planDetail.id}/items/${item.id}`, { actual_photos: act, photo_urls: JSON.stringify(urls) });
        const data = await api.get(`/inspection-v2/plans/${planDetail.id}`);
        setPlanDetail(data);
        message.success('照片已上传');
      } else {
        message.error('上传失败：' + (j?.error || '未知错误'));
      }
    } catch { message.error('上传失败'); }
  };

  // 完成执行：已改为「全部检查项提交即自动完成」，无需手动按钮

  // 签到签退
  const handleCheckIn = async () => {
    setCheckingIn(true);
    try {
      await api.post(`/inspection-v2/plans/${planDetail.id}/checkin`, {
        type: 'checkin', time: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      });
      message.success('签到成功');
    } catch { message.error('签到失败'); }
    setCheckingIn(false);
  };
  const handleCheckOut = async () => {
    setCheckingIn(true);
    try {
      await api.post(`/inspection-v2/plans/${planDetail.id}/checkout`, {
        type: 'checkout', time: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      });
      message.success('签退成功');
    } catch { message.error('签退失败'); }
    setCheckingIn(false);
  };

  // ---- 统计 ----
  const myPlans = plans.filter(p => p.assignee_id === currentUserId);
  const pendingPlans = plans.filter(p => p.status === 'active');
  const reviewPlans = plans.filter(p => p.status === 'submitted');

  // ---- 表格列 ----
  const planColumns = [
    { title: '序号', width: 60, render: (_, r, i) => i + 1 },
    { title: '计划名称', dataIndex: 'plan_name', width: 200,
      render: (t, r) => <a onClick={() => handleViewDetail(r)}>{t}</a> },
    { title: '负责人', dataIndex: 'assignee', width: 100 },
    { title: '周期', dataIndex: 'period', width: 80, render: p => <Tag>{periodLabelMap[p] || p}</Tag> },
    { title: '关联车辆', dataIndex: 'vehicle_name', width: 120, render: v => v ? <Tag>{v}</Tag> : '-' },
    { title: '站点数', dataIndex: 'site_count', width: 70, align: 'center' },
    { title: '状态', dataIndex: 'status', width: 80,
      render: s => <Tag color={statusColorMap[s] || 'default'}>{statusLabelMap[s] || s}</Tag> },
    { title: '完成率', dataIndex: 'completion_rate', width: 130,
      render: v => <Progress percent={v || 0} size="small" strokeColor={v >= 100 ? '#52c41a' : v > 50 ? '#faad14' : '#1890ff'} /> },
    { title: '创建时间', dataIndex: 'generate_date', width: 110 },
    { title: '操作', width: 300, render: (_, r) => {
      const btnProps = { size: 'small', loading: approveLoading === r.id };
      return (
        <Space size="small" wrap>
          {r.status === 'draft' && (
            <>
              <Button type="primary" size="small" ghost {...btnProps}
                onClick={() => handleApprove(r.id, 'submit')}>提交审批</Button>
              <Button size="small" icon={<EditOutlined />} onClick={() => handleViewDetail(r)}>编辑</Button>
            </>
          )}
          {r.status === 'submitted' && (
            <>
              <Button type="primary" size="small" {...btnProps}
                onClick={() => handleApprove(r.id, 'approve')}>批准</Button>
              <Button size="small" danger {...btnProps}
                onClick={() => { setRejectTarget(r.id); setRejectReason(''); }}>驳回</Button>
              <Button size="small" icon={<EyeOutlined />} onClick={() => handleViewDetail(r)}>查看</Button>
            </>
          )}
          {r.status === 'active' && (
            <Button type="primary" size="small" onClick={() => handleViewDetail(r)}>开始执行</Button>
          )}
          {r.status === 'rejected' && (
            <Button type="primary" size="small" ghost onClick={() => handleViewDetail(r)}>修改后重新提交</Button>
          )}
          {r.status === 'completed' && (
            <Button size="small" icon={<EyeOutlined />} onClick={() => handleViewDetail(r)}>查看</Button>
          )}
          {r.status !== 'completed' && (
            <Popconfirm title="删除此计划？删除后不可恢复" onConfirm={() => handleDelete(r.id)} okText="删除" cancelText="取消">
              <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          )}
          <Button size="small" icon={<StarOutlined />} onClick={() => handleAddFavorite(r)}>收藏</Button>
        </Space>
      );
    }},
  ];

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16, flexShrink: 0 }}>
        <Col span={8}>
          <Card size="small">
            <Statistic title="我的计划"
              value={myPlans.length}
              valueStyle={{ color: '#1890ff', fontSize: 28 }} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title="待执行"
              value={pendingPlans.length}
              valueStyle={{ color: '#fa8c16', fontSize: 28 }} />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title="待审核"
              value={reviewPlans.length}
              valueStyle={{ color: '#52c41a', fontSize: 28 }} />
          </Card>
        </Col>
      </Row>

      {/* 操作栏 */}
      <Card size="small" style={{ marginBottom: 16, flexShrink: 0 }}>
        <Space align="center" wrap>
          <Text strong>操作：</Text>
          <Button type="primary" icon={<PlusOutlined />}
            onClick={() => { createForm.resetFields(); setCreateOpen(true); }} size="small">
            手动创建
          </Button>
          <Button icon={<ThunderboltOutlined />} onClick={handleSmartGenerate} loading={smartLoading} size="small">
            智能生成
          </Button>
          <Button icon={<StarOutlined />} onClick={handleOpenFavorites} size="small">
            从收藏生成
          </Button>
          <Divider type="vertical" style={{ height: 24 }} />
          <Text type="secondary">筛选：</Text>
          <Select value={statusFilter} onChange={v => setStatusFilter(v || '')} style={{ width: 130 }} allowClear placeholder="全部状态">
            {Object.entries(statusLabelMap).map(([key, label]) => {
              const count = plans.filter(p => p.status === key).length;
              return <Option key={key} value={key}>{label} ({count})</Option>;
            })}
          </Select>
          <Select value={assigneeFilter} onChange={v => setAssigneeFilter(v || '')} style={{ width: 130 }} allowClear placeholder="全部负责人">
            {Array.from(new Map(plans.filter(p => p.assignee_id).map(p => [p.assignee_id, p.assignee])).entries())
              .map(([id, name]) => <Option key={id} value={String(id)}>{name || '未知'}</Option>)}
          </Select>
          <Select value={periodFilter} onChange={v => setPeriodFilter(v || '')} style={{ width: 120 }} allowClear placeholder="全部周期">
            {Object.entries(periodLabelMap).map(([key, label]) => {
              const count = plans.filter(p => p.period === key).length;
              if (count === 0 && !plans.some(p => p.period === key)) return null;
              return <Option key={key} value={key}>{label}</Option>;
            })}
          </Select>
          {(statusFilter || assigneeFilter || periodFilter) && (
            <Button size="small" onClick={() => { setStatusFilter(''); setAssigneeFilter(''); setPeriodFilter(''); }}>重置</Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={loadPlans} size="small">刷新</Button>
        </Space>
      </Card>

      {/* 计划列表 */}
      <div ref={planWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Table dataSource={plans} columns={planColumns} rowKey="id" loading={loading} size="small"
          pagination={false}
          scroll={planH ? { y: planH, x: 1250 } : undefined}
          locale={{ emptyText: <Empty description="暂无巡检计划，点击上方按钮创建或生成" /> }} />
      </div>

      {/* ===== 计划详情 Drawer ===== */}
      <Drawer title={planDetail?.plan_name || '计划详情'} width={800} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {planDetail && (
          <div>
            {/* 1. 基本信息 */}
            <Descriptions column={3} size="small" style={{ marginBottom: 16 }} bordered>
              <Descriptions.Item label="名称">{planDetail.plan_name}</Descriptions.Item>
              <Descriptions.Item label="负责人">{planDetail.assignee}</Descriptions.Item>
              <Descriptions.Item label="周期">{periodLabelMap[planDetail.period] || planDetail.period}</Descriptions.Item>
              <Descriptions.Item label="车辆（车牌号）">{planDetail.vehicle_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColorMap[planDetail.status] || 'default'}>{statusLabelMap[planDetail.status] || planDetail.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="完成率">
                <Progress percent={planDetail.completion_rate || 0} size="small" style={{ width: 120 }} />
              </Descriptions.Item>
            </Descriptions>

            <Divider style={{ margin: '12px 0' }} />

            {/* 2. 站点近况 */}
            <Title level={5} style={{ marginBottom: 12 }}>站点近况（近30天告警）</Title>
            <Row gutter={[8, 8]} style={{ marginBottom: 16 }}>
              {planDetail.site_groups?.map(group => {
                const alertCount = group.alert_count_30d || 0;
                return (
                  <Col key={group.site_id}>
                    {alertCount > 0 ? (
                      <Tag color="red" style={{ padding: '4px 8px', fontSize: 13, cursor: 'pointer' }}
                        onClick={async () => {
                          setSiteAlertsModal({ open: true, site: group, alerts: [] });
                          setSiteAlertsLoading(true);
                          try {
                            const alerts = await api.get(`/alerts?site_id=${group.site_id}`);
                            setSiteAlertsModal({ open: true, site: group, alerts: Array.isArray(alerts) ? alerts : [] });
                          } catch { message.warning('加载异常信息失败'); }
                          setSiteAlertsLoading(false);
                        }}>
                        {group.site_name} <span style={{ color: '#ff4d4f', fontWeight: 'bold' }}>{alertCount}</span>
                      </Tag>
                    ) : (
                      <Tag style={{ padding: '4px 8px', fontSize: 13, color: '#666' }}>
                        {group.site_name} 0
                      </Tag>
                    )}
                  </Col>
                );
              })}
            </Row>

            <Divider style={{ margin: '12px 0' }} />

            {/* 3. 检查项清单 */}
            <Title level={5} style={{ marginBottom: 12 }}>检查项清单</Title>
            <Table dataSource={planDetail.site_groups?.flatMap(g =>
              (g.items || []).map(i => ({ ...i, _site_name: g.site_name }))
            ) || []}
              rowKey="id" size="small" pagination={false}
              columns={[
                { title: '检查项名', dataIndex: 'item_name', width: 180 },
                { title: '站点', dataIndex: '_site_name', width: 120,
                  render: n => <Tag>{n}</Tag> },
                { title: '分类', dataIndex: 'category', width: 100,
                  render: t => <Tag>{itemCategoryMap[t] || t || '-'}</Tag> },
                { title: '状态', dataIndex: 'result', width: 100,
                  render: (v, r) => {
                    if (!v) return <Tag color="orange">待执行</Tag>;
                    if (v === 'normal') return <Tag color="green">已正常</Tag>;
                    if (v === 'abnormal' || v === 'anomaly_reported') return <Tag color="red">{inspectionItemResultMap[v] || v}</Tag>;
                    return <Tag>{inspectionItemResultMap[v] || v}</Tag>;
                  } },
                { title: '照片', dataIndex: 'required_photos', width: 80,
                  render: (req, r) => req > 0
                    ? <span><CameraOutlined /> {(r.actual_photos || 0)}/{req}</span>
                    : '-' },
                { title: '操作', width: 220, render: (_, r) => {
                  const photoNeeded = (r.required_photos || 0) > 0 && (r.actual_photos || 0) < (r.required_photos || 0);
                  const canExec = !r.result && planDetail.status === 'active';
                  return (
                  <Space size="small" wrap>
                    {canExec && (
                      <>
                        <Tooltip title={photoNeeded ? `该项需上传 ${r.required_photos} 张照片（已 ${r.actual_photos || 0} 张），未满足不可判正常` : ''}>
                          <span>
                            <Button size="small" type="primary" disabled={photoNeeded}
                              onClick={() => handleItemNormal(r)}>正常</Button>
                          </span>
                        </Tooltip>
                        <Button size="small" danger
                          onClick={() => handleOpenAbnormalModal(r)}>异常</Button>
                      </>
                    )}
                    {((r.required_photos || 0) > 0) && (
                      <Button size="small" icon={<CameraOutlined />}
                        disabled={planDetail.status !== 'active'}
                        onClick={() => {
                          const inp = document.createElement('input');
                          inp.type = 'file'; inp.accept = 'image/*';
                          inp.onchange = (e) => { const f = e.target.files && e.target.files[0]; if (f) handleUploadItemPhoto(r, f); };
                          inp.click();
                        }}>上传{(r.actual_photos || 0)}/{(r.required_photos || 0)}</Button>
                    )}
                    {r.result === 'normal' && (
                      <Tag color="green">已正常</Tag>
                    )}
                    {r.result && r.result !== 'normal' && (
                      <Button size="small" danger icon={<AlertOutlined />}
                        onClick={() => message.info('已上报异常，等待主管审核')}>{inspectionItemResultMap[r.result] || r.result}</Button>
                    )}
                  </Space>
                  );
                }},
              ]} />

            <Divider style={{ margin: '12px 0' }} />

            {/* 4. 备件预申报 */}
            <Title level={5} style={{ marginBottom: 12 }}>备件预申报</Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              根据检查项推导建议备件清单，可提前申报备件需求。
            </Text>
            <Button icon={<ShoppingCartOutlined />} onClick={() => setPartsModalOpen(true)}>
              预申报备件
            </Button>

            <Divider style={{ margin: '12px 0' }} />

            {/* 5. 完成执行已移除：全部检查项提交即自动完成 */}

            {/* 6. 签到打卡 */}
            <Title level={5} style={{ marginBottom: 12 }}>签到打卡</Title>
            <Space>
              <Button icon={<EnvironmentOutlined />} type="primary"
                onClick={handleCheckIn} loading={checkingIn}>
                到达签到（GPS）
              </Button>
              <Button icon={<EnvironmentOutlined />}
                onClick={handleCheckOut} loading={checkingIn}>
                离场签退（GPS）
              </Button>
            </Space>
          </div>
        )}
      </Drawer>

      {/* 智能生成确认窗口 */}
      <Modal
        title="智能生成巡检计划"
        open={smartModalOpen}
        onCancel={() => setSmartModalOpen(false)}
        width={720}
        footer={[
          <Button key="cancel" onClick={() => setSmartModalOpen(false)}>取消</Button>,
          <Button key="ok" type="primary" loading={smartLoading} onClick={handleSmartConfirm}>
            确认生成草稿
          </Button>,
        ]}
      >
        {smartData && (
          <div>
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              message={`已为你（${smartData.user?.name || ''}）筛选出 ${smartData.due_sites?.length || 0} 个待巡检站点，可勾选调整；可用车辆 ${smartData.available_vehicles?.length || 0} 辆`} />
            <div style={{ maxHeight: 'calc(100vh - 380px)', overflowY: 'auto', paddingRight: 4 }}>
              {(smartData.due_sites || []).map(s => (
                <Card key={s.site_id} size="small" style={{ marginBottom: 8 }}
                  title={
                    <Checkbox checked={!!selSites[s.site_id]}
                      onChange={e => setSelSites(prev => ({ ...prev, [s.site_id]: e.target.checked }))}>
                      <span style={{ fontWeight: 600 }}>{s.site_name}</span>
                      {s.overdue && <Tag color="red" style={{ marginLeft: 6 }}>已逾期</Tag>}
                      <span style={{ color: '#999', fontSize: 12, marginLeft: 6 }}>{siteTypeLabelMap[s.type] || s.type}</span>
                    </Checkbox>
                  }>
                  {s.last_plan_at && <div style={{ fontSize: 12, color: '#999' }}>上次生成：{s.last_plan_at}</div>}
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    {(s.schedules || []).map(sch => (
                      <Checkbox key={sch.schedule_id}
                        checked={!!selItems[`${s.site_id}:${sch.schedule_id}`]}
                        onChange={e => setSelItems(prev => ({ ...prev, [`${s.site_id}:${sch.schedule_id}`]: e.target.checked }))}>
                        {sch.item_name}
                        <span style={{ color: '#999', fontSize: 12, marginLeft: 6 }}>{frequencyLabelMap[sch.frequency] || sch.frequency} · 到期{sch.next_due_date}</span>
                      </Checkbox>
                    ))}
                  </Space>
                </Card>
              ))}
            </div>
            <Divider style={{ margin: '12px 0' }} />
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>计划名称</div>
                <Input value={smartName} onChange={e => setSmartName(e.target.value)} style={{ width: 240 }} />
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>周期</div>
                <Select value={smartPeriod} onChange={setSmartPeriod} style={{ width: 140 }} options={frequencyOptions} />
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>分配车辆</div>
                <Select value={smartVehicle} onChange={setSmartVehicle} style={{ width: 180 }} allowClear placeholder="不分配">
                  {(smartData.available_vehicles || []).map(v => (
                    <Option key={v.id} value={v.id}>{v.plate_no}（{v.model}）</Option>
                  ))}
                </Select>
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* 从收藏生成 */}
      <Modal title="我的收藏计划" open={favModalOpen}
        onCancel={() => setFavModalOpen(false)} footer={null} width={560}>
        <Spin spinning={favLoading}>
          {favorites.length === 0 ? (
            <Empty description="暂无收藏，在计划列表中点击「收藏」即可保存常用计划" />
          ) : (
            <List
              dataSource={favorites}
              renderItem={f => (
                <List.Item
                  actions={[
                    <Button key="apply" type="link" loading={favApplying === f.id} onClick={() => handleApplyFavorite(f.id)}>复用</Button>,
                    <Popconfirm key="del" title="删除此收藏？" onConfirm={() => handleDeleteFavorite(f.id)} okText="删除" cancelText="取消">
                      <Button type="link" danger>删除</Button>
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta title={f.name} description={`创建于 ${f.created_at}`} />
                </List.Item>
              )}
            />
          )}
        </Spin>
      </Modal>

      {/* 驳回原因弹窗（A5：驳回必须填原因） */}
      <Modal title="驳回计划" open={rejectTarget != null}
        onCancel={() => setRejectTarget(null)}
        onOk={async () => {
          if (!rejectReason.trim()) { message.warning('请填写驳回原因，便于运维人员修改'); return; }
          setApproveLoading(rejectTarget);
          try {
            await api.post(`/inspection-v2/plans/${rejectTarget}/approve`, { action: 'reject', reason: rejectReason.trim(), approver_id: currentUserId || 1 });
            message.success('已驳回，原因已记录');
            setRejectTarget(null);
            setRejectReason('');
            loadPlans();
          } catch (e) { message.error('操作失败：' + (e?.error || e?.message || '')); }
          setApproveLoading(null);
        }}
        okText="确认驳回" okButtonProps={{ danger: true }} width={420}>
        <Input.TextArea rows={4} value={rejectReason}
          onChange={e => setRejectReason(e.target.value)}
          placeholder="请填写驳回原因（必填），例如：站点选择不全 / 车辆未落实 / 周期不合理" />
      </Modal>

      {/* 手动创建计划 */}
      <Modal title="手动创建巡检计划" open={createOpen} onOk={handleCreatePlan}
        onCancel={() => setCreateOpen(false)} width={520}>
        <Form form={createForm} layout="vertical">
          <Form.Item name="plan_name" label="计划名称" rules={[{ required: true, message: '请输入计划名称' }]}>
            <Input placeholder="如：赣州站周巡检" />
          </Form.Item>
          <Form.Item name="period" label="周期" initialValue="weekly">
            <Select options={frequencyOptions} />
          </Form.Item>
          <Form.Item name="vehicle_id" label="关联车辆">
            <Select allowClear placeholder="选择车辆（可选）">
              {vehicles.map(v => (
                <Option key={v.id} value={v.id}>{v.plate_no} {v.brand || ''}</Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="site_ids" label="选择站点" rules={[{ required: true, message: '请选择至少一个自己负责的站点', type: 'array', min: 1 }]}>
            <Select mode="multiple" placeholder={user?.site_ids ? '选择自己负责的站点' : '选择关联站点'} showSearch optionFilterProp="label">
              {(user?.site_ids ? allSites.filter(s => user.site_ids.includes(s.id)) : allSites).map(s => (
                <Option key={s.id} value={s.id} label={s.name}>
                  {s.name} <Text type="secondary" style={{ fontSize: 11 }}>{siteTypeLabelMap[s.type] || s.type}</Text>
                </Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      {/* 异常上报弹窗 */}
      <Modal title="上报异常" open={abnormalModalOpen}
        onCancel={() => { setAbnormalModalOpen(false); setCurrentItem(null); }}
        onOk={handleSubmitAbnormal}
        okText="确认上报" width={400}>
        {currentItem && (
          <div style={{ padding: '8px 0' }}>
            <Text strong style={{ fontSize: 16, display: 'block', marginBottom: 16 }}>
              {currentItem.item_name}
            </Text>
            <Form layout="vertical">
              <Form.Item label="异常描述">
                <TextArea rows={3} placeholder="请描述异常情况..." />
              </Form.Item>
              <Form.Item label="现场照片">
                <Button icon={<CameraOutlined />}>拍照上传</Button>
                <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>可选，建议拍摄现场情况</Text>
              </Form.Item>
            </Form>
          </div>
        )}
      </Modal>

      {/* 站点告警详情弹窗 */}
      <Modal title={siteAlertsModal.site ? `${siteAlertsModal.site.site_name} · 近期告警` : '告警详情'}
        open={siteAlertsModal.open}
        onCancel={() => setSiteAlertsModal({ open: false, site: null, alerts: [] })}
        footer={null} width={680}>
        {siteAlertsLoading ? (
          <div style={{ textAlign: 'center', padding: 32 }}><Spin /></div>
        ) : siteAlertsModal.alerts.length === 0 ? (
          <Empty description="该站点暂无告警" />
        ) : (
          <List dataSource={siteAlertsModal.alerts.slice(0, 15)}
            renderItem={a => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <Space>
                      <Tag color={alertLevelColor[a.level] || 'gold'}>{alertLevelLabel[a.level] || a.level}</Tag>
                      <Tag color={a.status === 'pending' ? 'default' : a.status === 'acknowledged' ? 'blue' : 'green'}>{alertStatusMap[a.status] || a.status}</Tag>
                      <Text strong>{metricMap[a.metric] || a.metric || '-'}</Text>
                    </Space>
                  }
                  description={
                    <div>
                      <div>{a.message}</div>
                      <Text type="secondary" style={{ fontSize: 12 }}>{a.created_at}</Text>
                    </div>
                  }
                />
              </List.Item>
            )} />
        )}
      </Modal>

      {/* 备件预申报弹窗 */}
      <Modal title="备件预申报" open={partsModalOpen}
        onCancel={() => setPartsModalOpen(false)}
        okText="确认"
        cancelText="取消"
        onOk={() => {
          const formEl = document.getElementById('parts-form');
          if (formEl) formEl.requestSubmit();
        }}
        width={600}>
        <Form id="parts-form" layout="vertical"
          onFinish={handlePartsPredeclare}>
          <Alert message="从库存中选择备件并填写数量" type="info" showIcon style={{ marginBottom: 16 }} />
          <Form.List name="items" initialValue={[{ parts_id: undefined, quantity: 1 }]}>
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Row key={key} gutter={12} align="middle" style={{ marginBottom: 8 }}>
                    <Col flex="auto">
                      <Form.Item {...rest} name={[name, 'parts_id']}
                        rules={[{ required: true, message: '请选择备件' }]} noStyle>
                        <Select placeholder="选择备件" showSearch optionFilterProp="label"
                          style={{ width: '100%' }}>
                          {(Array.isArray(partsInventory) ? partsInventory : []).map(p => (
                            <Option key={p.id} value={p.id} label={`${p.part_code || ''} ${p.part_name || ''}`}>
                              <span>{p.part_code} {p.part_name}</span>
                              <Text type="secondary" style={{ marginLeft: 8 }}>库存 {p.quantity || 0}{p.unit || ''}</Text>
                            </Option>
                          ))}
                        </Select>
                      </Form.Item>
                    </Col>
                    <Col style={{ width: 100 }}>
                      <Form.Item {...rest} name={[name, 'quantity']}
                        rules={[{ required: true, message: '请输入数量' }]}
                        initialValue={1} noStyle>
                        <InputNumber min={1} style={{ width: '100%' }} placeholder="数量" />
                      </Form.Item>
                    </Col>
                    <Col>
                      {fields.length > 1 && (
                        <Button type="link" danger icon={<CloseOutlined />} onClick={() => remove(name)} />
                      )}
                    </Col>
                  </Row>
                ))}
                <Button type="dashed" onClick={() => add({ parts_id: undefined, quantity: 1 })}
                  icon={<PlusOutlined />} block>
                  添加备件
                </Button>
              </>
            )}
          </Form.List>
          <Form.Item name="remark" label="备注说明" style={{ marginTop: 16 }}>
            <Input.TextArea rows={2} placeholder="其他说明（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ==================== TemplateTab: 方案模板管理 ====================
function TemplateTab({ tokens }) {
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [selectedTpl, setSelectedTpl] = useState(null);
  const [itemsDrawerOpen, setItemsDrawerOpen] = useState(false);
  const [tplItems, setTplItems] = useState([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTpl, setEditingTpl] = useState(null);
  const [form] = Form.useForm();

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const params = categoryFilter ? `?category=${encodeURIComponent(categoryFilter)}` : '';
      const data = await api.get(`/inspection-v2/templates${params}`);
      setTemplates(data);
    } catch { message.error('加载模板失败'); }
    setLoading(false);
  }, [categoryFilter]);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const loadItems = async (tid) => {
    try {
      const data = await api.get(`/inspection-v2/templates/${tid}/items`);
      setTplItems(data);
    } catch { message.error('加载检查项失败'); }
  };

  const handleViewItems = (tpl) => {
    setSelectedTpl(tpl);
    setItemsDrawerOpen(true);
    loadItems(tpl.id);
  };

  const handleCreate = () => {
    setEditingTpl(null);
    form.resetFields();
    form.setFieldsValue({ frequency: 'monthly' });
    setModalOpen(true);
  };

  const handleEdit = (tpl) => {
    setEditingTpl(tpl);
    form.setFieldsValue(tpl);
    setModalOpen(true);
  };

  const handleDelete = async (id) => {
    try {
      await api.delete(`/inspection-v2/templates/${id}`);
      message.success('已删除');
      loadTemplates();
    } catch { message.error('删除失败'); }
  };

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields();
      if (editingTpl) {
        await api.put(`/inspection-v2/templates/${editingTpl.id}`, values);
        message.success('已更新');
      } else {
        await api.post('/inspection-v2/templates', values);
        message.success('已创建');
      }
      setModalOpen(false);
      loadTemplates();
    } catch { /* validation error */ }
  };

  const handleAddItem = async () => {
    if (!selectedTpl) return;
    const itemName = `新检查项-${tplItems.length + 1}`;
    try {
      await api.post(`/inspection-v2/templates/${selectedTpl.id}/items`, { item_name: itemName });
      loadItems(selectedTpl.id);
      loadTemplates();
    } catch { message.error('添加失败'); }
  };

  const handleDeleteItem = async (itemId) => {
    try {
      await api.delete(`/inspection-v2/templates/${selectedTpl.id}/items/${itemId}`);
      loadItems(selectedTpl.id);
      loadTemplates();
    } catch { message.error('删除失败'); }
  };

  const handleUpdateItem = async (itemId, changes) => {
    try {
      await api.put(`/inspection-v2/templates/${selectedTpl.id}/items/${itemId}`, changes);
      loadItems(selectedTpl.id);
    } catch { message.error('更新失败'); }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 50 },
    { title: '模板名称', dataIndex: 'template_name', width: 180,
      render: (t, r) => <a onClick={() => handleViewItems(r)}>{t}</a> },
    { title: '分类', dataIndex: 'category', width: 100,
      render: t => <Tag>{itemCategoryMap[t] || t}</Tag> },
    { title: '周期', dataIndex: 'frequency', width: 80,
      render: f => <Tag>{periodLabelMap[f] || f}</Tag> },
    { title: '检查项数', dataIndex: 'item_count', width: 90, align: 'center' },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '操作', width: 160, render: (_, r) => (
      <Space>
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => handleViewItems(r)}>检查项</Button>
        {canWrite && <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />}
        {canWrite && (
          <Popconfirm title="确认删除此模板？" onConfirm={() => handleDelete(r.id)} okText="删除" cancelText="取消">
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        )}
      </Space>
    )},
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Space>
          <Select value={categoryFilter} onChange={setCategoryFilter} style={{ width: 160 }} placeholder="按分类筛选"
            allowClear options={[{ value: '', label: '全部分类' }, ...categoryOptions.map(c => ({ value: c, label: c }))]} />
          <Button icon={<ReloadOutlined />} onClick={loadTemplates}>刷新</Button>
        </Space>
        {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建模板</Button>}
      </div>
      <Table dataSource={templates} columns={columns} rowKey="id" loading={loading} size="small"
        pagination={false}
        scroll={{ y: 'calc(100vh - 380px)' }} />

      {/* 检查项 Drawer */}
      <Drawer title={selectedTpl ? `${selectedTpl.template_name} - 检查项` : '检查项'} width={640}
        open={itemsDrawerOpen} onClose={() => setItemsDrawerOpen(false)}
        extra={canWrite ? <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleAddItem}>添加检查项</Button> : null}>
        <Table dataSource={tplItems} rowKey="id" size="small" pagination={false}
          columns={[
            { title: '排序', dataIndex: 'sort_order', width: 60 },
            { title: '检查项名称', dataIndex: 'item_name',
              render: (t, r) => <Input defaultValue={t} size="small" onBlur={e => {
                if (e.target.value !== t) handleUpdateItem(r.id, { item_name: e.target.value });
              }} /> },
            { title: '分类', dataIndex: 'category', width: 100,
              render: t => <Tag>{itemCategoryMap[t] || t || '-'}</Tag> },
            { title: '频次级别', dataIndex: 'frequency_level', width: 100,
              render: (v, r) => <Select size="small" value={v} style={{ width: 90 }}
                options={Object.entries(freqLevelMap).map(([k, l]) => ({ value: k, label: l }))}
                onChange={val => handleUpdateItem(r.id, { frequency_level: val })} /> },
            { title: '需拍照', dataIndex: 'photo_required', width: 80, align: 'center',
              render: (v, r) => <Switch size="small" checked={!!v} onChange={val => handleUpdateItem(r.id, { photo_required: val ? 1 : 0 })} /> },
            { title: '', width: 50, render: (_, r) => (
              <Popconfirm title="删除？" onConfirm={() => handleDeleteItem(r.id)} okText="删除" cancelText="取消">
                {canWrite && <Button type="link" size="small" danger icon={<DeleteOutlined />} />}
              </Popconfirm>
            )},
          ]} />
      </Drawer>

      {/* 新建/编辑 Modal */}
      <Modal title={editingTpl ? '编辑模板' : '新建模板'} open={modalOpen} onOk={handleModalOk}
        onCancel={() => setModalOpen(false)} okText={editingTpl ? '保存' : '创建'} cancelText="取消" width={520}>
        <Form form={form} layout="vertical">
          <Form.Item name="template_name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
            <Input placeholder="如：水位观测日常方案" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="category" label="分类" rules={[{ required: true, message: '请选择分类' }]}>
                <Select options={categoryOptions.map(c => ({ value: c, label: c }))} placeholder="选择分类" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="frequency" label="频次" rules={[{ required: true }]}>
                <Select options={frequencyOptions} placeholder="选择频次" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="description" label="描述"><TextArea rows={3} placeholder="模板描述" /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ==================== ConfigTab: 巡检配置 ====================
function ConfigTab({ tokens }) {
  const [activeSubTab, setActiveSubTab] = useState('rules');

  const subTabs = [
    { key: 'rules', label: '匹配规则', icon: <SettingOutlined /> },
    { key: 'skip', label: '跳过审核', icon: <StopOutlined /> },
  ];

  return (
    <div>
      <Alert
        message="巡检配置说明"
        description="巡检配置用于定义站点类型与模板的匹配规则。配置后在「一键生成」时会自动为符合条件的站点创建计划。"
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />
      <Tabs activeKey={activeSubTab} onChange={setActiveSubTab} items={subTabs.map(t => ({
        key: t.key, label: <span>{t.icon} {t.label}</span>,
      }))} />
      {activeSubTab === 'rules' && <MatchRulesPanel />}
      {activeSubTab === 'skip' && <SkipAuditPanel />}
    </div>
  );
}

// --- 子面板1：匹配规则 ---
function MatchRulesPanel() {
  const { user } = useAuth();
  const canWrite = user?.role === 'admin';
  const [configs, setConfigs] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [siteTypeFilter, setSiteTypeFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = siteTypeFilter ? `?site_type=${siteTypeFilter}` : '';
      const [cfgs, tpls] = await Promise.all([
        api.get(`/inspection-v2/configs${params}`),
        api.get('/inspection-v2/templates'),
      ]);
      setConfigs(cfgs);
      setTemplates(tpls);
    } catch { message.error('加载配置失败'); }
    setLoading(false);
  }, [siteTypeFilter]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      await api.post('/inspection-v2/configs', values);
      message.success('已创建');
      setModalOpen(false);
      form.resetFields();
      load();
    } catch { /* validation */ }
  };

  const handleDelete = async (id) => {
    await api.delete(`/inspection-v2/configs/${id}`);
    message.success('已删除');
    load();
  };

  const handleToggle = async (id, isActive) => {
    await api.put(`/inspection-v2/configs/${id}`, { is_active: isActive ? 1 : 0 });
    load();
  };

  const columns = [
    { title: '站点类型', dataIndex: 'site_type', width: 120,
      render: t => <Tag color="blue">{siteTypeLabelMap[t] || t}</Tag> },
    { title: '模板名称', dataIndex: 'template_name', width: 200 },
    { title: '模板分类', dataIndex: 'tpl_category', width: 120, render: t => <Tag>{itemCategoryMap[t] || t}</Tag> },
    { title: '模板频次', dataIndex: 'tpl_frequency', width: 100, render: f => frequencyLabelMap[f] || f },
    { title: '检查项数', dataIndex: 'item_count', width: 100, align: 'center' },
    { title: '状态', dataIndex: 'is_active', width: 80,
      render: (v, r) => <Switch size="small" checked={!!v} disabled={!canWrite} onChange={val => handleToggle(r.id, val)} /> },
    { title: '操作', width: 80, render: (_, r) => (
      <Popconfirm title="删除此配置？" onConfirm={() => handleDelete(r.id)}>
        {canWrite ? <Button type="link" size="small" danger icon={<DeleteOutlined />} /> : null}
      </Popconfirm>
    )},
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Space>
          <Text type="secondary">站点类型：</Text>
          <Select value={siteTypeFilter} onChange={setSiteTypeFilter} style={{ width: 160 }} allowClear placeholder="全部类型">
            <Select.Option value="">全部 ({configs.length})</Select.Option>
            {Object.entries(siteTypeLabelMap).map(([key, label]) => {
              const count = configs.filter(c => c.site_type === key).length;
              if (count === 0) return null;
              return <Select.Option key={key} value={key}>{label} ({count})</Select.Option>;
            })}
          </Select>
          {siteTypeFilter && <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>已筛选 {configs.length} 条结果</Text>}
        </Space>
        {canWrite && <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setModalOpen(true); }}>添加规则</Button>}
      </div>
      <Table dataSource={configs} columns={columns} rowKey="id" loading={loading} size="small"
        pagination={false} scroll={{ y: 'calc(100vh - 420px)' }} />

      <Modal title="添加匹配规则" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} okText="添加" cancelText="取消">
        <Form form={form} layout="vertical">
          <Form.Item name="site_type" label="站点类型" rules={[{ required: true }]}>
            <Select options={Object.entries(siteTypeLabelMap).map(([k, v]) => ({ value: k, label: v }))} placeholder="选择站点类型" />
          </Form.Item>
          <Form.Item name="template_id" label="关联模板" rules={[{ required: true }]}>
            <Select options={templates.map(t => ({ value: t.id, label: `${t.template_name} (${frequencyLabelMap[t.frequency] || t.frequency})` }))}
              placeholder="选择方案模板" showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="remark" label="备注"><Input placeholder="可选" /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// --- 子面板2：跳过审核 ---
function SkipAuditPanel() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const data = await api.get('/inspections/skip/history');
        setLogs(data);
      } catch { /* ignore */ }
      setLoading(false);
    })();
  }, []);

  return (
    <Table dataSource={logs} rowKey="id" loading={loading} size="small"
      pagination={false} scroll={{ y: 260 }}
      columns={[
        { title: '站点', dataIndex: 'site_id', width: 80 },
        { title: '检查项', dataIndex: 'check_item' },
        { title: '原因', dataIndex: 'reason', ellipsis: true },
        { title: '跳过次数', dataIndex: 'skip_count', width: 100, align: 'center',
          render: v => <Text type={v >= 3 ? 'danger' : undefined}>{v}</Text> },
        { title: '时间', dataIndex: 'created_at', width: 180 },
      ]} />
  );
}

// ==================== DashboardTab: 巡检态势看板 ====================
function DashboardTab({ tokens, onGeneratePlan }) {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  // 动态高度：两个列表共享同一行高度，分别测量更稳
  const [dueRef, dueH] = useTableAutoHeight({ headerOffset: 40, deps: [] });
  const [conflictRef, conflictH] = useTableAutoHeight({ headerOffset: 40, deps: [] });
  // 驳回弹窗
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectTarget, setRejectTarget] = useState(null); // { plan_id, plan_name, assignee }
  const [rejectReason, setRejectReason] = useState('');
  const [rejecting, setRejecting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api.get('/inspection-v2/dashboard');
      setData(d);
    } catch { message.error('加载态势看板失败'); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const openReject = (plan) => {
    setRejectTarget(plan);
    setRejectReason('');
    setRejectOpen(true);
  };

  const handleReject = async () => {
    if (!rejectReason.trim()) { message.error('请填写驳回原因'); return; }
    if (!rejectTarget) return;
    setRejecting(true);
    try {
      await api.put(`/inspection-v2/plans/${rejectTarget.plan_id}/approve`, {
        action: 'reject',
        reason: rejectReason.trim(),
        approver_id: user?.id || 0,
      });
      message.success(`已驳回计划 #${rejectTarget.plan_id} 并通知负责人${rejectTarget.assignee ? '（' + rejectTarget.assignee + '）' : ''}`);
      setRejectOpen(false);
      load();
    } catch (e) {
      message.error((e?.response?.data?.error) || '驳回失败');
    }
    setRejecting(false);
  };

  // 按已逾期数降序，紧急站点置顶
  const sortedDueSites = useMemo(() => {
    return [...(data?.due_sites || [])].sort((a, b) => (b.overdue || 0) - (a.overdue || 0));
  }, [data?.due_sites]);

  const s = data?.summary || {};

  const dueColumns = [
    { title: '站点', dataIndex: 'site_name', ellipsis: true },
    { title: '排程数', dataIndex: 'schedule_count', width: 80, align: 'center' },
    { title: '已逾期数', dataIndex: 'overdue', width: 90, align: 'center',
      render: v => v > 0 ? <Tag color="red">{v}</Tag> : <span style={{ color: tokens.colorTextSecondary }}>{v}</span> },
  ];
  const vehicleColumns = [
    { title: '车牌号', dataIndex: 'plate_no', width: 110 },
    { title: '占用计划', render: (_, r) => (
        <Space size={4} wrap>
          {r.plans.map(p => (
            <Tag key={p.id} color={p.status === 'active' ? 'green' : 'blue'}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              #{p.id} {p.plan_name}
              <Button type="link" size="small" danger style={{ padding: 0, height: 'auto', fontSize: 11 }}
                onClick={() => openReject({ plan_id: p.id, plan_name: p.plan_name, assignee: p.assignee })}>驳回</Button>
            </Tag>
          ))}
        </Space>
      ) },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Spin spinning={loading} wrapperClassName="dashboard-spin" style={{ height: '100%' }}>
        <Row gutter={[16, 16]} style={{ marginBottom: 16, flexShrink: 0 }}>
          <Col span={12}>
            <Card size="small">
              <Statistic
                title="该检未检"
                value={s.due_total || 0}
                valueStyle={{ color: (s.due_total || 0) > 0 ? '#cf1322' : tokens.colorTextSecondary, fontSize: 28 }}
                prefix={<WarningOutlined />}
              />
            </Card>
          </Col>
          <Col span={12}>
            <Card size="small">
              <Statistic
                title="车辆冲突"
                value={s.vehicle_conflict || 0}
                valueStyle={{ color: (s.vehicle_conflict || 0) > 0 ? '#fa8c16' : tokens.colorTextSecondary, fontSize: 28 }}
                prefix={<CarOutlined />}
              />
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ flex: 1, minHeight: 0 }}>
          <Col span={12} style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, flexShrink: 0 }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}><WarningOutlined style={{ color: '#cf1322', marginRight: 6 }} />该检未检站点</span>
              <Space size={4}>
                <Button size="small" type="primary" icon={<ThunderboltOutlined />} onClick={onGeneratePlan}>立即生成</Button>
                <Button size="small" type="text" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
              </Space>
            </div>
            <Card size="small"
              style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
              styles={{ body: { flex: 1, minHeight: 0, overflow: 'hidden', padding: 0, display: 'flex', flexDirection: 'column' } }}>
              <div ref={dueRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Table dataSource={sortedDueSites} columns={dueColumns} rowKey="site_id"
                  size="small" pagination={false}
                  scroll={dueH ? { y: dueH } : undefined}
                  locale={{ emptyText: <Empty description="无未检站点" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }} />
              </div>
            </Card>
          </Col>
          <Col span={12} style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, flexShrink: 0 }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}><CarOutlined style={{ color: '#fa8c16', marginRight: 6 }} />车辆冲突（同车多计划）</span>
              <Button size="small" type="text" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            </div>
            <Card size="small"
              style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
              styles={{ body: { flex: 1, minHeight: 0, overflow: 'hidden', padding: 0, display: 'flex', flexDirection: 'column' } }}>
              <div ref={conflictRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Table dataSource={data?.vehicle_conflicts || []} columns={vehicleColumns} rowKey="vehicle_id"
                  size="small" pagination={false}
                  scroll={conflictH ? { y: conflictH } : undefined}
                  locale={{ emptyText: (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
                      <div>
                        <div>无车辆冲突</div>
                        <div style={{ fontSize: 12, color: tokens.colorTextSecondary, marginTop: 4 }}>当前可用车辆 {s.vehicle_available ?? 0}/{s.vehicle_total ?? 0}</div>
                      </div>
                    } />
                  ) }} />
              </div>
            </Card>
          </Col>
        </Row>
      </Spin>

      <Modal title={`驳回计划${rejectTarget ? ` #${rejectTarget.plan_id}` : ''}`} open={rejectOpen}
        onOk={handleReject} onCancel={() => setRejectOpen(false)} confirmLoading={rejecting}
        okText="确认驳回并通知负责人" cancelText="取消" okButtonProps={{ danger: true }}>
        <p style={{ color: tokens.colorTextSecondary, marginTop: 0 }}>
          计划「{rejectTarget?.plan_name}」将被驳回回草稿，并通知负责人
          {rejectTarget?.assignee ? `（${rejectTarget.assignee}）` : ''}修改后重新提交。
        </p>
        <Form layout="vertical">
          <Form.Item label="驳回原因" required>
            <TextArea rows={4} value={rejectReason} onChange={e => setRejectReason(e.target.value)}
              placeholder="请说明驳回原因，如：车辆调度冲突，请调整用车时间或拆分计划" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

// ==================== 主页面 ====================
export default function MaintenancePage() {
  const { tokens } = useTheme();
  const [activeTab, setActiveTab] = useState('plans');
  const [planTabAction, setPlanTabAction] = useState(null); // 'smart' 等跨 tab 触发

  const handleDashboardGenerate = () => {
    setPlanTabAction('smart');
    setActiveTab('plans');
  };

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 24 }}>
      <style>{`
        .insp-tabs.ant-tabs { display: flex; flex-direction: column; }
        .insp-tabs .ant-tabs-content-holder { flex: 1; min-height: 0; }
        .insp-tabs .ant-tabs-content { height: 100%; }
        .insp-tabs .ant-tabs-tabpane { height: 100%; }
        .dashboard-spin { display: flex; flex-direction: column; height: 100%; }
        .dashboard-spin .ant-spin-container { flex: 1; min-height: 0; display: flex; flex-direction: column; }
      `}</style>
      <div style={{ marginBottom: 16, flexShrink: 0 }}>
        <Title level={4} style={{ margin: 0, color: tokens.colorText }}>巡检计划</Title>
      </div>
      <Tabs className="insp-tabs" activeKey={activeTab} onChange={setActiveTab}
        style={{ flex: 1, minHeight: 0 }}
        size="small"
        animated={{ inkBar: true, tabPane: false }}
        tabBarStyle={{ marginBottom: 16 }}
        items={[
          {
            key: 'plans',
            label: <span><ScheduleOutlined /> 巡检计划</span>,
            children: <PlanTab tokens={tokens} planTabAction={planTabAction} onPlanTabActionConsumed={() => setPlanTabAction(null)} />,
          },
          {
            key: 'templates',
            label: <span><FileTextOutlined /> 方案模板</span>,
            children: <TemplateTab tokens={tokens} />,
          },
          {
            key: 'dashboard',
            label: <span><DashboardOutlined /> 态势看板</span>,
            children: <DashboardTab tokens={tokens} onGeneratePlan={handleDashboardGenerate} />,
          },
        ]} />
    </div>
  );
}
