import { useState, useEffect, useCallback, useRef } from 'react';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import {
  Table, Card, Input, Button, Space, Tag, Typography, message,
  Modal, Descriptions, Tooltip, Tabs, Divider,
  Select, Image, Badge, Empty,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, AuditOutlined, CloseOutlined, CheckOutlined,
  CameraOutlined, FileTextOutlined, EnvironmentOutlined,
  ShoppingOutlined, QuestionCircleOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { statusColors } from '../../theme/tokens';
import FilterBar from '../../components/FilterBar';
import {
  pageRootStyle, tableCardStyle, tableCardBody,
  filterInputWidth, filterSelectWidth, filterSmallSelectWidth,
} from '../../services/pageStyles';
import DataReviewTab from '../alerts/components/DataReviewTab';

const { Title, Text } = Typography;
const { TextArea } = Input;

// ===========================================================================
// 统一待办审核页面（多 Tab）
// Tab：数据审核 / 巡检质控 / 工单审核 / 备件预申报 / 用车审批 / 影像审核
// 每个 Tab 结构一致：统计栏 + 搜索筛选栏 + 列表
// ===========================================================================

// 轻量指标卡
function MetricCard({ title, value, color }) {
  const { tokens } = useTheme();
  return (
    <div style={{
      background: tokens.colorFillSecondary,
      borderRadius: 8,
      padding: '10px 14px',
      minWidth: 100,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
    }}>
      <div style={{ fontSize: 12, color: tokens.colorTextDescription, marginBottom: 2, lineHeight: '18px' }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 700, lineHeight: '28px', color: color || tokens.colorTextHeading }}>{value}</div>
    </div>
  );
}

// 通用工具栏：搜索 + 筛选 + 刷新 + 计数
// 结构一致性（B 类）：统一走 FilterBar 组合——筛选控件左对齐为 children，动作按钮收 extra
function AuditToolbar({ searchText, onSearchChange, placeholder, filterSlot, extraAction, total, filteredCount, refresh, helpText }) {
  const { tokens } = useTheme();
  return (
    <FilterBar
      style={{ marginBottom: 12 }}
      extra={(
        <Space size={8}>
          {extraAction}
          {helpText && (
            <Tooltip title={helpText}>
              <QuestionCircleOutlined style={{ color: tokens.colorTextTertiary, fontSize: 14, cursor: 'help' }} />
            </Tooltip>
          )}
          <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
        </Space>
      )}
    >
      <Input.Search
        placeholder={placeholder}
        allowClear
        value={searchText}
        onChange={e => onSearchChange(e.target.value)}
        onSearch={onSearchChange}
        style={{ width: filterInputWidth }}
      />
      {filterSlot}
      <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
        {searchText ? `已筛选 ${filteredCount} 条` : `共 ${total} 项待审`}
      </Text>
    </FilterBar>
  );
}

