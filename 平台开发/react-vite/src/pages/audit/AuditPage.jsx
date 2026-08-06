import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Input, Button, Space, Tag, Typography, App as AntApp,
  Modal, Descriptions, Tooltip, Tabs, Divider,
  Select, Image, Badge, Alert, Checkbox,
} from 'antd';
import {
  ReloadOutlined, AuditOutlined, CloseOutlined, CheckOutlined,
  CameraOutlined, FileTextOutlined, EnvironmentOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { statusColors } from '../../theme/tokens';
import { pageRootStyle, filterInputWidth, filterSelectWidth, filterSmallSelectWidth } from '../../services/pageStyles';
import { FilterField, StatusStrip, ToolbarMeta, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';
import DataReviewTab from '../alerts/components/DataReviewTab';

const { Title, Text } = Typography;
const { TextArea } = Input;

// ===========================================================================
// 统一待办审核页面（多 Tab）
// Tab：数据审核 / 巡检质控 / 工单审核 / 备件预申报 / 用车审批 / 影像审核
// 每个 Tab 结构一致：统计栏 + 搜索筛选栏 + 列表
// ===========================================================================

// 轻量指标卡
function AuditEmptyState({ title, onRefresh, error }) {
  return <WorkspaceEmpty type={error ? 'error' : 'empty'} onRefresh={onRefresh}
    description={error ? `${title}加载失败，当前不能判断是否没有待处理事项。` : `${title}当前没有待处理事项`} />;
}

// 通用工具栏：搜索 + 筛选 + 刷新 + 计数
// 结构一致性（B 类）：统一走 FilterBar 组合——筛选控件左对齐为 children，动作按钮收 extra
function AuditToolbar({ searchText, onSearchChange, placeholder, filterSlot, extraAction, total, filteredCount, refresh, helpText }) {
  const { tokens } = useTheme();
  return (
    <WorkspaceToolbar
      actions={(
        <Space size={8}>
          {extraAction}
          {helpText && (
            <Tooltip title={helpText}>
              <Button type="text" size="small" aria-label="查看当前审核口径说明"
                icon={<QuestionCircleOutlined style={{ color: tokens.colorTextTertiary }} />} />
            </Tooltip>
          )}
          <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
        </Space>
      )}
    >
      <FilterField label="搜索">
        <Input.Search
          aria-label={placeholder}
          placeholder={placeholder}
          allowClear
          enterButton="查询"
          value={searchText}
          onChange={e => onSearchChange(e.target.value)}
          onSearch={onSearchChange}
          style={{ width: filterInputWidth }}
        />
      </FilterField>
      {filterSlot}
      <ToolbarMeta label="当前结果">
        {searchText ? `已筛选 ${filteredCount} 条` : `共 ${total} 项待审`}
      </ToolbarMeta>
    </WorkspaceToolbar>
  );
}

// ---------------------------------------------------------------------------
// 业务审核通用 Tab：按 sourceTypes 分组展示一类待办，含指标/筛选/列表
// ---------------------------------------------------------------------------
function BusinessAuditTab({ sourceTypes, title, statValue, allItems, loading, loadError, onOpenReview, onRefresh, extraAction, reviewerNames = [] }) {
  const { tokens, isDark } = useTheme();
  const mode = isDark ? 'dark' : 'light';
  const [searchText, setSearchText] = useState('');
  const [typeFilter, setTypeFilter] = useState(undefined);

  const filtered = allItems.filter(i => sourceTypes.includes(i.source_type));
  const typeFiltered = typeFilter
    ? filtered.filter(i => i.source_type === typeFilter)
    : filtered;
  const searched = searchText
    ? typeFiltered.filter(i => {
        const kw = searchText.toLowerCase();
        return (i.title || '').toLowerCase().includes(kw)
          || (i.site_name || '').toLowerCase().includes(kw)
          || (i.source_name || '').toLowerCase().includes(kw)
          || (i.source_label || '').toLowerCase().includes(kw);
      })
    : typeFiltered;

  const showType = sourceTypes.length > 1;
  const typeOptions = sourceTypes.map(t => {
    const map = {
      inspection: { label: '巡检质控' },
      workorder_review: { label: '工单办结' },
      photo_review: { label: '影像审核' },
      vehicle_application: { label: '用车审批' },
    };
    return { value: t, label: map[t]?.label || t };
  });

  // 业务指标
  const siteCount = new Set(filtered.map(i => i.site_id || i.site_name).filter(Boolean)).size;
  const photoMissing = filtered.filter(i => (i.actual_photos || 0) < (i.required_photos || 0)).length;
  const oldestDays = (() => {
    const times = filtered.map(i => i.submit_time).filter(Boolean);
    if (!times.length) return null;
    const oldest = new Date(Math.min(...times.map(t => new Date(t).getTime())));
    const days = Math.ceil((Date.now() - oldest.getTime()) / 86400000);
    return days;
  })();

  if (!loading && filtered.length === 0) {
    return <AuditEmptyState title={title} onRefresh={onRefresh} error={loadError} />;
  }

  let metrics = [];
  let helpText = '';
  if (sourceTypes.includes('inspection')) {
    metrics = [
      { title: '巡检待审', value: statValue || 0, color: (statValue || 0) > 0 ? statusColors.warning[mode] : statusColors.success[mode] },
      { title: '涉及站点', value: siteCount, color: statusColors.info[mode] },
      { title: '照片缺失', value: photoMissing, color: photoMissing > 0 ? statusColors.danger[mode] : statusColors.success[mode] },
      { title: '等待最久', value: oldestDays !== null ? `${oldestDays}天` : '-', color: tokens.colorTextTertiary },
    ];
    helpText = '点击「审核」处理巡检检查项；通过后数据正式生效，驳回后需执行人补充或整改。';
  } else if (sourceTypes.includes('workorder_review')) {
    const photoCount = filtered.reduce((sum, i) => sum + (i.actual_photos || 0), 0);
    metrics = [
      { title: '工单待审', value: statValue || 0, color: (statValue || 0) > 0 ? statusColors.warning[mode] : statusColors.success[mode] },
      { title: '处置影像', value: photoCount, color: statusColors.info[mode] },
      { title: '影像不足', value: photoMissing, color: photoMissing > 0 ? statusColors.danger[mode] : statusColors.success[mode] },
      { title: '涉及站点', value: siteCount, color: statusColors.success[mode] },
    ];
    helpText = '以工单为审核单元：在同一处查看处置说明和全部影像，再决定通过办结或退回补充。';
  } else if (sourceTypes.includes('photo_review')) {
    const flaggedCount = filtered.filter(i => i.is_flagged).length;
    metrics = [
      { title: '影像待审', value: statValue || 0, color: (statValue || 0) > 0 ? statusColors.warning[mode] : statusColors.success[mode] },
      { title: '标红待查', value: flaggedCount, color: flaggedCount > 0 ? statusColors.danger[mode] : statusColors.success[mode] },
      { title: '涉及站点', value: siteCount, color: statusColors.info[mode] },
      { title: '等待最久', value: oldestDays !== null ? `${oldestDays}天` : '-', color: tokens.colorTextTertiary },
    ];
    helpText = '正常照片可一键通过，仅系统标红（GPS偏离/时间异常/关联异常项）需人工确认；展开全部照片→只点异常→其余自动通过。';
  }

  const columns = [
    ...(showType ? [{
      title: '类型', dataIndex: 'source_label', width: 110,
      render: (t, r) => {
        // 类型 Tag 专用小映射：仅用 antd 预设色名（主题无关，符合规范 §7）
        const map = {
          inspection: ['orange', <FileTextOutlined />],
          workorder_review: ['blue', <AuditOutlined />],
          photo_review: ['cyan', <CameraOutlined />],
          vehicle_application: ['purple', <FileTextOutlined />],
        };
        const [color, icon] = map[r.source_type] || ['default', <FileTextOutlined />];
        return <Tag color={color} style={{ borderRadius: 4, fontSize: 11 }}>{icon} {t}</Tag>;
      },
    }] : []),
    {
      title: '待审内容', dataIndex: 'title', width: 220,
      render: (t, r) => (
        <div>
          <Text strong>{t}</Text>
          {r.source_type === 'workorder_review' && reviewerNames.includes(r.assignee) && (
            <Tag color="warning" style={{ marginLeft: 6 }}>本人处置 · 自审</Tag>
          )}
          {r.source_type === 'workorder_review' && r.source_title && r.source_title !== t && (
            <div><Text type="secondary" style={{ fontSize: 11 }}>{r.source_title}</Text></div>
          )}
          {r.source_type === 'photo_review' && r.remark && (
            <div><Text type="secondary" style={{ fontSize: 11 }}>{r.remark}</Text></div>
          )}
          {r.source_name && (
            <div><Text type="secondary" style={{ fontSize: 11 }}>{r.source_name}</Text></div>
          )}
          {r.source_type === 'photo_review' && r.is_flagged ? (
            <div style={{ marginTop: 4 }}>
              <Tag color="red" style={{ borderRadius: 4, fontSize: 11 }}>
                标红：{r.flag_reason || '触发标红规则'}
              </Tag>
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: '站点', dataIndex: 'site_name', width: 130,
      render: (t) => t ? <><EnvironmentOutlined style={{ fontSize: 11, marginRight: 4 }} />{t}</> : '-',
    },
    {
      title: '照片', width: 100, align: 'center',
      render: (_, r) => {
        const req = r.required_photos || 0;
        const act = r.actual_photos || 0;
        return req > 0 ? (
          <Space size={4}>
            <CameraOutlined style={{ color: statusColors.info[mode], fontSize: 12 }} />
            <Text type={act >= req ? 'success' : 'warning'} style={{ fontSize: 12 }}>
              {act}/{req}
            </Text>
          </Space>
        ) : <Text type="secondary" style={{ fontSize: 12 }}>-</Text>;
      },
    },
    {
      title: '提交时间', dataIndex: 'submit_time', width: 140,
      render: t => t || <Text type="secondary">历史记录未保存</Text>,
    },
    {
      title: '操作', width: 100, fixed: 'right',
      render: (_, r) => (
        <Button type="link" size="small" icon={<AuditOutlined />}
          aria-label={`审核 ${r.source_name || r.title}`}
          onClick={() => onOpenReview(r)}>
          审核
        </Button>
      ),
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <StatusStrip items={metrics.map((metric, index) => ({ key: index, label: metric.title, value: metric.value, color: metric.color }))} />
      <AuditToolbar
        searchText={searchText}
        onSearchChange={setSearchText}
        placeholder="搜索内容 / 站点 / 来源"
        filterSlot={showType ? (
          <FilterField label="审核类型">
            <Select
              aria-label="审核类型"
              placeholder="全部类型"
              allowClear
              value={typeFilter}
              onChange={setTypeFilter}
              style={{ width: filterSmallSelectWidth }}
              options={typeOptions}
            />
          </FilterField>
        ) : null}
        extraAction={extraAction}
        total={filtered.length}
        filteredCount={searched.length}
        refresh={onRefresh}
        helpText={helpText}
      />

      {loadError && <Alert type="warning" showIcon message="待审列表刷新失败，当前显示的数量和内容可能不是最新结果。" action={<Button size="small" onClick={onRefresh}>重试</Button>} />}
      <WorkspaceTable dataSource={searched} columns={columns} rowKey="id" loading={loading}
        emptyType={searchText || typeFilter ? 'filtered' : 'empty'} onRefresh={onRefresh} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 备件预申报专用 Tab：按明细行展示，含指标/筛选/富列表
// ---------------------------------------------------------------------------
function PartsRequestAuditTab({ allItems, loading, loadError, onOpenReview, onRefresh }) {
  const { tokens, isDark } = useTheme();
  const mode = isDark ? 'dark' : 'light';
  const [searchText, setSearchText] = useState('');
  const [sourceFilter, setSourceFilter] = useState(undefined);

  const flattened = allItems
    .filter(i => i.source_type === 'parts_request')
    .flatMap(parent => {
      const details = Array.isArray(parent.parts_detail) ? parent.parts_detail : [];
      const base = {
        parent_id: parent.id,
        parent_title: parent.title,
        source_name: parent.source_name,
        site_name: parent.site_name,
        requester_name: parent.requester_name,
        submit_time: parent.submit_time,
      };
      return details.map((d, idx) => ({
        ...base,
        id: `${parent.id}_${d.part_sku || idx}`,
        part_sku: d.part_sku,
        part_name: d.part_name || d.part_sku,
        manufacturer: d.manufacturer || '-',
        model: d.model || '-',
        quantity: d.quantity,
        rowIndex: idx,
        totalItems: details.length,
      }));
    });

  const sourceOptions = Array.from(new Set(flattened.map(i => i.source_name).filter(Boolean)))
    .map(name => ({ value: name, label: name }));

  const sourceFiltered = sourceFilter
    ? flattened.filter(i => i.source_name === sourceFilter)
    : flattened;

  const searched = searchText
    ? sourceFiltered.filter(i => {
        const kw = searchText.toLowerCase();
        return (i.part_name || '').toLowerCase().includes(kw)
          || (i.manufacturer || '').toLowerCase().includes(kw)
          || (i.model || '').toLowerCase().includes(kw)
          || (i.requester_name || '').toLowerCase().includes(kw)
          || (i.source_name || '').toLowerCase().includes(kw)
          || (i.part_sku || '').toLowerCase().includes(kw);
      })
    : sourceFiltered;

  const parents = allItems.filter(i => i.source_type === 'parts_request');
  const totalRequests = new Set(parents.map(i => i.id)).size;
  const distinctParts = new Set(searched.map(i => i.part_sku)).size;
  const totalQuantity = searched.reduce((sum, i) => sum + (Number(i.quantity) || 0), 0);
  const distinctSources = new Set(searched.map(i => i.source_name).filter(Boolean)).size;

  if (!loading && flattened.length === 0) {
    return <AuditEmptyState title="备件需求" onRefresh={onRefresh} error={loadError} />;
  }

  const metrics = [
    { title: '待审申请数', value: totalRequests, color: totalRequests > 0 ? statusColors.warning[mode] : statusColors.success[mode] },
    { title: '涉及种类', value: distinctParts, color: statusColors.info[mode] },
    { title: '待审总数量', value: totalQuantity, color: totalQuantity > 0 ? statusColors.warning[mode] : statusColors.success[mode] },
    { title: '来源计划数', value: distinctSources, color: statusColors.purple[mode] },
  ];

  const columns = [
    {
      title: '备件', key: 'part', width: 240,
      render: (_, r) => <>
        <Text strong>{r.part_name || r.part_sku}</Text>
        <Text style={{ display: 'block', color: tokens.colorPrimary, fontSize: 12 }}>{r.part_sku || '—'}</Text>
        <Text type="secondary" style={{ display: 'block', fontSize: 12 }} ellipsis>{[r.manufacturer, r.model].filter(value => value && value !== '-').join(' · ') || '规格待补充'}</Text>
      </>,
    },
    {
      title: '申请信息', key: 'request', width: 190,
      render: (_, r) => <>
        <Text>{r.requester_name || '—'} · {r.quantity || 0} 件</Text>
        <Text type="secondary" style={{ display: 'block', fontSize: 12 }} ellipsis>{r.source_name || '未关联计划'}</Text>
        <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{r.site_name || '未关联站点'}</Text>
      </>,
    },
    { title: '提交时间', dataIndex: 'submit_time', width: 140, render: v => <Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Text> },
    {
      title: '操作', width: 100, fixed: 'right',
      render: (_, r) => (
        <Button type="link" size="small" icon={<AuditOutlined />}
          onClick={() => onOpenReview(allItems.find(p => p.id === r.parent_id))}>
          审核
        </Button>
      ),
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <StatusStrip items={metrics.map((metric, index) => ({ key: index, label: metric.title, value: metric.value, color: metric.color }))} />
      <AuditToolbar
        searchText={searchText}
        onSearchChange={setSearchText}
        placeholder="搜索备件名称 / 编号 / 厂家 / 型号 / 申请人 / 计划"
        filterSlot={(
          <FilterField label="来源计划">
            <Select
              aria-label="来源计划"
              placeholder="全部计划"
              allowClear
              showSearch
              value={sourceFilter}
              onChange={setSourceFilter}
              style={{ width: filterSelectWidth }}
              options={sourceOptions}
              optionFilterProp="label"
            />
          </FilterField>
        )}
        total={flattened.length}
        filteredCount={searched.length}
        refresh={onRefresh}
        helpText="点击「审核」审批整条预申报；同一计划的多项备件将一并处理。"
      />

      {loadError && <Alert type="warning" showIcon message="备件待审列表刷新失败，当前内容可能不是最新结果。" action={<Button size="small" onClick={onRefresh}>重试</Button>} />}
      <WorkspaceTable dataSource={searched} columns={columns} rowKey="id" loading={loading}
        emptyType={searchText || sourceFilter ? 'filtered' : 'empty'} onRefresh={onRefresh} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export default function AuditPage() {
  const { message, modal } = AntApp.useApp();
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const userRoles = user?.roles || [user?.role];
  const isAdmin = userRoles.includes('admin');
  const isDualRole = isAdmin && userRoles.includes('operator');
  const reviewerNames = [user?.real_name, user?.login_name, user?.username, user?.name].filter(Boolean);
  const reviewerId = user?.id || 1;
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [pendingLoaded, setPendingLoaded] = useState(false);
  const [pendingError, setPendingError] = useState('');
  const [statsError, setStatsError] = useState('');
  const [dataStatsError, setDataStatsError] = useState('');
  const allowedTabs = isAdmin
    ? ['data', 'inspection', 'workorder', 'parts', 'vehicle', 'photo']
    : ['inspection', 'workorder', 'photo'];
  const defaultTab = isAdmin ? 'data' : 'inspection';
  const requestedTab = searchParams.get('tab');
  const initialTab = allowedTabs.includes(requestedTab) ? requestedTab : defaultTab;
  const [activeKey, setActiveKey] = useState(initialTab);

  useEffect(() => {
    const nextTab = allowedTabs.includes(searchParams.get('tab')) ? searchParams.get('tab') : defaultTab;
    setActiveKey(nextTab);
  }, [searchParams, isAdmin]);

  const handleTabChange = (key) => {
    setActiveKey(key);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('tab', key);
    setSearchParams(nextParams);
  };

  // ---- 统计 ----
  const [stats, setStats] = useState({
    total: 0, inspection_pending: 0, workorder_pending: 0,
    parts_pending: 0, vehicle_pending: 0, photo_pending: 0,
  });
  const [dataStats, setDataStats] = useState({ total: 0 });

  // ---- 审核弹窗 ----
  const [reviewModalOpen, setReviewModalOpen] = useState(false);
  const [reviewingItem, setReviewingItem] = useState(null);
  const reviewSessionRef = useRef(null);
  const focusedOrderRef = useRef('');
  const [reviewComment, setReviewComment] = useState('');
  const [processing, setProcessing] = useState(false);
  const [autoPassing, setAutoPassing] = useState(false);
  const [evidenceAcknowledged, setEvidenceAcknowledged] = useState(false);

  const loadPending = useCallback(async () => {
    setLoading(true);
    const [pendingResult, statsResult, dataStatsResult] = await Promise.allSettled([
      api.getStrict('/audit/pending'),
      api.getStrict('/audit/stats'),
      api.getStrict('/data-reviews/stats'),
    ]);
    if (pendingResult.status === 'fulfilled') {
      setItems(Array.isArray(pendingResult.value) ? pendingResult.value : []);
      setPendingError('');
    } else {
      setPendingError(pendingResult.reason?.message || '审核待办加载失败');
    }
    if (statsResult.status === 'fulfilled') {
      setStats(statsResult.value || { total: 0, inspection_pending: 0, workorder_pending: 0, parts_pending: 0, vehicle_pending: 0, photo_pending: 0 });
      setStatsError('');
    } else {
      setStatsError(statsResult.reason?.message || '审核统计加载失败');
    }
    if (dataStatsResult.status === 'fulfilled') {
      setDataStats(dataStatsResult.value || { total: 0 });
      setDataStatsError('');
    } else {
      setDataStatsError(dataStatsResult.reason?.message || '数据审核统计加载失败');
    }
    setPendingLoaded(true);
    setLoading(false);
  }, []);

  useEffect(() => { loadPending(); }, [loadPending]);

  // ---- 审核操作 ----
  const handleReview = async (item, action) => {
    setProcessing(true);
    try {
      let successMessage = '';
      if (item.source_type === 'inspection') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写现场需补充或整改的内容'); return;
        }
        const realId = item.id.replace('insp_', '');
        await api.putStrict(`/inspection-v2/items/${realId}/review`, { action, comment: reviewComment });
        successMessage = action === 'approve' ? '巡检审核通过' : '巡检已退回现场整改';
      } else if (item.source_type === 'workorder_review') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写现场需补充或整改的内容'); return;
        }
        await api.postStrict(`/workorders/${item.order_no}/${action === 'approve' ? 'approve' : 'reject'}`, {
          comment: reviewComment || '', reason: reviewComment || '', reviewer_id: reviewerId,
          evidence_acknowledged: evidenceAcknowledged,
        });
        successMessage = action === 'approve' ? '工单已办结' : '工单已退回现场补充';
      } else if (item.source_type === 'parts_request') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写原因'); return;
        }
        const realId = item.id.replace('pr_', '');
        const endpoint = `/parts/requests/${realId}/${action === 'approve' ? 'approve' : 'reject'}`;
        await api.putStrict(endpoint, { comment: reviewComment, approver_id: reviewerId });
        successMessage = action === 'approve' ? '备件需求已批准，未锁定库存' : '备件需求已驳回';
      } else if (item.source_type === 'vehicle_application') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写原因'); return;
        }
        const realId = item.id.replace('va_', '');
        await api.postStrict(`/vehicle/applications/${realId}/approve`, {
          action,
          reject_reason: action === 'reject' ? (reviewComment || '不符') : '',
          approver_id: reviewerId,
        });
        successMessage = action === 'approve' ? '用车申请已批准' : '用车申请已驳回';
      } else if (item.source_type === 'photo_review') {
        const attachmentIds = item.attachment_ids || [];
        if (attachmentIds.length === 0) { message.error('无待审核照片'); return; }
        await api.postStrict(`/operation-attachments/review`, {
          attachment_ids: attachmentIds,
          action: action,
          reject_reason: action === 'reject' ? (reviewComment || '未达标') : '',
          reviewer_id: reviewerId,
        });
        successMessage = action === 'approve' ? `已确认 ${attachmentIds.length} 张照片` : '照片已驳回，已通知重拍';
      } else {
        throw new Error('无法识别当前审核类型，请刷新待办后重试');
      }
      if (reviewSessionRef.current) {
        api.track('review.submitted', {
          review_id: reviewSessionRef.current,
          source_type: item.source_type,
          site_id: item.site_id,
          decision: action,
        });
        reviewSessionRef.current = null;
      }
      message.success(successMessage);
      setReviewModalOpen(false);
      setReviewingItem(null);
      setReviewComment('');
      setEvidenceAcknowledged(false);
      loadPending();
    } catch (err) {
      console.error('handleReview error:', err);
      message.error(err.message || '审核操作失败，请重试');
    } finally {
      setProcessing(false);
    }
  };

  const openReview = (item) => {
    reviewSessionRef.current = 'rev_' + Date.now() + '_' + Math.floor(Math.random() * 1e6);
    api.track('review.opened', {
      review_id: reviewSessionRef.current,
      source_type: item.source_type,
      site_id: item.site_id,
    });
    setReviewingItem(item);
    setReviewComment('');
    setEvidenceAcknowledged(false);
    setReviewModalOpen(true);
  };

  useEffect(() => {
    const orderNo = searchParams.get('order') || '';
    if (!orderNo || loading || !pendingLoaded || pendingError || focusedOrderRef.current === orderNo) return;
    const target = items.find((item) => item.source_type === 'workorder_review'
      && (item.order_no === orderNo || item.source_name === orderNo));
    focusedOrderRef.current = orderNo;
    if (target) openReview(target);
    else message.info(`工单 ${orderNo} 当前已不在待审核列表中`);
  }, [items, loading, message, pendingError, pendingLoaded, searchParams]);

  // 一键通过正常照片（影像抽样审核核心减负动作）
  const handleAutoPassNormal = async () => {
    try {
      const preview = await api.postStrict('/operation-attachments/auto-review', { dry_run: true });
      if (!preview.approved) {
        message.info('当前权限范围内没有可自动通过的正常照片');
        return;
      }
      modal.confirm({
        title: '确认一键通过正常照片',
        content: `将在当前权限范围内通过 ${preview.approved} 张正常照片，保留 ${preview.remaining_flagged || 0} 张标红照片供人工审核。`,
        okText: '确认通过',
        cancelText: '取消',
        onOk: async () => {
          setAutoPassing(true);
          try {
            const result = await api.postStrict('/operation-attachments/auto-review', {});
            message.success(
              `已自动通过 ${result.approved || 0} 张正常照片，剩余 ${result.remaining_flagged || 0} 张标红待查`
            );
            loadPending();
          } catch (error) {
            message.error(error.message || '自动通过失败，请重试');
          } finally {
            setAutoPassing(false);
          }
        },
      });
    } catch (e) {
      console.error('auto-pass error:', e);
      message.error(e.message || '自动通过失败');
    }
  };

  // ===== 审核弹窗 =====
  function ReviewModal() {
    if (!reviewModalOpen) return null;
    const item = reviewingItem;
    if (!item) return null;
    const riskyEvidence = item.source_type === 'workorder_review'
      ? (item.attachment_details || []).filter(photo => photo.is_flagged || photo.duplicate_of_id || !photo.taken_at)
      : [];
    const isSelfReview = item.source_type === 'workorder_review' && reviewerNames.includes(item.assignee);
    const missingResolution = item.source_type === 'workorder_review' && !item.resolution_note;
    const missingRequiredPhotos = item.source_type === 'workorder_review'
      && Number(item.actual_photos || 0) < Number(item.required_photos || 0);
    return (
      <Modal
        title={<Space><AuditOutlined />{item.source_type === 'workorder_review' ? `工单审核 · ${item.source_name}` : `审核 · ${item.title}`}</Space>}
        open={reviewModalOpen}
        onCancel={() => { setReviewModalOpen(false); setReviewingItem(null); }}
        footer={[
          <Button key="cancel" onClick={() => { setReviewModalOpen(false); setReviewingItem(null); }}>取消</Button>,
          <Button key="reject" danger loading={processing}
            onClick={() => handleReview(item, 'reject')}
            icon={<CloseOutlined />}>驳回</Button>,
          <Button key="approve" type="primary" loading={processing}
            disabled={missingResolution || missingRequiredPhotos || (riskyEvidence.length > 0 && !evidenceAcknowledged)}
            onClick={() => handleReview(item, 'approve')}
            icon={<CheckOutlined />}>审核通过</Button>,
        ]}
        width={680}
        styles={{ body: { maxHeight: 'calc(100vh - 220px)', overflowY: 'auto' } }}
      >
        {isSelfReview && <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="当前为本人处置事项，审核结果将标记为自审"
          description="请独立核对现场说明和全部影像；通过或退回都会记录当前审核人。" />}
        {(missingResolution || missingRequiredPhotos) && <Alert type="error" showIcon style={{ marginBottom: 12 }}
          message="当前证据不满足办结条件，只能退回现场补充"
          description={[missingResolution ? '未记录现场处置说明' : '', missingRequiredPhotos ? '处置影像数量不足' : ''].filter(Boolean).join('；')} />}
        <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
          <Descriptions.Item label="类型">
            <Tag color={item.source_type === 'inspection' ? 'orange' : 'blue'} style={{ borderRadius: 4, fontSize: 11 }}>
              {item.source_label}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="内容">{item.title}</Descriptions.Item>
          {item.source_type === 'workorder_review' && (
            <>
              <Descriptions.Item label="工单编号">{item.source_name}</Descriptions.Item>
              <Descriptions.Item label="负责人">{item.assignee || '未记录'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{item.created_at || '未记录'}</Descriptions.Item>
              <Descriptions.Item label="到站签到">{item.check_in_time || '未记录'}</Descriptions.Item>
              <Descriptions.Item label="提交审核">{item.submit_time || <Text type="secondary">历史记录未保存该时间</Text>}</Descriptions.Item>
              <Descriptions.Item label="原始故障描述">{item.original_description || '未记录'}</Descriptions.Item>
              <Descriptions.Item label="现场处置说明">{item.resolution_note || <Text type="danger">未记录，建议退回补充</Text>}</Descriptions.Item>
            </>
          )}
          {item.source_type === 'inspection' && (
            <Descriptions.Item label="巡检计划">{item.source_name}</Descriptions.Item>
          )}
          <Descriptions.Item label="站点">{item.site_name || '-'}</Descriptions.Item>
          {item.source_type === 'parts_request' && (
            <>
              <Descriptions.Item label="处理方式">{item.fulfillment_label || '备件需求'}</Descriptions.Item>
              <Descriptions.Item label="关联任务">{item.source_name}</Descriptions.Item>
              <Descriptions.Item label="申报人">{item.requester_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="用途说明">{item.reason || '-'}</Descriptions.Item>
              {item.specification && <Descriptions.Item label="规格型号">{item.specification}</Descriptions.Item>}
              {item.estimated_amount !== null && item.estimated_amount !== undefined && (
                <Descriptions.Item label="预计金额">¥{Number(item.estimated_amount).toFixed(2)}</Descriptions.Item>
              )}
              <Descriptions.Item label="备件明细">
                {(item.parts_detail || []).length > 0
                  ? item.parts_detail.map((p, i) => (
                    <div key={i}>{p.part_sku} × {p.quantity}</div>
                  ))
                  : '-'}
              </Descriptions.Item>
            </>
          )}
          {item.source_type === 'photo_review' && (
            <>
              <Descriptions.Item label="自动归类">{item.recognized_category || '-'}</Descriptions.Item>
              <Descriptions.Item label="水印说明">{item.remark || '-'}</Descriptions.Item>
              {item.is_flagged ? (
                <Descriptions.Item label="系统标红">
                  <Tag color="red" style={{ borderRadius: 4 }}>{item.flag_reason || '触发标红规则'}</Tag>
                </Descriptions.Item>
              ) : (
                <Descriptions.Item label="系统标红"><Tag color="green" style={{ borderRadius: 4 }}>正常照片</Tag></Descriptions.Item>
              )}
            </>
          )}
          <Descriptions.Item label="照片进度">
            {item.actual_photos || 0} / {item.required_photos || 0} 张
          </Descriptions.Item>
          {item.remark && item.source_type !== 'workorder_review' && (
            <Descriptions.Item label="检查标准">{item.remark}</Descriptions.Item>
          )}
        </Descriptions>
        <div>
        {item.source_type === 'photo_review' && item.attachment_details && item.attachment_details[0]?.stored_path && (
          <div style={{ marginTop: 12 }}>
            <Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>照片预览</Text>
            <Image width={220} src={item.attachment_details[0].stored_path}
              style={{ borderRadius: 6, objectFit: 'cover' }} preview={{ mask: '预览' }} />
          </div>
        )}
        {item.source_type === 'inspection' && item.photo_urls && (() => {
          try {
            const urls = typeof item.photo_urls === 'string' ? JSON.parse(item.photo_urls) : item.photo_urls;
            if (!Array.isArray(urls) || !urls.length) return <Text type="secondary">本项未附现场照片</Text>;
            return (
              <div style={{ marginTop: 12 }}>
                <Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>现场照片（{urls.length}张）</Text>
                <Image.PreviewGroup>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {urls.map((url, index) => <Image key={index} src={url} width={100} height={100}
                      style={{ objectFit: 'cover', borderRadius: 6 }} preview={{ mask: '预览' }} />)}
                  </div>
                </Image.PreviewGroup>
              </div>
            );
          } catch { return <Text type="secondary">现场照片数据格式异常</Text>; }
        })()}
        {item.source_type === 'workorder_review' && item.attachment_details && (
          <div style={{ marginTop: 12 }}>
            <Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>处置影像（{item.actual_photos || 0}张）</Text>
            {(item.attachment_details || []).length ? <Image.PreviewGroup>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {item.attachment_details.map((photo) => (
                  <div key={photo.id} style={{ width: 112 }}>
                    <Image src={photo.stored_path} width={100} height={100}
                      style={{ objectFit: 'cover', borderRadius: 6 }} preview={{ mask: '预览' }} />
                    <Text type={photo.is_flagged || !photo.taken_at ? 'danger' : 'secondary'} style={{ display: 'block', fontSize: 11 }}>
                      {photo.flag_reason || (photo.taken_at ? photo.taken_at : '拍摄时间未知')}
                    </Text>
                  </div>
                ))}
              </div>
            </Image.PreviewGroup> : <Text type="secondary">未上传处置影像</Text>}
            {riskyEvidence.length > 0 && <Alert type="warning" showIcon style={{ marginTop: 12 }}
              message={`${riskyEvidence.length} 张影像需要人工核对`}
              description={<Checkbox checked={evidenceAcknowledged} onChange={event => setEvidenceAcknowledged(event.target.checked)}>
                我已核对重复风险、拍摄时间与现场上下文，确认可作为本工单办结证据
              </Checkbox>} />}
          </div>
        )}
        </div>
        <Divider style={{ margin: '8px 0' }} />
        <div>
          <Text strong>审核意见</Text>
          <TextArea rows={3} value={reviewComment} onChange={e => setReviewComment(e.target.value)}
        placeholder={['workorder_review', 'inspection'].includes(item.source_type)
          ? '通过可留空；退回时必须填写需补充或整改内容'
          : '请输入审核意见（可选）'} style={{ marginTop: 8 }} />
        </div>
      </Modal>
    );
  }

  // ---- Tab 标签（带待审数徽标）----
  const tabLabel = (text, count) => (
    <Space size={6}>
      <span>{text}</span>
      {count > 0 ? <Badge count={count} size="small" overflowCount={999}
        style={{ backgroundColor: statusColors.warning[isDark ? 'dark' : 'light'] }} /> : null}
    </Space>
  );

  const tabItems = [
    isAdmin ? {
      key: 'data',
      label: tabLabel('数据审核', dataStats.total || 0),
      children: <DataReviewTab tokens={tokens} />,
    } : null,
    {
      key: 'inspection',
      label: tabLabel('巡检质控', stats.inspection_pending || 0),
      children: (
        <BusinessAuditTab
          sourceTypes={['inspection']}
          title="巡检质控"
          statValue={stats.inspection_pending}
          allItems={items}
          loading={loading}
          loadError={pendingError}
          onOpenReview={openReview}
          onRefresh={loadPending}
          reviewerNames={reviewerNames}
        />
      ),
    },
    {
      key: 'workorder',
      label: tabLabel('工单审核', stats.workorder_pending || 0),
      children: (
        <BusinessAuditTab
          sourceTypes={['workorder_review']}
          title="工单审核"
          statValue={stats.workorder_pending}
          allItems={items}
          loading={loading}
          loadError={pendingError}
          onOpenReview={openReview}
          onRefresh={loadPending}
          reviewerNames={reviewerNames}
        />
      ),
    },
    isAdmin ? {
      key: 'parts',
      label: tabLabel('备件需求', stats.parts_pending || 0),
      children: (
        <PartsRequestAuditTab
          statValue={stats.parts_pending}
          allItems={items}
          loading={loading}
          loadError={pendingError}
          onOpenReview={openReview}
          onRefresh={loadPending}
        />
      ),
    } : null,
    isAdmin ? {
      key: 'vehicle',
      label: tabLabel('用车审批', items.filter(i => i.source_type === 'vehicle_application').length || 0),
      children: (
        <BusinessAuditTab
          sourceTypes={['vehicle_application']}
          title="用车审批"
          statValue={items.filter(i => i.source_type === 'vehicle_application').length}
          allItems={items}
          loading={loading}
          loadError={pendingError}
          onOpenReview={openReview}
          onRefresh={loadPending}
          reviewerNames={reviewerNames}
        />
      ),
    } : null,
    {
      key: 'photo',
      label: tabLabel('影像审核', stats.photo_pending || 0),
      children: (
        <BusinessAuditTab
          sourceTypes={['photo_review']}
          title="影像审核"
          statValue={stats.photo_pending}
          allItems={items}
          loading={loading}
          loadError={pendingError}
          onOpenReview={openReview}
          onRefresh={loadPending}
          reviewerNames={reviewerNames}
          extraAction={
            <Button
              type="primary"
              icon={<CheckOutlined />}
              loading={autoPassing}
              onClick={handleAutoPassNormal}
              style={{
                background: statusColors.success[isDark ? 'dark' : 'light'],
                borderColor: statusColors.success[isDark ? 'dark' : 'light'],
              }}
            >
              一键通过正常照片
            </Button>
          }
        />
      ),
    },
  ].filter(Boolean);

  return (
    <div style={pageRootStyle}>
      <style>{`
        .audit-tabs { height: 100%; }
        .audit-tabs > .ant-tabs-content-holder { flex: 1 1 auto; min-height: 0; }
        .audit-tabs > .ant-tabs-content-holder > .ant-tabs-content { height: 100%; }
        .audit-tabs .ant-tabs-tabpane-active { height: 100%; }
      `}</style>
      <div style={{ marginBottom: 16, flexShrink: 0 }}>
        <Title level={4} style={{ margin: 0, color: tokens.colorText }}>
          统一审核
        </Title>
        <Text type="secondary">按事项核验现场成果、影像和资源申请，处理后自动回写对应业务单据。</Text>
      </div>

      {isDualRole && <Alert type="info" showIcon style={{ marginBottom: 12, flexShrink: 0 }}
        message="当前使用管理员审核职责"
        description="此账号同时具有运维人员身份；本人提交或处置的事项会明确标记为自审。" />}
      {(statsError || dataStatsError) && <Alert type="warning" showIcon style={{ marginBottom: 12, flexShrink: 0 }}
        message="部分待办数量加载失败，页签徽标可能不完整"
        action={<Button size="small" onClick={loadPending}>重试</Button>} />}

      <Tabs
        className="audit-tabs"
        activeKey={activeKey}
        onChange={handleTabChange}
        items={tabItems}
        size="small"
        type="line"
        animated={{ inkBar: true, tabPane: false }}
        style={{ flex: 1, minHeight: 0 }}
        tabBarStyle={{ marginBottom: 16 }}
      />

      <ReviewModal />
    </div>
  );
}
