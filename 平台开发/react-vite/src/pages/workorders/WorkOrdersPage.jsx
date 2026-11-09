import { useState, useEffect, useCallback, useMemo } from 'react';
import { useRef } from 'react';
import { useSearchParams, useLocation, useNavigate } from 'react-router-dom';
import {
  Input, Select, Button, Space, Tag, Badge, Modal, Upload, Spin,
  Typography, Form, Tooltip, Alert,
  Row, Col, Descriptions, Timeline, Drawer, Image, App,
} from 'antd';
import {
  PlusOutlined, SearchOutlined, ReloadOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined, ClockCircleOutlined, ToolOutlined, CheckCircleOutlined,
  InboxOutlined, SwapOutlined, CheckOutlined, AuditOutlined,
  UploadOutlined, DownloadOutlined,
  ArrowLeftOutlined,
} from '@ant-design/icons';
import { HoverGifIcon } from '../../components/HoverGifIcon';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import {
  orderStatusMap, orderLevelMap, orderSourceMap, orderStatusBadge, orderLevelBadge,
} from '../../services/constants';
import { statusColors } from '../../theme/tokens';
import { filterInputWidth, filterSelectWidth, filterSmallSelectWidth } from '../../services/pageStyles';
import WorkspacePage, { FilterField, ToolbarMeta, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';
import { listFilterOptions, listFilterValue } from '../../utils/listFilterOptions';
import { listLastPage } from '../../utils/listPagination';
import ReportsPage from '../reports/ReportsPage';

const { Text } = Typography;

const manualSourceOptions = [
  { value: 'manual', label: '管理新建' },
  { value: 'patrol', label: '现场巡查发现' },
  { value: 'superior', label: '上级交办' },
  { value: 'hotline', label: '热线反馈' },
];

const timelineStatusLabels = {
  pending: '待受理',
  accepted: '已受理',
  dispatched: '已派发',
  in_progress: '处置中',
  reviewing: '待审核',
  acceptance: '验收中',
  resolved: '已办结',
  closed: '已办结',
};

export function buildWorkorderTimelineFacts(workorder) {
  const order = workorder || {};
  const rawEvents = Array.isArray(order.flow_events) ? order.flow_events : [];
  const facts = rawEvents.map((event, index) => {
    const type = event?.type || `event-${index}`;
    const reason = event?.remark || (type === 'ended_without_processing' ? order.closure_reason : '');
    return {
      key: `${type}-${event?.time || index}`,
      type,
      title: event?.label || type,
      detail: type === 'ended_without_processing' && reason ? `原因：${reason}` : (event?.remark || ''),
      meta: [event?.operator, event?.time].filter(Boolean).join(' · '),
      tone: event?.tone || 'completed',
    };
  });

  if (order.closure_type === 'no_action_required'
      && !facts.some((fact) => fact.type === 'ended_without_processing')) {
    facts.push({
      key: `ended-without-processing-${order.closure_time || facts.length}`,
      type: 'ended_without_processing',
      title: '无需处理，提前结束',
      detail: order.closure_reason ? `原因：${order.closure_reason}` : '原因未记录',
      meta: order.closure_time || '',
      tone: 'completed',
    });
  }
  if (facts.length > 0) return facts;

  const fallback = [{
    key: 'created-fallback',
    type: 'created',
    title: '工单创建',
    detail: '完整流程记录暂不可用',
    meta: order.created_at || '',
    tone: 'completed',
  }];
  const status = order.status || 'pending';
  fallback.push({
    key: `status-${status}`,
    type: 'status',
    title: order.closure_type === 'no_action_required'
      ? '无需处理，提前结束'
      : (timelineStatusLabels[status] || status),
    detail: order.closure_type === 'no_action_required'
      ? (order.closure_reason ? `原因：${order.closure_reason}` : '原因未记录')
      : '仅显示当前状态，未推断受理、处置或审核节点',
    meta: order.closure_time || order.resolved_at || '',
    tone: ['closed', 'resolved'].includes(status) ? 'completed' : 'current',
  });
  return fallback;
}

export async function executeNoActionClose({
  order, reason, submit, onCommitted, loadDetail, refreshOrders,
}) {
  const response = await submit();
  const closureTime = response?.closure_time || order?.closure_time || order?.resolved_at || '';
  const committedOrder = {
    ...(order || {}),
    status: 'closed',
    conclusion: 'no_action_required',
    closure_type: 'no_action_required',
    closure_reason: response?.closure_reason || reason,
    closure_time: closureTime,
    resolved_at: closureTime || order?.resolved_at || '',
    can_end_without_processing: false,
    actions: { ...(order?.actions || {}), can_end_without_processing: false },
  };
  onCommitted?.(committedOrder, response);

  const [detailResult, listResult] = await Promise.allSettled([
    loadDetail ? loadDetail() : Promise.resolve(null),
    refreshOrders ? refreshOrders() : Promise.resolve(null),
  ]);
  const detail = detailResult.status === 'fulfilled' && detailResult.value
    ? { ...committedOrder, ...detailResult.value, can_end_without_processing: false }
    : committedOrder;
  const refreshFailures = [];
  if (detailResult.status === 'rejected') refreshFailures.push('详情');
  if (listResult.status === 'rejected') refreshFailures.push('列表');
  return { response, committedOrder, detail, refreshFailures };
}

export async function loadWorkorderDetailBundle({
  orderNo, fallbackOrder, loadDetail, loadRelated, loadPhotos,
}) {
  const relatedPromise = Promise.resolve().then(() => loadRelated(orderNo));
  const photoPromise = Promise.resolve().then(() => loadPhotos(orderNo));
  const detailResult = await Promise.resolve()
    .then(() => loadDetail(orderNo))
    .then((value) => ({ status: 'fulfilled', value }))
    .catch((reason) => ({ status: 'rejected', reason }));
  const errors = [];
  const detail = detailResult.status === 'fulfilled' && detailResult.value
    ? detailResult.value
    : fallbackOrder;
  if (detailResult.status === 'rejected') errors.push('最新工单信息');
  const auxiliaryPromise = Promise.allSettled([relatedPromise, photoPromise]).then(
    ([relatedResult, photoResult]) => ({
      related: relatedResult.status === 'fulfilled' ? relatedResult.value : null,
      photos: photoResult.status === 'fulfilled' ? photoResult.value : null,
      errors: [
        ...(relatedResult.status === 'rejected' ? ['关联业务'] : []),
        ...(photoResult.status === 'rejected' ? ['处置影像'] : []),
      ],
    }),
  );
  return {
    detail,
    detailError: detailResult.status === 'rejected' ? detailResult.reason : null,
    errors,
    auxiliaryPromise,
  };
}

export function createWorkorderDetailRequestGuard() {
  let current = 0;
  return {
    begin: () => {
      current += 1;
      return current;
    },
    invalidate: () => {
      current += 1;
    },
    isCurrent: (requestId) => requestId === current,
  };
}

export function shouldShowEndWithoutProcessingBlock(order) {
  const status = String(order?.status || '').toLowerCase();
  return Boolean(
    order
    && !order.can_end_without_processing
    && order.end_without_processing_block_reason
    && !['closed', 'resolved'].includes(status),
  );
}

export function buildWorkorderListRequest(searchString = '') {
  const activeParams = new URLSearchParams(searchString);
  const page = Math.max(1, Number(activeParams.get('page') || 1));
  const pageSize = Math.max(1, Math.min(Number(activeParams.get('page_size') || 20), 100));
  const query = new URLSearchParams({
    paginated: '1',
    page: String(page),
    page_size: String(pageSize),
  });
  ['search', 'assignee', 'level', 'status', 'site_id'].forEach((key) => {
    const value = activeParams.get(key);
    if (value) query.set(key, value);
  });
  return { path: `/workorders?${query.toString()}`, page, pageSize };
}

export default function WorkOrdersPage() {
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const userRoles = user?.roles?.length ? user.roles : [user?.role];
  const isAdmin = userRoles.includes('admin');
  const canCreateWorkorder = userRoles.some((role) => role === 'admin' || role === 'operator');
  const canReview = userRoles.some((role) => role === 'admin' || role === 'reviewer');
  const { modal, message } = App.useApp();  // 使用实例方法，避免Tracking Prevention阻断
  // 语义状态色统一走 statusColors（禁硬编码 hex），半透明背景用 alpha 后缀派生
  const purpleColor = statusColors.purple[isDark ? 'dark' : 'light'];
  const infoColor = statusColors.info[isDark ? 'dark' : 'light'];
  const [form] = Form.useForm();
  const selectedCreateSiteId = Form.useWatch('site_id', form);
  const [searchParams, setSearchParams] = useSearchParams();
  const sourceView = searchParams.get('view') === 'reports';
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
  const [ordersLoaded, setOrdersLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [page, setPage] = useState(Math.max(1, Number(searchParams.get('page') || 1)));
  const [pageSize, setPageSize] = useState(Math.max(1, Number(searchParams.get('page_size') || 20)));
  const [totalOrders, setTotalOrders] = useState(0);

  // Filter state - initialize from URL params
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [assigneeFilter, setAssigneeFilter] = useState(searchParams.get('assignee') || '');
  const [siteFilter, setSiteFilter] = useState(searchParams.get('site_id') || '');
  const [levelFilter, setLevelFilter] = useState(searchParams.get('level') || undefined);
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') || undefined);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalLoading, setModalLoading] = useState(false);

  // View drawer state
  const [viewOpen, setViewOpen] = useState(false);
  const [viewingOrder, setViewingOrder] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState('');
  const [viewUnavailable, setViewUnavailable] = useState(null);
  const [relatedData, setRelatedData] = useState({ parts: [], recycles: [] });
  const [operationPhotos, setOperationPhotos] = useState([]);
  const [noActionOpen, setNoActionOpen] = useState(false);
  const [noActionReason, setNoActionReason] = useState('');
  const [noActionLoading, setNoActionLoading] = useState(false);
  const [noActionRefreshOrderNo, setNoActionRefreshOrderNo] = useState('');
  const focusedOrderRef = useRef('');
  const orderListRequestRef = useRef(0);
  const detailRequestGuardRef = useRef(null);
  if (!detailRequestGuardRef.current) {
    detailRequestGuardRef.current = createWorkorderDetailRequestGuard();
  }

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
  const canOperateOrder = useCallback((order) => order?.can_operate === true, []);

  // 拉取工单列表
  const fetchOrders = useCallback(async (options = {}) => {
    const requestId = ++orderListRequestRef.current;
    const throwOnError = options?.throwOnError === true;
    const request = buildWorkorderListRequest(location.search);
    const requestedPage = request.page;
    const requestedPageSize = request.pageSize;
    let correctingPage = false;
    setLoading(true);
    setLoadError('');
    try {
      const data = await api.getStrict(request.path);
      if (requestId !== orderListRequestRef.current) return;
      const list = Array.isArray(data) ? data : (data?.items || []);
      const responseTotal = Array.isArray(data) ? list.length : Number(data?.total || 0);
      const lastPage = listLastPage(responseTotal, requestedPageSize);
      if (responseTotal > 0 && requestedPage > lastPage) {
        correctingPage = true;
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          if (lastPage > 1) next.set('page', String(lastPage));
          else next.delete('page');
          return next;
        }, { replace: true });
        return;
      }
      setAllOrders(list);
      const responseCounts = Array.isArray(data) ? null : (data?.counts || {});
      const computedCounts = responseCounts ? {
        total: responseTotal,
        pending: Number(responseCounts.pending || 0),
        accepted: Number(responseCounts.accepted || 0),
        generated: Number(responseCounts.generated || 0),
        dispatched: Number(responseCounts.dispatched || 0),
        in_progress: Number(responseCounts.in_progress || 0),
        reviewing: Number(responseCounts.reviewing || 0),
        closed: Number(responseCounts.closed || 0) + Number(responseCounts.resolved || 0),
      } : {
        total: responseTotal,
        pending: 0,
        accepted: 0,
        generated: 0,
        dispatched: 0,
        in_progress: 0,
        reviewing: 0,
        closed: 0,
      };
      if (!responseCounts) {
        list.forEach(o => { if (computedCounts[o.status] !== undefined) computedCounts[o.status]++; });
      }
      setCounts(computedCounts);
      setTotalOrders(responseTotal);
      setPage(Array.isArray(data) ? requestedPage : Number(data?.page || requestedPage));
      setPageSize(Array.isArray(data) ? requestedPageSize : Number(data?.page_size || requestedPageSize));
    } catch (error) {
      if (requestId !== orderListRequestRef.current) return;
      setLoadError(error.message || '工单列表加载失败');
      if (throwOnError) throw error;
    } finally {
      if (requestId === orderListRequestRef.current && !correctingPage) {
        setLoading(false);
        setOrdersLoaded(true);
      }
    }
  }, [location.search, setSearchParams]);

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
  const [assignees, setAssignees] = useState([]);
  const [assigneesLoading, setAssigneesLoading] = useState(false);
  const [assigneesError, setAssigneesError] = useState('');

  const loadAssignees = useCallback(async () => {
    setAssigneesLoading(true);
    setAssigneesError('');
    try {
      const data = await api.get('/assignees');
      if (!Array.isArray(data)) throw new Error('负责人列表返回格式无效');
      setAssignees(data);
    } catch (error) {
      setAssigneesError(error?.message || '负责人列表加载失败');
    } finally {
      setAssigneesLoading(false);
    }
  }, []);

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
    loadAssignees();
  }, [loadAssignees]);

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
    const urlSite = searchParams.get('site_id') || '';
    setSearch(urlSearch);
    setAssigneeFilter(urlAssignee);
    setLevelFilter(urlLevel);
    setStatusFilter(urlStatus);
    setSiteFilter(urlSite);
    // Refetch data when navigating from other pages with URL params
    fetchOrders();
  }, [location.search, fetchOrders]);

  const hasActiveFilters = Boolean(search || assigneeFilter || levelFilter || statusFilter || siteFilter);
  const tablePagination = useMemo(() => ({
    current: page,
    pageSize,
    total: totalOrders,
    showSizeChanger: true,
    pageSizeOptions: [20, 50, 100],
    onChange: (nextPage, nextPageSize) => {
      const next = new URLSearchParams(searchParams);
      const sizeChanged = nextPageSize !== pageSize;
      next.set('page', String(sizeChanged ? 1 : nextPage));
      next.set('page_size', String(nextPageSize));
      setSearchParams(next, { replace: true });
    },
  }), [page, pageSize, searchParams, setSearchParams, totalOrders]);

  const handleSearch = (value) => {
    setSearch(value);
    const next = new URLSearchParams(searchParams);
    if (value) next.set('search', value);
    else next.delete('search');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const handleLevelChange = (value) => {
    setLevelFilter(value);
    const next = new URLSearchParams(searchParams);
    if (value) next.set('level', value);
    else next.delete('level');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const handleStatusChange = (value) => {
    setStatusFilter(value);
    const next = new URLSearchParams(searchParams);
    if (value) next.set('status', value);
    else next.delete('status');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const handleSiteChange = (value) => {
    setSiteFilter(value || '');
    const next = new URLSearchParams(searchParams);
    if (value) next.set('site_id', value);
    else next.delete('site_id');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const handleAssigneeChange = (value) => {
    setAssigneeFilter(value || '');
    const next = new URLSearchParams(searchParams);
    if (value) next.set('assignee', value);
    else next.delete('assignee');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const handleAssigneeClear = () => {
    setAssigneeFilter('');
    const next = new URLSearchParams(searchParams);
    next.delete('assignee');
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const handleReset = () => {
    setSearch('');
    setAssigneeFilter('');
    setSiteFilter('');
    setLevelFilter(undefined);
    setStatusFilter(undefined);
    const next = new URLSearchParams(searchParams);
    ['search', 'assignee', 'level', 'status', 'site_id', 'page'].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  };

  const handleCreate = () => {
    form.resetFields();
    setModalOpen(true);
  };

  const openNoAction = (record) => {
    setViewingOrder(record);
    setNoActionReason('');
    setNoActionOpen(true);
  };

  const retryNoActionRefresh = async () => {
    const orderNo = noActionRefreshOrderNo || viewingOrder?.order_no;
    if (!orderNo) return;
    setViewLoading(true);
    const [detailResult, listResult] = await Promise.allSettled([
      api.getStrict(`/workorders/${orderNo}`),
      fetchOrders({ throwOnError: true }),
    ]);
    if (detailResult.status === 'fulfilled') setViewingOrder(detailResult.value);
    const failures = [];
    if (detailResult.status === 'rejected') failures.push('详情');
    if (listResult.status === 'rejected') failures.push('列表');
    if (failures.length > 0) {
      setViewError(`工单已提前结束，但${failures.join('、')}更新失败，请重新加载。`);
      message.warning('工单已提前结束，部分页面信息仍未更新');
    } else {
      setViewError('');
      setNoActionRefreshOrderNo('');
      message.success('工单详情和列表已更新');
    }
    setViewLoading(false);
  };

  const submitNoAction = async () => {
    const reason = noActionReason.trim();
    if (!viewingOrder?.order_no || !reason) {
      message.error('请填写无需处理的原因');
      return;
    }
    setNoActionLoading(true);
    try {
      const order = viewingOrder;
      const result = await executeNoActionClose({
        order,
        reason,
        submit: () => api.postStrict(`/workorders/${order.order_no}/end-without-processing`, { reason }),
        onCommitted: (committedOrder) => {
          setViewingOrder(committedOrder);
          setAllOrders((items) => items.map((item) => (
            item.order_no === committedOrder.order_no ? { ...item, ...committedOrder } : item
          )));
          setCounts((current) => {
            const priorStatus = order.status;
            if (!priorStatus || priorStatus === 'closed' || current[priorStatus] === undefined) return current;
            return {
              ...current,
              [priorStatus]: Math.max(0, current[priorStatus] - 1),
              closed: current.closed + 1,
            };
          });
          setNoActionOpen(false);
          setNoActionReason('');
          setNoActionRefreshOrderNo(order.order_no);
          setViewError('');
        },
        loadDetail: () => api.getStrict(`/workorders/${order.order_no}`),
        refreshOrders: () => fetchOrders({ throwOnError: true }),
      });
      setViewingOrder(result.detail);
      if (result.refreshFailures.length > 0) {
        setViewError(`工单已提前结束，但${result.refreshFailures.join('、')}更新失败，请重新加载。`);
        message.warning('工单已提前结束，详情或列表更新失败');
      } else {
        setNoActionRefreshOrderNo('');
        message.success('工单已提前结束');
      }
    } catch (error) {
      message.error(error?.message || '提前结束失败，请稍后重试');
    } finally {
      setNoActionLoading(false);
    }
  };

  const handleView = async (record) => {
    const requestId = detailRequestGuardRef.current.begin();
    setViewOpen(true);
    setViewingOrder(record);
    setViewLoading(true);
    setViewError('');
    setViewUnavailable(null);
    setRelatedData({ parts: [], recycles: [] });
    setOperationPhotos([]);
    const orderNo = record.order_no;
    if (orderNo) {
      const result = await loadWorkorderDetailBundle({
        orderNo,
        fallbackOrder: record,
        loadDetail: (value) => api.getStrict(`/workorders/${value}`),
        loadRelated: (value) => api.getStrict(`/workorders/${value}/related`),
        loadPhotos: (value) => api.getStrict(`/workorders/${value}/photos`),
      });
      if (!detailRequestGuardRef.current.isCurrent(requestId)) return;
      if (result.detailError?.status === 404 || result.detailError?.status === 403) {
        setViewingOrder(null);
        setViewUnavailable({
          orderNo,
          status: result.detailError.status,
          message: result.detailError.message,
        });
        setViewLoading(false);
        return;
      }
      setViewingOrder(result.detail);
      setViewLoading(false);
      if (result.errors.length) {
        setViewError(`${result.errors.join('、')}加载失败，当前详情可能不完整。`);
      }

      const auxiliary = await result.auxiliaryPromise;
      if (!detailRequestGuardRef.current.isCurrent(requestId)) return;
      if (auxiliary.related) {
        setRelatedData({
          parts: auxiliary.related.parts || [],
          recycles: auxiliary.related.recycles || [],
        });
      }
      if (auxiliary.photos) {
        const photoData = auxiliary.photos;
        if (photoData) {
          const allPhotos = [];
          if (photoData.item_progress && Array.isArray(photoData.item_progress)) {
            photoData.item_progress.forEach(item => {
              if (item.photos && Array.isArray(item.photos)) {
                item.photos.forEach(p => {
                  allPhotos.push({
                    url: p.url || p.stored_path || p,
                    name: p.filename || item.item_name || '',
                    takenAt: p.taken_at || '',
                    uploadedAt: p.created_at || '',
                    uploader: p.uploader_name || '',
                  });
                });
              }
            });
          }
          setOperationPhotos(allPhotos);
        }
      }
      const allErrors = [...result.errors, ...auxiliary.errors];
      if (allErrors.length) {
        setViewError(`${allErrors.join('、')}加载失败，当前详情可能不完整。`);
      }
    }
    if (detailRequestGuardRef.current.isCurrent(requestId)) setViewLoading(false);
  };

  useEffect(() => {
    const orderNo = searchParams.get('order') || '';
    if (!orderNo) {
      focusedOrderRef.current = '';
      return;
    }
    if (sourceView || loading || !ordersLoaded || focusedOrderRef.current === orderNo) return;
    const target = allOrders.find((order) => String(order.order_no) === String(orderNo));
    focusedOrderRef.current = orderNo;
    handleView(target || { order_no: orderNo });
  }, [allOrders, loading, message, ordersLoaded, searchParams, sourceView]);

  // 服务端事件是流程事实；缺失时只显示有限状态，不推断未发生的节点。
  const buildTimelineItems = useCallback((wo, recycles) => {
    const facts = buildWorkorderTimelineFacts(wo);
    const items = facts.map((fact) => ({
      color: fact.type === 'rejected' ? 'red' : (fact.tone === 'current' ? 'blue' : 'green'),
      children: (
        <div key={fact.key}>
          <Text strong>{fact.title}</Text>
          {fact.detail && <div style={{ fontSize: 12, color: tokens.colorTextSecondary }}>{fact.detail}</div>}
          {fact.meta && <div style={{ fontSize: 11, color: tokens.colorTextTertiary }}>{fact.meta}</div>}
        </div>
      ),
    }));
    if (recycles && recycles.length > 0) {
      const recycleItems = recycles.map((r, index) => ({
          color: 'blue',
          children: (
            <div key={`recycle-${r.id || index}`}>
              <Text strong>设备更换</Text>
              <div style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
                {r.device_name} ({r.device_code}) → {r.destination === 'scrap' ? '报废' : r.destination === 'repair' ? '维修' : r.destination === 'replace' ? '更换' : '回收'}
              </div>
              <div style={{ fontSize: 11, color: tokens.colorTextTertiary }}>{r.recycle_date || r.created_at || ''}</div>
            </div>
          ),
        }));
      const terminalIndex = facts.findIndex((fact) => (
        ['approved', 'closed', 'ended_without_processing'].includes(fact.type)
      ));
      items.splice(terminalIndex >= 0 ? terminalIndex : items.length, 0, ...recycleItems);
    }
    return items;
  }, [tokens]);

  const handleDeletePhoto = useCallback(async (url) => {
    if (!viewingOrder) return;
    if (!canOperateOrder(viewingOrder)) {
      message.error('仅工单负责人可修改现场照片');
      return;
    }
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
  }, [viewingOrder, fetchOrders, canOperateOrder, message, modal]);

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields();
      setModalLoading(true);
      const result = await api.post('/workorders', values);
      if (result && !result.error) {
        message.success('工单已创建');
        setModalOpen(false);
        fetchOrders();
      } else {
        message.error(result?.error || '创建失败');
      }
    } catch {
      // validation error, do nothing
    } finally {
      setModalLoading(false);
    }
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
        <Input.TextArea className="workorder-confirm-textarea" autoSize={{ minRows: 3, maxRows: 5 }} maxLength={500} showCount
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
        try {
          const result = newStatus === 'reviewing'
            ? await api.postStrict(`/workorders/${record.order_no}/submit-review`, { client: 'web', resolution_note: resolutionNote.trim() })
            : await api.put(`/workorders/${record.order_no}/status`, { status: newStatus });
          if (result && !result.error) {
            message.success(`工单已${label}`);
            fetchOrders();
            return;
          }
          throw new Error(result?.error || '操作失败');
        } catch (error) {
          message.error(error?.message || '操作失败，请稍后重试');
          return Promise.reject(error);
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
      render: (val, record) => {
        const label = record.closure_type === 'no_action_required'
          ? '已提前结束' : (orderStatusMap[val] || val || '-');
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
          if (record.closure_type === 'no_action_required') {
            return <Tooltip title={`无需处理，提前结束：${record.closure_reason || '未记录原因'}`}>
              <Text type="secondary">已提前结束</Text>
            </Tooltip>;
          }
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

        // 现场状态流转只对工单实际负责人开放；审核保持独立职责。
        if (canOperateOrder(record) && s === 'pending') {
          primaryAction = {
            key: 'accept',
            label: '受理',
            icon: <CheckOutlined />,
            onClick: () => handleStatusTransition(record, 'accepted', '受理'),
          };
        }
        if (canOperateOrder(record) && s === 'accepted') {
          primaryAction = {
            key: 'start',
            label: '开始处置',
            icon: <ToolOutlined />,
            onClick: () => handleStatusTransition(record, 'in_progress', '开始处置'),
          };
        }
        if (canOperateOrder(record) && s === 'dispatched') {
          primaryAction = {
            key: 'start',
            label: '开始处置',
            icon: <ToolOutlined />,
            onClick: () => handleStatusTransition(record, 'in_progress', '开始处置'),
          };
        }
        if (canOperateOrder(record) && s === 'in_progress') {
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
        if (canReview && s === 'reviewing') {
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
              <Button type="text" size="small" icon={<HoverGifIcon name="eye" size={14} />} aria-label={`查看工单 ${record.order_no} 详情`}
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
          </Space>
        );
      },
    },
  ];

  const siteOptions = (Array.isArray(sites) ? sites : [])
    .map((site) => ({ value: String(site.id), label: site.name || `站点 #${site.id}` }));
  const assigneeOptions = (Array.isArray(assignees) ? assignees : [])
    .map((item) => ({ value: item.name, label: item.name }));
  const statusOptions = Object.entries(orderStatusMap).map(([value, label]) => ({ value, label }));
  const levelOptions = [
    { value: 'normal', label: '一般' },
    { value: 'urgent', label: '紧急' },
    { value: 'critical', label: '重大' },
  ];
  const createAssigneeOptions = assignees
    .filter(item => (item.site_ids || []).some(siteId => String(siteId) === String(selectedCreateSiteId || '')))
    .map(item => ({ value: item.id, label: item.name }));
  if (sourceView && isAdmin) {
    return <div>
      <div style={{ padding: '16px 24px 0' }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => {
          const next = new URLSearchParams(searchParams);
          next.delete('view');
          setSearchParams(next);
        }}>返回工单</Button>
      </div>
      <ReportsPage embedded statusParamKey="report_status" typeParamKey="report_type" />
    </div>;
  }

  return (
    <WorkspacePage
      title="工单"
      subtitle="查看并推进当前运维事项；待审核工单统一进入审核工作台核验。"
      secondaryAction={<Button icon={<DownloadOutlined />} onClick={() => downloadExport('/api/export/work-orders?period=month', '工单明细_本月.xlsx')}>导出</Button>}
      primaryAction={<Space>
        {isAdmin && <Button icon={<FileTextOutlined />} onClick={() => {
          const next = new URLSearchParams(searchParams);
          next.set('view', 'reports');
          setSearchParams(next);
        }}>异常来源</Button>}
        {canCreateWorkorder && <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建工单</Button>}
      </Space>}
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
          <FilterField label="级别"><Select aria-label="级别" placeholder="全部级别" allowClear value={levelFilter} onChange={(value) => handleLevelChange(listFilterValue(value))} style={{ width: filterSmallSelectWidth }} options={listFilterOptions('全部级别', levelOptions)} /></FilterField>
          <FilterField label="状态"><Select aria-label="状态" placeholder="全部状态" allowClear value={statusFilter} onChange={(value) => handleStatusChange(listFilterValue(value))} style={{ width: filterSelectWidth }} options={listFilterOptions('全部状态', statusOptions)} /></FilterField>
          <FilterField label="所属站点"><Select aria-label="所属站点" placeholder="全部站点" allowClear showSearch optionFilterProp="label" value={siteFilter || undefined} onChange={(value) => handleSiteChange(listFilterValue(value))} style={{ width: filterSelectWidth }} options={listFilterOptions('全部站点', siteOptions)} /></FilterField>
          <FilterField label="负责人"><Select aria-label="负责人" placeholder="全部负责人" allowClear showSearch optionFilterProp="label" value={assigneeFilter || undefined} onChange={(value) => handleAssigneeChange(listFilterValue(value))} style={{ width: filterSelectWidth }} options={listFilterOptions('全部负责人', assigneeOptions)} /></FilterField>
          {assigneeFilter && <ToolbarMeta label="负责人"><Tag closable onClose={handleAssigneeClear}>{assigneeFilter}</Tag></ToolbarMeta>}
          <ToolbarMeta label={hasActiveFilters ? '当前筛选' : '当前范围'}>
            共 {totalOrders} 条，第 {totalOrders ? page : 0}/{totalOrders ? Math.ceil(totalOrders / pageSize) : 0} 页
          </ToolbarMeta>
        </WorkspaceToolbar>
      )}
    >
      {/* Table */}
      {loadError && !loading
        ? <WorkspaceEmpty type="error" description="工单列表加载失败，当前不能判断是否没有工单。" onRefresh={fetchOrders} />
        : <WorkspaceTable columns={columns} dataSource={allOrders} rowKey={(r) => r.order_no || r.id}
            loading={loading} pagination={tablePagination} emptyType={hasActiveFilters ? 'filtered' : 'empty'} onRefresh={fetchOrders} />}

      {/* Create Modal */}
      <Modal
        title="新建工单"
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => setModalOpen(false)}
        confirmLoading={modalLoading}
        okText="创建"
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
          <Form.Item name="site_id" label="站点" rules={[{ required: true, message: '请选择站点' }]}>
            <Select placeholder="请选择站点" allowClear showSearch
              onChange={() => form.setFieldValue('assignee_id', undefined)}
              filterOption={(input, option) => (option.label || '').toLowerCase().includes(input.toLowerCase())}
              options={sites.map(s => ({ value: s.id, label: `${s.name || s.code} (${s.code || s.id})` }))} />
          </Form.Item>
          {isAdmin && <>
            {assigneesError && <Alert
              type="error"
              showIcon
              message="负责人列表加载失败"
              description={assigneesError}
              action={<Button size="small" icon={<ReloadOutlined />} loading={assigneesLoading} onClick={loadAssignees}>重试</Button>}
              style={{ marginBottom: 12 }}
            />}
            {selectedCreateSiteId && !assigneesLoading && !assigneesError && createAssigneeOptions.length === 0 && <Alert
              type="warning"
              showIcon
              message="该站点暂无可指派的负责人"
              description="负责人需具备该站点的有效运维权限。可更换站点，或联系管理员开通权限后重新加载。"
              action={<Button size="small" icon={<ReloadOutlined />} onClick={loadAssignees}>重新加载</Button>}
              style={{ marginBottom: 12 }}
            />}
            <Form.Item name="assignee_id" label="负责人" rules={[{ required: true, message: '请选择该站点的负责人' }]}>
              <Select
                placeholder={selectedCreateSiteId ? '请选择负责人' : '请先选择站点'}
                disabled={!selectedCreateSiteId || assigneesLoading || !!assigneesError}
                loading={assigneesLoading}
                notFoundContent={assigneesLoading
                  ? <Spin size="small" />
                  : assigneesError
                    ? '负责人列表加载失败，请重试'
                    : selectedCreateSiteId ? '该站点暂无可用负责人' : '请先选择站点'}
                options={createAssigneeOptions}
              />
            </Form.Item>
          </>}
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
        onClose={() => {
          detailRequestGuardRef.current.invalidate();
          setViewOpen(false);
          setViewingOrder(null);
          setViewUnavailable(null);
          setNoActionRefreshOrderNo('');
        }}
        width={520}
      >
        {viewUnavailable ? (
          <Alert
            type="warning"
            showIcon
            message={viewUnavailable.status === 404 ? '工单不存在' : '无权查看该工单'}
            description={viewUnavailable.status === 404
              ? `未找到工单 ${viewUnavailable.orderNo}，该工单可能已不存在。请返回列表确认工单号，或从原通知重新进入。`
              : (viewUnavailable.message || `当前账号无权查看工单 ${viewUnavailable.orderNo}。`)}
          />
        ) : viewingOrder && (
          <Spin spinning={viewLoading} tip="正在加载最新工单信息">
          <div>
            {viewError && <Alert
              type="warning"
              showIcon
              message={viewError}
              action={noActionRefreshOrderNo ? (
                <Button size="small" icon={<ReloadOutlined />} onClick={retryNoActionRefresh}>重新加载</Button>
              ) : undefined}
              style={{ marginBottom: 12 }}
            />}
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
                {viewingOrder.status ? <Badge status={orderStatusBadge[viewingOrder.status] || 'default'} text={viewingOrder.closure_type === 'no_action_required' ? '已提前结束' : (orderStatusMap[viewingOrder.status] || viewingOrder.status)} /> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="负责人">{viewingOrder.assignee || '-'}</Descriptions.Item>
              {viewingOrder.closure_type === 'no_action_required' && <>
                <Descriptions.Item label="结束方式">无需处理，提前结束</Descriptions.Item>
                <Descriptions.Item label="结束原因">{viewingOrder.closure_reason || '-'}</Descriptions.Item>
                <Descriptions.Item label="结束时间">{viewingOrder.closure_time || viewingOrder.resolved_at || '-'}</Descriptions.Item>
              </>}
              <Descriptions.Item label={viewingOrder.closure_type === 'no_action_required' ? 'SLA记录' : (viewingOrder.status === 'closed' ? 'SLA结果' : 'SLA截止')}>
                {viewingOrder.closure_type === 'no_action_required'
                  ? <Text type="secondary">工单已提前结束，不记为维修完成</Text>
                  : viewingOrder.sla_deadline ? viewingOrder.status === 'closed' ? (() => {
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
                              {canOperateOrder(viewingOrder) && viewingOrder.status === 'in_progress' && <button
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

            {viewingOrder.can_end_without_processing && (
              <div style={{ marginTop: 16 }}>
                <Button danger icon={<ExclamationCircleOutlined />} onClick={() => openNoAction(viewingOrder)}>
                  无需处理，提前结束
                </Button>
              </div>
            )}

            {shouldShowEndWithoutProcessingBlock(viewingOrder) && (
              <Alert
                style={{ marginTop: 16 }}
                type="info"
                showIcon
                message="当前不能提前结束"
                description={viewingOrder.end_without_processing_block_reason}
              />
            )}

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
            {viewingOrder && canOperateOrder(viewingOrder) && viewingOrder.status === 'in_progress' && (
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
                                    takenAt: p.taken_at || '',
                                    uploadedAt: p.created_at || '',
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
                        const uploadError = new Error(data?.error || '上传失败');
                        message.error(uploadError.message);
                        onError && onError(uploadError);
                      }
                    } catch (e) {
                      message.error(e?.message || '上传失败，请稍后重试');
                      onError && onError(e);
                    }
                  }}
                >
                  <div><UploadOutlined /><div style={{ marginTop: 4 }}>上传照片</div></div>
                </Upload>
              </div>
            )}

            {/* 操作按钮：从工单发起备件申请/设备回收 */}
            {canOperateOrder(viewingOrder) && ['in_progress', 'dispatched', 'accepted'].includes(viewingOrder.status) && (
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

      <Modal
        title="无需处理，提前结束"
        open={noActionOpen}
        onOk={submitNoAction}
        onCancel={() => { if (!noActionLoading) { setNoActionOpen(false); setNoActionReason(''); } }}
        confirmLoading={noActionLoading}
        okText="确认提前结束"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        maskClosable={!noActionLoading}
      >
        <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
          <Descriptions.Item label="工单">{viewingOrder?.order_no || '-'}</Descriptions.Item>
          <Descriptions.Item label="当前状态">
            {viewingOrder ? (orderStatusMap[viewingOrder.status] || viewingOrder.status || '-') : '-'}
          </Descriptions.Item>
        </Descriptions>
        <Input.TextArea
          value={noActionReason}
          onChange={(event) => setNoActionReason(event.target.value)}
          autoSize={{ minRows: 4, maxRows: 8 }}
          maxLength={500}
          showCount
          placeholder="请说明为何无需继续处理"
        />
        <Alert type="info" showIcon message="结束后工单、签到、照片、处置和审核记录都会保留。" style={{ marginTop: 12 }} />
      </Modal>

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