// ---------------------------------------------------------------------------
// 业务审核通用 Tab：按 sourceTypes 分组展示一类待办，含指标/筛选/列表
// ---------------------------------------------------------------------------
function BusinessAuditTab({ sourceTypes, title, statValue, allItems, loading, onOpenReview, onRefresh, extraAction }) {
  const { tokens, isDark } = useTheme();
  const mode = isDark ? 'dark' : 'light';
  const [searchText, setSearchText] = useState('');
  const [typeFilter, setTypeFilter] = useState(undefined);

  const [listWrapRef, listH] = useTableAutoHeight({ headerOffset: 40, deps: [loading, allItems.length] });

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
      spare_part_request: { label: '备件申请' },
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
          spare_part_request: ['geekblue', <FileTextOutlined />],
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
          {r.source_type === 'workorder_review' && r.source_title && (
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
      render: t => t || '-',
    },
    {
      title: '操作', width: 100, fixed: 'right',
      render: (_, r) => (
        <Button type="link" size="small" icon={<AuditOutlined />}
          onClick={() => onOpenReview(r)}>
          审核
        </Button>
      ),
    },
  ];

  return (
    <div style={{ height: '100%', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 指标条 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexShrink: 0, flexWrap: 'wrap' }}>
        {metrics.map((m, idx) => <MetricCard key={idx} {...m} />)}
      </div>

      {/* 工具栏 */}
      <AuditToolbar
        searchText={searchText}
        onSearchChange={setSearchText}
        placeholder="搜索内容 / 站点 / 来源"
        filterSlot={showType ? (
          <Select
            placeholder="类型筛选"
            allowClear
            value={typeFilter}
            onChange={setTypeFilter}
            style={{ width: filterSmallSelectWidth }}
            options={typeOptions}
          />
        ) : null}
        extraAction={extraAction}
        total={filtered.length}
        filteredCount={searched.length}
        refresh={onRefresh}
        helpText={helpText}
      />

      {/* 列表 */}
      <Card style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
        <div ref={listWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <Table
            dataSource={searched}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={false}
            scroll={listH ? { y: listH } : undefined}
            locale={{ emptyText: <Empty description="暂无待审核项" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          />
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 备件预申报专用 Tab：按明细行展示，含指标/筛选/富列表
// ---------------------------------------------------------------------------
function PartsRequestAuditTab({ statValue, allItems, loading, onOpenReview, onRefresh }) {
  const { tokens, isDark } = useTheme();
  const mode = isDark ? 'dark' : 'light';
  const [searchText, setSearchText] = useState('');
  const [sourceFilter, setSourceFilter] = useState(undefined);

  const [listWrapRef, listH] = useTableAutoHeight({ headerOffset: 40, deps: [loading, allItems.length] });

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
    <div style={{ height: '100%', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 指标条 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexShrink: 0, flexWrap: 'wrap' }}>
        {metrics.map((m, idx) => <MetricCard key={idx} {...m} />)}
      </div>

      {/* 工具栏 */}
      <AuditToolbar
        searchText={searchText}
        onSearchChange={setSearchText}
        placeholder="搜索备件名称 / 编号 / 厂家 / 型号 / 申请人 / 计划"
        filterSlot={(
          <Select
            placeholder="来源计划"
            allowClear
            showSearch
            value={sourceFilter}
            onChange={setSourceFilter}
            style={{ width: filterSelectWidth }}
            options={sourceOptions}
            optionFilterProp="label"
          />
        )}
        total={flattened.length}
        filteredCount={searched.length}
        refresh={onRefresh}
        helpText="点击「审核」审批整条预申报；同一计划的多项备件将一并处理。"
      />

      {/* 列表 */}
      <Card style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
        <div ref={listWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <Table
            dataSource={searched}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={false}
            scroll={listH ? { y: listH } : undefined}
            locale={{ emptyText: <Empty description="暂无备件预申报待审" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          />
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export default function AuditPage() {
  const { tokens, isDark } = useTheme();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const isAdmin = user?.role === 'admin';
  const reviewerId = user?.id || 1;
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const requestedTab = searchParams.get('tab');
  const initialTab = requestedTab && (isAdmin || requestedTab !== 'data')
    ? requestedTab
    : (isAdmin ? 'data' : 'inspection');
  const [activeKey, setActiveKey] = useState(initialTab);

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
  const [reviewComment, setReviewComment] = useState('');
  const [processing, setProcessing] = useState(false);
  const [autoPassing, setAutoPassing] = useState(false);

  const loadPending = useCallback(async () => {
    setLoading(true);
    try {
      const [data, st, ds] = await Promise.all([
        api.get('/audit/pending'),
        api.get('/audit/stats'),
        api.get('/data-reviews/stats'),
      ]);
      setItems(Array.isArray(data) ? data : []);
      setStats(st || { total: 0, inspection_pending: 0, workorder_pending: 0, parts_pending: 0, vehicle_pending: 0, photo_pending: 0 });
      setDataStats(ds || { total: 0 });
    } catch { setItems([]); }
    setLoading(false);
  }, []);

  useEffect(() => { loadPending(); }, [loadPending]);

  // ---- 审核操作 ----
  const handleReview = async (item, action) => {
    setProcessing(true);
    try {
      if (item.source_type === 'inspection') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写现场需补充或整改的内容'); setProcessing(false); return;
        }
        const realId = item.id.replace('insp_', '');
        const result = await api.put(`/inspection-v2/items/${realId}/review`, { action, comment: reviewComment });
        if (result && !result.error) {
          message.success(action === 'approve' ? '审核通过' : '已驳回');
        } else {
          message.error(result?.error || '操作失败');
          setProcessing(false);
          return;
        }
      } else if (item.source_type === 'workorder_review') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写现场需补充或整改的内容'); setProcessing(false); return;
        }
        const result = await api.post(`/workorders/${item.order_no}/${action === 'approve' ? 'approve' : 'reject'}`, {
          comment: reviewComment || '', reason: reviewComment || '', reviewer_id: reviewerId,
        });
        if (result && !result.error) {
          message.success(action === 'approve' ? '工单已办结' : '工单已退回现场补充');
        } else {
          message.error(result?.error || '操作失败');
          setProcessing(false);
          return;
        }
      } else if (item.source_type === 'parts_request') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写原因'); setProcessing(false); return;
        }
        const realId = item.id.replace('pr_', '');
        const endpoint = `/parts/requests/${realId}/${action === 'approve' ? 'approve' : 'reject'}`;
        const result = await api.put(endpoint, { comment: reviewComment, approver_id: reviewerId });
        if (result && !result.error) {
          message.success(action === 'approve' ? '备件需求已批准，未锁定库存' : '已驳回');
        } else {
          message.error(result?.error || '操作失败');
          setProcessing(false);
          return;
        }
      } else if (item.source_type === 'spare_part_request') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写原因'); setProcessing(false); return;
        }
        const realId = item.id.replace('spr_', '');
        const result = await api.put(`/api/parts/requests/${realId}/approve`, {
          action,
          comment: reviewComment,
          approver_id: reviewerId,
        });
        if (result && !result.error) {
          message.success(action === 'approve' ? '备件申请已批准' : '已驳回');
        } else {
          message.error(result?.error || '操作失败');
          setProcessing(false);
          return;
        }
      } else if (item.source_type === 'vehicle_application') {
        if (action === 'reject' && !reviewComment.trim()) {
          message.error('驳回需填写原因'); setProcessing(false); return;
        }
        const realId = item.id.replace('va_', '');
        const result = await api.put(`/api/vehicle/applications/${realId}/approve`, {
          action,
          reject_reason: action === 'reject' ? (reviewComment || '不符') : '',
          approver_id: reviewerId,
        });
        if (result && !result.error) {
          message.success(action === 'approve' ? '用车申请已批准' : '已驳回');
        } else {
          message.error(result?.error || '操作失败');
          setProcessing(false);
          return;
        }
      } else if (item.source_type === 'photo_review') {
        const attachmentIds = item.attachment_ids || [];
        if (attachmentIds.length === 0) { message.error('无待审核照片'); setProcessing(false); return; }
        const result = await api.post(`/operation-attachments/review`, {
          attachment_ids: attachmentIds,
          action: action,
          reject_reason: action === 'reject' ? (reviewComment || '未达标') : '',
          reviewer_id: reviewerId,
        });
        if (result && !result.error) {
          message.success(action === 'approve' ? `已确认${attachmentIds.length}张照片` : `已驳回，已通知重拍`);
        } else {
          message.error(result?.error || '操作失败');
          setProcessing(false);
          return;
        }
      } else {
        message.error('未知类型');
        setProcessing(false);
        return;
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
      message.success(action === 'approve' ? '审核通过' : '已驳回');
      setReviewModalOpen(false);
      setReviewingItem(null);
      setReviewComment('');
      loadPending();
    } catch (err) {
      console.error('handleReview error:', err);
      message.error('审核操作失败');
    }
    setProcessing(false);
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
    setReviewModalOpen(true);
  };

  // 一键通过正常照片（影像抽样审核核心减负动作）
  const handleAutoPassNormal = async () => {
    try {
      const preview = await api.post('/operation-attachments/auto-review', { dry_run: true });
      if (!preview || preview.error) {
        message.error(preview?.error || '无法获取批量审核范围');
        return;
      }
      if (!preview.approved) {
        message.info('当前权限范围内没有可自动通过的正常照片');
        return;
      }
      Modal.confirm({
        title: '确认一键通过正常照片',
        content: `将在当前权限范围内通过 ${preview.approved} 张正常照片，保留 ${preview.remaining_flagged || 0} 张标红照片供人工审核。`,
        okText: '确认通过',
        cancelText: '取消',
        onOk: async () => {
          setAutoPassing(true);
          try {
            const result = await api.post('/operation-attachments/auto-review', {});
            if (result && !result.error) {
              message.success(
                `已自动通过 ${result.approved || 0} 张正常照片，剩余 ${result.remaining_flagged || 0} 张标红待查`
              );
              loadPending();
            } else {
              message.error(result?.error || '操作失败');
            }
          } finally {
            setAutoPassing(false);
          }
        },
      });
    } catch (e) {
      console.error('auto-pass error:', e);
      message.error('自动通过失败');
    }
  };

  // ===== 审核弹窗 =====
  function ReviewModal() {
    if (!reviewModalOpen) return null;
    const item = reviewingItem;
    if (!item) return null;
    return (
      <Modal
        title={<Space><AuditOutlined />审核 - {item.title}</Space>}
        open={reviewModalOpen}
        onCancel={() => { setReviewModalOpen(false); setReviewingItem(null); }}
        footer={[
          <Button key="cancel" onClick={() => { setReviewModalOpen(false); setReviewingItem(null); }}>取消</Button>,
          <Button key="reject" danger loading={processing}
            onClick={() => handleReview(item, 'reject')}
            icon={<CloseOutlined />}>驳回</Button>,
          <Button key="approve" type="primary" loading={processing}
            onClick={() => handleReview(item, 'approve')}
            icon={<CheckOutlined />}>审核通过</Button>,
        ]}
        width={520}
      >
        <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
          <Descriptions.Item label="类型">
            <Tag color={item.source_type === 'inspection' ? 'orange' : 'blue'} style={{ borderRadius: 4, fontSize: 11 }}>
              {item.source_label}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="内容">{item.title}</Descriptions.Item>
          {item.source_type === 'workorder_review' && (
            <Descriptions.Item label="工单编号">{item.source_name}</Descriptions.Item>
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
          {item.remark && (
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
          } catch (_) { return <Text type="secondary">现场照片数据格式异常</Text>; }
        })()}
        {item.source_type === 'workorder_review' && item.attachment_details && (
          <div style={{ marginTop: 12 }}>
            <Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>处置影像（{item.actual_photos || 0}张）</Text>
            {(item.attachment_details || []).length ? <Image.PreviewGroup>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {item.attachment_details.map((photo) => (
                  <Image key={photo.id} src={photo.stored_path} width={100} height={100}
                    style={{ objectFit: 'cover', borderRadius: 6 }} preview={{ mask: '预览' }} />
                ))}
              </div>
            </Image.PreviewGroup> : <Text type="secondary">未上传处置影像</Text>}
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
      children: <DataReviewTab tokens={tokens} isDark={isDark} />,
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
          onOpenReview={openReview}
          onRefresh={loadPending}
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
          onOpenReview={openReview}
          onRefresh={loadPending}
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
          onOpenReview={openReview}
          onRefresh={loadPending}
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
          onOpenReview={openReview}
          onRefresh={loadPending}
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
          <AuditOutlined style={{ marginRight: 8 }} />统一审核
        </Title>
      </div>

      <Tabs
        className="audit-tabs"
        activeKey={activeKey}
        onChange={setActiveKey}
        items={tabItems}
        size="small"
        type="card"
        animated={{ inkBar: true, tabPane: false }}
        style={{ flex: 1, minHeight: 0 }}
        tabBarStyle={{ marginBottom: 16 }}
      />

      <ReviewModal />
    </div>
  );
}
