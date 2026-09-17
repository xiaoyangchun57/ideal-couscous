import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, App, Button, Card, Checkbox, DatePicker, Descriptions, Image, Input, Modal,
  Pagination, Segmented, Select, Space, Spin, Tag, Typography,
} from 'antd';
import {
  AppstoreOutlined, DownloadOutlined, FileTextOutlined, PictureOutlined,
  ReloadOutlined, SearchOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate, useSearchParams } from 'react-router-dom';
import WorkspacePage, {
  FilterField, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar,
} from '../../components/WorkspacePage';
import { api } from '../../services/api';
import { useAuth } from '../../hooks/useAuth';
import {
  archiveHistoryStatus, archivePrimaryTitle, archivePurgeEligibility, hasAdminRole,
  normalizeDeleteReason,
} from './attachmentDeletion';
import { ARCHIVE_PAGE_SIZE, archiveLastPage, archiveNavigationState } from './archivePagination';
import { archiveCaptureLabel, archiveSourceLabel, hasArchiveFilters } from './archivePresentation';
import { listFilterOptions, listFilterValue } from '../../utils/listFilterOptions';
import './ArchivePage.css';

const { Text } = Typography;
const { RangePicker } = DatePicker;

const BUSINESS_OPTIONS = [
  { value: 'workorder', label: '工单处置' },
  { value: 'inspection', label: '巡检与现场取证' },
  { value: 'calibration', label: '站点校准' },
  { value: 'reagent', label: '试剂作业' },
  { value: 'vehicle', label: '车辆记录' },
  { value: 'maintenance', label: '设备养护' },
  { value: 'test', label: '试验资料' },
  { value: 'other', label: '其他资料' },
];

const HISTORY_STATUS = {
  pending: { label: '待所属业务审核', color: 'processing' },
  rejected: { label: '已驳回', color: 'error' },
  voided: { label: '已作废', color: 'default' },
  superseded: { label: '已替换', color: 'default' },
};

const emptyFilters = { keyword: '', site_id: undefined, business_type: undefined, date_range: null };

function filtersFromParams(params) {
  const from = params.get('date_from');
  const to = params.get('date_to');
  return {
    keyword: params.get('keyword') || '',
    site_id: params.get('site_id') ? Number(params.get('site_id')) : undefined,
    business_type: params.get('business_type') || undefined,
    date_range: from && to ? [dayjs(from), dayjs(to)] : null,
  };
}

function sourceLabel(item) {
  return archiveSourceLabel(item.source_type);
}

function itemLabel(item) {
  return item.item_name || item.description || '未关联检查项';
}

function displayTitle(item) {
  return archivePrimaryTitle(item);
}

