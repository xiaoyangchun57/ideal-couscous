import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams, useLocation } from 'react-router-dom';
import {
  Table, Card, Input, Select, Button, Space, Tag, Badge, Modal, Upload, Spin,
  Typography, Form, DatePicker, Divider, Empty, Dropdown, Menu,
  Row, Col, Descriptions, Timeline, Drawer, Image, App,
} from 'antd';
import {
  PlusOutlined, SearchOutlined, ReloadOutlined, EyeOutlined,
  EditOutlined, DeleteOutlined, ExclamationCircleOutlined,
  SendOutlined, FileTextOutlined, ClockCircleOutlined, ToolOutlined, CheckCircleOutlined,
  InboxOutlined, SwapOutlined, CheckOutlined, CloseOutlined, MoreOutlined, AuditOutlined,
  CameraOutlined, UploadOutlined, DownloadOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import {
  orderStatusMap, orderLevelMap, orderSourceMap, orderStatusBadge,
  CONCLUSION_OPTIONS,
} from '../../services/constants';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const levelColorMap = {
  normal: 'default',
  medium: 'default',
  urgent: 'orange',
  critical: 'red',
};

export default function WorkOrdersPage() {
  const { tokens } = useTheme();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { modal, message } = App.useApp();  // 使用实例方法，避免Tracking Prevention阻断
  const [form] = Form.useForm();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();

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

  // Filter state - initialize from URL params
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [levelFilter, setLevelFilter] = useState(undefined);
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') || undefined);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);
  const [editingOrder, setEditingOrder] = useState(null);

  // View drawer state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingOrder, setViewingOrder] = useState(null);
  const [relatedData, setRelatedData] = useState({ parts: [], recycles: [] });
  const [operationPhotos, setOperationPhotos] = useState([]);

  // Spare part request from work order
  const [partReqOpen, setPartReqOpen] = useState(false);
  const [partReqLoading, setPartReqLoading] = useState(false);
  const [partReqForm] = Form.useForm();

  // Device recycle from work order
  const [recycleOpen, setRecycleOpen] = useState(false);
  const [recycleLoading, setRecycleLoading] = useState(false);
  const [recycleForm] = Form.useForm();
  const [devices, setDevices] = useState([]);

  // 关单（核验通过）弹窗 state
  const [closeModalOpen, setCloseModalOpen] = useState(false);
  const [closeTarget, setCloseTarget] = useState(null);
  const [closePhotos, setClosePhotos] = useState([]);
  const [closePhotoUploading, setClosePhotoUploading] = useState(false);
  const [closeForm] = Form.useForm();

  // 拉取工单列表（提前声明，供关单回调引用，避免 TDZ）
  const fetchOrders = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get('/workorders');
      const list = Array.isArray(data) ? data : [];
      setAllOrders(list);
      const computedCounts = { total: list.length, pending: 0, dispatched: 0, in_progress: 0, reviewing: 0, closed: 0 };
      list.forEach(o => { if (computedCounts[o.status] !== undefined) computedCounts[o.status]++; });
      setCounts(computedCounts);
    } catch (err) {
      message.error('加载工单失败');
      setAllOrders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // 打开关单弹窗
  const handleCloseOpen = useCallback((record) => {
    setCloseTarget(record);
    setClosePhotos([]);
    setClosePhotoUploading(false);
    closeForm.resetFields();
    setCloseModalOpen(true);
  }, [closeForm]);

  // 提交关单：PUT /workorders/<no>/status，带 conclusion 联动告警/审核
  const handleCloseSubmit = useCallback(async () => {
    if (!closeTarget) return;
    try {
      const values = await closeForm.validateFields();
      const existing = (() => {
        try {
          const raw = closeTarget.images;
          return raw ? (typeof raw === 'string' ? JSON.parse(raw) : raw) : [];
        } catch { return []; }
      })();
      const allImages = [...new Set([...existing, ...closePhotos])];
      const result = await api.put(`/workorders/${closeTarget.order_no}/status`, {
        status: 'closed',
        conclusion: values.conclusion,
        remark: values.remark || '',
        images: allImages.length > 0 ? JSON.stringify(allImages) : undefined,
      });
      if (result && !result.error) {
        message.success(`工单 ${closeTarget.order_no} 已关单`);
        setCloseModalOpen(false);
        setCloseTarget(null);
        setClosePhotos([]);
        fetchOrders();
      } else {
        message.error(result?.error || '关单失败');
      }
    } catch {
      // 校验错误
    }
  }, [closeTarget, closeForm, fetchOrders, closePhotos]);

  // Counts state
  const [counts, setCounts] = useState({ total: 0, pending: 0, dispatched: 0, in_progress: 0, reviewing: 0, closed: 0 });
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
      });
    }
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

  useEffect(() => {
    fetchOrders();
  }, [fetchOrders]);

  // Sync filters from URL params when navigating with ?search= or ?status=
  useEffect(() => {
    const urlSearch = searchParams.get('search') || '';
    const urlStatus = searchParams.get('status') || undefined;
    setSearch(urlSearch);
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
    if (statusFilter) {
      list = list.filter((o) => o.status === statusFilter);
    }
    return list;
  }, [allOrders, search, levelFilter, statusFilter]);

  const [tableWrapRef, tableBodyHeight] = useTableAutoHeight({
    deps: [filteredOrders.length, loading],
  });

  const handleSearch = (value) => {
    setSearch(value);
  };

  const handleLevelChange = (value) => {
    setLevelFilter(value);
  };

  const handleStatusChange = (value) => {
    setStatusFilter(value);
  };

  const handleReset = () => {
    setSearch('');
    setLevelFilter(undefined);
    setStatusFilter(undefined);
  };

  const handleCreate = () => {
    setEditingOrder(null);
    form.resetFields();
    setModalOpen(true);
  };

  const handleView = async (record) => {
    setViewOpen(true);
    setRelatedData({ parts: [], recycles: [] });
    setOperationPhotos([]);
    // 重新获取最新工单数据（包括images字段，可能被移动端更新的）
    try {
      const freshList = await api.get('/workorders');
      const fresh = (Array.isArray(freshList) ? freshList : []).find(o => o.order_no === record.order_no);
      setViewingOrder(fresh || record);
    } catch {
      setViewingOrder(record);
    }
    const orderNo = record.order_no;
    if (orderNo) {
      try {
        const [relData, photoData] = await Promise.all([
          api.get(`/workorders/${orderNo}/related`),
          api.get(`/workorders/${orderNo}/photos`),
        ]);
        if (relData) {
          setRelatedData({ parts: relData.parts || [], recycles: relData.recycles || [] });
        }
        // 提取操作附件中的照片
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
      } catch { /* ignore */ }
    }
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
          // 刷新工单详情
          setViewingOrder(prev => {
            if (!prev) return prev;
            const imgs = typeof prev.images === 'string' ? JSON.parse(prev.images) : (prev.images || []);
            return { ...prev, images: JSON.stringify(imgs.filter(u => u !== url)) };
          });
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
    try {
    modal.confirm({
      title: label,
      icon: <ExclamationCircleOutlined />,
      content: `确认将工单 ${record.order_no} ${label}？`,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        const result = await api.put(`/workorders/${record.order_no}/status`, { status: newStatus });
        if (result && !result.error) {
          message.success(`工单已${label}`);
          fetchOrders();
        } else {
          message.error(result?.error || '操作失败');
        }
      },
    });
  } catch(e) { /* 状态流转异常 */ }
}, [fetchOrders]);

  const columns = [
    {
      title: '工单号',
      dataIndex: 'order_no',
      key: 'order_no',
      width: 120,
      fixed: 'left',
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
      render: (text) => text || '-',
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
        const color = levelColorMap[val] || 'default';
        return val ? <Tag color={color} style={{ borderRadius: 4, fontSize: 11 }}>{label}</Tag> : '-';
      },
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      width: 160,
      ellipsis: true,
      render: (text) => text || '-',
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
      render: (text) => text || '-',
    },
    {
      title: 'SLA',
      dataIndex: 'sla_deadline',
      key: 'sla_deadline',
      width: 100,
      render: (val) => {
        if (!val) return '-';
        const isOverdue = new Date(val) < new Date();
        return (
          <Text style={{ color: isOverdue ? tokens.colorError : tokens.colorTextSecondary, fontSize: 13 }}>
            {isOverdue ? '已超时' : val}
          </Text>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      fixed: 'right',
      render: (_, record) => {
        const s = record.status;
        
        // Build action items for dropdown
        const actionItems = [];
        
        // Status transition actions
        if (s === 'pending') {
          actionItems.push({
            key: 'accept',
            label: '受理',
            icon: <CheckOutlined />,
            onClick: () => handleStatusTransition(record, 'accepted', '受理'),
          });
        }
        if (s === 'accepted') {
          actionItems.push({
            key: 'start',
            label: '开始处置',
            icon: <ToolOutlined />,
            onClick: () => handleStatusTransition(record, 'in_progress', '开始处置'),
          });
        }
        if (s === 'dispatched') {
          actionItems.push({
            key: 'start',
            label: '开始处置',
            icon: <ToolOutlined />,
            onClick: () => handleStatusTransition(record, 'in_progress', '开始处置'),
          });
        }
        if (s === 'in_progress') {
          actionItems.push({
            key: 'complete',
            label: '提交审核',
            icon: <CheckOutlined />,
            onClick: () => handleStatusTransition(record, 'reviewing', '提交审核'),
          });
          actionItems.push({
            key: 'return',
            label: '退回受理',
            icon: <SwapOutlined />,
            onClick: () => handleStatusTransition(record, 'accepted', '退回受理'),
          });
        }
        if (s === 'reviewing') {
          // 审核中状态：关单/核验通过在「核验通过」按钮进行
        }
        
        // Common actions (not shown when in reviewing/audit)
        if (s !== 'closed' && s !== 'reviewing') {
          actionItems.push({
            key: 'edit',
            label: '编辑',
            icon: <EditOutlined />,
            onClick: () => handleEdit(record),
          });
        }
        if (s !== 'closed' && s !== 'reviewing') {
          actionItems.push({
            key: 'delete',
            label: '删除',
            icon: <DeleteOutlined />,
            danger: true,
            onClick: () => handleDelete(record),
          });
        }
        
        const menuItems = actionItems.map(item => ({
          key: item.key,
          label: item.label,
          icon: item.icon,
          danger: item.danger,
          onClick: item.onClick,
        }));
        
        return (
          <Space size={4}>
            <Button type="link" size="small" icon={<EyeOutlined />}
              onClick={() => handleView(record)}>
              查看
            </Button>
            {s === 'reviewing' && isAdmin && (
              <Button type="link" size="small" icon={<CheckCircleOutlined />}
                style={{ color: tokens.colorSuccess }}
                onClick={() => handleCloseOpen(record)}>
                核验通过
              </Button>
            )}
            {menuItems.length > 0 && (
              <Dropdown menu={{ items: menuItems }} trigger={['click']}>
                <Button type="link" size="small" icon={<MoreOutlined />}>
                  更多
                </Button>
              </Dropdown>
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
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 24 }}>
      {/* Page Header */}
      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, flexShrink: 0 }}>
        <Title level={4} style={{ margin: 0, color: tokens.colorText }}>工单管理</Title>
        <Space>
          <Button icon={<DownloadOutlined />} onClick={() => downloadExport('/api/export/work-orders?period=month', '工单明细_本月.xlsx')}>
            导出工单
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}
            style={{ background: `linear-gradient(135deg, ${tokens.colorPrimary}, ${tokens.colorPrimaryHover})`, border: 'none' }}>
            新建工单
          </Button>
        </Space>
      </div>

      {/* Compact status summary keeps the list as the primary work surface. */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 1,
        marginBottom: 12, border: `1px solid ${tokens.colorBorder}`, borderRadius: 6,
        background: tokens.colorBorder, overflow: 'hidden', flexShrink: 0,
      }}>
        {[
          { title: '工单总数', value: counts.total, color: tokens.colorPrimary, icon: <FileTextOutlined /> },
          { title: '待受理', value: counts.pending, color: tokens.colorWarning, icon: <ClockCircleOutlined /> },
          { title: '已派发', value: counts.dispatched, color: tokens.colorInfo, icon: <SendOutlined /> },
          { title: '处置中', value: counts.in_progress, color: tokens.colorPrimary, icon: <ToolOutlined /> },
          { title: '待审核', value: counts.reviewing, color: '#722ed1', icon: <AuditOutlined /> },
          { title: '已完成', value: counts.closed, color: tokens.colorSuccess, icon: <CheckCircleOutlined /> },
        ].map(item => (
          <div key={item.title} style={{ padding: '8px 12px', background: tokens.colorBgContainer, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: tokens.colorTextSecondary, fontSize: 12, whiteSpace: 'nowrap' }}>
              {React.cloneElement(item.icon, { style: { fontSize: 13 } })}{item.title}
            </div>
            <div style={{ color: item.color, fontSize: 20, fontWeight: 650, lineHeight: 1.25, marginTop: 2 }}>{item.value}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{
        marginBottom: 12, padding: '8px 0', borderTop: `1px solid ${tokens.colorBorder}`,
        borderBottom: `1px solid ${tokens.colorBorder}`, flexShrink: 0,
      }}>
        <Space wrap size={12} style={{ width: '100%' }}>
          <Input
            placeholder="搜索工单号、标题、站点..."
            prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
            allowClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onPressEnter={(e) => handleSearch(e.target.value)}
            style={{ width: 280, borderRadius: 8 }}
          />
          <Select
            placeholder="级别"
            allowClear
            value={levelFilter}
            onChange={handleLevelChange}
            style={{ width: 120 }}
            options={levelOptions}
          />
          <Select
            placeholder="状态"
            allowClear
            value={statusFilter}
            onChange={handleStatusChange}
            style={{ width: 130 }}
            options={statusOptions}
          />
          <Button icon={<SearchOutlined />} onClick={() => handleSearch(search)}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={handleReset}>重置</Button>
          {(search || levelFilter || statusFilter) && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              已筛选 {filteredOrders.length} 条结果
            </Text>
          )}
        </Space>
      </div>

      {/* Table */}
      <Card size="small" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }} bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div ref={tableWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Table
          columns={columns}
          dataSource={filteredOrders}
          rowKey={(r) => r.order_no || r.id}
          loading={loading}
          pagination={false}
          scroll={tableBodyHeight ? { y: tableBodyHeight } : undefined}
          locale={{ emptyText: <Empty description="暂无工单数据" /> }}
          size="middle"
        />
        </div>
      </Card>

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
        destroyOnClose
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
                  options={Object.entries(orderSourceMap).map(([value, label]) => ({ value, label }))}
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
            {viewingOrder && <Tag color={levelColorMap[viewingOrder.level] || 'default'} style={{ borderRadius: 4, fontSize: 11 }}>{orderLevelMap[viewingOrder.level] || viewingOrder.level}</Tag>}
          </Space>
        }
        open={viewOpen}
        onClose={() => { setViewOpen(false); setViewingOrder(null); }}
        width={520}
      >
        {viewingOrder && (
          <div>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="工单号">
                <Text strong style={{ color: tokens.colorPrimary }}>{viewingOrder.order_no || `#${viewingOrder.id}`}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="标题">{viewingOrder.title || '-'}</Descriptions.Item>
              <Descriptions.Item label="站点">{viewingOrder.site_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="来源">{orderSourceMap[viewingOrder.source] || viewingOrder.source || '-'}</Descriptions.Item>
              <Descriptions.Item label="级别">
                {viewingOrder.level ? <Tag color={levelColorMap[viewingOrder.level] || 'default'} style={{ borderRadius: 4, fontSize: 11 }}>{orderLevelMap[viewingOrder.level] || viewingOrder.level}</Tag> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {viewingOrder.status ? <Badge status={orderStatusBadge[viewingOrder.status] || 'default'} text={orderStatusMap[viewingOrder.status] || viewingOrder.status} /> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="负责人">{viewingOrder.assignee || '-'}</Descriptions.Item>
              <Descriptions.Item label="SLA截止">
                {viewingOrder.sla_deadline ? (
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
                              <span
                                onClick={() => handleDeletePhoto(url)}
                                style={{
                                  position: 'absolute', top: -6, right: -6,
                                  width: 18, height: 18, borderRadius: '50%',
                                  background: '#ff4d4f', color: '#fff',
                                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  cursor: 'pointer', fontSize: 11, lineHeight: '18px',
                                  boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
                                }}
                                title="删除照片"
                              >✕</span>
                            </div>
                          ))}
                        </div>
                      </Image.PreviewGroup>
                    </Descriptions.Item>
                  );
                } catch (_) { return null; }
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
              <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 8, background: 'rgba(114, 46, 209, 0.06)', border: '1px solid rgba(114, 46, 209, 0.15)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <AuditOutlined style={{ color: '#722ed1', fontSize: 16 }} />
                  <div>
                    <Text strong style={{ fontSize: 13, color: '#722ed1' }}>待审核</Text>
                    <div style={{ fontSize: 12, color: tokens.colorTextSecondary, marginTop: 2 }}>
                      处置已提交，请在「待办审核」中进行审核操作
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
            {viewingOrder && ['pending', 'accepted', 'dispatched', 'in_progress', 'reviewing'].includes(viewingOrder.status) && (
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
        )}
      </Drawer>

      {/* ===== Spare Part Request Modal (from work order) ===== */}
      <Modal
        title="申请备件"
        open={partReqOpen}
        onOk={handlePartReqOk}
        onCancel={() => { setPartReqOpen(false); partReqForm.resetFields(); }}
        confirmLoading={partReqLoading}
        okText="提交申请"
        cancelText="取消"
        destroyOnClose
      >
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: 'rgba(24,144,255,0.06)', border: '1px solid rgba(24,144,255,0.15)' }}>
          <Text style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
            关联工单：<Text strong style={{ color: tokens.colorPrimary }}>{viewingOrder?.order_no}</Text>
          </Text>
        </div>
        <Form form={partReqForm} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="part_name" label="备件名称" rules={[{ required: true, message: '请输入备件名称' }]}>
            <Input placeholder="请输入需要申请的备件名称" />
          </Form.Item>
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
        destroyOnClose
      >
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 8, background: 'rgba(24,144,255,0.06)', border: '1px solid rgba(24,144,255,0.15)' }}>
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
          <Form.Item name="operator" label="操作人">
            <Input placeholder="操作人姓名" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="可选备注" />
          </Form.Item>
        </Form>
      </Modal>

      {/* ===== 工单关单（核验通过）Modal ===== */}
      <Modal
        title="工单关单"
        open={closeModalOpen}
        onOk={handleCloseSubmit}
        onCancel={() => { setCloseModalOpen(false); setCloseTarget(null); }}
        okText="确认关单"
        cancelText="取消"
        width={520}
      >
        {closeTarget && (
          <div style={{ marginTop: 16 }}>
            <div style={{ padding: '10px 14px', borderRadius: 8, background: 'rgba(0,0,0,0.02)', marginBottom: 16 }}>
              <Text style={{ fontSize: 13, color: tokens.colorTextSecondary }}>
                工单号：<Text strong>{closeTarget.order_no}</Text>
              </Text>
            </div>
            <Form form={closeForm} layout="vertical">
              <Form.Item
                name="conclusion"
                label="现场结论"
                rules={[{ required: true, message: '请选择现场结论' }]}
              >
                <Select placeholder="选择处置结论" options={CONCLUSION_OPTIONS} allowClear />
              </Form.Item>
              <Form.Item name="remark" label="备注说明">
                <Input.TextArea rows={3} placeholder="可选：补充说明..." />
              </Form.Item>
            </Form>
            {/* 关单附件照片：与结论一起提交 */}
            <div style={{ marginTop: 16 }}>
              <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>
                <CameraOutlined /> 关单照片
              </Text>
              <Upload
                listType="picture-card"
                fileList={closePhotos.map((url, i) => ({ uid: `-${i}`, name: `照片${i + 1}`, status: 'done', url }))}
                onRemove={file => {
                  const url = file.url || file.response?.url;
                  setClosePhotos(prev => prev.filter(u => u !== url));
                }}
                customRequest={async ({ file, onSuccess, onError }) => {
                  if (!closeTarget) return;
                  setClosePhotoUploading(true);
                  try {
                    const fd = new FormData();
                    fd.append('file', file);
                    const res = await fetch(`/api/workorders/${closeTarget.order_no}/photos`, {
                      method: 'POST',
                      headers: { Authorization: `Bearer ${localStorage.getItem('water_ops_token') || ''}` },
                      body: fd,
                    });
                    const data = await res.json();
                    if (data && data.success) {
                      // 后端返回的是整体结果，没有单文件 url；需要从 images 或 photo 返回中取
                      const url = data.url || data.stored_path || (data.images && data.images[data.images.length - 1]);
                      if (url) {
                        setClosePhotos(prev => [...prev, url]);
                        onSuccess && onSuccess(data);
                      } else {
                        onError && onError(new Error('未返回照片地址'));
                      }
                    } else {
                      onError && onError(new Error(data?.error || '上传失败'));
                    }
                  } catch (e) {
                    onError && onError(e);
                  } finally {
                    setClosePhotoUploading(false);
                  }
                }}
                disabled={closePhotoUploading}
              >
                {closePhotoUploading ? <Spin /> : <div><UploadOutlined /><div style={{ marginTop: 4 }}>上传照片</div></div>}
              </Upload>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
