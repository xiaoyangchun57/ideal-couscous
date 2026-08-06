import { useState, useEffect, useCallback } from 'react';
import {
  Card, Input, Select, Button, Space, Tag, Row, Col,
  Alert, App, Modal, Typography, Spin, Image, DatePicker,
  Tooltip, Checkbox, Segmented,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined,
  PictureOutlined,
  FileTextOutlined, DownloadOutlined, InboxOutlined, RollbackOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import dayjs from 'dayjs';
import WorkspacePage, { FilterField, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../components/WorkspacePage';
import {
  attachmentSourceTypeMap, attachmentCategoryColor, attachmentCategoryMap,
  attachmentReviewStatusMap, ATTACHMENT_REVIEW_STATUS_OPTIONS,
} from '../../services/constants';
import { useNavigate, useSearchParams } from 'react-router-dom';

const { Text } = Typography;
const { RangePicker } = DatePicker;

const ATTACHMENT_BUSINESS_OPTIONS = [
  { value: 'workorder', label: '工单处置' },
  { value: 'inspection', label: '巡检与现场取证' },
  { value: 'calibration', label: '站点校准' },
  { value: 'reagent', label: '试剂作业' },
  { value: 'vehicle', label: '车辆记录' },
  { value: 'maintenance', label: '设备养护' },
  { value: 'test', label: '试验资料' },
  { value: 'other', label: '其他资料' },
];

const SOURCE_TO_BUSINESS = {
  workorder: 'workorder', inspection: 'inspection', patrol: 'inspection', site_photo: 'inspection',
  calibration: 'calibration', reagent: 'reagent', vehicle: 'vehicle', maintenance: 'maintenance', test: 'test',
};

const LEGACY_CATEGORY_TO_BUSINESS = {
  巡检照片: 'inspection', 现场照片: 'inspection', 环境照片: 'inspection',
  校准报告: 'calibration', 仪器照片: 'calibration', 签字确认: 'calibration',
  试剂配置: 'reagent', 车辆里程: 'vehicle', 车辆加油: 'vehicle',
  养护记录: 'maintenance', 设备照片: 'maintenance', 其他: 'other',
};

const ATTACHMENT_STATE_OPTIONS = [
  { label: '审核状态', options: ATTACHMENT_REVIEW_STATUS_OPTIONS.map(item => ({ ...item, value: `review:${item.value}` })) },
  { label: '归档状态', options: [{ value: 'archive:0', label: '未归档' }, { value: 'archive:1', label: '已归档' }] },
];

const stateFilterValue = filters => filters.review_status
  ? `review:${filters.review_status}`
  : filters.archived !== '' ? `archive:${filters.archived}` : undefined;

// 上传人渲染
const uploaderOf = (it) => it.uploader_name || '-';
const hasVerifiedCaptureTime = (it) => Boolean(
  it?.taken_at && it.capture_source && it.capture_source !== 'unknown',
);
const recordTimeOf = (it) => hasVerifiedCaptureTime(it)
  ? { label: '拍摄', value: it.taken_at }
  : { label: '上传', value: it.created_at || '时间未记录' };
const captureSourceMap = {
  camera: '小程序现场拍摄',
  watermark_album: '水印相册上传',
  album: '普通相册上传',
  unknown: '来源未记录',
  web_upload: '网页上传',
};

const EMPTY_FILTERS = {
  site_id: undefined,
  business_type: undefined,
  review_status: undefined,
  keyword: '',
  date_range: null,
  archived: '',
};

const filtersFromParams = (params) => {
  const dateFrom = params.get('date_from');
  const dateTo = params.get('date_to');
  const sourceType = params.get('source_type') || undefined;
  const category = params.get('category') || undefined;
  const reviewStatus = params.get('review_status') || undefined;
  return {
    site_id: params.get('site_id') ? Number(params.get('site_id')) : undefined,
    business_type: params.get('business_type')
      || SOURCE_TO_BUSINESS[sourceType]
      || LEGACY_CATEGORY_TO_BUSINESS[category]
      || undefined,
    review_status: reviewStatus,
    keyword: params.get('keyword') || '',
    date_range: dateFrom && dateTo ? [dayjs(dateFrom), dayjs(dateTo)] : null,
    archived: reviewStatus ? '' : params.get('archived') || '',
  };
};

// 审核状态标签（仅用 antd 预设色名，主题无关，符合规范 §7）
const ReviewTag = ({ it }) => {
  if (!it.review_required) return <Tag color="default">无需审核</Tag>;
  const map = attachmentReviewStatusMap;
  const color = it.review_status === 'pending' ? 'processing'
    : it.review_status === 'approved' ? 'green' : 'red';
  return <Tag color={color}>{map[it.review_status] || it.review_status}</Tag>;
};

export default function ArchivePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { tokens } = useTheme();
  const { user } = useAuth();
  const { modal, message } = App.useApp();
  const isAdmin = (user?.roles || [user?.role]).includes('admin');

  // 筛选条件
  const [filters, setFilters] = useState(() => filtersFromParams(searchParams));
  const [appliedFilters, setAppliedFilters] = useState(() => filtersFromParams(searchParams));
  const [archiveVisible, setArchiveVisible] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [archiveReason, setArchiveReason] = useState('');
  const [sites, setSites] = useState([]);
  const [stats, setStats] = useState(null);
  const [list, setList] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [statsError, setStatsError] = useState('');
  const [sitesError, setSitesError] = useState('');
  const [page, setPage] = useState(1);
  const [view, setView] = useState(() => searchParams.get('view') === 'grid' ? 'grid' : 'table');
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [batchArchiving, setBatchArchiving] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewItem, setPreviewItem] = useState(null);


  const loadSites = useCallback(async () => {
    try {
      const data = await api.getStrict('/sites');
      setSites(Array.isArray(data) ? data : []);
      setSitesError('');
    } catch (error) {
      setSitesError(error.message || '站点选项加载失败');
    }
  }, []);

  const refreshStats = useCallback(async () => {
    try {
      const data = await api.getStrict('/attachments/stats');
      setStats(data);
      setStatsError('');
    } catch (error) {
      setStatsError(error.message || '影像统计加载失败');
    }
  }, []);

  useEffect(() => { loadSites(); }, [loadSites]);
  useEffect(() => { refreshStats(); }, [refreshStats]);

  // 加载附件列表
  const loadList = useCallback(async (p = 1) => {
    setLoading(true);
    const params = new URLSearchParams();
    params.set('page', p);
    params.set('limit', 100);
    if (appliedFilters.site_id) params.set('site_id', appliedFilters.site_id);
    if (appliedFilters.business_type) params.set('business_type', appliedFilters.business_type);
    if (appliedFilters.review_status) params.set('review_status', appliedFilters.review_status);
    if (appliedFilters.keyword) params.set('keyword', appliedFilters.keyword);
    if (appliedFilters.date_range && appliedFilters.date_range[0]) {
      params.set('date_from', appliedFilters.date_range[0].format('YYYY-MM-DD'));
      params.set('date_to', appliedFilters.date_range[1].format('YYYY-MM-DD'));
    }
    if (appliedFilters.archived !== '' && appliedFilters.archived !== undefined) {
      params.set('archived', appliedFilters.archived);
    }
    try {
      const data = await api.getStrict(`/attachments?${params.toString()}`);
      setList(data.items || []);
      setTotal(data.total ?? 0);
      setSelectedRowKeys(keys => keys.filter(k => (data.items || []).some(i => i.id === k)));
      setPage(p);
      setListError('');
    } catch (error) {
      setListError(error.message || '影像列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [appliedFilters]);

  useEffect(() => { loadList(1); }, [loadList]);

  const writeFiltersToUrl = useCallback((nextFilters, nextView = view) => {
    const next = new URLSearchParams(searchParams);
    ['keyword', 'site_id', 'category', 'source_type', 'business_type', 'review_status', 'archived', 'date_from', 'date_to', 'view']
      .forEach(key => next.delete(key));
    if (nextFilters.keyword) next.set('keyword', nextFilters.keyword);
    if (nextFilters.site_id) next.set('site_id', nextFilters.site_id);
    if (nextFilters.business_type) next.set('business_type', nextFilters.business_type);
    if (nextFilters.review_status) next.set('review_status', nextFilters.review_status);
    if (nextFilters.archived !== '') next.set('archived', nextFilters.archived);
    if (nextFilters.date_range?.[0] && nextFilters.date_range?.[1]) {
      next.set('date_from', nextFilters.date_range[0].format('YYYY-MM-DD'));
      next.set('date_to', nextFilters.date_range[1].format('YYYY-MM-DD'));
    }
    if (nextView === 'grid') next.set('view', 'grid');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, view]);

  const applyFilters = () => {
    const nextFilters = { ...filters, keyword: filters.keyword.trim() };
    setFilters(nextFilters);
    setAppliedFilters(nextFilters);
    setPage(1);
    writeFiltersToUrl(nextFilters);
  };

  const resetFilters = () => {
    const nextFilters = { ...EMPTY_FILTERS };
    setFilters(nextFilters);
    setAppliedFilters(nextFilters);
    setSelectedRowKeys([]);
    setPage(1);
    writeFiltersToUrl(nextFilters);
  };

  const changeStateFilter = (value) => {
    const [kind, selectedValue] = value?.split(':') || [];
    setFilters(current => ({
      ...current,
      review_status: kind === 'review' ? selectedValue : undefined,
      archived: kind === 'archive' ? selectedValue : '',
    }));
  };

  const changeView = (nextView) => {
    setView(nextView);
    writeFiltersToUrl(appliedFilters, nextView);
  };

  // 批量归档
  const batchArchive = async () => {
    if (!selectedRowKeys.length || batchArchiving) return;
    setBatchArchiving(true);
    const ids = [...selectedRowKeys];
    try {
      const results = await Promise.allSettled(
        ids.map(id => api.postStrict(`/attachments/${id}/archive`, { archive_reason: '批量归档' })),
      );
      const failedIds = ids.filter((_, index) => results[index].status === 'rejected');
      const successCount = ids.length - failedIds.length;
      setSelectedRowKeys(failedIds);
      await Promise.allSettled([loadList(page), refreshStats()]);
      if (!failedIds.length) {
        message.success(`已归档 ${successCount} 项`);
      } else if (successCount > 0) {
        message.warning(`已归档 ${successCount} 项，失败 ${failedIds.length} 项；失败项仍保持选中，可重试`);
      } else {
        const firstError = results.find(result => result.status === 'rejected')?.reason;
        message.error(firstError?.message || `归档失败 ${failedIds.length} 项，请检查网络后重试`);
      }
    } finally {
      setBatchArchiving(false);
    }
  };

  // 打开预览
  const handlePreview = (item) => { setPreviewItem(item); setPreviewVisible(true); };

  // 打开归档弹窗（填归档原因）
  const openArchive = (item) => { setArchiveTarget(item); setArchiveReason(''); setArchiveVisible(true); };

  // 确认归档
  const confirmArchive = async () => {
    if (!archiveTarget) return;
    try {
      await api.postStrict(`/attachments/${archiveTarget.id}/archive`, { archive_reason: archiveReason });
      message.success('已归档');
      setArchiveVisible(false); setArchiveTarget(null);
      setPreviewItem(it => it ? { ...it, archived: 1 } : it);
      loadList(page); refreshStats();
    } catch (error) {
      message.error(error.message || '归档失败，请重试');
    }
  };

  // 取消归档
  const handleUnarchive = async (item) => {
    modal.confirm({
      title: '取消归档',
      content: `确定将 "${item.filename}" 取消归档？`,
      okText: '取消归档',
      okType: 'default',
      cancelText: '返回',
      onOk: async () => {
        try {
          await api.postStrict(`/attachments/${item.id}/unarchive`);
          message.success('已取消归档');
          setPreviewItem(it => it ? { ...it, archived: 0 } : it);
          loadList(page); refreshStats();
        } catch (error) {
          message.error(error.message || '取消归档失败，请重试');
          throw error;
        }
      },
    });
  };

  // 格式化文件大小
  const fmtSize = (bytes) => {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
  };

  const isImg = (it) => it.file_type === 'image' || /\.(jpg|jpeg|png|webp|bmp)$/i.test(it.filename || '');

  const isGarbled = (s) => {
    if (!s) return false;
    // 检测到替换字符或不可打印控制字符即判定为乱码
    // eslint-disable-next-line no-control-regex
    return /[\uFFFD\x00-\x08\x0B-\x0C\x0E-\x1F]/.test(s);
  };

  const baseName = (filename) => {
    if (!filename) return '未命名';
    return filename.replace(/\.[^/.]+$/, '');
  };

  const displayTitle = (it) => {
    const d = it.description;
    if (d && !isGarbled(d)) return d;
    return baseName(it.filename || '');
  };

  const sourceLabel = (t) => attachmentSourceTypeMap[t] || t || '—';
  const catColor = (c) => attachmentCategoryColor[c] || 'default';

  // 表格列
  const columns = [
    {
      title: '缩略图', dataIndex: 'stored_path', key: 'thumb', width: 72,
      render: (url, it) => isImg(it) ? (
        <Image.PreviewGroup
          items={[{ src: url, alt: it.filename }]}
        >
          <Image src={url} alt={it.filename} style={{ width: 48, height: 48, objectFit: 'cover', borderRadius: 4 }}
            preview={false} />
        </Image.PreviewGroup>
      ) : <FileTextOutlined style={{ fontSize: 22, color: tokens.colorTextTertiary }} />,
    },
    { title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true,
      render: (v, it) => <Text ellipsis style={{ fontSize: 13 }}>{it.description || v}</Text> },
    {
      title: '属性', key: 'attributes', width: 150,
      render: (_, it) => (
        <Space size={[4, 4]} wrap>
          {it.category && <Tag color={catColor(it.category)} style={{ fontSize: 11 }}>{attachmentCategoryMap[it.category] || it.category}</Tag>}
          <Tag style={{ fontSize: 11 }}>{sourceLabel(it.source_type)}</Tag>
          <Tag style={{ fontSize: 11 }}>{captureSourceMap[it.capture_source] || it.capture_source || '来源未记录'}</Tag>
        </Space>
      ),
    },
    {
      title: '关联信息', key: 'association', width: 160, ellipsis: true,
      render: (_, it) => (
        <div>
          <Text ellipsis style={{ display: 'block', fontSize: 12 }}>{it.site_name || '未关联站点'}</Text>
          {it.order_no && <Button type="link" size="small" style={{ padding: 0, height: 18, fontSize: 11 }}
            aria-label={`查看关联工单 ${it.order_no}`}
            onClick={() => navigate(`/workorders?search=${encodeURIComponent(it.order_no)}`)}>{it.order_no}</Button>}
          <Text type="secondary" ellipsis style={{ display: 'block', fontSize: 11 }}>上传人：{uploaderOf(it)}</Text>
        </div>
      ),
    },
    { title: '时间', key: 'record_time', width: 148,
      render: (_, it) => {
        const recordTime = recordTimeOf(it);
        return (
          <div>
            <Text style={{ display: 'block', fontSize: 12 }}>{recordTime.label}：{recordTime.value}</Text>
            {!hasVerifiedCaptureTime(it) && <Text type="secondary" style={{ fontSize: 11 }}>拍摄时间未核实</Text>}
          </div>
        );
      } },
    {
      title: '状态', key: 'status', width: 104,
      render: (_, it) => (
        <Space direction="vertical" size={2}>
          <ReviewTag it={it} />
          {it.is_flagged ? <Tag color="orange">需复核</Tag> : null}
          {it.archived ? <Tag color="green">已归档</Tag> : <Tag>未归档</Tag>}
        </Space>
      ),
    },
    {
      title: '操作', key: 'op', width: isAdmin ? 128 : 64,
      render: (_, it) => (
        <Space size={4}>
          <Button type="link" size="small" aria-label={`预览 ${it.filename}`} onClick={() => handlePreview(it)}>预览</Button>
          {isAdmin && (it.archived
            ? <Button type="link" size="small" aria-label={`取消归档 ${it.filename}`} onClick={() => handleUnarchive(it)}>取消归档</Button>
            : <Button type="link" size="small" aria-label={`归档 ${it.filename}`} onClick={() => openArchive(it)}>归档</Button>)}
        </Space>
      ),
    },
  ];

  const rowSelection = isAdmin ? {
    selectedRowKeys,
    onChange: (keys) => setSelectedRowKeys(keys),
  } : undefined;

  const hasAnyAttachment = (stats?.total || 0) > 0;
  if (!loading && !listError && stats && !statsError && !hasAnyAttachment) {
    return (
      <WorkspacePage title="影像与记录" subtitle="集中查询现场取证、附件与归档记录。">
        <WorkspaceEmpty description="当前还没有可归档的现场影像或记录" onRefresh={() => { loadList(1); refreshStats(); }} />
      </WorkspacePage>
    );
  }

  return (
    <WorkspacePage title="影像与记录" subtitle="集中查询现场取证、附件与归档记录"
      statusItems={[
        { key: 'total', label: '影像总数', value: stats?.total || 0 },
        { key: 'pending', label: '待审核', value: stats?.review_pending || 0, color: tokens.colorWarning },
        { key: 'archived', label: '已归档', value: stats?.archived || 0 },
      ]}>
      {(listError || statsError || sitesError) && (
        <Alert
          type="warning"
          showIcon
          message="部分影像信息未能更新"
          description={[listError && `列表：${listError}`, statsError && `统计：${statsError}`, sitesError && `站点选项：${sitesError}`]
            .filter(Boolean).join('；')}
          action={<Button size="small" onClick={() => { loadList(page); refreshStats(); loadSites(); }}>重新加载</Button>}
        />
      )}

      {/* 筛选栏 + 工具 */}
      <WorkspaceToolbar
        layout="stacked"
        className="archive-toolbar"
        actions={(
          <>
            <Button type="primary" icon={<SearchOutlined />} onClick={applyFilters}>查询</Button>
            <Button icon={<ReloadOutlined />} onClick={resetFilters}>重置</Button>
            <Segmented
              aria-label="影像展示方式"
              value={view}
              onChange={changeView}
              options={[
                { value: 'grid', label: '网格', icon: <AppstoreOutlined /> },
                { value: 'table', label: '表格', icon: <FileTextOutlined /> },
              ]}
            />
          </>
        )}
      >
        <FilterField label="影像搜索"><Input aria-label="影像搜索" placeholder="搜索文件名或描述" prefix={<SearchOutlined />}
          value={filters.keyword} onChange={e => setFilters(f => ({ ...f, keyword: e.target.value }))}
          onPressEnter={applyFilters} allowClear /></FilterField>
        <FilterField label="站点"><Select aria-label="站点" placeholder="全部站点" allowClear showSearch optionFilterProp="label"
          value={filters.site_id} onChange={v => setFilters(f => ({ ...f, site_id: v }))}
          options={sites.map(s => ({ value: s.id, label: s.name }))} /></FilterField>
        <FilterField label="业务归属"><Select aria-label="业务归属" placeholder="全部业务归属" allowClear
          value={filters.business_type} onChange={v => setFilters(f => ({ ...f, business_type: v }))}
          options={ATTACHMENT_BUSINESS_OPTIONS} /></FilterField>
        <FilterField label="记录状态"><Select aria-label="记录状态" placeholder="全部状态" allowClear
          value={stateFilterValue(filters)} onChange={changeStateFilter}
          options={ATTACHMENT_STATE_OPTIONS} /></FilterField>
        <FilterField label="日期范围"><RangePicker aria-label="日期范围" value={filters.date_range} onChange={v => setFilters(f => ({ ...f, date_range: v }))}
          placeholder={['开始日期', '结束日期']} /></FilterField>
      </WorkspaceToolbar>

      {/* 批量操作条 */}
      {selectedRowKeys.length > 0 && (
        <div style={{ marginBottom: 12, padding: '8px 12px', background: tokens.colorFillSecondary, borderRadius: tokens.borderRadius, display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
          <Text strong>已选 {selectedRowKeys.length} 项</Text>
          <Button size="small" icon={<InboxOutlined />} onClick={batchArchive} loading={batchArchiving} disabled={!isAdmin}>批量归档</Button>
          <Button size="small" type="text" onClick={() => setSelectedRowKeys([])}>取消选择</Button>
        </div>
      )}

      {/* 图片网格 / 表格 */}
      <Spin spinning={loading}>
        {view === 'grid' ? (
          list.length === 0 && !loading ? (
            listError
              ? <WorkspaceEmpty type="error" description="当前不能判断是否没有影像记录" onRefresh={() => loadList(1)} />
              : <WorkspaceEmpty type={Object.values(appliedFilters).some(Boolean) ? 'filtered' : 'empty'} onRefresh={() => loadList(1)} />
          ) : <Row gutter={[12, 12]}>
            {list.map(item => {
              const img = isImg(item);
              const checked = selectedRowKeys.includes(item.id);
              return (
                <Col key={item.id} xs={12} sm={8} md={6} lg={4}>
                  <Card
                    hoverable size="small"
                    cover={
                      <div
                        style={{
                          height: 160, background: tokens.colorBgContainerDisabled,
                          overflow: 'hidden', position: 'relative',
                        }}
                      >
                        {isAdmin && <Checkbox
                          aria-label={`选择 ${item.filename}`}
                          checked={checked}
                          onChange={e => {
                            setSelectedRowKeys(keys => e.target.checked
                              ? [...new Set([...keys, item.id])]
                              : keys.filter(k => k !== item.id));
                          }}
                          style={{ position: 'absolute', top: 8, left: 8, zIndex: 2 }}
                        />}
                        <button
                          type="button"
                          aria-label={`预览 ${item.filename}`}
                          onClick={() => handlePreview(item)}
                          style={{
                            width: '100%', height: '100%', padding: 0, border: 0,
                            background: 'transparent', cursor: 'pointer', display: 'flex',
                            alignItems: 'center', justifyContent: 'center', overflow: 'hidden',
                          }}
                        >
                          {img ? (
                            <>
                              <PictureOutlined
                                aria-hidden
                                style={{ position: 'absolute', fontSize: 48, color: tokens.colorTextQuaternary }}
                              />
                              <img
                                src={item.stored_path}
                                alt={item.filename}
                                onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}
                                style={{ width: '100%', height: '100%', objectFit: 'cover', position: 'relative' }}
                              />
                            </>
                          ) : (
                            <span style={{ fontSize: 48, color: tokens.colorTextQuaternary }}>{item.file_type === 'video' ? '🎬' : '📄'}</span>
                          )}
                        </button>
                      </div>
                    }
                    actions={[
                      <Button key="view" type="text" size="small" title="查看" aria-label={`预览 ${item.filename}`}
                        icon={<SearchOutlined />} onClick={() => handlePreview(item)} />,
                      <Button key="download" type="text" size="small" title="下载" aria-label={`下载 ${item.filename}`}
                        icon={<DownloadOutlined />} onClick={() => window.open(item.stored_path, '_blank')} />,
                      isAdmin && (item.archived
                        ? <Button key="unarchive" type="text" size="small" title="取消归档" aria-label={`取消归档 ${item.filename}`}
                            icon={<RollbackOutlined />} onClick={() => handleUnarchive(item)} />
                        : <Button key="archive" type="text" size="small" title="归档" aria-label={`归档 ${item.filename}`}
                            icon={<InboxOutlined />} onClick={() => openArchive(item)} />),
                    ].filter(Boolean)}
                  >
                    <Card.Meta
                      title={
                        <Tooltip title={item.description && !isGarbled(item.description) ? item.description : item.filename}>
                          <Text ellipsis style={{ fontSize: 13, maxWidth: '100%' }}>{displayTitle(item)}</Text>
                        </Tooltip>
                      }
                      description={
                        <div>
                          <div style={{ fontSize: 11, color: tokens.colorTextTertiary, marginBottom: 2 }}>
                            <span>{recordTimeOf(item).label} {recordTimeOf(item).value?.slice(0, 10)}</span>
                            {item.uploader_name && <span> · {item.uploader_name}</span>}
                            {item.site_name && <span> · {item.site_name}</span>}
                          </div>
                          <div style={{ fontSize: 11, color: tokens.colorTextTertiary, marginTop: 2 }}>
                            {item.category && <Tag color={catColor(item.category)} style={{ fontSize: 11 }}>{attachmentCategoryMap[item.category] || item.category}</Tag>}
                            <span>{sourceLabel(item.source_type)} · {fmtSize(item.file_size)}</span>
                          </div>
                        </div>
                      }
                    />
                  </Card>
                </Col>
              );
            })}
          </Row>
        ) : (
          <WorkspaceTable rowKey="id" dataSource={list} columns={columns} loading={loading}
            rowSelection={rowSelection}
            emptyType={listError ? 'error' : Object.values(appliedFilters).some(Boolean) ? 'filtered' : 'empty'}
            onRefresh={() => loadList(page)}
            pagination={total > 100 ? {
              current: page,
              pageSize: 100,
              total,
              showSizeChanger: false,
              showTotal: value => `共 ${value} 条`,
              onChange: nextPage => loadList(nextPage),
            } : false} />
        )}

        {/* 分页 */}
        {view === 'grid' && total > 100 && (
          <div style={{ textAlign: 'center', marginTop: 24 }}>
            <Button disabled={page <= 1} onClick={() => loadList(page - 1)} style={{ marginRight: 8 }}>上一页</Button>
            <Text style={{ margin: '0 16px' }}>{page} / {Math.ceil(total / 100)}</Text>
            <Button disabled={page >= Math.ceil(total / 100)} onClick={() => loadList(page + 1)}>下一页</Button>
          </div>
        )}
      </Spin>

      {/* 预览弹窗 */}
      <Modal
        title={previewItem?.description || previewItem?.filename || '附件预览'}
        open={previewVisible}
        onCancel={() => { setPreviewVisible(false); setPreviewItem(null); }}
        footer={previewItem ? (
          <Space>
            <Button type="primary" icon={<DownloadOutlined />} onClick={() => window.open(previewItem.stored_path, '_blank')}>下载文件</Button>
            {isAdmin && (previewItem.archived
              ? <Button icon={<RollbackOutlined />} onClick={() => { setPreviewVisible(false); handleUnarchive(previewItem); }}>取消归档</Button>
              : <Button icon={<InboxOutlined />} onClick={() => { setPreviewVisible(false); openArchive(previewItem); }}>归档</Button>)}
          </Space>
        ) : null}
        width={800}
        styles={{ body: { maxHeight: 'calc(100vh - 210px)', overflowY: 'auto' } }}
        destroyOnHidden
      >
        {previewItem && (
          <div>
            {isImg(previewItem) && (
              <div style={{ textAlign: 'center', marginBottom: 16 }}>
                <Image src={previewItem.stored_path} alt={previewItem.filename}
                  style={{ maxWidth: '100%', maxHeight: 500 }}
                  fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0iI2VlZSIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiBmb250LXNpemU9IjE2IiBmaWxsPSIjOTk5IiB0ZXh0LWFuY2hvcj0iTWlkZGxlIj7lm77nieaKlue7jOaOkzwvdGV4dD48L3N2Zz4=" />
              </div>
            )}
            <Card size="small" title="详细信息">
              <Row gutter={[16, 8]}>
                <Col span={12}><Text type="secondary">文件名：</Text><Text>{previewItem.filename}</Text></Col>
                <Col span={12}><Text type="secondary">文件大小：</Text><Text>{fmtSize(previewItem.file_size)}</Text></Col>
                <Col span={12}><Text type="secondary">分类：</Text><Tag color={catColor(previewItem.category)}>{attachmentCategoryMap[previewItem.category] || previewItem.category || '未分类'}</Tag></Col>
                <Col span={12}><Text type="secondary">来源：</Text><Text>{sourceLabel(previewItem.source_type)}</Text></Col>
                <Col span={12}><Text type="secondary">采集方式：</Text><Text>{captureSourceMap[previewItem.capture_source] || previewItem.capture_source || '来源未记录'}</Text></Col>
                <Col span={12}><Text type="secondary">关联站点：</Text><Text>{previewItem.site_name || '-'}</Text></Col>
                <Col span={12}><Text type="secondary">拍摄时间：</Text><Text>{hasVerifiedCaptureTime(previewItem) ? previewItem.taken_at : '未核实（历史记录仅能确认上传时间）'}</Text></Col>
                <Col span={12}><Text type="secondary">上传人：</Text><Text>{uploaderOf(previewItem)}</Text></Col>
                <Col span={12}><Text type="secondary">审核状态：</Text><ReviewTag it={previewItem} /></Col>
                <Col span={12}><Text type="secondary">归档状态：</Text>{previewItem.archived ? <Tag color="green">已归档</Tag> : <Tag>未归档</Tag>}</Col>
                <Col span={24}><Text type="secondary">描述：</Text><Text>{previewItem.description || '-'}</Text></Col>
                {previewItem.gps_lat && (
                  <Col span={24}><Text type="secondary">GPS位置：</Text><Text>{previewItem.gps_lat?.toFixed(6)}, {previewItem.gps_lng?.toFixed(6)}</Text></Col>
                )}
                {previewItem.watermark_code ? (
                  <Col span={24}><Text type="secondary">水印防伪码：</Text><Text copyable>{previewItem.watermark_code}</Text></Col>
                ) : null}
                {previewItem.watermark_text ? (
                  <Col span={24}>
                    <Text type="secondary">水印识别：</Text>
                    <div style={{ marginTop: 4, padding: 8, maxHeight: 120, overflow: 'auto', whiteSpace: 'pre-wrap', background: tokens.colorFillAlter, borderRadius: 4, fontSize: 12 }}>
                      {previewItem.watermark_text}
                    </div>
                  </Col>
                ) : null}
                <Col span={24}><Text type="secondary">上传时间：</Text><Text>{previewItem.created_at}</Text></Col>
                {previewItem.order_no ? (
                  <Col span={24}><Text type="secondary">关联工单：</Text><Button type="link" size="small"
                    onClick={() => { setPreviewVisible(false); navigate(`/workorders?search=${encodeURIComponent(previewItem.order_no)}`); }}>
                    {previewItem.order_no} · 查看关联工单
                  </Button></Col>
                ) : null}
                {previewItem.is_flagged ? (
                  <Col span={24}><Text type="secondary">复核原因：</Text><Text type="warning">{previewItem.flag_reason || '系统判定需人工复核'}</Text></Col>
                ) : null}
              </Row>
            </Card>
          </div>
        )}
      </Modal>

      {/* 归档原因弹窗 */}
      <Modal
        title={<Space><InboxOutlined />归档影像资料</Space>}
        open={archiveVisible}
        onCancel={() => { setArchiveVisible(false); setArchiveTarget(null); }}
        onOk={confirmArchive}
        okText="确认归档"
        cancelText="取消"
        destroyOnHidden
      >
        {archiveTarget && (
          <div>
            <p style={{ marginBottom: 8 }}>即将归档：<Text strong>{archiveTarget.filename}</Text></p>
            <div style={{ marginBottom: 6, color: tokens.colorTextSecondary, fontSize: 13 }}>
              归档后可独立检索、长期留存。可填写归档说明（选填）：
            </div>
            <Input.TextArea rows={3} value={archiveReason}
              onChange={e => setArchiveReason(e.target.value)}
              placeholder="如：2026年7月 邓埠站例行运维影像，已审核通过" maxLength={200} />
          </div>
        )}
      </Modal>
    </WorkspacePage>
  );
}
