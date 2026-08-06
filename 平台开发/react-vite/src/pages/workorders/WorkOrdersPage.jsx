import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams, useLocation, useNavigate } from 'react-router-dom';
import {
  Input, Select, Button, Space, Tag, Badge, Modal, Upload, Spin,
  Typography, Form, Tooltip, Alert,
  Row, Col, Descriptions, Timeline, Drawer, Image, App,
} from 'antd';
import {
  PlusOutlined, SearchOutlined, ReloadOutlined, EyeOutlined,
  EditOutlined, DeleteOutlined, ExclamationCircleOutlined,
  FileTextOutlined, ClockCircleOutlined, ToolOutlined, CheckCircleOutlined,
  InboxOutlined, SwapOutlined, CheckOutlined, AuditOutlined,
  UploadOutlined, DownloadOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import {
  orderStatusMap, orderLevelMap, orderSourceMap, orderStatusBadge, orderLevelBadge,
} from '../../services/constants';
import { statusColors } from '../../theme/tokens';
import { filterInputWidth, filterSelectWidth, filterSmallSelectWidth } from '../../services/pageStyles';
import WorkspacePage, { FilterField, ToolbarMeta, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';

const { Text } = Typography;

const manualSourceOptions = [
  { value: 'manual', label: '管理新建' },
  { value: 'patrol', label: '现场巡查发现' },
  { value: 'superior', label: '上级交办' },
  { value: 'hotline', label: '热线反馈' },
];

export default function WorkOrdersPage() {
  const { tokens, isDark } = useTheme();
  const { modal, message } = App.useApp();  // 使用实例方法，避免Tracking Prevention阻断
  // 语义状态色统一走 statusColors（禁硬编码 hex），半透明背景用 alpha 后缀派生
  const purpleColor = statusColors.purple[isDark ? 'dark' : 'light'];
  const infoColor = statusColors.info[isDark ? 'dark' : 'light'];
  const [form] = Form.useForm();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();

  // xlsx 导出辅助
  const downloadExport = async (url, filename) => {
    try {
      const token = (() => { try { return localStorage.getItem('water_ops_token') || ''; } catch { return ''; } })();
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error('导出失败：' + res.status);
      const blob = await res.blob();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    } catch (e) {
      message.error(e.message || '导出失败');
    }
  };

  // Data state
  const [allOrders, setAllOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  // Filter state - initialize from URL params
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [assigneeFilter, setAssigneeFilter] = useState(searchParams.get('assignee') || '');
  const [levelFilter, setLevelFilter] = useState(searchParams.get('level') || undefined);
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') || undefined);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [editingOrder, setEditingOrder] = useState(null);

  // View drawer state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingOrder, setViewingOrder] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState('');
  const [relatedData, setRelatedData] = useState({ parts: [], recycles: [] });
  const [operationPhotos, setOperationPhotos] = useState([]);

  // Spare part request from work order
  const [partReqOpen, setPartReqOpen] = useState(false);
  const [partReqLoading, setPartReqLoading] = useState(false);
  const [partReqForm] = Form.useForm();
  const [partInventory, setPartInventory] = useState([]);
  const partFulfillmentType = Form.useWatch('fulfillment_type', partReqForm) || 'stock';

  // Device recycle from work order
  const [recycleOpen, setRecycleOpen] = useState(false);
  const [recycleLoading, setRecycleLoading] = useState(false);
  const [recycleForm] = Form.useForm();
  const [devices, setDevices] = useState([]);

  // 拉取工单列表
  const fetchOrders = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const data = await api.getStrict('/workorders');
      const list = Array.isArray(data) ? data : [];
      setAllOrders(list);
      const computedCounts = {
        total: list.length,
        pending: 0,
        accepted: 0,
        generated: 0,
        dispatched: 0,
        in_progress: 0,
        reviewing: 0,
        closed: 0,
      };
      list.forEach(o => { if (computedCounts[o.status] !== undefined) computedCounts[o.status]++; });
      setCounts(computedCounts);
    } catch (error) {
      setLoadError(error.message || '工单列表加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  // Counts state
  const [counts, setCounts] = useState({
    total: 0,
    pending: 0,
    accepted: 0,
    generated: 0,
    dispatched: 0,
    in_progress: 0,
    reviewing: 0,
    closed: 0,
  });
  const [sites, setSites] = useState([]);

  // Fetch sites for dropdown
  useEffect(() => {
    api.get('/sites').then(data => {
      const list = Array.isArray(data) ? data : (data?.sites || []);
      setSites(list);
    }).catch(() => {});
    // Fetch devices for recycle dropdown
    api.get('/devices').then(data => {
      const list = Array.isArray(data) ? data : (data?.devices || []);
      setDevices(list);
    }).catch(() => {});
  }, []);

  // ---- Spare part request from work order ----
  const handlePartReqOpen = useCallback(() => {
    partReqForm.resetFields();
    if (viewingOrder) {
      partReqForm.setFieldsValue({
        site_id: viewingOrder.site_id,
        work_order_no: viewingOrder.order_no,
        fulfillment_type: 'stock',
        quantity: 1,
      });
    }
    api.get('/parts/inventory').then(rows => setPartInventory(Array.isArray(rows) ? rows : [])).catch(() => setPartInventory([]));
    setPartReqOpen(true);
  }, [partReqForm, viewingOrder]);

  const handlePartReqOk = useCallback(async () => {
    try {
      const values = await partReqForm.validateFields();
      setPartReqLoading(true);
      const result = await api.post('/parts/requests', {
        ...values,
        work_order_no: viewingOrder?.order_no || '',
      });
      if (result && !result.error) {
        message.success(`备件申请已提交 (${result.request_no})`);
        setPartReqOpen(false);
        // Refresh related data
        if (viewingOrder?.order_no) {
          const data = await api.get(`/workorders/${viewingOrder.order_no}/related`);
          if (data) setRelatedData({ parts: data.parts || [], recycles: relatedData.recycles });
        }
      } else {
        message.error(result?.error || '提交失败');
      }
    } catch { /* validation error */ }
    setPartReqLoading(false);
  }, [partReqForm, viewingOrder, relatedData.recycles]);

  // ---- Device recycle from work order ----
  const handleRecycleOpen = useCallback(() => {
    recycleForm.resetFields();
    setRecycleOpen(true);
  }, [recycleForm]);

  const handleRecycleOk = useCallback(async () => {
    try {
      const values = await recycleForm.validateFields();
      setRecycleLoading(true);
      const result = await api.post('/device-recycle', {
        ...values,
        work_order_no: viewingOrder?.order_no || '',
      });
      if (result && !result.error) {
        message.success('设备回收已登记');
        setRecycleOpen(false);
        // Refresh related data
        if (viewingOrder?.order_no) {
          const data = await api.get(`/workorders/${viewingOrder.order_no}/related`);
          if (data) setRelatedData({ parts: relatedData.parts, recycles: data.recycles || [] });
        }
      } else {
        message.error(result?.error || '登记失败');
      }
    } catch { /* validation error */ }
    setRecycleLoading(false);
  }, [recycleForm, viewingOrder, relatedData.parts]);

  // Sync filters from URL params when navigating with ?search= or ?status=
  useEffect(() => {
    const urlSearch = searchParams.get('search') || '';
    const urlAssignee = searchParams.get('assignee') || '';
    const urlLevel = searchParams.get('level') || undefined;
    const urlStatus = searchParams.get('status') || undefined;
    setSearch(urlSearch);
    setAssigneeFilter(urlAssignee);
    setLevelFilter(urlLevel);
    setStatusFilter(urlStatus);
    // Refetch data when navigating from other pages with URL params
    fetchOrders();
  }, [location.search, fetchOrders]);

  // Client-side filtering
  const filteredOrders = useMemo(() => {
    let list = allOrders;
    if (search) {
      const q = search.toLowerCase();
      list = list.filter((o) =>
        (o.order_no && o.order_no.toLowerCase().includes(q)) ||
        (o.title && o.title.toLowerCase().includes(q)) ||
        (o.site_name && o.site_name.toLowerCase().includes(q))
      );
    }
    if (levelFilter) {
      list = list.filter((o) => o.level === levelFilter);
    }
    if (assigneeFilter) {
      list = list.filter((o) => o.assignee === assigneeFilter);
    }
    if (statusFilter) {
      list = list.filter((o) => o.status === statusFilter);
    }
    return list;
  }, [allOrders, search, levelFilter, statusFilter, assigneeFilter]);

  const handleSearch = (value) => {
    setSearch(value);
    const next = new URLSearchParams(searchParams);
    if (value) next.set('search', value);
    else next.delete('search');
    setSearchParams(next, { replace: true });
  };

  const handleLevelChange = (value) => {
    setLevelFilter(value);
    const next = new URLSearchParams(searchParams);
    if (value) next.set('level', value);
    else next.delete('level');
    setSearchParams(next, { replace: true });
  };

  const handleStatusChange = (value) => {
    setStatusFilter(value);
    const next = new URLSearchParams(searchParams);
    if (value) next.set('status', value);
    else next.delete('status');
    setSearchParams(next, { replace: true });
  };

  const handleAssigneeClear = () => {
    setAssigneeFilter('');
    const next = new URLSearchParams(searchParams);
    next.delete('assignee');
    setSearchParams(next, { replace: true });
  };

  const handleReset = () => {
    setSearch('');
    setAssigneeFilter('');
    setLevelFilter(undefined);
    setStatusFilter(undefined);
    const next = new URLSearchParams(searchParams);
    ['search', 'assignee', 'level', 'status'].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  };

  const handleCreate = () => {
    setEditingOrder(null);
    form.resetFields();
    setModalOpen(true);
  };

  const handleView = async (record) => {
    setViewOpen(true);
    setViewingOrder(record);
    setViewLoading(true);
    setViewError('');
    setRelatedData({ parts: [], recycles: [] });
    setOperationPhotos([]);
    const orderNo = record.order_no;
    if (orderNo) {
      const [freshResult, relatedResult, photoResult] = await Promise.allSettled([
        api.getStrict('/workorders'),
        api.getStrict(`/workorders/${orderNo}/related`),
        api.getStrict(`/workorders/${orderNo}/photos`),
      ]);
      const errors = [];
      if (freshResult.status === 'fulfilled') {
        const fresh = (Array.isArray(freshResult.value) ? freshResult.value : []).find(o => o.order_no === orderNo);
        setViewingOrder(fresh || record);
      } else errors.push('最新工单信息');
      if (relatedResult.status === 'fulfilled') {
        const relData = relatedResult.value;
        if (relData) {
          setRelatedData({ parts: relData.parts || [], recycles: relData.recycles || [] });
        }
      } else errors.push('关联业务');
      if (photoResult.status === 'fulfilled') {
        const photoData = photoResult.value;
        if (photoData) {
          const allPhotos = [];
          if (photoData.item_progress && Array.isArray(photoData.item_progress)) {
            photoData.item_progress.forEach(item => {
              if (item.photos && Array.isArray(item.photos)) {
                item.photos.forEach(p => {
                  allPhotos.push({
                    url: p.url || p.stored_path || p,
                    name: p.filename || item.item_name || '',
                    time: p.created_at || p.taken_at || '',
                    uploader: p.uploader_name || '',
                  });
                });
              }
            });
          }
          setOperationPhotos(allPhotos);
        }
      } else errors.push('处置影像');
      if (errors.length) setViewError(`${errors.join('、')}加载失败，当前详情可能不完整。`);
    }
    setViewLoading(false);
  };

  // 构建动态Timeline（基于实际timeline_events + 状态回退）
  const buildTimelineItems = useCallback((wo, recycles) => {
    const s = wo?.status || 'pending';
    // 状态流转顺序：受理 → 处置 → 待审核 → 办结（派发环节已取消，人员与站点强关联）
    const STATUS_FLOW = ['pending', 'accepted', 'in_progress', 'reviewing', 'closed'];
    // 找到当前状态在流程中的位置
    const currentIdx = STATUS_FLOW.indexOf(s);
    // 辅助函数：判断步骤颜色——当前步骤蓝色(进行中)，已完成步骤绿色
    const stepColor = (stepIdx) => {
      if (s === 'closed') return 'green';
      return stepIdx === currentIdx ? 'blue' : 'green';
    };
    const items = [
      { color: 'green', children: <div><Text strong>工单创建</Text><div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{(wo && wo.created_at) || '—'}</div></div> },
    ];
    // 只添加实际经过的状态节点，当前步骤显示"进行中"
    if (currentIdx >= 1) items.push({ color: stepColor(1), children: <div><Text strong>受理</Text><div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{currentIdx === 1 ? '处理中...' : '已受理'}</div></div> });
    if (currentIdx >= 2) items.push({ color: stepColor(2), children: <div><Text strong>处置</Text><div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{currentIdx === 2 ? '处置中...' : '已处置'}</div></div> });
    // 设备更换
    if (recycles && recycles.length > 0) {
      recycles.forEach((r) => {
        items.push({
          color: 'blue',
          children: (
            <div key={`recycle-${r.id || Math.random()}`}>
              <Text strong>设备更换</Text>
              <div style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
                {r.device_name} ({r.device_code}) → {r.destination === 'scrap' ? '报废' : r.destination === 'repair' ? '维修' : r.destination === 'replace' ? '更换' : '回收'}
              </div>
              <div style={{ fontSize: 11, color: tokens.colorTextTertiary }}>{r.recycle_date || r.created_at || ''}</div>
            </div>
          ),
        });
      });
    }
    if (currentIdx >= 2) items.push({ color: stepColor(2), children: <div><Text strong>处置完成</Text><div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{currentIdx === 2 ? '处置中...' : '已提交'}</div></div> });
    if (currentIdx >= 3) items.push({ color: stepColor(3), children: <div><Text strong>待审核</Text><div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{currentIdx === 3 ? '等待审核中...' : '审核通过'}</div></div> });
    if (s === 'closed') items.push({ color: 'green', children: <div><Text strong>办结</Text><div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{(wo && wo.resolved_at) || '已完成'}</div></div> });
    return items;
  }, [tokens]);

  const handleDeletePhoto = useCallback(async (url) => {
    if (!viewingOrder) return;
    modal.confirm({
      title: '删除照片',
      icon: <ExclamationCircleOutlined />,
      content: '确认删除此照片？删除后不可恢复。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        const result = await api.post(`/workorders/${viewingOrder.order_no}/photos`, { delete_url: url });
        if (result && result.success) {
          message.success('照片已删除');
          const path = (() => { try { return new URL(url, window.location.origin).pathname; } catch { return url; } })();
          // 同时刷新旧缓存照片区与附件照片区，避免删除后界面残留旧缩略图。
          setViewingOrder(prev => {
            if (!prev) return prev;
            const imgs = typeof prev.images === 'string' ? JSON.parse(prev.images) : (prev.images || []);
            return { ...prev, images: JSON.stringify(imgs.filter(u => u !== url && u !== path)) };
          });
          setOperationPhotos(prev => prev.filter(photo => photo.url !== url && photo.url !== path));
          fetchOrders();
        } else {
          message.error(result?.error || '删除失败');
        }
      },
    });
  }, [viewingOrder, fetchOrders]);

  const handleEdit = (record) => {
    console.log('[DEBUG] handleEdit clicked:', record?.order_no);
    setEditingOrder(record);
    form.setFieldsValue({
      title: record.title,
      level: record.level,
      source: record.source,
      site_id: record.site_name || record.site_id,
      assignee: record.assignee,
      description: record.description,
    });
    setModalOpen(true);
  };

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields();
      setModalLoading(true);
      let result;
      if (editingOrder) {
        result = await api.put(`/workorders/${editingOrder.order_no}/status`, {
          ...values,
          status: values.status || editingOrder.status,
        });
      } else {
        result = await api.post('/workorders', values);
      }
      if (result && !result.error) {
        message.success(editingOrder ? '工单已更新' : '工单已创建');
        setModalOpen(false);
        setEditingOrder(null);
        fetchOrders();
      } else {
        message.error(result?.error || (editingOrder ? '更新失败' : '创建失败'));
      }
    } catch {
      // validation error, do nothing
    } finally {
      setModalLoading(false);
    }
  };

  const handleDelete = (record) => {
    modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: `确认删除工单 ${record.order_no || record.id}？此操作不可撤销。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        const result = await api.delete(`/workorders/${record.order_no}`);
        if (result && !result.error) {
          message.success('工单已删除');
          fetchOrders();
        } else {
          message.error('删除失败');
        }
      },
    });
  };

  // Generic status transition handler (uses PUT /status)
  const handleStatusTransition = useCallback(async (record, newStatus, label) => {
    let resolutionNote = '';
    try {
    modal.confirm({
      title: label,
      icon: <ExclamationCircleOutlined />,
      content: newStatus === 'reviewing' ? <div>
        <Text>提交后审核员将根据现场处置说明和影像判断是否办结。</Text>
        <Input.TextArea autoSize={{ minRows: 3, maxRows: 5 }} maxLength={500} showCount
          placeholder="请填写做了什么、现场结果和仍需关注的事项"
          onChange={(event) => { resolutionNote = event.target.value; }} style={{ marginTop: 12 }} />
      </div> : `确认将工单 ${record.order_no} ${label}？`,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        if (newStatus === 'reviewing' && !resolutionNote.trim()) {
          message.error('请填写现场处置说明');
          return Promise.reject(new Error('resolution note required'));
        }
        const result = newStatus === 'reviewing'
          ? await api.postStrict(`/workorders/${record.order_no}/submit-review`, { client: 'web', resolution_note: resolutionNote.trim() })
          : await api.put(`/workorders/${record.order_no}/status`, { status: newStatus });
        if (result && !result.error) {
          message.success(`工单已${label}`);
          fetchOrders();
        } else {
          message.error(result?.error || '操作失败');
        }
      },
    });
  } catch { /* 状态流转异常 */ }
}, [fetchOrders, message, modal]);

  const columns = [
    {
      title: '工单号',
      dataIndex: 'order_no',
      key: 'order_no',
      width: 120,
      render: (text, record) => (
        <Text strong style={{ color: tokens.colorPrimary, fontSize: 13 }}>
          {text || `#${record.id}`}
        </Text>
      ),
    },
    {
      title: '站点',
      dataIndex: 'site_name',
      key: 'site_name',
      width: 100,
      ellipsis: true,
      render: (text) => <span title={text}>{text || '-'}</span>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 70,
      render: (val) => orderSourceMap[val] || val || '-',
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 70,
      render: (val) => {
        const label = orderLevelMap[val] || val || '-';
        const color = orderLevelBadge[val] || 'default';
        return val ? <Tag color={color} style={{ borderRadius: 4, fontSize: 11 }}>{label}</Tag> : '-';
      },
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      width: 160,
      ellipsis: true,
      render: (text) => <span title={text}>{text || '-'}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (val) => {
        const label = orderStatusMap[val] || val || '-';
        const badge = orderStatusBadge[val] || 'default';
        return val ? <Badge status={badge} text={label} /> : '-';
      },
    },
    {
      title: '负责人',
      dataIndex: 'assignee',
      key: 'assignee',
      width: 70,
      ellipsis: true,
      render: (text) => <span title={text}>{text || '-'}</span>,
    },
    {
      title: 'SLA',
      dataIndex: 'sla_deadline',
      key: 'sla_deadline',
      width: 100,
      render: (val, record) => {
        if (!val) return '-';
        const hours = { normal: 72, urgent: 24, critical: 2 };
        if (record.status === 'closed') {
          const completedAt = record.resolved_at || record.closed_at;
          if (!completedAt) {
            return <Text type="secondary">已完成 · 完成时间未记录</Text>;
          }
          const completedLate = new Date(completedAt) > new Date(val);
          return (
            <Tooltip title={`SLA 截止：${val}；完成时间：${completedAt}`}>
              <Text style={{ color: completedLate ? tokens.colorError : tokens.colorSuccess, fontSize: 13 }}>
                {completedLate ? '超时完成' : '按时完成'}
              </Text>
            </Tooltip>
          );
        }
        const isOverdue = new Date(val) < new Date();
        return (
          <Tooltip title={`按工单级别自动计算：一般 72 小时、紧急 24 小时、重大 2 小时。当前截止时间：${val}`}>
            <Text style={{ color: isOverdue ? tokens.colorError : tokens.colorTextSecondary, fontSize: 13 }}>
              {isOverdue ? '已超时' : `${val}（${hours[record.level] || 72}小时）`}
            </Text>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, record) => {
        const s = record.status;
        let primaryAction = null;
        let returnAction = null;

        // Status transition actions
        if (s === 'pending') {
          primaryAction = {
            key: 'accept',
            label: '受理',
            icon: <CheckOutlined />,
            onClick: () => handleStatusTransition(record, 'accepted', '受理'),
          };
        }
        if (s === 'accepted') {
          primaryAction = {
            key: 'start',
            label: '开始处置',
            icon: <ToolOutlined />,
            onClick: () => handleStatusTransition(record, 'in_progress', '开始处置'),
          };
        }
        if (s === 'dispatched') {
          primaryAction = {
            key: 'start',
            label: '开始处置',
            icon: <ToolOutlined />,
            onClick: () => handleStatusTransition(record, 'in_progress', '开始处置'),
          };
        }
        if (s === 'in_progress') {
          primaryAction = {
            key: 'complete',
            label: '提交审核',
            icon: <CheckOutlined />,
            onClick: () => handleStatusTransition(record, 'reviewing', '提交审核'),
          };
          returnAction = {
            key: 'return',
            label: '退回受理',
            icon: <SwapOutlined />,
            onClick: () => handleStatusTransition(record, 'accepted', '退回受理'),
          };
        }
        if (s === 'reviewing') {
          primaryAction = {
            key: 'review',
            label: '前往审核',
            icon: <AuditOutlined />,
            onClick: () => navigate(`/audit?tab=workorder&order=${encodeURIComponent(record.order_no)}`),
          };
        }

        return (
          <Space size={4}>
            <Tooltip title="查看工单详情">
              <Button type="text" size="small" icon={<EyeOutlined />} aria-label={`查看工单 ${record.order_no} 详情`}
                onClick={() => handleView(record)} />
            </Tooltip>
            {primaryAction && (
              <Button size="small" type="primary" icon={primaryAction.icon}
                aria-label={`${record.order_no} ${primaryAction.label}`}
                onClick={primaryAction.onClick}>
                {primaryAction.label}
              </Button>
            )}
            {returnAction && (
              <Tooltip title={returnAction.label}>
                <Button type="text" size="small" icon={returnAction.icon} aria-label={`${record.order_no} ${returnAction.label}`}
                  onClick={returnAction.onClick} />
              </Tooltip>
            )}
            {s !== 'closed' && s !== 'reviewing' && (
              <Tooltip title="编辑工单">
                <Button type="text" size="small" icon={<EditOutlined />} aria-label={`编辑工单 ${record.order_no}`}
                  onClick={() => handleEdit(record)} />
              </Tooltip>
            )}
            {s !== 'closed' && s !== 'reviewing' && (
              <Tooltip title="删除工单">
                <Button type="text" danger size="small" icon={<DeleteOutlined />} aria-label={`删除工单 ${record.order_no}`}
                  onClick={() => handleDelete(record)} />
              </Tooltip>
            )}
          </Space>
        );
      },
    },
  ];

  const statusOptions = Object.entries(orderStatusMap).map(([value, label]) => ({ value, label }));
  const levelOptions = [
    { value: 'normal', label: '一般' },
    { value: 'urgent', label: '紧急' },
    { value: 'critical', label: '重大' },
  ];
  return (
    <WorkspacePage
      title="工单"
      subtitle="查看并推进当前运维事项；待审核工单统一进入审核工作台核验。"
      secondaryAction={<Button icon={<DownloadOutlined />} onClick={() => downloadExport('/api/export/work-orders?period=month', '工单明细_本月.xlsx')}>导出</Button>}
      primaryAction={<Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建工单</Button>}
      statusItems={[
        { label: '待受理', value: counts.pending, color: tokens.colorWarning },
        { label: '已受理', value: counts.accepted, color: tokens.colorPrimary },
        { label: '已生成', value: counts.generated, color: tokens.colorTextSecondary },
        { label: '已派发', value: counts.dispatched, color: tokens.colorPrimary },
        { label: '处置中', value: counts.in_progress, color: tokens.colorPrimary },
        { label: '待审核', value: counts.reviewing, color: purpleColor },
        { label: '已完成', value: counts.closed, color: tokens.colorSuccess },
      ]}
      toolbar={(
        <WorkspaceToolbar actions={<><Button icon={<SearchOutlined />} onClick={() => handleSearch(search)}>查询</Button><Button icon={<ReloadOutlined />} onClick={handleReset}>重置</Button></>}>
          <FilterField label="工单搜索"><Input aria-label="工单搜索" placeholder="搜索工单号、标题、站点..." prefix={<SearchOutlined />} allowClear value={search}
            onChange={(e) => setSearch(e.target.value)} onPressEnter={(e) => handleSearch(e.target.value)} style={{ width: filterInputWidth }} /></FilterField>
          <FilterField label="级别"><Select aria-label="级别" placeholder="级别" allowClear value={levelFilter} onChange={handleLevelChange} style={{ width: filterSmallSelectWidth }} options={levelOptions} /></FilterField>
          <FilterField label="状态"><Select aria-label="状态" placeholder="状态" allowClear value={statusFilter} onChange={handleStatusChange} style={{ width: filterSelectWidth }} options={statusOptions} /></FilterField>
          {assigneeFilter && <ToolbarMeta label="负责人"><Tag closable onClose={handleAssigneeClear}>{assigneeFilter}</Tag></ToolbarMeta>}
          {(search || assigneeFilter || levelFilter || statusFilter) && <ToolbarMeta label="当前结果">已筛选 {filteredOrders.length} 条</ToolbarMeta>}
        </WorkspaceToolbar>
      )}
    >
      {/* Table */}
      {loadError && !loading
        ? <WorkspaceEmpty type="error" description="工单列表加载失败，当前不能判断是否没有工单。" onRefresh={fetchOrders} />
        : <WorkspaceTable columns={columns} dataSource={filteredOrders} rowKey={(r) => r.order_no || r.id}
            loading={loading} emptyType={(search || assigneeFilter || levelFilter || statusFilter) ? 'filtered' : 'empty'} onRefresh={fetchOrders} />}

      {/* Create/Edit Modal */}
      <Modal
        title={editingOrder ? '编辑工单' : '新建工单'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => { setModalOpen(false); setEditingOrder(null); }}
        confirmLoading={modalLoading}
        okText={editingOrder ? '保存' : '创建'}
        cancelText="取消"
        width={560}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入工单标题' }]}>
            <Input placeholder="请输入工单标题" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="level" label="级别" rules={[{ required: true, message: '请选择级别' }]}>
                <Select placeholder="请选择级别" options={levelOptions} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="source" label="来源" rules={[{ required: true, message: '请选择来源' }]}>
                <Select
                  placeholder="请选择来源"
                  options={manualSourceOptions}
                />
              </Form.Item>
            </Col>
          </Row>
          {editingOrder && (
            <Form.Item name="status" label="状态">
              <Select placeholder="请选择状态" options={statusOptions} />
            </Form.Item>
          )}
          <Form.Item name="site_id" label="站点">
            <Select placeholder="请选择站点" allowClear showSearch
              filterOption={(input, option) => (option.label || '').toLowerCase().includes(input.toLowerCase())}
              options={sites.map(s => ({ value: s.id, label: `${s.name || s.code} (${s.code || s.id})` }))} />
          </Form.Item>
          <Form.Item name="assignee" label="负责人">
            <Input placeholder="负责人姓名" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="工单详细描述" />
          </Form.Item>
        </Form>
      </Modal>
      {/* View Drawer */}
      <Drawer
        title={
          <Space>
            <FileTextOutlined />
            <span>工单详情</span>
            {viewingOrder && <Tag color={orderLevelBadge[viewingOrder.level] || 'default'} style={{ borderRadius: 4, fontSize: 11 }}>{orderLevelMap[viewingOrder.level] || viewingOrder.level}</Tag>}
          </Space>
        }
        open={viewOpen}
        onClose={() => { setViewOpen(false); setViewingOrder(null); }}
        width={520}
      >
        {viewingOrder && (
          <Spin spinning={viewLoading} tip="正在加载最新工单信息">
          <div>
            {viewError && <Alert type="warning" showIcon message={viewError} style={{ marginBottom: 12 }} />}
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="工单号">
                <Text strong style={{ color: tokens.colorPrimary }}>{viewingOrder.order_no || `#${viewingOrder.id}`}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="标题">{viewingOrder.title || '-'}</Descriptions.Item>
              <Descriptions.Item label="站点">{viewingOrder.site_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="来源">{orderSourceMap[viewingOrder.source] || viewingOrder.source || '-'}</Descriptions.Item>
              <Descriptions.Item label="级别">
                {viewingOrder.level ? <Tag color={orderLevelBadge[viewingOrder.level] || 'default'} style={{ borderRadius: 4, fontSize: 11 }}>{orderLevelMap[viewingOrder.level] || viewingOrder.level}</Tag> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {viewingOrder.status ? <Badge status={orderStatusBadge[viewingOrder.status] || 'default'} text={orderStatusMap[viewingOrder.status] || viewingOrder.status} /> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="负责人">{viewingOrder.assignee || '-'}</Descriptions.Item>
              <Descriptions.Item label={viewingOrder.status === 'closed' ? 'SLA结果' : 'SLA截止'}>
                {viewingOrder.sla_deadline ? viewingOrder.status === 'closed' ? (() => {
                  const completedAt = viewingOrder.resolved_at || viewingOrder.closed_at;
                  if (!completedAt) return <Text type="secondary">已完成 · 完成时间未记录</Text>;
                  const completedLate = new Date(completedAt) > new Date(viewingOrder.sla_deadline);
                  return <Text style={{ color: completedLate ? tokens.colorError : tokens.colorSuccess }}>
                    {completedLate ? '超时完成' : '按时完成'} · 截止 {viewingOrder.sla_deadline} · 完成 {completedAt}
                  </Text>;
                })() : (
                  <Text style={{ color: new Date(viewingOrder.sla_deadline) < new Date() ? tokens.colorError : tokens.colorText }}>
                    {new Date(viewingOrder.sla_deadline) < new Date() ? '已超时 · ' : ''}{viewingOrder.sla_deadline}
                  </Text>
                ) : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">{viewingOrder.created_at || '-'}</Descriptions.Item>
              <Descriptions.Item label="描述">{viewingOrder.description || '暂无描述'}</Descriptions.Item>
              {/* 现场照片 */}
              {(() => {
                try {
                  const imgs = typeof viewingOrder.images === 'string' ? JSON.parse(viewingOrder.images) : (viewingOrder.images || []);
                  if (!Array.isArray(imgs) || imgs.length === 0) return null;
                  return (
                    <Descriptions.Item label="现场照片">
                      <Image.PreviewGroup>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          {imgs.map((url, i) => (
                            <div key={i} style={{ position: 'relative', display: 'inline-block' }}>
                              <Image
                                src={url}
                                alt={`照片${i + 1}`}
                                width={80}
                                height={80}
                                style={{ objectFit: 'cover', borderRadius: 6, border: '1px solid var(--border-color)' }}
                                preview={{ mask: '预览' }}
                              />
                              {!['reviewing', 'closed'].includes(viewingOrder.status) && <button
                                type="button"
                                aria-label={`删除照片 ${i + 1}`}
                                onClick={() => handleDeletePhoto(url)}
                                style={{
                                  position: 'absolute', top: -6, right: -6,
                                  width: 32, height: 32, borderRadius: '50%', border: 0, padding: 0,
                                  background: tokens.colorError, color: '#fff',
                                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  cursor: 'pointer', fontSize: 11, lineHeight: '18px',
                                  boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
                                }}
                                title="删除照片"
                              >✕</button>}
                            </div>
                          ))}
                        </div>
                      </Image.PreviewGroup>
                    </Descriptions.Item>
                  );
                } catch { return null; }
              })()}
              {/* 备件使用 - 融合在现有区块中 */}
              {relatedData.parts.length > 0 && (
                <Descriptions.Item label="备件使用">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {relatedData.parts.map((p, i) => (
                      <div key={p.id || i} style={{ fontSize: 12 }}>
                        <Tag color="blue" style={{ borderRadius: 4, fontSize: 11, marginRight: 4 }}>{p.request_no || `#${p.id}`}</Tag>
                        <Text>{p.part_name}</Text>
                        <Text type="secondary" style={{ marginLeft: 8 }}>×{p.quantity}</Text>
                        <Tag color={p.status === 'approved' ? 'green' : p.status === 'rejected' ? 'red' : 'orange'} style={{ borderRadius: 4, fontSize: 11, marginLeft: 4 }}>
                          {p.status === 'approved' ? '已批准' : p.status === 'rejected' ? '已驳回' : '待审批'}
                        </Tag>
                      </div>
                    ))}
                  </div>
                </Descriptions.Item>
              )}
              {/* 设备更换 - 融合在现有区块中 */}
              {relatedData.recycles.length > 0 && (
                <Descriptions.Item label="设备更换">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {relatedData.recycles.map((r, i) => (
                      <div key={r.id || i} style={{ fontSize: 12 }}>
                        <Tag color="purple" style={{ borderRadius: 4, fontSize: 11, marginRight: 4 }}>{r.device_code || `#${r.id}`}</Tag>
                        <Text>{r.device_name}</Text>
                        <Tag color={r.destination === 'scrap' ? 'red' : 'blue'} style={{ borderRadius: 4, fontSize: 11, marginLeft: 4 }}>
                          {r.destination === 'scrap' ? '报废' : r.destination === 'repair' ? '维修' : r.destination === 'replace' ? '更换' : r.destination || '回收'}
                        </Tag>
                      </div>
                    ))}
                  </div>
                </Descriptions.Item>
              )}
            </Descriptions>

            <div style={{ marginTop: 24 }}>
              <Text strong style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 16 }}>
                <ClockCircleOutlined /> 处理流程
              </Text>
              <Timeline items={buildTimelineItems(viewingOrder, relatedData.recycles)} />

            {/* 审核状态提示（只读，审核操作在待办审核页面） */}
            {viewingOrder.status === 'reviewing' && (
              <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 8, background: `${purpleColor}0F`, border: `1px solid ${purpleColor}26` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <AuditOutlined style={{ color: purpleColor, fontSize: 16 }} />
                  <div>
                    <Text strong style={{ fontSize: 13, color: purpleColor }}>待审核</Text>
                    <div style={{ fontSize: 12, color: tokens.colorTextSecondary, marginTop: 2 }}>
                      处置已提交，请在「统一审核」中进行审核操作
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* 处理过程照片（来自操作附件） */}
            {operationPhotos.length > 0 && (
              <div style={{ marginTop: 20 }}>
                <Text strong style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
                  <CheckCircleOutlined /> 处置照片
                  <Text type="secondary" style={{ fontSize: 12, fontWeight: 'normal' }}>共 {operationPhotos.length} 张</Text>
                </Text>
                <Image.PreviewGroup>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {operationPhotos.map((photo, i) => (
                      <div key={i} style={{ position: 'relative' }}>
                        <Image
                          src={photo.url}
                          alt={photo.name || `处置照片${i + 1}`}
                          width={80}
                          height={80}
                          style={{ objectFit: 'cover', borderRadius: 6, border: '1px solid var(--border-color)' }}
                          preview={{ mask: '预览' }}
                        />
                        {photo.uploader && (
                          <div style={{ fontSize: 10, color: tokens.colorTextTertiary, marginTop: 2, textAlign: 'center', maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {photo.uploader}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </Image.PreviewGroup>
              </div>
            )}

            {/* 独立上传处置照片 */}
            {viewingOrder && ['pending', 'accepted', 'dispatched', 'in_progress'].includes(viewingOrder.status) && (
              <div style={{ marginTop: 20, padding: '12px', borderRadius: 8, background: tokens.colorFillSecondary }}>
                <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>
                  <UploadOutlined /> 上传处置照片
                </Text>
                <Upload
                  listType="picture-card"
                  customRequest={async ({ file, onSuccess, onError }) => {
                    try {
                      const fd = new FormData();
                      fd.append('file', file);
                      const res = await fetch(`/api/workorders/${viewingOrder.order_no}/photos`, {
                        method: 'POST',
                        headers: { Authorization: `Bearer ${localStorage.getItem('water_ops_token') || ''}` },
                        body: fd,
                      });
                      const data = await res.json();
                      if (data && data.success) {
                        onSuccess && onSuccess(data);
                        // 刷新本工单照片
                        const photoData = await api.get(`/workorders/${viewingOrder.order_no}/photos`);
                        if (photoData) {
                          const allPhotos = [];
                          if (photoData.item_progress && Array.isArray(photoData.item_progress)) {
                            photoData.item_progress.forEach(item => {
                              if (item.photos && Array.isArray(item.photos)) {
                                item.photos.forEach(p => {
                                  allPhotos.push({
                                    url: p.url || p.stored_path || p,
                                    name: p.filename || item.item_name || '',
                                    time: p.created_at || p.taken_at || '',
                                    uploader: p.uploader_name || '',
                                  });
                                });
                              }
                            });
                          }
                          setOperationPhotos(allPhotos);
                        }
                        fetchOrders();
                      } else {
                        onError && onError(new Error(data?.error || '上传失败'));
                      }
                    } catch (e) {
                      onError && onError(e);
                    }
                  }}
                >
                  <div><UploadOutlined /><div style={{ marginTop: 4 }}>上传照片</div></div>
                </Upload>
              </div>
            )}

            {/* 操作按钮：从工单发起备件申请/设备回收 */}
            {['in_progress', 'dispatched', 'accepted'].includes(viewingOrder.status) && (
              <div style={{ marginTop: 20, paddingTop: 16, borderTop: `1px solid ${tokens.colorBorder}` }}>
                <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 10 }}>关联操作</Text>
                <Space size={8} wrap>
                  <Button size="small" icon={<InboxOutlined />} onClick={handlePartReqOpen}>
                    申请备件
                  </Button>
                  <Button size="small" icon={<SwapOutlined />} onClick={handleRecycleOpen}>
                    设备回收
                  </Button>
                </Space>
              </div>
            )}
          </div>
          </div>
          </Spin>
        )}
      </Drawer>

      {/* ===== Spare Part Request Modal (from work order) ===== */}
      <Modal
        title="备件需求"
        open={partReqOpen}
        onOk={handlePartReqOk}
        onCancel={() => { setPartReqOpen(false); partReqForm.resetFields(); }}
        confirmLoading={partReqLoading}
        okText="提交需求"
        cancelText="取消"
        destroyOnHidden
      >
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: `${infoColor}0F`, border: `1px solid ${infoColor}26` }}>
          <Text style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
            关联工单：<Text strong style={{ color: tokens.colorPrimary }}>{viewingOrder?.order_no}</Text>
          </Text>
        </div>
        <Form form={partReqForm} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="fulfillment_type" label="处理方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'stock', label: '使用现有库存' },
              { value: 'local_purchase', label: '附近紧急购买' },
              { value: 'vendor_order', label: '厂家订购' },
            ]} />
          </Form.Item>
          {partFulfillmentType === 'stock' ? (
            <>
              <Form.Item name="spare_part_id" label="库存备件" rules={[{ required: true, message: '请选择库存备件' }]}>
                <Select showSearch optionFilterProp="label" placeholder="选择现有库存" options={partInventory.map(part => ({
                  value: part.id,
                  label: `${part.part_name}（${part.part_code}）· 当前 ${part.quantity}${part.unit || '件'}`,
                }))} onChange={id => {
                  const part = partInventory.find(row => row.id === id);
                  if (part) partReqForm.setFieldValue('part_name', part.part_name);
                }} />
              </Form.Item>
              <Form.Item name="part_name" hidden><Input /></Form.Item>
            </>
          ) : (
            <>
              <Form.Item name="part_name" label="备件名称" rules={[{ required: true, message: '请输入备件名称' }]}>
                <Input placeholder="如：pH 电极" />
              </Form.Item>
              <Form.Item name="specification" label="规格型号">
                <Input placeholder="厂家、型号或可识别规格" />
              </Form.Item>
              <Form.Item name="estimated_amount" label="预计金额（元）">
                <Input type="number" min={0} placeholder="可暂不填写" />
              </Form.Item>
            </>
          )}
          <div style={{ marginBottom: 16, color: tokens.colorTextSecondary, fontSize: 12 }}>
            {partFulfillmentType === 'stock'
              ? '批准只代表允许使用，不锁库；现场确认领用时才扣库。'
              : '批准后补充供应商、票据和实际金额，系统自动生成到货及领用台账。'}
          </div>
          <Form.Item name="quantity" label="数量" rules={[{ required: true, message: '请输入数量' }]}>
            <Input type="number" min={1} placeholder="申请数量" />
          </Form.Item>
          <Form.Item name="reason" label="用途说明">
            <Input.TextArea rows={2} placeholder="说明备件用途" />
          </Form.Item>
          <Form.Item name="site_id" hidden>
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      {/* ===== Device Recycle Modal (from work order) ===== */}
      <Modal
        title="设备回收登记"
        open={recycleOpen}
        onOk={handleRecycleOk}
        onCancel={() => { setRecycleOpen(false); recycleForm.resetFields(); }}
        confirmLoading={recycleLoading}
        okText="确认登记"
        cancelText="取消"
        destroyOnHidden
      >
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: `${infoColor}0F`, border: `1px solid ${infoColor}26` }}>
          <Text style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
            关联工单：<Text strong style={{ color: tokens.colorPrimary }}>{viewingOrder?.order_no}</Text>
          </Text>
        </div>
        <Form form={recycleForm} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="device_id" label="回收设备" rules={[{ required: true, message: '请选择设备' }]}>
            <Select placeholder="请选择需要回收的设备" showSearch allowClear
              filterOption={(input, option) => (option.label || '').toLowerCase().includes(input.toLowerCase())}
              options={devices.map(d => ({
                value: d.id,
                label: `${d.device_name || d.device_code} (${d.device_code || d.id})`,
              }))} />
          </Form.Item>
          <Form.Item name="reason" label="回收原因" rules={[{ required: true, message: '请输入原因' }]}>
            <Input placeholder="如: 设备故障更换、到期报废" />
          </Form.Item>
          <Form.Item name="destination" label="回收方式" rules={[{ required: true, message: '请选择回收方式' }]}>
            <Select placeholder="请选择" options={[
              { value: 'repair', label: '维修' },
              { value: 'replace', label: '更换' },
              { value: 'scrap', label: '报废' },
              { value: 'return', label: '退回' },
            ]} />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="可选备注" />
          </Form.Item>
        </Form>
      </Modal>

    </WorkspacePage>
  );
}