function formatSize(bytes) {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function historyStatus(item) {
  return archiveHistoryStatus(item, HISTORY_STATUS);
}

export default function ArchivePage() {
  const { message } = App.useApp();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { page, archiveMode, view, filterQuery, requestQuery } = useMemo(() => archiveNavigationState(searchParams), [searchParams]);
  const applied = useMemo(() => filtersFromParams(new URLSearchParams(filterQuery)), [filterQuery]);
  const hasAppliedFilters = useMemo(() => hasArchiveFilters(applied), [applied]);
  const [filters, setFilters] = useState(() => filtersFromParams(searchParams));
  const [sites, setSites] = useState([]);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState(null);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgePreview, setPurgePreview] = useState(null);
  const [purgePreviewLoading, setPurgePreviewLoading] = useState(false);
  const [purgeError, setPurgeError] = useState('');
  const [purgeReason, setPurgeReason] = useState('');
  const [purgeLoading, setPurgeLoading] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [batchPurgeOpen, setBatchPurgeOpen] = useState(false);
  const [batchPreview, setBatchPreview] = useState(null);
  const [batchReason, setBatchReason] = useState('');
  const [batchError, setBatchError] = useState('');
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchResultUnknown, setBatchResultUnknown] = useState(false);
  const batchKeyRef = useRef('');
  const batchRequestRef = useRef(null);
  const purgeRef = useRef(false);
  const keywordTimerRef = useRef(null);
  const requestRef = useRef({ id: 0, controller: null });
  const gridRef = useRef(null);
  const mountedRef = useRef(true);
  const searchParamsWriterRef = useRef(setSearchParams);

  useEffect(() => { searchParamsWriterRef.current = setSearchParams; }, [setSearchParams]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(keywordTimerRef.current);
      requestRef.current.id += 1;
      requestRef.current.controller?.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api.getStrict('/sites', { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setSites(Array.isArray(data) ? data : []);
    }).catch(() => { if (!controller.signal.aborted) setSites([]); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    setFilters(applied);
    setDetail(null);
  }, [applied, archiveMode]);

  useEffect(() => {
    setSelectedIds([]);
  }, [archiveMode, filterQuery, page, view]);

  const writeUrl = useCallback((nextFilters, nextView = view, nextMode = archiveMode, nextPage = 1) => {
    const params = new URLSearchParams();
    if (nextFilters.keyword) params.set('keyword', nextFilters.keyword);
    if (nextFilters.site_id) params.set('site_id', nextFilters.site_id);
    if (nextFilters.business_type) params.set('business_type', nextFilters.business_type);
    if (nextFilters.date_range?.[0] && nextFilters.date_range?.[1]) {
      params.set('date_from', nextFilters.date_range[0].format('YYYY-MM-DD'));
      params.set('date_to', nextFilters.date_range[1].format('YYYY-MM-DD'));
    }
    if (nextView === 'grid') params.set('view', 'grid');
    if (nextMode === 'history') params.set('scope', 'history');
    if (nextPage > 1) params.set('page', String(nextPage));
    setSearchParams(params, { replace: true });
  }, [archiveMode, setSearchParams, view]);

  const load = useCallback(async () => {
    requestRef.current.controller?.abort();
    const controller = new AbortController();
    const requestId = requestRef.current.id + 1;
    requestRef.current = { id: requestId, controller };
    let correctingPage = false;
    setLoading(true);
    try {
      const data = await api.getStrict(`/attachments?${requestQuery}`, { signal: controller.signal });
      if (!mountedRef.current || requestId !== requestRef.current.id) return;
      if (!Array.isArray(data?.items) || !Number.isSafeInteger(data.total) || data.total < 0) {
        throw new Error('影像档案分页数据格式异常，请重试');
      }
      const requestedPage = Number(new URLSearchParams(requestQuery).get('page'));
      const lastPage = archiveLastPage(data.total);
      if (requestedPage > lastPage) {
        correctingPage = true;
        searchParamsWriterRef.current((current) => {
          const next = new URLSearchParams(current);
          if (lastPage > 1) next.set('page', String(lastPage));
          else next.delete('page');
          return next;
        }, { replace: true });
        return;
      }
      setItems(data.items);
      setTotal(data.total);
      setError('');
      gridRef.current?.scrollTo({ top: 0 });
    } catch (requestError) {
      if (mountedRef.current && requestId === requestRef.current.id && requestError?.code !== 'REQUEST_ABORTED') {
        setError(requestError.message || '影像档案加载失败');
      }
    } finally {
      if (mountedRef.current && requestId === requestRef.current.id && !correctingPage) setLoading(false);
    }
  }, [requestQuery]);

  useEffect(() => {
    load();
    return () => {
      requestRef.current.id += 1;
      requestRef.current.controller?.abort();
    };
  }, [load]);

  const applyFilters = () => {
    clearTimeout(keywordTimerRef.current);
    const next = { ...filters, keyword: filters.keyword.trim() };
    setFilters(next);
    writeUrl(next);
  };

  const updateImmediateFilter = (patch) => {
    clearTimeout(keywordTimerRef.current);
    const next = { ...filters, ...patch };
    setFilters(next);
    writeUrl(next);
  };

  const updateKeyword = (value) => {
    const next = { ...filters, keyword: value };
    setFilters(next);
    clearTimeout(keywordTimerRef.current);
    if (!value) {
      writeUrl(next);
      return;
    }
    keywordTimerRef.current = setTimeout(() => {
      const committed = { ...next, keyword: value.trim() };
      setFilters(committed);
      writeUrl(committed);
    }, 400);
  };

  const resetFilters = () => {
    clearTimeout(keywordTimerRef.current);
    const next = { ...emptyFilters };
    setFilters(next);
    writeUrl(next);
  };

  const changeView = (nextView) => {
    clearTimeout(keywordTimerRef.current);
    const committed = { ...filters, keyword: filters.keyword.trim() };
    const keywordChanged = committed.keyword !== applied.keyword;
    setFilters(committed);
    writeUrl(committed, nextView, archiveMode, keywordChanged ? 1 : page);
  };

  const submitRejectedPurge = async () => {
    const reason = normalizeDeleteReason(purgeReason);
    if (!archivePurgeEligibility(purgePreview, user).allowed || !reason || purgeRef.current) return;
    purgeRef.current = true;
    setPurgeError('');
    setPurgeLoading(true);
    try {
      await api.postStrict(`/attachments/${detail.id}/purge`, { reason });
      message.success('历史影像已彻底清理，删除摘要已保留');
      setPurgeOpen(false); setDetail(null);
      await load();
    } catch (requestError) {
      setPurgeError(requestError?.message || '彻底删除失败，请重试');
    } finally {
      purgeRef.current = false;
      if (mountedRef.current) setPurgeLoading(false);
    }
  };

  const openRejectedPurge = async () => {
    if (!archivePurgeEligibility(purgePreview, user).allowed || purgePreviewLoading) return;
    setPurgeReason(''); setPurgeError(''); setPurgeOpen(true);
  };

  const loadBatchPreview = async (attachmentIds = selectedIds) => {
    const preview = await api.postStrict('/attachments/purge-batch/preview', {
      attachment_ids: attachmentIds,
    });
    if (mountedRef.current) setBatchPreview(preview);
    return preview;
  };

  const openBatchPurge = async () => {
    if (!selectedIds.length || batchLoading) return;
    setBatchPurgeOpen(true);
    setBatchPreview(null);
    setBatchReason('');
    setBatchError('');
    setBatchResultUnknown(false);
    batchKeyRef.current = `archive-purge-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    batchRequestRef.current = null;
    setBatchLoading(true);
    try {
      await loadBatchPreview(selectedIds);
    } catch (requestError) {
      setBatchError(requestError?.message || '批量清理资格读取失败，请重试');
    } finally {
      if (mountedRef.current) setBatchLoading(false);
    }
  };

  const submitBatchPurge = async () => {
    const reason = normalizeDeleteReason(batchReason);
    const request = batchResultUnknown ? batchRequestRef.current : {
      attachment_ids: [...selectedIds],
      reason,
      idempotency_key: batchKeyRef.current,
    };
    const canSubmit = batchResultUnknown
      ? Boolean(request)
      : Boolean(batchPreview?.can_purge && reason);
    if (!canSubmit || batchLoading) return;
    if (!batchResultUnknown) batchRequestRef.current = request;
    setBatchLoading(true);
    setBatchError('');
    try {
      const result = await api.postStrict('/attachments/purge-batch', request);
      if (result.temporary_cleanup_pending > 0) {
        message.warning(`已清理 ${result.purged_count} 条记录，但有 ${result.temporary_cleanup_pending} 个临时文件待管理员处理`);
      } else {
        message.success(`已彻底清理 ${result.purged_count} 条历史影像，删除摘要已保留`);
      }
      setBatchResultUnknown(false);
      batchRequestRef.current = null;
      setBatchPurgeOpen(false);
      setSelectedIds([]);
      await load();
    } catch (requestError) {
      const uncertainResult = ['NETWORK_ERROR', 'REQUEST_TIMEOUT', 'INVALID_JSON_RESPONSE']
        .includes(requestError?.code);
      if (uncertainResult) {
        setBatchResultUnknown(true);
        setBatchError('未能确认服务端处理结果。请使用原请求确认结果或重试；期间不要重新选择或修改原因。');
      } else {
        setBatchResultUnknown(false);
        batchRequestRef.current = null;
        setBatchError(requestError?.message || '批量清理未执行，请根据提示处理后重试');
        try { await loadBatchPreview(selectedIds); } catch { /* Preserve the submit error and selection. */ }
      }
    } finally {
      if (mountedRef.current) setBatchLoading(false);
    }
  };

  useEffect(() => {
    setPurgePreview(null);
    setPurgeError('');
    if (!detail || archiveMode !== 'history' || !hasAdminRole(user)) return undefined;
    const controller = new AbortController();
    setPurgePreviewLoading(true);
    api.getStrict(`/attachments/${detail.id}/purge`, { signal: controller.signal }).then(preview => {
      if (!controller.signal.aborted) setPurgePreview(preview);
    }).catch(requestError => {
      if (!controller.signal.aborted && requestError?.code !== 'REQUEST_ABORTED') {
        setPurgeError(requestError?.message || '清理资格读取失败，请重试');
      }
    }).finally(() => {
      if (!controller.signal.aborted && mountedRef.current) setPurgePreviewLoading(false);
    });
    return () => controller.abort();
  }, [archiveMode, detail, user]);

  const columns = useMemo(() => [
    {
      title: '影像', dataIndex: 'stored_path', width: 68,
      render: (path, item) => <Image src={path} width={44} height={44}
        alt={displayTitle(item)} style={{ objectFit: 'cover', borderRadius: 4 }} />,
    },
    {
      title: '档案名称', ellipsis: true,
      render: (_, item) => <div><Text strong>{displayTitle(item)}</Text>
        <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{item.original_filename || item.filename || '-'}</Text></div>,
    },
    {
      title: '站点 / 关联业务', width: 200,
      render: (_, item) => <div><Text>{item.site_name || '未关联站点'}</Text>
        <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{sourceLabel(item)} · {itemLabel(item)}</Text></div>,
    },
    {
      title: '拍摄时间', dataIndex: 'taken_at', width: 170,
      render: value => value || '-',
    },
    {
      title: '来源', width: 150,
      render: (_, item) => archiveCaptureLabel(item.capture_source),
    },
    ...(archiveMode === 'history' ? [{
      title: '记录状态', width: 130,
      render: (_, item) => { const status = historyStatus(item); return <Tag color={status.color}>{status.label}</Tag>; },
    }] : []),
    {
      title: '操作', width: 120,
      render: (_, item) => <Space><Button type="link" size="small" onClick={() => setDetail(item)}>详情</Button>
        <Button type="link" size="small" onClick={() => window.open(item.stored_path, '_blank')}>下载</Button></Space>,
    },
  ], [archiveMode]);

  const emptyActions = <Space wrap>
    {hasAppliedFilters && <Button type="primary" icon={<ReloadOutlined />} onClick={resetFilters}>重置筛选</Button>}
    <Button onClick={() => navigate('/audit?tab=inspection')}>前往巡检质控</Button>
    <Button onClick={() => navigate('/audit?tab=workorder')}>前往工单审核</Button>
  </Space>;
  const currentPageIds = items.map(item => item.id);
  const currentPageSelected = currentPageIds.length > 0
    && currentPageIds.every(id => selectedIds.includes(id));
  const currentAttachmentById = new Map(items.map(item => [item.id, item]));
  const batchPreviewBlocked = Boolean(batchPreview && !batchPreview.can_purge);
  const batchHasSubmitAction = batchResultUnknown || Boolean(batchPreview?.can_purge);

  return (
    <WorkspacePage title="影像档案" subtitle="集中查询巡检与工单等业务留存的影像记录">
      <WorkspaceToolbar className="archive-toolbar" actions={<>
        {archiveMode === 'history' && hasAdminRole(user) && <Button
          disabled={!currentPageIds.length}
          onClick={() => setSelectedIds(currentPageSelected ? [] : currentPageIds)}>
          {currentPageSelected ? '取消当前页' : '选择当前页'}
        </Button>}
        {archiveMode === 'history' && hasAdminRole(user) && <Button danger disabled={!selectedIds.length}
          onClick={openBatchPurge}>批量清理 ({selectedIds.length})</Button>}
        <Segmented value={archiveMode} onChange={(nextMode) => {
          clearTimeout(keywordTimerRef.current);
          const committed = { ...filters, keyword: filters.keyword.trim() };
          setFilters(committed); setDetail(null); writeUrl(committed, view, nextMode);
        }} options={[{ value: 'current', label: '当前档案' }, { value: 'history', label: '历史记录' }]} />
        <Button icon={<ReloadOutlined />} onClick={resetFilters}>重置</Button>
        <Segmented value={view} onChange={changeView}
          options={[{ value: 'table', icon: <FileTextOutlined />, label: '表格' },
            { value: 'grid', icon: <AppstoreOutlined />, label: '网格' }]} />
      </>}>
        <FilterField label="影像搜索"><Input aria-label="影像搜索" placeholder="搜索档案名称或描述" allowClear
          prefix={<SearchOutlined />} value={filters.keyword}
          onChange={event => updateKeyword(event.target.value)}
          onPressEnter={applyFilters} /></FilterField>
        <FilterField label="站点"><Select aria-label="站点" placeholder="全部站点" allowClear showSearch optionFilterProp="label"
          value={filters.site_id} options={listFilterOptions('全部站点', sites.map(site => ({ value: site.id, label: site.name })))}
          onChange={value => updateImmediateFilter({ site_id: listFilterValue(value) })} /></FilterField>
        <FilterField label="业务来源"><Select aria-label="业务来源" placeholder="全部业务来源" allowClear
          value={filters.business_type} options={listFilterOptions('全部业务来源', BUSINESS_OPTIONS)}
          onChange={value => updateImmediateFilter({ business_type: listFilterValue(value) })} /></FilterField>
        <FilterField label="可信拍摄日期"><RangePicker aria-label="可信拍摄日期" value={filters.date_range}
          placeholder={['拍摄开始', '拍摄结束']}
          onChange={value => updateImmediateFilter({ date_range: value })} /></FilterField>
      </WorkspaceToolbar>

      {error && <WorkspaceEmpty type="error" description={error} onRefresh={load} />}
      {!error && <div className="archive-results"><Spin spinning={loading}>
        {!loading && items.length === 0 ? <WorkspaceEmpty
          type={hasAppliedFilters ? 'filtered' : 'empty'}
          description={hasAppliedFilters ? '没有符合当前筛选条件的记录' : archiveMode === 'current'
            ? '当前没有已完成业务审核且仍有效的影像；巡检照片在巡检质控审核，工单照片随工单审核。'
            : '当前没有驳回、作废、替换、待审或补充材料记录。'}
          onRefresh={load}>{emptyActions}</WorkspaceEmpty> : view === 'table' ? <WorkspaceTable rowKey="id" dataSource={items} columns={columns}
          loading={loading} fillHeight emptyType={hasAppliedFilters ? 'filtered' : 'empty'}
          onRefresh={load} scroll={{ x: 900, y: 'calc(100vh - 330px)' }}
          rowSelection={archiveMode === 'history' && hasAdminRole(user) ? {
            selectedRowKeys: selectedIds,
            onChange: setSelectedIds,
          } : undefined}
          pagination={total > ARCHIVE_PAGE_SIZE ? { current: page, pageSize: ARCHIVE_PAGE_SIZE, total, showSizeChanger: false, disabled: loading,
            showTotal: value => `共 ${value} 条`, onChange: (next) => writeUrl(applied, view, archiveMode, next) } : false} /> :
          <div className="archive-grid-view">
          <div className="archive-grid-scroll" ref={gridRef} role="region" aria-label="影像档案网格" tabIndex={0}>
          <div className="archive-grid-items">{items.map(item =>
            <Card key={item.id} size="small" hoverable
              extra={archiveMode === 'history' && hasAdminRole(user) ? <Checkbox
                aria-label={`选择历史影像 ${displayTitle(item)}`}
                checked={selectedIds.includes(item.id)}
                onChange={(event) => setSelectedIds(current => event.target.checked
                  ? [...current, item.id] : current.filter(id => id !== item.id))} /> : null}
              cover={<button type="button" onClick={() => setDetail(item)}
              style={{ width: '100%', height: 170, padding: 0, border: 0, overflow: 'hidden', cursor: 'pointer' }}>
              <img src={item.stored_path} alt={displayTitle(item)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            </button>} actions={[
              <Button key="detail" type="text" icon={<PictureOutlined />} onClick={() => setDetail(item)}>详情</Button>,
              <Button key="download" type="text" icon={<DownloadOutlined />} onClick={() => window.open(item.stored_path, '_blank')}>下载</Button>,
            ]}>
              <Card.Meta title={displayTitle(item)} description={<Space direction="vertical" size={2}>
                <span>{item.site_name || '未关联站点'} · {item.taken_at || '-'}</span>
                {archiveMode === 'history' && (() => { const status = historyStatus(item); return <Tag color={status.color}>{status.label}</Tag>; })()}
              </Space>} />
            </Card>
          )}</div></div>
          <div className="archive-grid-footer">
            <Text type="secondary">共 {total} 条</Text>
            {total > ARCHIVE_PAGE_SIZE && <Pagination current={page} pageSize={ARCHIVE_PAGE_SIZE} total={total}
              showSizeChanger={false} showLessItems responsive size="small" disabled={loading}
              onChange={(next) => writeUrl(applied, view, archiveMode, next)} />}
          </div></div>}
      </Spin></div>}

      <Modal title={detail ? displayTitle(detail) : '影像详情'} open={Boolean(detail)}
        onCancel={() => setDetail(null)} width={760}
        footer={<Space>{archivePurgeEligibility(purgePreview, user, purgeLoading).allowed &&
          <Button danger onClick={openRejectedPurge}>彻底清理历史影像</Button>}
          <Button onClick={() => setDetail(null)}>关闭</Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={() => {
            if (!detail?.stored_path) return message.error('文件地址不可用');
            window.open(detail.stored_path, '_blank');
          }}>下载文件</Button></Space>}>
        {detail && <>
          <div style={{ textAlign: 'center', marginBottom: 16 }}><Image src={detail.stored_path}
            alt={displayTitle(detail)} style={{ maxHeight: 420, maxWidth: '100%' }} /></div>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="拍摄时间">{detail.taken_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="上传时间">{detail.created_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="采集来源">{archiveCaptureLabel(detail.capture_source)}</Descriptions.Item>
            <Descriptions.Item label="站点">{detail.site_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="业务来源">{sourceLabel(detail)}</Descriptions.Item>
            <Descriptions.Item label="检查项">{itemLabel(detail)}</Descriptions.Item>
            <Descriptions.Item label="关联计划">{detail.plan_name || (detail.plan_id ? `#${detail.plan_id}` : '-')}</Descriptions.Item>
            <Descriptions.Item label="上传人">{detail.uploader_name || detail.uploader_real_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="记录状态">{archiveMode === 'current' ? '当前有效' : historyStatus(detail).label}</Descriptions.Item>
            <Descriptions.Item label="文件大小">{formatSize(detail.file_size)}</Descriptions.Item>
            {archiveMode === 'history' && <Descriptions.Item label="历史原因" span={2}>
              {historyStatus(detail).reason || '未记录具体原因'}
            </Descriptions.Item>}
            {archiveMode === 'history' && hasAdminRole(user) && <Descriptions.Item label="清理资格" span={2}>
              {purgePreviewLoading ? '正在核对服务端资格…' : purgeError || (purgePreview?.can_purge
                ? `${purgePreview.qualification_reason || '服务端已确认可清理'}；${purgePreview.impact || '删除前仍会重新核验'}`
                : purgePreview?.block_reason || '当前记录不可彻底清理')}
            </Descriptions.Item>}
            <Descriptions.Item label="原始文件" span={2}>{detail.original_filename || detail.filename || '-'}</Descriptions.Item>
          </Descriptions>
        </>}
      </Modal>
      <Modal open={purgeOpen} title="彻底清理历史影像"
        okText="确认彻底清理"
        cancelText="返回" okButtonProps={{ danger: true, disabled: !normalizeDeleteReason(purgeReason) || purgePreviewLoading }}
        confirmLoading={purgeLoading || purgePreviewLoading}
        closable={!purgeLoading && !purgePreviewLoading} maskClosable={!purgeLoading && !purgePreviewLoading} destroyOnHidden
        onOk={submitRejectedPurge} onCancel={() => { if (!purgeLoading) setPurgeOpen(false); }}>
        <Typography.Paragraph type="danger">
          此操作不可恢复。服务端将再次核验记录状态、业务关联和文件引用，并保留不可改写的删除摘要。
        </Typography.Paragraph>
        <Input.TextArea aria-label="清理原因" value={purgeReason} maxLength={200} showCount
          placeholder="请填写清理原因" onChange={event => setPurgeReason(event.target.value)} />
        {purgeError && <Typography.Paragraph type="danger">{purgeError}</Typography.Paragraph>}
      </Modal>
      <Modal open={batchPurgeOpen} title="批量彻底清理历史影像"
        okText={batchResultUnknown ? '确认结果 / 重试' : `确认清理 ${batchPreview?.purgeable_count || 0} 条`}
        cancelText="返回"
        okButtonProps={{ danger: true, disabled: batchResultUnknown
          ? !batchRequestRef.current
          : !batchPreview?.can_purge || !normalizeDeleteReason(batchReason) }}
        cancelButtonProps={{ disabled: batchResultUnknown }}
        confirmLoading={batchLoading}
        closable={!batchLoading && !batchResultUnknown}
        maskClosable={!batchLoading && !batchResultUnknown}
        keyboard={!batchLoading && !batchResultUnknown}
        destroyOnHidden
        footer={(_, { CancelBtn, OkBtn }) => <Space>
          <CancelBtn />
          {batchHasSubmitAction && <OkBtn />}
        </Space>}
        onOk={submitBatchPurge}
        onCancel={() => { if (!batchLoading && !batchResultUnknown) setBatchPurgeOpen(false); }}>
        {batchLoading && !batchPreview ? <Spin /> : <>
          {batchPreviewBlocked && <Alert type="warning" showIcon
            message={batchPreview.blocked_count > 0
              ? `有 ${batchPreview.blocked_count} 条不可清理，请返回移除后重试`
              : '当前所选内容不可清理，请返回重新选择'}
            description={<Space direction="vertical" size={2}>{(batchPreview.items || [])
              .filter(item => !item.can_purge)
              .map(item => {
                const attachment = currentAttachmentById.get(item.attachment_id);
                return <div key={item.attachment_id}>
                  <Text strong>{attachment
                    ? displayTitle(attachment)
                    : `未找到对应照片（记录 #${item.attachment_id}）`}</Text>
                  <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                    {attachment
                      ? `${attachment.site_name || '未关联站点'} · ${attachment.taken_at || '-'} · 记录 #${item.attachment_id}`
                      : '当前列表中没有这条记录，请返回刷新后重新选择'}
                  </Text>
                  <Text>阻断原因：{item.block_reason || '服务端未提供具体原因'}</Text>
                </div>;
              })}</Space>} />}
          {batchPreview?.can_purge && <Typography.Paragraph>
            将彻底清理 {batchPreview.purgeable_count} 条历史影像，此操作不可恢复。提交前服务端会再次核验全部记录，任一项状态变化时整批不执行；每条记录均保留审计快照，共享文件不会误删。
          </Typography.Paragraph>}
          {batchPreview?.can_purge && <div style={{ marginBottom: 24 }}>
            <Input.TextArea aria-label="批量清理原因" value={batchReason} maxLength={200} showCount
              disabled={batchResultUnknown}
              placeholder="请填写本批共同清理原因"
              onChange={event => setBatchReason(event.target.value)} />
          </div>}
          {batchError && !batchPreviewBlocked && <Typography.Paragraph type="danger">{batchError}</Typography.Paragraph>}
        </>}
      </Modal>
    </WorkspacePage>
  );
}
