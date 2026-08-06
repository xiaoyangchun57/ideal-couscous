import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Table, Card, Button, Space, Tag, Typography, message, Modal, Select, Empty,
  Drawer, Descriptions, Alert, Input, Tooltip, Badge, DatePicker,
} from 'antd';
import {
  ReloadOutlined, CheckOutlined, CloseOutlined, ExclamationCircleOutlined,
  CarOutlined, ToolOutlined, CalendarOutlined, FileSearchOutlined, BulbOutlined,
  MobileOutlined,
  StarOutlined, FolderOpenOutlined, DeleteOutlined,
  ClearOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { filterSelectWidth, filterSmallSelectWidth } from '../../services/pageStyles';
import WorkspacePage, { FilterField, ToolbarMeta, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';
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
  const canUseFavorites = userRoles.includes('operator');

  const [list, setList] = useState([]);
  const [teamOverview, setTeamOverview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [teamOverviewError, setTeamOverviewError] = useState('');
  const [recommendationsError, setRecommendationsError] = useState('');
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
  const [routeDay, setRouteDay] = useState(null);
  const [acting, setActing] = useState(false);
  const [draftRecommendations, setDraftRecommendations] = useState([]);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [followUpRecommendations, setFollowUpRecommendations] = useState([]);
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const [recommendationsExpanded, setRecommendationsExpanded] = useState(false);
  const [teamOverviewExpanded, setTeamOverviewExpanded] = useState(false);
  const [executionGuide, setExecutionGuide] = useState(null);
  const [closingExecution, setClosingExecution] = useState(null);
  const [overdueCloseReason, setOverdueCloseReason] = useState('');
  const [overdueClosing, setOverdueClosing] = useState(false);
  const [favoritesOpen, setFavoritesOpen] = useState(false);
  const [favorites, setFavorites] = useState([]);
  const [favoriteId, setFavoriteId] = useState(undefined);
  const [favoriteStart, setFavoriteStart] = useState(null);
  const [favoriteLoading, setFavoriteLoading] = useState(false);
  const [favoritesError, setFavoritesError] = useState('');


  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = [];
      if (statusFilter) params.push(`status=${statusFilter}`);
      if (typeFilter) params.push(`schedule_type=${typeFilter}`);
      if (attentionFilter) params.push(`attention=${attentionFilter}`);
      const [rowsResult, overviewResult, recommendationResult, followUpResult] = await Promise.allSettled([
        api.getStrict('/plan-schedules' + (params.length ? '?' + params.join('&') : '')),
        canApprove ? api.getStrict('/plan-schedules/overview') : Promise.resolve(null),
        api.getStrict('/plan-schedules/draft-recommendations'),
        api.getStrict('/plan-schedules/follow-up-recommendations'),
      ]);
      if (rowsResult.status === 'fulfilled') {
        setList(Array.isArray(rowsResult.value) ? rowsResult.value : []);
        setLoadError('');
      } else {
        setLoadError(rowsResult.reason?.message || '计划列表加载失败');
      }
      if (overviewResult.status === 'fulfilled') {
        setTeamOverview(overviewResult.value);
        setTeamOverviewError('');
      } else {
        setTeamOverviewError(overviewResult.reason?.message || '团队概览加载失败');
      }
      if (recommendationResult.status === 'fulfilled' && followUpResult.status === 'fulfilled') {
        setDraftRecommendations(recommendationResult.value?.recommendations || []);
        setFollowUpRecommendations(followUpResult.value?.recommendations || []);
        setRecommendationsError('');
      } else {
        setRecommendationsError('排程建议加载失败，当前建议数量不完整');
      }
    } catch (error) {
      setLoadError(error.message || '计划列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, typeFilter, attentionFilter, canApprove]);

  const createRecommendedDraft = async (item) => {
    setRecommendationLoading(true);
    try {
      await api.postStrict('/plan-schedules/draft-recommendations', {
        user_id: item.user_id,
        schedule_type: item.schedule_type,
        period_start: item.period_start,
      });
      message.success('已生成待确认草稿；尚未派发执行任务或占用资源');
      load();
    } catch (error) {
      message.error(error?.message || '生成草稿失败，请刷新后重试');
    } finally {
      setRecommendationLoading(false);
    }
  };

  const createFollowUpDraft = async (item) => {
    setFollowUpLoading(true);
    try {
      await api.postStrict('/plan-schedules/follow-up-recommendations', {
        user_id: item.user_id,
        site_id: item.site_id,
        anomaly_type: item.anomaly_type,
      });
      message.success('已生成复查草稿；仍需确认资源并提交审批');
      load();
    } catch (error) {
      message.error(error?.message || '生成复查草稿失败，请刷新后重试');
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
      await api.postStrict(`/plan-schedule-favorites/${favoriteId}/draft`, { period_start: favoriteStart.format('YYYY-MM-DD') });
      message.success('已从常用计划生成可编辑草稿');
      setFavoritesOpen(false);
      load();
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

  useEffect(() => { load(); }, [load]);

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
    setDrawerOpen(true);
    setDetailLoading(true);
    setDetail(null);
    setSuggestions(null);
    setValidation(null);
    setRouteDay(null);
    try {
      const det = await api.getStrict(`/plan-schedules/${id}`);
      setDetail(det);
      const siteIds = Object.keys(det?.site_map || {});
      if (siteIds.length > 0) {
        const sug = await api.getStrict(`/plan-schedules/suggestions?site_ids=${siteIds.join(',')}`);
        setSuggestions(sug);
      }
      const val = await api.postStrict('/plan-schedules/validate', {
        user_id: det?.user_id,
        schedule_type: det?.schedule_type,
        period_start: det?.period_start,
        period_end: det?.period_end,
        plan_data: det?.plan_data || {},
        vehicle_days: det?.vehicle_days || {},
        exclude_schedule_id: det?.id,
      });
      setValidation(val);
    } catch (error) {
      message.error(error.message || '计划详情加载失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    const scheduleId = Number(searchParams.get('schedule'));
    if (Number.isInteger(scheduleId) && scheduleId > 0) openDetail(scheduleId);
  }, [searchParams, openDetail]);

  const showExecutionGuide = (task) => {
    setExecutionGuide(task);
  };

  const handleOverdueAction = async (task, action, reason = '') => {
    try {
      const result = await api.postStrict(`/insp-plans/${task.id}/overdue-action`, { action, reason });
      message.success(action === 'remind' ? '已发送逾期催办' : `已异常关闭，取消 ${result.cancelled_items || 0} 个未完成检查项`);
      setExecutionGuide(null);
      load();
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
      load();
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
      load();
      openDetail(detail.id);
    } catch (error) { message.error(error.message || '退回失败'); } finally {
      setActing(false);
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

  // 详情内：按日期排序的行程
  const dayRows = useMemo(() => {
    if (!detail?.plan_data) return [];
    return Object.entries(detail.plan_data)
      .filter(([, v]) => v && Array.isArray(v.sites) && v.sites.length > 0)
      .sort(([a], [b]) => a.localeCompare(b));
  }, [detail]);

  const hasScheduledSites = Number(detail?.site_count || 0) > 0 || dayRows.length > 0;
  const generatedPlanCount = (detail?.generated_plans || []).length;
  const isApprovedSchedule = ['approved', 'archived'].includes(detail?.status);

  // 详情内：风险预警汇总（校验警告 + 高危排序提示）
  const riskWarnings = useMemo(() => {
    const warns = [];
    (validation?.warnings || []).forEach(w => warns.push({ type: 'coverage', text: w }));
    (validation?.errors || []).forEach(w => warns.push({ type: 'conflict', text: w }));
    // 高危站点排在周期后半段 → 提示
    if (detail && suggestions?.site_scores && dayRows.length >= 2) {
      const midDate = dayRows[Math.floor(dayRows.length / 2)][0];
      dayRows.forEach(([date, dayData]) => {
        if (date < midDate) return;
        (dayData.sites || []).forEach(sid => {
          const score = suggestions.site_scores[String(sid)] || 0;
          if (score >= 30) {
            const name = detail.site_map?.[sid]?.name || `站点${sid}`;
            warns.push({ type: 'priority', text: `${name}优先级高（评分${score}）但排在${date}（${weekdayOf(date)}），建议提前` });
          }
        });
      });
    }
    return warns;
  }, [validation, suggestions, detail, dayRows]);

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
    if (score === undefined || score === null) return null;
    const lv = scoreLevel(score, tokens);
    return (
      <Tooltip title={`优先级评分 ${score}：${(suggestions?.site_reasons?.[String(sid)] || []).join('；') || '无特殊事项'}`}>
        <Tag style={{ marginLeft: 4, color: lv.color, borderColor: lv.color, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
          {lv.label}·{score}
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

  const generatedTasksSection = (detail?.generated_plans || []).length > 0 ? (
    <div className="plan-generated-tasks">
      <div className="plan-detail-section-heading">
        <Text strong style={{ fontSize: 13 }}>已生成执行任务</Text>
        {(detail.generated_plans || []).some(task => task.status === 'active') && (
          <Text type="secondary" style={{ fontSize: 12 }}>现场记录请在小程序“今日执行”完成</Text>
        )}
      </div>
      <Table size="small" rowKey="id" pagination={false} style={{ marginTop: 8 }}
        tableLayout="fixed"
        dataSource={detail.generated_plans}
        columns={[
          { title: '任务', dataIndex: 'plan_name', width: 130, ellipsis: true },
          { title: '日期', dataIndex: 'generate_date', width: 92 },
          {
            title: '状态', dataIndex: 'status', width: 68,
            render: v => <Tag color={v === 'active' ? 'blue' : v === 'completed' ? 'green' : 'default'}>{v === 'active' ? '待执行' : v === 'completed' ? '已完成' : v}</Tag>,
          },
          {
            title: '完成率', dataIndex: 'completion_rate', width: 62,
            render: v => <Text style={{ fontSize: 11 }}>{Math.round(v || 0)}%</Text>,
          },
          {
            title: '现场执行', width: 220,
            render: (_, task) => task.status === 'active' ? (
              <Space size={0}>
                <Button size="small" type="link" icon={<MobileOutlined />}
                  aria-label={`查看${task.plan_name}执行说明`} onClick={() => showExecutionGuide(task)}>执行说明</Button>
                {canApprove && dayjs(task.generate_date).isBefore(dayjs(), 'day') && <>
                  <Button size="small" type="link" aria-label={`催办${task.plan_name}`}
                    onClick={() => handleOverdueAction(task, 'remind')}>催办</Button>
                  <Button size="small" type="link" danger aria-label={`登记${task.plan_name}未执行原因并关闭`}
                    onClick={() => closeOverdueExecution(task)}>登记并关闭</Button>
                </>}
              </Space>
            ) : <Text type="secondary">-</Text>,
          },
        ]} />
    </div>
  ) : null;

  return (
    <WorkspacePage
      title="巡检计划"
      subtitle="按周期编排站点、车辆和现场资源；审批通过后生成可执行任务。"
      primaryAction={canUseFavorites
        ? <Button type="primary" icon={<FolderOpenOutlined />} onClick={openFavorites}>从常用计划生成草稿</Button>
        : null}
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
      {recommendationsError && <Alert type="warning" showIcon message={recommendationsError} action={<Button size="small" onClick={load}>重试</Button>} />}
      {teamOverviewError && canApprove && <Alert type="warning" showIcon message="团队执行概览加载失败，当前不能判断是否没有关注事项" action={<Button size="small" onClick={load}>重试</Button>} />}

      {(draftRecommendations.length > 0 || followUpRecommendations.length > 0) && (
        <Alert
          type={followUpRecommendations.length > 0 ? "warning" : "info"}
          showIcon
          icon={<BulbOutlined />}
          message="待确认排程建议"
          action={<Button size="small" type="link" onClick={() => setRecommendationsExpanded(value => !value)}>{recommendationsExpanded ? '收起' : `查看 ${draftRecommendations.length + followUpRecommendations.length} 条`}</Button>}
          description={recommendationsExpanded && (
            <Space wrap size={[8, 6]}>
              {draftRecommendations.slice(0, 3).map(item => (
                <Space key={`${item.user_id}-${item.schedule_type}-${item.period_start}`} size={4}>
                  <Text style={{ fontSize: 12 }}>
                    {item.user_name} · {TYPE_MAP[item.schedule_type] || item.schedule_type}：
                    {item.site_count}站 / {item.due_item_count}项到期
                  </Text>
                  <Button size="small" type="link" loading={recommendationLoading}
                    onClick={() => createRecommendedDraft(item)}>
                    生成待确认草稿
                  </Button>
                </Space>
              ))}
              {draftRecommendations.length > 3 && <Text type="secondary">另有 {draftRecommendations.length - 3} 条建议</Text>}
              {followUpRecommendations.map(item => (
                <Space key={`follow-up-${item.user_id}-${item.site_id}-${item.anomaly_type}`} size={4}>
                  <Text style={{ fontSize: 12 }}>
                    系统性异常复查：{item.user_name} · {item.site_name} · {item.anomaly_type}（{item.window_days}天{item.occurrence_count}次）
                  </Text>
                  <Button size="small" type="link" danger loading={followUpLoading}
                    onClick={() => createFollowUpDraft(item)}>
                    生成复查草稿
                  </Button>
                </Space>
              ))}
            </Space>
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
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
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

        {loadError && !loading ? <WorkspaceEmpty type="error" description="巡检计划加载失败，当前不能判断是否暂无计划。" onRefresh={load} /> : <WorkspaceTable
          rowKey="id"
          dataSource={list}
          loading={loading}
          columns={columns}
          emptyType={statusFilter || typeFilter || attentionFilter ? 'filtered' : 'empty'}
          onRefresh={load}
          onRow={r => ({ onClick: () => openDetail(r.id), style: { cursor: 'pointer' } })}
        />}
      </section>

      {/* 详情抽屉：审批决策支撑（风险预警 + 站点情况 + 行程 + 资源 + 任务） */}
      <Drawer
        title={detail ? `${detail.user_name || ''}的${TYPE_MAP[detail.schedule_type] || '巡检'}计划（${detail.period_start} ~ ${detail.period_end}）` : '计划详情'}
        open={drawerOpen} onClose={() => setDrawerOpen(false)} width={680} destroyOnHidden
        footer={detail && canApprove && (detail.status === 'submitted' || detail.status === 'change_submitted') ? (
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button danger icon={<CloseOutlined />} onClick={() => setRejectOpen(true)} loading={acting}>退回</Button>
            <Button type="primary" icon={<CheckOutlined />} onClick={() => onApprove(detail.id)} loading={acting}>审批通过</Button>
          </div>
        ) : null}
      >
        {detailLoading && <div style={{ textAlign: 'center', padding: 40 }}>加载中…</div>}
        {!detailLoading && detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {!hasScheduledSites ? (
              <Alert type="warning" showIcon message="该排程尚未安排站点，无法生成现场执行任务。请补充站点后重新提交。" />
            ) : isApprovedSchedule && generatedPlanCount === 0 ? (
              <Alert type="error" showIcon message={`该排程已安排 ${detail.site_count} 个站点，但尚未生成现场执行任务。请联系管理员核对审批流转。`} />
            ) : null}
            {/* 基本信息 */}
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="排程人">{detail.user_name}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Badge status={(SCHEDULE_STATUS_MAP[detail.status] || {}).color} text={(SCHEDULE_STATUS_MAP[detail.status] || {}).label || detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label="周期">{detail.period_start} ~ {detail.period_end}</Descriptions.Item>
              <Descriptions.Item label="版本">v{detail.version || 1}</Descriptions.Item>
              <Descriptions.Item label="提交时间">{detail.submitted_at || '-'}</Descriptions.Item>
              <Descriptions.Item label="审批人">{detail.approver_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="备注" span={2}>{detail.remarks || '-'}</Descriptions.Item>
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

            {generatedTasksSection}

            {/* 每日行程 + 站点情况 */}
            <div>
              <Text strong style={{ fontSize: 13 }}>每日行程与站点情况</Text>
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {dayRows.length === 0 && <Empty description="未安排站点" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
                {dayRows.map(([date, dayData]) => (
                  <Card key={date} size="small"
                    styles={{ body: { padding: '8px 12px', borderLeft: `3px solid ${tokens.colorPrimary}` } }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <Space size={8}>
                        <Text strong style={{ fontSize: 13 }}>{date}</Text>
                        <Tag style={{ fontSize: 10 }}>{weekdayOf(date)}</Tag>
                      </Space>
                      {detail.vehicle_days?.[date] && (() => {
                        const vehicleId = detail.vehicle_days[date];
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
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {(dayData.sites || []).slice(0, 10).map(sid => {
                        const s = detail.site_map?.[sid];
                        return (
                          <span key={sid} style={{
                            padding: '2px 8px', borderRadius: 4, fontSize: 12,
                            background: tokens.colorPrimaryBg, border: `1px solid ${tokens.colorBorder}`,
                          }}>
                            {s?.name || `站点${sid}`}
                            {scoreBadge(sid)}
                          </span>
                        );
                      })}
                      {(dayData.sites || []).length > 10 && <Tag>其余 {(dayData.sites || []).length - 10} 站</Tag>}
                    </div>
                    {dayData.notes && <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>{dayData.notes}</Text>}
                  </Card>
                ))}
              </div>
            </div>

            {/* 路线示意图（折返检测可视化） */}
            {dayRows.length > 0 && detail.site_map && (() => {
              const dates = dayRows.map(([d]) => d);
              const selDate = routeDay || dates[0];
              const dayData = detail.plan_data?.[selDate] || {};
              const siteIds = dayData.sites || [];
              const pts = siteIds
                .map(sid => { const s = detail.site_map[sid]; return s && s.lat && s.lng ? { sid, name: s.name, lat: s.lat, lng: s.lng } : null; })
                .filter(Boolean);
              if (pts.length < 2) return null;
              if (pts.length > 12) return <Alert type="info" showIcon message={`本站点日共 ${pts.length} 站，已完成路线距离与折返校验；节点图仅适用于 12 站以内的短路线。`} />;
              // 归一化到 SVG 坐标 (280x160 viewport, padding 30)
              const lats = pts.map(p => p.lat), lngs = pts.map(p => p.lng);
              const minLat = Math.min(...lats), maxLat = Math.max(...lats);
              const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
              const spanLat = maxLat - minLat || 0.01, spanLng = maxLng - minLng || 0.01;
              const W = 280, H = 160, PAD = 30;
              const toX = lng => PAD + ((lng - minLng) / spanLng) * (W - 2 * PAD);
              const toY = lat => H - PAD - ((lat - minLat) / spanLat) * (H - 2 * PAD);
              const coords = pts.map(p => ({ ...p, x: toX(p.lng), y: toY(p.lat) }));
              // 折返检测（与后端同逻辑）
              const backtrack = new Set();
              for (let i = 0; i < coords.length - 2; i++) {
                const a = coords[i], b = coords[i + 1], c = coords[i + 2];
                const dAB = Math.hypot(a.x - b.x, a.y - b.y);
                const dAC = Math.hypot(a.x - c.x, a.y - c.y);
                if (dAC < dAB * 0.85) { backtrack.add(i + 1); }
              }
              return (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Text strong style={{ fontSize: 13 }}>路线示意图</Text>
                    <Select size="small" style={{ width: 120 }} value={selDate} onChange={setRouteDay}
                      options={dates.map(d => ({ value: d, label: `${d.slice(5)} ${weekdayOf(d)}` }))} />
                  </div>
                  <div style={{ marginTop: 8, background: tokens.colorBgLayout, borderRadius: 8, padding: 8, overflow: 'hidden' }}>
                    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
                      {/* 连线 */}
                      {coords.slice(1).map((c, i) => {
                        const prev = coords[i];
                        const isBT = backtrack.has(i);
                        return <line key={i} x1={prev.x} y1={prev.y} x2={c.x} y2={c.y}
                          stroke={isBT ? tokens.colorError : tokens.colorPrimary} strokeWidth={isBT ? 2.5 : 1.5}
                          strokeDasharray={isBT ? '6 3' : undefined} markerEnd="url(#arrow)" />;
                      })}
                      <defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
                        <path d="M0,0 L6,3 L0,6 Z" fill={tokens.colorTextSecondary} />
                      </marker></defs>
                      {/* 站点圆点 + 序号 + 名称 */}
                      {coords.map((c, i) => (
                        <g key={c.sid}>
                          <circle cx={c.x} cy={c.y} r={7} fill={backtrack.has(i - 1) ? tokens.colorErrorBg : tokens.colorBgContainer}
                            stroke={backtrack.has(i - 1) ? tokens.colorError : tokens.colorPrimary} strokeWidth={1.5} />
                          <text x={c.x} y={c.y + 3.5} textAnchor="middle" fontSize={8} fill={tokens.colorText} fontWeight="bold">{i + 1}</text>
                          <text x={c.x} y={c.y - 11} textAnchor="middle" fontSize={8} fill={tokens.colorTextSecondary}>{c.name}</text>
                        </g>
                      ))}
                    </svg>
                    {backtrack.size > 0 && (
                      <div style={{ fontSize: 11, color: tokens.colorError, marginTop: 4 }}>
                        红色虚线为折返段，建议调整站点顺序以减少路程
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}

            {/* 站点情况卡（审批决策支撑） */}
            {suggestions && Object.keys(suggestions.site_scores || {}).length > 0 && (
              <div>
                <Text strong style={{ fontSize: 13 }}>站点情况（优先级与近期问题）</Text>
                <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {Object.entries(suggestions.site_scores)
                    .sort(([, a], [, b]) => b - a)
                    .map(([sid, score]) => {
                      const s = detail.site_map?.[Number(sid)];
                      const reasons = suggestions.site_reasons?.[sid] || [];
                      const lv = scoreLevel(score, tokens);
                      return (
                        <Card key={sid} size="small" styles={{ body: { padding: '6px 12px' } }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Space size={8}>
                              <Text strong style={{ fontSize: 12 }}>{s?.name || `站点${sid}`}</Text>
                              <Tag style={{ color: lv.color, borderColor: lv.color, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>优先级{lv.label}·{score}</Tag>
                            </Space>
                          </div>
                          {reasons.length > 0 ? (
                            <div style={{ marginTop: 4 }}>
                              {reasons.map((r, i) => (
                                <div key={i} style={{ fontSize: 11, color: tokens.colorTextSecondary, lineHeight: '18px' }}>· {r}</div>
                              ))}
                            </div>
                          ) : (
                            <Text type="secondary" style={{ fontSize: 11 }}>近期无异常记录</Text>
                          )}
                        </Card>
                      );
                    })}
                </div>
              </div>
            )}

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

      <Modal open={Boolean(executionGuide)} title="现场执行请在小程序完成" footer={null}
        onCancel={() => setExecutionGuide(null)} destroyOnHidden>
        <div style={{ lineHeight: 1.8 }}>
          <div><Text strong>{executionGuide?.plan_name || '巡检任务'}</Text></div>
          <div><Text type="secondary">执行日期：{executionGuide?.generate_date || '-'}</Text></div>
          {Number(executionGuide?.site_count || executionGuide?.total_sites || 0) > 0 && (
            <div><Text type="secondary">本站任务：{executionGuide.site_count || executionGuide.total_sites} 个站点</Text></div>
          )}
          <div style={{ marginTop: 10 }}>请打开微信小程序，在“今日执行”中下拉刷新后选择该任务；到站打卡、检查项、现场照片和车辆记录均在小程序内完成。</div>
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Button type="primary" onClick={() => setExecutionGuide(null)}>我知道了</Button>
          </div>
        </div>
      </Modal>

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
