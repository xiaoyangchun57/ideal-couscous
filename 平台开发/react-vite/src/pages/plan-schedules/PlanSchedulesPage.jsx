import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Table, Card, Button, Space, Tag, Typography, message, Modal, Select, Empty,
  Drawer, Descriptions, Alert, Input, InputNumber, Tooltip, Badge, DatePicker, Checkbox,
} from 'antd';
import {
  ReloadOutlined, CheckOutlined, CloseOutlined, ExclamationCircleOutlined,
  CarOutlined, ToolOutlined, CalendarOutlined, FileSearchOutlined, BulbOutlined,
  StarOutlined, FolderOpenOutlined, DeleteOutlined,
  ClearOutlined, EditOutlined, PlusOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { filterSelectWidth, filterSmallSelectWidth } from '../../services/pageStyles';
import WorkspacePage, { FilterField, ToolbarMeta, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';
import { replaceReworkWithSchedule, resolveReworkScheduleId } from './planScheduleNavigation';
import {
  DEFAULT_FOLLOW_UP_SCOPE, followUpRecommendationActionPayload, loadFollowUpRecommendations,
} from './planRecommendationScope';
import {
  canCancelPlanSchedule,
  groupExecutionPackagesByDate,
  itineraryRowSiteIds,
  normalizePlanCancelReason,
  normalizePlanPurgeReason,
  planDetailItineraryRows,
  planExecutionPresentation,
  sitePriorityPresentation,
  shouldShowPreExecutionRisks,
} from './planExecutionPackages';
import {
  cleanupCandidateFactRows, cleanupCandidateIdentityRows, reconcileCleanupSelection,
} from './cleanupCandidateFacts';
import { applyPlanVehicleSelection, buildPlanValidationPayload } from './planVehicleState';
import './PlanSchedulesPage.css';

const { Text } = Typography;

// 计划状态映射（调度层状态机）
const SCHEDULE_STATUS_MAP = {
  draft: { label: '草稿', color: 'default' },
  submitted: { label: '待审批', color: 'processing' },
  approved: { label: '已通过', color: 'success' },
  rejected: { label: '已退回', color: 'error' },
  modifying: { label: '变更中', color: 'warning' },
  change_submitted: { label: '变更待审', color: 'processing' },
  cancelled: { label: '已取消', color: 'default' },
  archived: { label: '已归档', color: 'default' },
};

const TYPE_MAP = { weekly: '周巡检', monthly: '月巡检', quarterly: '季巡检', yearly: '年巡检' };
const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const ATTENTION_MAP = {
  overdue: '逾期执行',
  coverage: '漏站例外',
  resource: '资源阻塞',
};

function weekdayOf(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return isNaN(d.getTime()) ? '' : WEEK_CN[d.getDay()];
}

// 优先级评分 → 档位
function scoreLevel(score, tokens) {
  if (score >= 30) return { color: tokens.colorError, label: '高' };
  if (score >= 15) return { color: tokens.colorWarning, label: '中' };
  return { color: tokens.colorSuccess, label: '低' };
}

export default function PlanSchedulesPage() {
  const { tokens } = useTheme();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const userRoles = user?.roles || [user?.role];
  const canApprove = userRoles.some(role => role === 'admin' || role === 'manager');
  const isAdmin = userRoles.includes('admin');
  const canCleanup = userRoles.includes('admin');
  const canUseFavorites = userRoles.includes('operator');

  const [list, setList] = useState([]);
  const [teamOverview, setTeamOverview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [teamOverviewError, setTeamOverviewError] = useState('');
  const [followUpError, setFollowUpError] = useState('');
  const [followUpScope, setFollowUpScope] = useState(DEFAULT_FOLLOW_UP_SCOPE);
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') || undefined);
  const [attentionFilter, setAttentionFilter] = useState(searchParams.get('attention') || undefined);
  const [typeFilter, setTypeFilter] = useState(searchParams.get('type') || undefined);

  // 详情抽屉
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [suggestions, setSuggestions] = useState(null);
  const [validation, setValidation] = useState(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [acting, setActing] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelError, setCancelError] = useState('');
  const [cancelLoading, setCancelLoading] = useState(false);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgeReason, setPurgeReason] = useState('');
  const [purgeError, setPurgeError] = useState('');
  const [purgeLoading, setPurgeLoading] = useState(false);
  const [followUpRecommendations, setFollowUpRecommendations] = useState([]);
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const [followUpExpanded, setFollowUpExpanded] = useState(false);
  const [teamOverviewExpanded, setTeamOverviewExpanded] = useState(false);
  const [closingExecution, setClosingExecution] = useState(null);
  const [overdueCloseReason, setOverdueCloseReason] = useState('');
  const [overdueClosing, setOverdueClosing] = useState(false);
  const [favoritesOpen, setFavoritesOpen] = useState(false);
  const [favorites, setFavorites] = useState([]);
  const [favoriteId, setFavoriteId] = useState(undefined);
  const [favoriteStart, setFavoriteStart] = useState(null);
  const [favoriteLoading, setFavoriteLoading] = useState(false);
  const [favoritesError, setFavoritesError] = useState('');
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupLoading, setCleanupLoading] = useState(false);
  const [cleanupCandidates, setCleanupCandidates] = useState([]);
  const [cleanupSelected, setCleanupSelected] = useState([]);
  const [cleanupError, setCleanupError] = useState('');
  const [supplementTarget, setSupplementTarget] = useState(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editDraft, setEditDraft] = useState(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState('');
  const [editValidation, setEditValidation] = useState(null);
  const [editSites, setEditSites] = useState([]);
  const [editItemOptions, setEditItemOptions] = useState({});
  const [editItemsReady, setEditItemsReady] = useState(false);
  const [editItemError, setEditItemError] = useState('');
  const [editItemsReloadKey, setEditItemsReloadKey] = useState(0);
  const [editVehicles, setEditVehicles] = useState([]);
  const [editParts, setEditParts] = useState([]);
  const [editResourcesReady, setEditResourcesReady] = useState(false);
  const [newScheduleDate, setNewScheduleDate] = useState(null);
  const listRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const editorRequestRef = useRef(0);
  const cleanupRequestRef = useRef(0);
  const followUpRequestRef = useRef(0);
  const cleanupActionRef = useRef(false);
  const cancelActionRef = useRef(false);
  const purgeActionRef = useRef(false);
  const mountedRef = useRef(true);

  const loadList = useCallback(async () => {
    const requestId = ++listRequestRef.current;
    setLoading(true);
    setLoadError('');
    try {
      const params = [];
      if (statusFilter) params.push(`status=${statusFilter}`);
      if (typeFilter) params.push(`schedule_type=${typeFilter}`);
      if (attentionFilter) params.push(`attention=${attentionFilter}`);
      const rows = await api.getStrict('/plan-schedules' + (params.length ? '?' + params.join('&') : ''));
      if (mountedRef.current && requestId === listRequestRef.current) {
        setList(Array.isArray(rows) ? rows : []);
      }
    } catch (error) {
      if (mountedRef.current && requestId === listRequestRef.current) {
        setLoadError(error.message || '计划列表加载失败');
      }
    } finally {
      if (mountedRef.current && requestId === listRequestRef.current) setLoading(false);
    }
  }, [statusFilter, typeFilter, attentionFilter]);

  const loadCleanupCandidates = useCallback(async ({ notice = '' } = {}) => {
    const requestId = ++cleanupRequestRef.current;
    setCleanupLoading(true);
    try {
      const result = await api.getStrict('/admin/data-cleanup/candidates');
      if (!mountedRef.current || requestId !== cleanupRequestRef.current) return false;
      const candidates = Array.isArray(result?.candidates) ? result.candidates : [];
      setCleanupCandidates(candidates);
      setCleanupSelected(current => reconcileCleanupSelection(current, candidates));
      setCleanupError(notice);
      return true;
    } catch (error) {
      if (!mountedRef.current || requestId !== cleanupRequestRef.current) return false;
      setCleanupError([notice, error?.message || '清理候选加载失败'].filter(Boolean).join('；'));
      return false;
    } finally {
      if (mountedRef.current && requestId === cleanupRequestRef.current) setCleanupLoading(false);
    }
  }, []);

  const openCleanup = useCallback(async () => {
    setCleanupOpen(true);
    setCleanupSelected([]);
    setCleanupError('');
    await loadCleanupCandidates();
  }, [loadCleanupCandidates]);

  const applyCleanup = useCallback(async () => {
    if (!cleanupSelected.length || cleanupActionRef.current) return;
    cleanupActionRef.current = true;
    setCleanupLoading(true);
    try {
      await api.postStrict('/admin/data-cleanup/apply', { items: cleanupSelected });
      message.success(`已处理 ${cleanupSelected.length} 条无效记录`);
      setCleanupOpen(false);
      loadList();
    } catch (error) {
      if (error?.code === 'CLEANUP_CANDIDATE_CHANGED') {
        const notice = '候选已变化，所选记录均未处理；列表已重新加载，请重新确认';
        setCleanupSelected([]);
        await loadCleanupCandidates({ notice });
      } else {
        setCleanupError(error?.message || '处理失败，数据未改变，可保留当前选择后重试');
      }
    } finally {
      cleanupActionRef.current = false;
      if (mountedRef.current) setCleanupLoading(false);
    }
  }, [cleanupSelected, loadCleanupCandidates, loadList]);

  const loadOverview = useCallback(async () => {
    if (!canApprove) return;
    try {
      setTeamOverview(await api.getStrict('/plan-schedules/overview'));
      setTeamOverviewError('');
    } catch (error) {
      setTeamOverviewError(error?.message || '团队概览加载失败');
    }
  }, [canApprove]);

  const loadFollowUps = useCallback(async () => {
    const requestId = ++followUpRequestRef.current;
    const result = await loadFollowUpRecommendations(
      api,
      followUpScope,
      () => mountedRef.current && requestId === followUpRequestRef.current,
    );
    if (!result) return;
    if (result.followUpRecommendations !== null) setFollowUpRecommendations(result.followUpRecommendations);
    setFollowUpError(result.error);
  }, [followUpScope]);

  const refreshAll = useCallback(() => {
    loadList();
    loadOverview();
    loadFollowUps();
  }, [loadList, loadOverview, loadFollowUps]);

  const handleFollowUpRecommendation = async (item) => {
    setFollowUpLoading(true);
    try {
      const created = await api.postStrict(
        '/plan-schedules/follow-up-recommendations',
        followUpRecommendationActionPayload(followUpScope, item),
      );
      if (followUpScope === 'team') {
        message.success(created?.notified ? '已通知负责人' : '负责人已有未读通知，无需重复通知');
      } else {
        message.success('已生成复查草稿；仍需确认资源并提交审批');
      }
      refreshAll();
      if (created?.schedule?.id) openDetail(created.schedule.id);
    } catch (error) {
      message.error(error?.message || (followUpScope === 'team'
        ? '通知失败，请刷新后重试' : '生成草稿失败，请刷新后重试'));
    } finally {
      setFollowUpLoading(false);
    }
  };

  const openFavorites = async () => {
    setFavoritesOpen(true);
    setFavoriteLoading(true);
    setFavoritesError('');
    try {
      const rows = await api.getStrict('/plan-schedule-favorites');
      const items = Array.isArray(rows) ? rows : [];
      setFavorites(items);
      const first = items[0];
      setFavoriteId(first?.id);
      setFavoriteStart(first?.suggested_period_start ? dayjs(first.suggested_period_start) : null);
    } catch (error) {
      setFavoritesError(error.message || '常用计划加载失败');
    } finally {
      setFavoriteLoading(false);
    }
  };

  const selectFavorite = (id) => {
    setFavoriteId(id);
    const item = favorites.find(favorite => favorite.id === id);
    setFavoriteStart(item?.suggested_period_start ? dayjs(item.suggested_period_start) : null);
  };

  const addFavorite = async (schedule, event) => {
    event?.stopPropagation();
    try {
      await api.postStrict('/plan-schedule-favorites', { schedule_id: schedule.id });
      message.success('已加入常用计划');
    } catch (error) { message.error(error.message || '收藏失败'); }
  };

  const createFavoriteDraft = async () => {
    if (!favoriteId || !favoriteStart) { message.warning('请选择常用计划和新周期开始日期'); return; }
    setFavoriteLoading(true);
    try {
      const created = await api.postStrict(`/plan-schedule-favorites/${favoriteId}/draft`, { period_start: favoriteStart.format('YYYY-MM-DD') });
      message.success('已从常用计划生成可编辑草稿');
      setFavoritesOpen(false);
      refreshAll();
      if (created?.schedule?.id) openDetail(created.schedule.id);
    } catch (error) { message.error(error.message || '生成草稿失败'); }
    finally { setFavoriteLoading(false); }
  };

  const deleteFavorite = async () => {
    if (!favoriteId) return;
    setFavoriteLoading(true);
    try {
    await api.deleteStrict(`/plan-schedule-favorites/${favoriteId}`);
    const rows = await api.getStrict('/plan-schedule-favorites');
    const items = Array.isArray(rows) ? rows : [];
    setFavorites(items);
    const first = items[0];
    setFavoriteId(first?.id);
    setFavoriteStart(first?.suggested_period_start ? dayjs(first.suggested_period_start) : null);
    setFavoriteLoading(false);
    message.success('已删除收藏模板，不影响原计划');
    } catch (error) { message.error(error.message || '删除收藏失败'); setFavoriteLoading(false); }
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      listRequestRef.current += 1;
      detailRequestRef.current += 1;
    };
  }, []);

  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => { loadOverview(); loadFollowUps(); }, [loadOverview, loadFollowUps]);

  useEffect(() => {
    setStatusFilter(searchParams.get('status') || undefined);
    setAttentionFilter(searchParams.get('attention') || undefined);
    setTypeFilter(searchParams.get('type') || undefined);
  }, [searchParams]);

  const updateFilter = useCallback((key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const resetFilters = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    ['status', 'type', 'attention'].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  // 打开详情：计划详情 + 智能建议 + 校验结果（审批决策支撑三件套）
  const openDetail = useCallback(async (id) => {
    const requestId = ++detailRequestRef.current;
    setDrawerOpen(true);
    setDetailLoading(true);
    setDetail(null);
    setSuggestions(null);
    setValidation(null);
    try {
      const det = await api.getStrict(`/plan-schedules/${id}`);
      if (!mountedRef.current || requestId !== detailRequestRef.current) return;
      setDetail(det);
      const siteIds = Object.keys(det?.site_map || {});
      if (siteIds.length > 0) {
        const sug = await api.getStrict(`/plan-schedules/suggestions?site_ids=${siteIds.join(',')}`);
        if (!mountedRef.current || requestId !== detailRequestRef.current) return;
        setSuggestions(sug);
      }
      const val = await api.postStrict('/plan-schedules/validate', buildPlanValidationPayload(det));
      if (!mountedRef.current || requestId !== detailRequestRef.current) return;
      setValidation(val);
    } catch (error) {
      if (mountedRef.current && requestId === detailRequestRef.current) {
        message.error(error.message || '计划详情加载失败');
      }
    } finally {
      if (mountedRef.current && requestId === detailRequestRef.current) setDetailLoading(false);
    }
  }, [message]);

  const openEditor = useCallback(async (schedule = detail) => {
    if (!schedule) return;
    const requestId = ++editorRequestRef.current;
    const scheduleVehicleId = schedule.vehicle_id || (() => {
      const ids = [...new Set(Object.values(schedule.vehicle_days || {}).filter(Boolean).map(Number))];
      return ids.length === 1 ? ids[0] : null;
    })();
    setEditOpen(true);
    setEditSaving(true);
    setEditError('');
    setEditValidation(null);
    setEditResourcesReady(false);
    setEditItemsReady(false);
    setEditItemError('');
    setNewScheduleDate(null);
    setEditDraft({
      id: schedule.id,
      version: Number(schedule.version || 1),
      user_id: schedule.user_id,
      schedule_type: schedule.schedule_type,
      period_start: schedule.period_start,
      period_end: schedule.period_end,
      plan_data: JSON.parse(JSON.stringify(schedule.plan_data || {})),
      vehicle_days: { ...(schedule.vehicle_days || {}) },
      vehicle_id: scheduleVehicleId,
      spare_parts: (schedule.spare_parts || []).map(part => ({ ...part })),
      work_order_ids: [...(schedule.work_order_ids || [])],
      remarks: schedule.remarks || '',
      coverage_exception_reason: schedule.coverage_exception_reason || '',
      vehicle_exception_reason: scheduleVehicleId ? '' : schedule.vehicle_exception_reason || '',
    });
    try {
      const requests = [api.getStrict('/sites'), api.getStrict('/vehicles'), api.getStrict('/parts/inventory')];
      if (canApprove) {
        requests.push(api.getStrict(`/users/${schedule.user_id}/sites`));
      }
      const [siteRows, vehicleRows, partRows, scopeResult] = await Promise.all(requests);
      if (!mountedRef.current || requestId !== editorRequestRef.current) return;
      const authorizedIds = scopeResult?.site_ids ? new Set(scopeResult.site_ids.map(Number)) : null;
      setEditSites((Array.isArray(siteRows) ? siteRows : siteRows?.sites || [])
        .filter(site => !authorizedIds || authorizedIds.has(Number(site.id))));
      setEditItemOptions({});
      setEditVehicles((Array.isArray(vehicleRows) ? vehicleRows : []).filter(vehicle => vehicle.status !== 'retired'));
      setEditParts(Array.isArray(partRows) ? partRows : []);
      setEditResourcesReady(true);
    } catch (error) {
      if (mountedRef.current && requestId === editorRequestRef.current) {
        setEditError(error?.message || '编辑所需的站点和资源加载失败');
      }
    } finally {
      if (mountedRef.current && requestId === editorRequestRef.current) setEditSaving(false);
    }
  }, [canApprove, detail, user?.id]);

  const selectedEditSiteIds = useMemo(() => [...new Set(Object.values(editDraft?.plan_data || {})
    .flatMap(day => Array.isArray(day?.sites) ? day.sites.map(Number) : []))], [editDraft?.plan_data]);

  useEffect(() => {
    if (!editOpen || !editDraft?.schedule_type) return undefined;
    if (!selectedEditSiteIds.length) {
      setEditItemOptions({});
      setEditItemError('');
      setEditItemsReady(true);
      return undefined;
    }
    let cancelled = false;
    setEditItemsReady(false);
    setEditItemError('');
    Promise.all(selectedEditSiteIds.map(async siteId => {
        const result = await api.getStrict(`/inspection-v2/configs/match?site_id=${siteId}&schedule_type=${encodeURIComponent(editDraft.schedule_type)}`);
        const items = Array.isArray(result?.items) ? result.items : [];
        const unique = [...new Map(items.map(item => [Number(item.id), item])).values()];
        return [siteId, unique];
    })).then(entries => {
      if (cancelled || !mountedRef.current) return;
      const options = Object.fromEntries(entries);
      setEditItemOptions(options);
      setEditItemsReady(true);
      setEditDraft(current => {
        if (!current) return current;
        let changed = false;
        const planData = Object.fromEntries(Object.entries(current.plan_data || {}).map(([date, day]) => {
          const selected = { ...(day?.inspection_items || {}) };
          (day?.sites || []).forEach(siteId => {
            const key = String(siteId);
            if (!(key in selected)) {
              selected[key] = (options[Number(siteId)] || []).map(item => Number(item.id));
              changed = true;
            }
          });
          return [date, { ...day, inspection_items: selected }];
        }));
        return changed ? { ...current, plan_data: planData } : current;
      });
    }).catch(error => {
      if (cancelled || !mountedRef.current) return;
      setEditItemOptions({});
      setEditItemError(error?.message || '站点检查项加载失败，请重试后再保存计划');
      setEditItemsReady(false);
    });
    return () => { cancelled = true; };
  }, [editOpen, editDraft?.schedule_type, selectedEditSiteIds.join(','), editItemsReloadKey]);

  const updateEditDay = useCallback((date, patch) => {
    setEditDraft(current => current ? {
      ...current,
      plan_data: {
        ...current.plan_data,
        [date]: { ...(current.plan_data?.[date] || { sites: [], notes: '', inspection_items: {} }), ...patch },
      },
    } : current);
  }, [isAdmin]);

  const updateEditSiteItems = useCallback((date, siteId, itemIds) => {
    setEditDraft(current => {
      if (!current) return current;
      const day = current.plan_data?.[date] || { sites: [], notes: '' };
      return {
        ...current,
        plan_data: {
          ...current.plan_data,
          [date]: {
            ...day,
            inspection_items: {
              ...(day.inspection_items || {}),
              [String(siteId)]: (itemIds || []).map(Number).filter(Number.isInteger),
            },
          },
        },
      };
    });
  }, []);

  const removeEditDay = useCallback((date) => {
    setEditDraft(current => {
      if (!current) return current;
      const planData = { ...current.plan_data };
      const vehicleDays = { ...current.vehicle_days };
      delete planData[date];
      delete vehicleDays[date];
      return { ...current, plan_data: planData, vehicle_days: vehicleDays };
    });
  }, []);

  const addEditDay = useCallback(() => {
    if (!editDraft || !newScheduleDate) return;
    const date = newScheduleDate.format('YYYY-MM-DD');
    if (date < editDraft.period_start || date > editDraft.period_end) {
      setEditError('安排日期必须位于计划周期内');
      return;
    }
    updateEditDay(date, {});
    setNewScheduleDate(null);
    setEditError('');
  }, [editDraft, newScheduleDate, updateEditDay]);

  const setPlanVehicle = useCallback((vehicleId) => {
    setEditDraft(current => applyPlanVehicleSelection(current, vehicleId));
  }, []);

  const updateEditPart = useCallback((index, patch) => {
    setEditDraft(current => current ? {
      ...current,
      spare_parts: current.spare_parts.map((part, partIndex) => partIndex === index ? { ...part, ...patch } : part),
    } : current);
  }, []);

  const removeEditPart = useCallback((index) => {
    setEditDraft(current => current ? {
      ...current,
      spare_parts: current.spare_parts.filter((_, partIndex) => partIndex !== index),
    } : current);
  }, []);

  const addEditPart = useCallback(() => {
    const selected = new Set((editDraft?.spare_parts || []).map(part => Number(part.part_id)));
    const available = editParts.find(part => !selected.has(Number(part.id)));
    if (!available) {
      setEditError(editParts.length ? '可用备件均已加入计划' : '暂无可用备件');
      return;
    }
    setEditDraft(current => current ? {
      ...current,
      spare_parts: [...current.spare_parts, {
        part_id: Number(available.id), part_name: available.part_name, quantity: 1,
      }],
    } : current);
    setEditError('');
  }, [editDraft?.spare_parts, editParts]);

  const saveEdit = useCallback(async (submitAfterSave = false) => {
    if (!editDraft || editSaving || !editResourcesReady) return;
    if (!editItemsReady) {
      setEditError(editItemError || '站点检查项尚未加载完成，请稍后重试');
      return;
    }
    const normalizedPlan = Object.fromEntries(Object.entries(editDraft.plan_data || {})
      .filter(([, day]) => Array.isArray(day?.sites) && day.sites.length > 0)
      .map(([date, day]) => {
        const sites = [...new Set(day.sites.map(Number))];
        const inspectionItems = Object.fromEntries(Object.entries(day.inspection_items || {})
          .filter(([siteId, ids]) => sites.includes(Number(siteId)) && Array.isArray(ids))
          .map(([siteId, ids]) => [String(siteId), [...new Set(ids.map(Number).filter(Number.isInteger))]]));
        return [date, { sites, notes: String(day.notes || '').trim(), inspection_items: inspectionItems }];
      }));
    const plannedDates = new Set(Object.keys(normalizedPlan));
    const normalizedVehicleDays = Object.fromEntries(Object.entries(editDraft.vehicle_days || {})
      .filter(([date, vehicleId]) => plannedDates.has(date) && vehicleId));
    const payload = { ...editDraft, plan_data: normalizedPlan, vehicle_days: normalizedVehicleDays };
    setEditSaving(true);
    setEditError('');
    try {
      const validationResult = await api.postStrict('/plan-schedules/validate', {
        ...payload,
        vehicle_id: payload.vehicle_id || null,
        exclude_schedule_id: payload.id,
      });
      setEditValidation(validationResult);
      if (submitAfterSave && validationResult?.errors?.length) {
        setEditError(validationResult.errors.join('；'));
        return;
      }
      const saved = await api.putStrict(`/plan-schedules/${payload.id}`, payload);
      setEditDraft(current => current ? {
        ...current,
        version: saved.version,
        plan_data: normalizedPlan,
        vehicle_days: saved.vehicle_days || normalizedVehicleDays,
      } : current);
      if (submitAfterSave) {
        try {
          await api.postStrict(`/plan-schedules/${payload.id}/submit`, { version: saved.version });
        } catch (error) {
          setEditError(`草稿已保存，但提交失败：${error?.message || '请检查计划内容后重试'}`);
          refreshAll();
          await openDetail(payload.id);
          return;
        }
      }
      const issueCount = Number(saved?.draft_issue_count || 0);
      message.success(submitAfterSave
        ? '计划已保存并提交审批'
        : issueCount > 0 ? `草稿已保存，仍有 ${issueCount} 项待完善` : '计划草稿已保存');
      setEditOpen(false);
      refreshAll();
      await openDetail(payload.id);
    } catch (error) {
      setEditError(error?.message || '计划保存失败，请稍后重试');
    } finally {
      setEditSaving(false);
    }
  }, [editDraft, editItemError, editItemsReady, editResourcesReady, editSaving, message, openDetail, refreshAll]);

  useEffect(() => {
    const scheduleId = Number(searchParams.get('schedule'));
    if (Number.isInteger(scheduleId) && scheduleId > 0) openDetail(scheduleId);
  }, [searchParams, openDetail]);

  useEffect(() => {
    const reworkPlanId = searchParams.get('rework_plan');
    const focusItemId = Number(searchParams.get('focus_item'));
    const focusSiteId = Number(searchParams.get('site_id'));
    if (!reworkPlanId) return undefined;
    let cancelled = false;
    resolveReworkScheduleId(
      reworkPlanId,
      planId => api.getStrict(`/inspection-v2/plans/${planId}`),
    ).then(scheduleId => {
      if (cancelled) return;
      if (!scheduleId) {
        if (Number.isInteger(focusItemId) && focusItemId > 0 && Number.isInteger(focusSiteId) && focusSiteId > 0) {
          api.getStrict(`/inspection-v2/plans/${reworkPlanId}`).then((plan) => {
            if (cancelled) return;
            const item = (plan?.items || []).find(candidate => Number(candidate.id) === focusItemId
              && Number(candidate.site_id) === focusSiteId);
            if (!item) {
              message.error('补传通知对应的检查项不存在或当前账号无权查看');
              return;
            }
            setSupplementTarget({ planId: Number(reworkPlanId), itemId: focusItemId, siteId: focusSiteId, itemName: item.item_name || `检查项 #${focusItemId}` });
          }).catch(() => {
            if (!cancelled) message.error('补传通知对应的计划不存在或当前账号无权查看');
          });
          return;
        }
        message.error('整改执行包未关联巡检排程，无法打开计划详情');
        return;
      }
      setSearchParams(replaceReworkWithSchedule(searchParams, scheduleId), { replace: true });
    }).catch(error => {
      if (!cancelled) message.error(error?.message || '整改计划详情加载失败');
    });
    return () => { cancelled = true; };
  }, [searchParams, setSearchParams]);

  const handleOverdueAction = async (task, action, reason = '') => {
    try {
      const result = await api.postStrict(`/insp-plans/${task.id}/overdue-action`, { action, reason });
      message.success(action === 'remind' ? '已发送逾期催办' : `已异常关闭，取消 ${result.cancelled_items || 0} 个未完成检查项`);
      refreshAll();
      if (detail?.id) openDetail(detail.id);
      return true;
    } catch (error) {
      message.error(error.message || '逾期任务处置失败');
      return false;
    }
  };

  const closeOverdueExecution = (task) => {
    setClosingExecution(task);
    setOverdueCloseReason('');
  };

  const submitOverdueClose = async () => {
    const reason = overdueCloseReason.trim();
    if (!reason) {
      message.error('请填写未执行原因');
      return;
    }
    setOverdueClosing(true);
    const succeeded = await handleOverdueAction(closingExecution, 'close', reason);
    setOverdueClosing(false);
    if (succeeded) {
      setClosingExecution(null);
      setOverdueCloseReason('');
    }
  };

  const onApprove = async (id) => {
    setActing(true);
    try {
      const res = await api.postStrict(`/plan-schedules/${id}/approve`);
      if (res.is_change) {
        message.success(`变更已通过：保留${res.kept || 0}个已执行任务、重建${res.plans_created || 0}个`);
      } else {
        message.success(`审批通过：已生成${res.plans_created || 0}个巡检任务、锁定${res.vehicle_locked || 0}天用车、记录${res.parts_planned || 0}类备件需求（现场领用时扣库）`);
      }
      refreshAll();
      openDetail(id);
    } catch (error) { message.error(error.message || '审批失败'); } finally {
      setActing(false);
    }
  };

  const onReject = async () => {
    if (!rejectReason.trim()) { message.warning('请填写退回原因'); return; }
    setActing(true);
    try {
      const res = await api.postStrict(`/plan-schedules/${detail.id}/reject`, { reason: rejectReason.trim() });
      message.success(res.rolled_back ? '已驳回变更，恢复原计划' : '已退回，排程人将收到通知');
      setRejectOpen(false);
      setRejectReason('');
      refreshAll();
      openDetail(detail.id);
    } catch (error) { message.error(error.message || '退回失败'); } finally {
      setActing(false);
    }
  };

  const openPlanCancellation = () => {
    if (!canCancelPlanSchedule(detail, user) || cancelActionRef.current) return;
    setCancelReason('');
    setCancelError('');
    setCancelOpen(true);
  };

  const submitPlanCancellation = async () => {
    const normalized = normalizePlanCancelReason(cancelReason);
    if (normalized.error) {
      setCancelError(normalized.error);
      return;
    }
    if (!detail || !canCancelPlanSchedule(detail, user) || cancelActionRef.current) return;
    const scheduleId = detail.id;
    const version = Number(detail.version || 1);
    cancelActionRef.current = true;
    setCancelReason(normalized.value);
    setCancelError('');
    setCancelLoading(true);
    try {
      await api.postStrict(`/plan-schedules/${detail.id}/cancel`, { reason: normalized.value, version });
      message.success('计划已取消');
      setCancelOpen(false);
      setCancelReason('');
      refreshAll();
      await openDetail(scheduleId);
    } catch (error) {
      setCancelError(error?.message || '取消计划失败，请稍后重试');
    } finally {
      cancelActionRef.current = false;
      if (mountedRef.current) setCancelLoading(false);
    }
  };

  const openPlanPurge = () => {
    if (!detail || !isAdmin || purgeActionRef.current) return;
    setPurgeReason('');
    setPurgeError('');
    setPurgeOpen(true);
  };

  const submitPlanPurge = async () => {
    const normalized = normalizePlanPurgeReason(purgeReason);
    if (normalized.error) {
      setPurgeError(normalized.error);
      return;
    }
    if (!detail || !isAdmin || purgeActionRef.current) return;
    purgeActionRef.current = true;
    setPurgeReason(normalized.value);
    setPurgeError('');
    setPurgeLoading(true);
    try {
      await api.postStrict(`/plan-schedules/${detail.id}/purge`, {
        reason: normalized.value,
        version: Number(detail.version || 1),
      });
      message.success('无效计划已彻底删除');
      setPurgeOpen(false);
      setPurgeReason('');
      detailRequestRef.current += 1;
      setDrawerOpen(false);
      setDetail(null);
      refreshAll();
    } catch (error) {
      setPurgeError(error?.message || '彻底删除失败，请稍后重试');
    } finally {
      purgeActionRef.current = false;
      if (mountedRef.current) setPurgeLoading(false);
    }
  };

  // 顶部指标
  const stats = useMemo(() => ({
    draft: list.filter(r => r.status === 'draft').length,
    submitted: list.filter(r => r.status === 'submitted').length,
    approved: list.filter(r => r.status === 'approved').length,
    rejected: list.filter(r => r.status === 'rejected').length,
    modifying: list.filter(r => r.status === 'modifying').length,
    changeSubmitted: list.filter(r => r.status === 'change_submitted').length,
    archived: list.filter(r => r.status === 'archived').length,
    total: list.length,
  }), [list]);

  const teamColumns = [
    { title: '人员', dataIndex: 'real_name', width: 100, render: v => <Text strong>{v}</Text> },
    { title: '当前计划', dataIndex: 'approved_schedules', width: 90, align: 'center', render: v => v ? <Tag color="blue">{v}项</Tag> : '—' },
    { title: '今日巡检', width: 150, render: (_, r) => r.today_items ? <Text>{r.completed_items}/{r.today_items}（{r.completion_rate}%）</Text> : <Text type="secondary">无安排</Text> },
    { title: '现场异常', dataIndex: 'abnormal_items', width: 90, align: 'center', render: v => v ? <Tag color="red">{v}</Tag> : '0' },
    { title: '未闭环工单', dataIndex: 'open_workorders', width: 110, align: 'center', render: v => v ? <Tag color="orange">{v}</Tag> : '0' },
    { title: '逾期执行', dataIndex: 'overdue_executions', width: 90, align: 'center', render: v => v ? <Tag color="red">{v}</Tag> : '0' },
  ];

  // 详情内：生成后的日期×站点事实优先；未生成的日期才展示排程站点。
  const generatedSiteTasks = detail?.generated_site_tasks || [];
  const itineraryRows = useMemo(
    () => planDetailItineraryRows(detail?.plan_data || {}, generatedSiteTasks),
    [detail, generatedSiteTasks],
  );
  const hasScheduledSites = Number(detail?.site_count || 0) > 0 || itineraryRows.length > 0;
  const generatedPlanCount = generatedSiteTasks.length;
  const executionPackagesByDate = useMemo(
    () => groupExecutionPackagesByDate(generatedSiteTasks),
    [generatedSiteTasks],
  );
  const showPreExecutionRisks = shouldShowPreExecutionRisks(detail || {});
  const shouldWarnMissingExecution = detail?.status === 'approved'
    && planExecutionPresentation(detail).key !== 'completed';
  const canCancelDetail = canCancelPlanSchedule(detail, user);
  const canEditDetail = detail && ['draft', 'rejected', 'modifying'].includes(detail.status)
    && (Number(detail.user_id) === Number(user?.id) || canApprove);
  const canReviewDetail = detail && canApprove
    && (detail.status === 'submitted' || detail.status === 'change_submitted');
  const canPurgeDetail = Boolean(detail && isAdmin);

  // 详情内：风险预警汇总（校验警告 + 高危排序提示）
  const riskWarnings = useMemo(() => {
    if (!showPreExecutionRisks) return [];
    const warns = [];
    (validation?.warnings || []).forEach(w => warns.push({ type: 'coverage', text: w }));
    (validation?.errors || []).forEach(w => warns.push({ type: 'conflict', text: w }));
    // 高危站点排在周期后半段 → 提示
    if (detail && suggestions?.site_scores && itineraryRows.length >= 2) {
      const midDate = itineraryRows[Math.floor(itineraryRows.length / 2)].date;
      itineraryRows.forEach(row => {
        if (row.date < midDate) return;
        itineraryRowSiteIds(row).forEach(sid => {
          const score = suggestions.site_scores[String(sid)] || 0;
          if (score >= 30) {
            const name = detail.site_map?.[sid]?.name || `站点${sid}`;
            warns.push({ type: 'priority', text: `${name}优先级高（评分${score}）但排在${row.date}（${weekdayOf(row.date)}），建议提前` });
          }
        });
      });
    }
    return warns;
  }, [showPreExecutionRisks, validation, suggestions, detail, itineraryRows]);

  const columns = [
    { title: '排程人', dataIndex: 'user_name', width: 90 },
    {
      title: '类型', dataIndex: 'schedule_type', width: 80,
      render: v => <Tag color={v === 'weekly' ? 'blue' : 'purple'}>{TYPE_MAP[v] || v}</Tag>,
    },
    {
      title: '周期与覆盖', width: 230,
      render: (_, r) => (
        <span><CalendarOutlined style={{ color: tokens.colorTextTertiary, marginRight: 4 }} />
          <Text style={{ fontSize: 12 }}>{r.period_start} ~ {r.period_end}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}> · {r.day_count}天 {r.site_count}站</Text></span>
      ),
    },
    {
      title: '资源 / 风险', width: 160,
      render: (_, r) => {
        const hasNoExecution = Number(r.day_count || 0) === 0 || Number(r.site_count || 0) === 0;
        if (hasNoExecution) return <Tag color="error">无执行内容</Tag>;
        const n = Object.keys(r.vehicle_days || {}).length;
        return <Space size={4}>{n > 0 && <><CarOutlined /><Text style={{ fontSize: 12 }}>{n}天</Text></>}
          {r.attention_reason ? <Tag color="red">{r.attention_reason}</Tag> : n === 0 && <Text type="secondary">无关注项</Text>}</Space>;
      },
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: v => {
        const s = SCHEDULE_STATUS_MAP[v] || { label: v, color: 'default' };
        return <Badge status={s.color} text={s.label} />;
      },
    },
    {
      title: '现场', dataIndex: 'execution_status', width: 110,
      render: (_, record) => {
        const s = planExecutionPresentation(record);
        return <Badge status={s.color} text={s.label} />;
      },
    },
    {
      title: '操作', width: 130,
      render: (_, r) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<FileSearchOutlined />}
            aria-label={`${r.status === 'submitted' || r.status === 'change_submitted' ? '审阅' : '查看'}${r.user_name}的${TYPE_MAP[r.schedule_type] || '巡检'}计划`}
            onClick={() => openDetail(r.id)}
          >
            {r.status === 'submitted' || r.status === 'change_submitted' ? '审阅' : '详情'}
          </Button>
          {canUseFavorites && Number(r.user_id) === Number(user?.id) && Number(r.site_count || 0) > 0 && (
            <Tooltip title="收藏站点、相对日期、车辆与备件">
              <Button size="small" icon={<StarOutlined />} onClick={event => addFavorite(r, event)}>收藏</Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  const scoreBadge = (sid) => {
    const score = suggestions?.site_scores?.[String(sid)];
    const reasons = suggestions?.site_reasons?.[String(sid)] || [];
    const presentation = sitePriorityPresentation(showPreExecutionRisks, score, reasons);
    if (!presentation) return null;
    const color = presentation.tone === 'warning'
      ? tokens.colorWarning
      : scoreLevel(presentation.score, tokens).color;
    return (
      <Tooltip title={presentation.tooltip}>
        <Tag style={{ marginLeft: 4, color, borderColor: color, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
          {presentation.label}
        </Tag>
      </Tooltip>
    );
  };

  const hasTeamAttention = canApprove && teamOverview && [
    teamOverview.summary?.overdue_executions,
    teamOverview.summary?.coverage_exceptions,
    teamOverview.summary?.resource_blocks,
  ].some(Boolean);
  const attentionActions = [
    { key: 'overdue', label: '逾期执行', count: teamOverview?.summary?.overdue_executions || 0, danger: true },
    { key: 'coverage', label: '漏站例外', count: teamOverview?.summary?.coverage_exceptions || 0 },
    { key: 'resource', label: '资源阻塞', count: teamOverview?.summary?.resource_blocks || 0, danger: true },
  ].filter(item => item.count > 0);

  return (
    <WorkspacePage
      title="巡检计划"
      subtitle="按周期编排站点、车辆和现场资源；审批通过后生成可执行任务。"
      primaryAction={<Space>
        {canUseFavorites && <Button type="primary" icon={<FolderOpenOutlined />} onClick={openFavorites}>从常用计划生成草稿</Button>}
        {canCleanup && <Button icon={<DeleteOutlined />} danger onClick={openCleanup}>清理无效数据</Button>}
      </Space>}
      statusItems={[
        { key: 'total', label: '当前结果', value: stats.total, color: tokens.colorText, always: true },
        { key: 'draft', label: '草稿', value: stats.draft, color: tokens.colorTextSecondary },
        { key: 'submitted', label: '待审批', value: stats.submitted, color: tokens.colorWarning },
        { key: 'modifying', label: '变更中', value: stats.modifying, color: tokens.colorWarning },
        { key: 'change-submitted', label: '变更待审', value: stats.changeSubmitted, color: tokens.colorWarning },
        { key: 'approved', label: '已通过', value: stats.approved, color: tokens.colorSuccess },
        { key: 'rejected', label: '已退回', value: stats.rejected, color: tokens.colorError },
        { key: 'archived', label: '已归档', value: stats.archived, color: tokens.colorTextSecondary },
      ]}
    >
      {followUpError && <Alert type="warning" showIcon message={followUpError} action={<Button size="small" onClick={loadFollowUps}>重试</Button>} />}
      {teamOverviewError && canApprove && <Alert type="warning" showIcon message="团队执行概览加载失败，当前不能判断是否没有关注事项" action={<Button size="small" onClick={loadOverview}>重试</Button>} />}
      {supplementTarget && <Alert type="warning" showIcon
        message={`检查项待补传：${supplementTarget.itemName}（计划 #${supplementTarget.planId}，检查项 #${supplementTarget.itemId}）`}
        description="该网页暂不提供补传入口；请在小程序巡检中打开同一计划和站点完成补传。" />}

      {(isAdmin || followUpRecommendations.length > 0) && (
        <Alert
          type={followUpRecommendations.length > 0 ? "warning" : "info"}
          showIcon
          icon={<BulbOutlined />}
          message={followUpScope === 'team' ? '团队系统性异常复查' : '我的系统性异常复查'}
          action={<Space size={4} wrap>
            {isAdmin && <Button size="small" type="link" onClick={() => {
              setFollowUpRecommendations([]); setFollowUpError('');
              setFollowUpScope(scope => scope === 'team' ? 'mine' : 'team');
            }}>{followUpScope === 'team' ? '返回我的复查' : '查看团队复查'}</Button>}
            <Button size="small" type="link" onClick={() => setFollowUpExpanded(value => !value)}>
              {followUpExpanded ? '收起' : `查看 ${followUpRecommendations.length} 条`}
            </Button>
          </Space>}
          description={followUpExpanded && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {!followUpRecommendations.length && <Text type="secondary">当前范围暂无系统性异常复查建议</Text>}
              {followUpRecommendations.map(item => (
                <div key={`follow-up-${item.user_id}-${item.site_id}-${item.anomaly_type}`}>
                  <Text style={{ fontSize: 12 }}>复查建议：{item.user_name} · {item.site_name} · {item.anomaly_type}</Text>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                    {item.window_days} 天内出现 {item.occurrence_count} 次；最近一次 {item.latest_at}；建议由负责人确认本周执行日期
                  </Text>
                  <Button size="small" type="link" danger loading={followUpLoading}
                    onClick={() => handleFollowUpRecommendation(item)}>
                    {followUpScope === 'team' ? '通知负责人' : '生成复查草稿'}
                  </Button>
                </div>
              ))}
            </div>
          )}
        />
      )}

      {hasTeamAttention && (
        <>
          <section className="plan-attention-strip" aria-label="需要处理的巡检事项">
            <span className="plan-attention-strip__label"><ExclamationCircleOutlined /> 需要处理</span>
            <Space size={8} wrap>
              {attentionActions.map(item => (
                <Button key={item.key} size="small" danger={item.danger}
                  onClick={() => updateFilter('attention', item.key)}>
                  {item.label} {item.count}
                </Button>
              ))}
            </Space>
          </section>
          <Card size="small" title={`团队执行概览 · ${teamOverview.date}`} styles={{ body: { padding: teamOverviewExpanded ? 0 : '0 12px' } }}
            extra={<Button size="small" type="link" onClick={() => setTeamOverviewExpanded(value => !value)}>{teamOverviewExpanded ? '收起' : '展开人员明细'}</Button>}>
            {teamOverviewExpanded
              ? <Table rowKey="user_id" size="small" pagination={false} columns={teamColumns}
                  dataSource={teamOverview.people || []} scroll={{ x: 650 }}
                  locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无人员执行数据" /> }} />
              : <Text type="secondary" style={{ fontSize: 12 }}>按需展开查看每位人员的今日巡检、现场异常和未闭环工单。</Text>}
          </Card>
        </>
      )}

      <section className="plan-list-section" aria-label="巡检计划列表">
        <WorkspaceToolbar actions={<Space size={8}>
          {(statusFilter || typeFilter || attentionFilter) && (
            <Button icon={<ClearOutlined />} onClick={resetFilters}>重置筛选</Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={refreshAll}>刷新</Button>
        </Space>}>
          <FilterField label="计划状态">
            <Select allowClear placeholder="全部状态" style={{ width: filterSmallSelectWidth }} value={statusFilter} onChange={(value) => updateFilter('status', value)}
              options={Object.entries(SCHEDULE_STATUS_MAP).map(([k, v]) => ({ value: k, label: v.label }))} />
          </FilterField>
          <FilterField label="计划类型">
            <Select allowClear placeholder="全部类型" style={{ width: filterSelectWidth }} value={typeFilter} onChange={(value) => updateFilter('type', value)}
              options={Object.entries(TYPE_MAP).map(([k, v]) => ({ value: k, label: v }))} />
          </FilterField>
          {attentionFilter && <ToolbarMeta label="关注条件"><Tag closable onClose={() => updateFilter('attention', undefined)}>{ATTENTION_MAP[attentionFilter]}</Tag></ToolbarMeta>}
        </WorkspaceToolbar>

        {loadError && !loading ? <WorkspaceEmpty type="error" description="巡检计划加载失败，当前不能判断是否暂无计划。" onRefresh={loadList} /> : <WorkspaceTable
          rowKey="id"
          dataSource={list}
          loading={loading}
          columns={columns}
          emptyType={statusFilter || typeFilter || attentionFilter ? 'filtered' : 'empty'}
          onRefresh={loadList}
          onRow={r => ({ onClick: () => openDetail(r.id), style: { cursor: 'pointer' } })}
        />}
      </section>

      {/* 详情抽屉：审批决策支撑（风险预警 + 站点情况 + 行程 + 资源 + 任务） */}
      <Drawer
        title={detail ? `${detail.user_name || ''}的${TYPE_MAP[detail.schedule_type] || '巡检'}计划（${detail.period_start} ~ ${detail.period_end}）` : '计划详情'}
        open={drawerOpen} onClose={() => {
          if (cancelLoading) return;
          detailRequestRef.current += 1;
          setDrawerOpen(false);
        }} width={680} destroyOnHidden closable={!cancelLoading}
        footer={detail && (canCancelDetail || canEditDetail || canReviewDetail || canPurgeDetail) ? (
          <div className="plan-detail-actions">
            {canPurgeDetail && (
              <Button danger icon={<DeleteOutlined />} onClick={openPlanPurge}
                loading={purgeLoading}>彻底删除无效计划</Button>
            )}
            {canCancelDetail && (
              <Button danger icon={<CloseOutlined />} onClick={openPlanCancellation}
                loading={cancelLoading}>取消计划</Button>
            )}
            {canEditDetail && (
              <Button type="primary" icon={<EditOutlined />} onClick={() => openEditor(detail)}>编辑计划</Button>
            )}
            {canReviewDetail && (
              <>
                <Button danger icon={<CloseOutlined />} onClick={() => setRejectOpen(true)} loading={acting}>退回</Button>
                <Button type="primary" icon={<CheckOutlined />} onClick={() => onApprove(detail.id)} loading={acting}>审批通过</Button>
              </>
            )}
          </div>
        ) : null}
      >
        {detailLoading && <div style={{ textAlign: 'center', padding: 40 }}>加载中…</div>}
        {!detailLoading && detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {!hasScheduledSites ? (
              <Alert type="warning" showIcon message="该排程尚未安排站点，无法生成现场执行任务。请补充站点后重新提交。" />
            ) : shouldWarnMissingExecution && generatedPlanCount === 0 ? (
              <Alert type="error" showIcon message={`该排程已安排 ${detail.site_count} 个站点，但尚未生成现场执行任务。请联系管理员核对审批流转。`} />
            ) : null}
            {/* 基本信息 */}
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="排程人">{detail.user_name}</Descriptions.Item>
              <Descriptions.Item label="排程状态">
                <Badge status={(SCHEDULE_STATUS_MAP[detail.status] || {}).color} text={(SCHEDULE_STATUS_MAP[detail.status] || {}).label || detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label="周期">{detail.period_start} ~ {detail.period_end}</Descriptions.Item>
              <Descriptions.Item label="执行进度">
                <Badge status={planExecutionPresentation(detail).color} text={planExecutionPresentation(detail).label} />
              </Descriptions.Item>
              {Number(detail.version || 1) > 1 && (
                <Descriptions.Item label="变更版本"><Text type="secondary">v{detail.version}</Text></Descriptions.Item>
              )}
              {detail.submitted_at && (
                <Descriptions.Item label="提交时间"><Text type="secondary">{detail.submitted_at}</Text></Descriptions.Item>
              )}
              {detail.approver_name && (
                <Descriptions.Item label="审批人" span={2}><Text type="secondary">{detail.approver_name}</Text></Descriptions.Item>
              )}
              {detail.remarks?.trim() && (
                <Descriptions.Item label="备注" span={2}>{detail.remarks}</Descriptions.Item>
              )}
              {detail.coverage_exception_reason && (
                <Descriptions.Item label="漏站例外说明" span={2}>
                  <Text type="warning">{detail.coverage_exception_reason}</Text>
                </Descriptions.Item>
              )}
              {detail.status === 'rejected' && detail.reject_reason && (
                <Descriptions.Item label="退回原因" span={2}>
                  <Text type="danger">{detail.reject_reason}</Text>
                </Descriptions.Item>
              )}
              {(detail.status === 'modifying' || detail.status === 'change_submitted') && detail.change_reason && (
                <Descriptions.Item label="变更原因" span={2}>
                  <Text type="warning">{detail.change_reason}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>

            {/* 风险预警 */}
            {riskWarnings.length > 0 && (
              <div>
                <Text strong style={{ fontSize: 13 }}>风险预警</Text>
                <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {riskWarnings.slice(0, 3).map((w, i) => (
                    <Alert key={i} type={w.type === 'conflict' ? 'error' : 'warning'} showIcon
                      icon={<ExclamationCircleOutlined />} message={<span style={{ fontSize: 12 }}>{w.text}</span>} />
                  ))}
                  {riskWarnings.length > 3 && <Text type="secondary" style={{ fontSize: 12 }}>另有 {riskWarnings.length - 3} 项路线提示，审批后可在执行跟踪中处理。</Text>}
                </div>
              </div>
            )}

            {/* 行程与执行事实 */}
            <div>
              <Text strong style={{ fontSize: 13 }}>行程与执行进度</Text>
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {itineraryRows.length === 0 && <Empty description="未安排站点" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
                {itineraryRows.map(row => (
                  <Card key={row.date} size="small"
                    styles={{ body: { padding: '8px 12px', borderLeft: `3px solid ${tokens.colorPrimary}` } }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <Space size={8}>
                        <Text strong style={{ fontSize: 13 }}>{row.date}</Text>
                        <Tag style={{ fontSize: 10 }}>{weekdayOf(row.date)}</Tag>
                      </Space>
                      {detail.vehicle_days?.[row.date] && (() => {
                        const vehicleId = detail.vehicle_days[row.date];
                        const vehicle = detail.vehicle_map?.[vehicleId];
                        return (
                          <Space size={4}>
                            <CarOutlined style={{ color: tokens.colorTextSecondary }} />
                            <Text style={{ fontSize: 12 }}>
                              {vehicle?.plate_no || `车辆已删除（原编号 #${vehicleId}）`}
                            </Text>
                          </Space>
                        );
                      })()}
                    </div>
                    {row.tasks.length > 0 ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {row.tasks.map(task => (
                          <div key={`${task.date}-${task.site_id}`} style={{
                            display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6,
                            padding: '4px 0', minWidth: 0,
                          }}>
                            <Text strong style={{ fontSize: 12 }}>{task.site_name}{scoreBadge(task.site_id)}</Text>
                            <Text type="secondary" style={{ fontSize: 11 }}>{task.assignee || '未指定负责人'}</Text>
                            <Tag color={task.status === 'completed' ? 'green' : task.status === 'change_pending' ? 'gold' : task.status === 'partial' ? 'blue' : 'default'}>
                              {task.status_cn || task.status}
                            </Tag>
                            <Text type="secondary" style={{ fontSize: 11 }}>完成 {task.completed_items}/{task.total_items}（{Math.round(task.completion_rate || 0)}%）</Text>
                            {task.attention && <Text type="warning" style={{ fontSize: 11 }}>{task.attention}</Text>}
                          </div>
                        ))}
                        {(executionPackagesByDate[row.date] || []).filter(task => canApprove && task.can_handle_overdue).map(task => (
                          <div key={`package-${task.plan_id}`} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4 }}>
                            <Button size="small" type="link" aria-label={`催办${row.date}逾期执行`}
                              onClick={() => handleOverdueAction(task, 'remind')}>催办</Button>
                            <Button size="small" type="link" danger aria-label={`登记${row.date}未执行原因并关闭`}
                              onClick={() => closeOverdueExecution(task)}>登记并关闭</Button>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {row.planned_site_ids.slice(0, 10).map(sid => {
                          const site = detail.site_map?.[sid];
                          return (
                            <span key={sid} style={{
                              padding: '2px 8px', borderRadius: 4, fontSize: 12,
                              background: tokens.colorPrimaryBg, border: `1px solid ${tokens.colorBorder}`,
                            }}>
                              {site?.name || `站点${sid}`}
                              {scoreBadge(sid)}
                            </span>
                          );
                        })}
                        {row.planned_site_ids.length > 10 && <Tag>其余 {row.planned_site_ids.length - 10} 站</Tag>}
                      </div>
                    )}
                    {row.notes && <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>{row.notes}</Text>}
                  </Card>
                ))}
              </div>
            </div>

            {/* 备件需求 */}
            {(detail.spare_parts || []).length > 0 && (
              <div>
                <Text strong style={{ fontSize: 13 }}><ToolOutlined style={{ marginRight: 4 }} />备件需求</Text>
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {detail.spare_parts.map((p, i) => (
                    <Tag key={i}>{p.part_name || p.name || '备件'} × {p.quantity || 1}</Tag>
                  ))}
                </div>
              </div>
            )}

          </div>
        )}
      </Drawer>

      <Modal
        open={cancelOpen}
        title="取消计划"
        okText="确认取消计划"
        cancelText="返回"
        okButtonProps={{ danger: true }}
        confirmLoading={cancelLoading}
        maskClosable={!cancelLoading}
        closable={!cancelLoading}
        onOk={submitPlanCancellation}
        onCancel={() => { if (!cancelLoading) setCancelOpen(false); }}
        destroyOnHidden
      >
        <Alert type="warning" showIcon
          message="取消后保留审批和操作记录；已有现场或实际资源事实时服务端会拒绝。"
          style={{ marginBottom: 12 }} />
        {cancelError && <Alert type="error" showIcon message={cancelError} style={{ marginBottom: 12 }} />}
        <div className="plan-destructive-reason">
          <Input.TextArea
            value={cancelReason}
            onChange={(event) => { setCancelReason(event.target.value); setCancelError(''); }}
            placeholder="请填写取消原因"
            maxLength={500}
            showCount
            autoSize={{ minRows: 3, maxRows: 6 }}
            disabled={cancelLoading}
          />
        </div>
      </Modal>

      <Modal
        open={purgeOpen}
        title="彻底删除无效计划"
        okText="确认彻底删除"
        cancelText="返回"
        okButtonProps={{ danger: true }}
        confirmLoading={purgeLoading}
        maskClosable={!purgeLoading}
        closable={!purgeLoading}
        onOk={submitPlanPurge}
        onCancel={() => { if (!purgeLoading) setPurgeOpen(false); }}
        destroyOnHidden
      >
        <Alert type="error" showIcon
          message="此操作不可恢复。计划、现场任务、审核记录和计划专属影像将被彻底删除。"
          style={{ marginBottom: 12 }} />
        {purgeError && <Alert type="error" showIcon message={purgeError} style={{ marginBottom: 12 }} />}
        <div className="plan-destructive-reason">
          <Input.TextArea
            value={purgeReason}
            onChange={(event) => { setPurgeReason(event.target.value); setPurgeError(''); }}
            placeholder="请填写彻底删除原因"
            maxLength={500}
            showCount
            autoSize={{ minRows: 3, maxRows: 6 }}
            disabled={purgeLoading}
          />
        </div>
      </Modal>

      <Modal
        open={editOpen}
        title={editDraft ? `编辑${TYPE_MAP[editDraft.schedule_type] || '巡检'}计划 · ${editDraft.period_start} ~ ${editDraft.period_end}` : '编辑巡检计划'}
        width={860}
        destroyOnHidden
        maskClosable={false}
        onCancel={() => { if (!editSaving) setEditOpen(false); }}
        footer={[
          <Button key="cancel" disabled={editSaving} onClick={() => setEditOpen(false)}>取消</Button>,
          <Button key="save" loading={editSaving} disabled={!editDraft || !editResourcesReady || !editItemsReady} onClick={() => saveEdit(false)}>保存草稿</Button>,
          <Button key="submit" type="primary" loading={editSaving} disabled={!editDraft || !editResourcesReady || !editItemsReady} onClick={() => saveEdit(true)}>保存并提交</Button>,
        ]}
      >
        {editError && <Alert type="error" showIcon message={editError} style={{ marginBottom: 12 }} />}
        {editItemError && <Alert type="error" showIcon message={editItemError}
          action={<Button size="small" onClick={() => setEditItemsReloadKey(value => value + 1)}>重新加载检查项</Button>}
          style={{ marginBottom: 12 }} />}
        {editValidation?.warnings?.length > 0 && (
          <Alert type="warning" showIcon message="计划可保存，但提交前需确认以下事项"
            description={editValidation.warnings.join('；')} style={{ marginBottom: 12 }} />
        )}
        {!editDraft ? <div style={{ padding: 32, textAlign: 'center' }}>加载中…</div> : (
          <div className="plan-edit-form">
            <div className="plan-edit-form__row">
              <div>
                <Text type="secondary">添加安排日期</Text>
                <Space.Compact style={{ width: '100%', marginTop: 6 }}>
                  <DatePicker value={newScheduleDate} onChange={setNewScheduleDate} style={{ flex: 1 }}
                    minDate={dayjs(editDraft.period_start)} maxDate={dayjs(editDraft.period_end)} />
                  <Button icon={<PlusOutlined />} disabled={!newScheduleDate} onClick={addEditDay}>添加</Button>
                </Space.Compact>
              </div>
              <div>
                <Text type="secondary">计划车辆（同一计划共用）</Text>
                <Select allowClear showSearch optionFilterProp="label" style={{ width: '100%', marginTop: 6 }}
                  value={editDraft.vehicle_id || undefined} placeholder="选择车辆；清空表示无需用车"
                  onChange={setPlanVehicle}
                  options={editVehicles.map(vehicle => ({
                    value: Number(vehicle.id),
                    label: `${vehicle.plate_no || `车辆 #${vehicle.id}`}${vehicle.dispatchable === false ? ` · ${vehicle.dispatch_block_reason || '不可调度'}` : ''}`,
                    disabled: vehicle.dispatchable === false,
                  }))} />
              </div>
              <div>
                <Text type="secondary">无需用车说明</Text>
                <Input value={editDraft.vehicle_exception_reason} style={{ marginTop: 6 }}
                  disabled={Boolean(editDraft.vehicle_id)}
                  placeholder={editDraft.vehicle_id ? '已选择计划车辆，无需填写' : '未安排车辆时必填'}
                  onChange={event => setEditDraft(current => ({ ...current, vehicle_exception_reason: event.target.value }))} />
              </div>
            </div>

            <div className="plan-edit-days">
              {Object.keys(editDraft.plan_data || {}).sort().map(date => {
                const day = editDraft.plan_data[date] || { sites: [], notes: '' };
                return <div className="plan-edit-day" key={date}>
                  <div className="plan-edit-day__header">
                    <Space><Text strong>{date}</Text><Tag>{weekdayOf(date)}</Tag></Space>
                    <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除${date}安排`} onClick={() => removeEditDay(date)} />
                  </div>
                  <div className="plan-edit-form__row">
                    <div>
                      <Text type="secondary">站点</Text>
                      <Select mode="multiple" showSearch optionFilterProp="label" style={{ width: '100%', marginTop: 6 }}
                        value={day.sites || []} placeholder="选择当日巡检站点"
                        onChange={sites => updateEditDay(date, { sites })}
                        options={editSites.map(site => ({ value: Number(site.id), label: site.name }))} />
                      {(day.sites || []).map(siteId => {
                        const choices = editItemOptions[Number(siteId)] || [];
                        const selected = day.inspection_items?.[String(siteId)] || [];
                        const siteName = editSites.find(site => Number(site.id) === Number(siteId))?.name || `站点 #${siteId}`;
                        return <div key={`items-${siteId}`} style={{ marginTop: 6 }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>{siteName} 检查项</Text>
                          {choices.length ? (
                            <Select mode="multiple" allowClear showSearch optionFilterProp="label" style={{ width: '100%', marginTop: 4 }}
                              value={selected} placeholder="选择本次计划需要的检查项"
                              onChange={ids => updateEditSiteItems(date, siteId, ids)}
                              options={choices.map(item => ({ value: Number(item.id), label: item.item_name }))} />
                          ) : (
                            <div style={{ marginTop: 4 }}><Text type="secondary">本站当前设备没有适用检查项</Text></div>
                          )}
                        </div>;
                      })}
                    </div>
                  </div>
                  <Input value={day.notes || ''} placeholder="当日备注（可选）" maxLength={200}
                    onChange={event => updateEditDay(date, { notes: event.target.value })} />
                </div>;
              })}
              {!Object.keys(editDraft.plan_data || {}).length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未添加安排日期" />}
            </div>

            <div>
              <div className="plan-edit-section-heading">
                <Text strong>备件需求</Text>
                <Button size="small" icon={<PlusOutlined />} onClick={addEditPart}>添加备件</Button>
              </div>
              <div className="plan-edit-parts">
                {(editDraft.spare_parts || []).map((part, index) => (
                  <Space.Compact key={`${part.part_id}-${index}`} block>
                    <Select showSearch optionFilterProp="label" style={{ flex: 1 }} value={Number(part.part_id)}
                      onChange={partId => {
                        const selected = editParts.find(item => Number(item.id) === Number(partId));
                        updateEditPart(index, { part_id: Number(partId), part_name: selected?.part_name || '' });
                      }}
                      options={editParts.map(item => ({ value: Number(item.id), label: `${item.part_name}（库存 ${item.quantity ?? 0}${item.unit || ''}）` }))} />
                    <InputNumber min={1} max={9999} precision={0} value={Number(part.quantity || 1)}
                      addonAfter={editParts.find(item => Number(item.id) === Number(part.part_id))?.unit || '件'}
                      onChange={quantity => updateEditPart(index, { quantity: Number(quantity || 1) })} />
                    <Button danger icon={<DeleteOutlined />} aria-label={`移除备件${index + 1}`} onClick={() => removeEditPart(index)} />
                  </Space.Compact>
                ))}
                {!editDraft.spare_parts?.length && <Text type="secondary">无备件需求</Text>}
              </div>
            </div>

            <div>
              <Text type="secondary">计划备注</Text>
              <Input.TextArea rows={3} maxLength={500} showCount style={{ marginTop: 6 }}
                value={editDraft.remarks}
                onChange={event => setEditDraft(current => ({ ...current, remarks: event.target.value }))} />
            </div>
            <div>
              <Text type="secondary">周巡检漏站例外说明</Text>
              <Input value={editDraft.coverage_exception_reason} style={{ marginTop: 6 }}
                placeholder="周巡检未覆盖全部负责站点时，提交前必填"
                onChange={event => setEditDraft(current => ({ ...current, coverage_exception_reason: event.target.value }))} />
            </div>
          </div>
        )}
      </Modal>

      {/* 退回原因弹窗 */}
      <Modal open={rejectOpen} title="退回计划" okText="确认退回" cancelText="取消"
        onOk={onReject} onCancel={() => { setRejectOpen(false); setRejectReason(''); }}
        confirmLoading={acting} okButtonProps={{ danger: true }} destroyOnHidden>
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>退回后系统将通知排程人，计划回到草稿状态可修改后重新提交。</Text>
        </div>
        <Input.TextArea rows={3} placeholder="请填写退回原因（必填）" value={rejectReason} onChange={e => setRejectReason(e.target.value)} />
      </Modal>

      <Modal
        open={Boolean(closingExecution)}
        title={`异常关闭“${closingExecution?.plan_name || '巡检任务'}”`}
        okText="登记并关闭"
        cancelText="取消"
        onOk={submitOverdueClose}
        onCancel={() => { setClosingExecution(null); setOverdueCloseReason(''); }}
        confirmLoading={overdueClosing}
        okButtonProps={{ danger: true }}
        destroyOnHidden
      >
        <Text>仅取消尚未完成的检查项，已有现场记录保留。请登记未执行原因，后续如仍需巡检应重新排程。</Text>
        <Input.TextArea
          rows={3}
          maxLength={300}
          showCount
          aria-label="未执行原因"
          placeholder="例如：道路封闭，已与站点确认改期"
          value={overdueCloseReason}
          onChange={(event) => setOverdueCloseReason(event.target.value)}
          style={{ marginTop: 12 }}
        />
      </Modal>

      {canCleanup && <Modal open={cleanupOpen} title="处理无效数据" okText="确认处理所选" cancelText="取消"
        onOk={applyCleanup} onCancel={() => { if (!cleanupLoading) setCleanupOpen(false); }}
        cancelButtonProps={{ disabled: cleanupLoading }} confirmLoading={cleanupLoading}
        okButtonProps={{ danger: true, disabled: !cleanupSelected.length }} destroyOnHidden>
        <Alert type="warning" showIcon message="逐条确认处理方式" description="空草稿和无业务事实工单会物理删除；已生成但无现场事实的超期计划只会异常关闭并保留审计。系统会在确认时再次复核，任一候选变化则整批回滚。" />
        {cleanupError && <Alert type="error" showIcon message={cleanupError}
          action={<Button size="small" onClick={() => loadCleanupCandidates()}>重新加载候选</Button>}
          style={{ marginTop: 12 }} />}
        <div style={{ marginTop: 12, maxHeight: 360, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {cleanupCandidates.length === 0 && !cleanupLoading && !cleanupError && <Empty description="暂无可安全清理的候选" />}
          {cleanupCandidates.map(item => {
            const value = `${item.kind}:${item.id}`;
            return <Checkbox key={value} checked={cleanupSelected.some(selected => selected.kind === item.kind && Number(selected.id) === Number(item.id))}
              onChange={(event) => setCleanupSelected(current => event.target.checked
                ? [...current, { kind: item.kind, id: item.id }]
                : current.filter(selected => !(selected.kind === item.kind && Number(selected.id) === Number(item.id))))}>
              <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 4, verticalAlign: 'top' }}>
                <span><Tag color={item.cleanup_action === 'physical_delete' ? 'red' : 'orange'}>
                  {item.cleanup_action === 'physical_delete' ? '物理删除' : '异常关闭'}
                </Tag>{item.label} · {item.reason}</span>
                {cleanupCandidateIdentityRows(item).map(text => (
                  <Text key={text} type="secondary" style={{ fontSize: 12 }}>{text}</Text>
                ))}
                <span style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px' }}>
                  {cleanupCandidateFactRows(item.activity_facts).map(fact => (
                    <Text key={fact.key} type="secondary" style={{ fontSize: 12 }}>
                      {fact.label}：{fact.value}
                    </Text>
                  ))}
                </span>
              </span>
            </Checkbox>;
          })}
        </div>
      </Modal>}

      {canUseFavorites && <Modal open={favoritesOpen} title="从常用计划生成草稿" okText="生成草稿" cancelText="取消"
        onOk={createFavoriteDraft} onCancel={() => setFavoritesOpen(false)} confirmLoading={favoriteLoading}
        okButtonProps={{ disabled: !favoriteId || !favoriteStart }} destroyOnHidden>
        {favoritesError ? (
          <WorkspaceEmpty type="error" description="常用计划加载失败，当前不能判断是否暂无收藏。" onRefresh={openFavorites} />
        ) : favorites.length === 0 ? (
          <Empty description="暂无常用计划，可在计划列表中点击“收藏”保存" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <Text type="secondary">常用计划</Text>
              <Select value={favoriteId} onChange={selectFavorite} style={{ width: '100%', marginTop: 6 }}
                options={favorites.map(item => ({ value: item.id, label: item.name }))} />
            </div>
            {(() => {
              const selected = favorites.find(item => item.id === favoriteId);
              return selected ? <Alert type="info" showIcon message={`${TYPE_MAP[selected.schedule_type] || selected.schedule_type} · ${selected.site_count}站 · ${selected.vehicle_day_count}天用车 · ${selected.part_count}类备件`} /> : null;
            })()}
            <div>
              <Text type="secondary">新周期开始日期</Text>
              <DatePicker value={favoriteStart} onChange={setFavoriteStart} style={{ width: '100%', marginTop: 6 }} />
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              系统按原计划的相对日期复用站点、车辆和备件，并重新校验冲突；一次性工单、审批和执行记录不会复制。
            </Text>
            <div style={{ textAlign: 'right' }}>
              <Button danger type="text" icon={<DeleteOutlined />} onClick={deleteFavorite} loading={favoriteLoading}>删除此收藏</Button>
            </div>
          </div>
        )}
      </Modal>}
    </WorkspacePage>
  );
}
