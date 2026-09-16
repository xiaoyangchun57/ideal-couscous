import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  App, Button, Card, DatePicker, Descriptions, Image, Input, Modal,
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
  archiveHistoryStatus, rejectedPurgeEligibility,
} from './attachmentDeletion';
import { ARCHIVE_PAGE_SIZE, archiveLastPage, archiveNavigationState } from './archivePagination';
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

const SOURCE_LABELS = {
  inspection: '巡检', workorder: '工单', site_photo: '现场影像', calibration: '校准',
  reagent: '试剂作业', vehicle: '车辆记录', maintenance: '设备养护', test: '试验资料',
};

const CAPTURE_LABELS = {
  camera: '小程序现场拍摄', watermark_album: '水印相册', web_upload: '网页补充',
};

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
  return SOURCE_LABELS[item.source_type] || item.source_type || '未记录';
}

function itemLabel(item) {
  return item.item_name || item.description || '未关联检查项';
}

function displayTitle(item) {
  return item.archive_name || item.description || item.original_filename || item.filename || `影像 #${item.id}`;
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
  const [purgeLoading, setPurgeLoading] = useState(false);
  const purgeRef = useRef(false);
  const requestRef = useRef({ id: 0, controller: null });
  const gridRef = useRef(null);
  const mountedRef = useRef(true);
  const searchParamsWriterRef = useRef(setSearchParams);

  useEffect(() => { searchParamsWriterRef.current = setSearchParams; }, [setSearchParams]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
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
    const next = { ...filters, keyword: filters.keyword.trim() };
    setFilters(next);
    writeUrl(next);
  };

  const resetFilters = () => {
    const next = { ...emptyFilters };
    setFilters(next);
    writeUrl(next);
  };

  const submitRejectedPurge = async () => {
    if (!rejectedPurgeEligibility(detail, user).allowed || !purgePreview?.count || purgeRef.current) return;
    purgeRef.current = true;
    setPurgeError('');
    setPurgeLoading(true);
    try {
      const result = await api.postStrict(`/attachments/${detail.id}/purge-rejected-batch`, {});
      message.success(`已清理本次整改 ${result.count || purgePreview.count} 张已驳回照片`);
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
    if (!rejectedPurgeEligibility(detail, user).allowed || purgePreviewLoading) return;
    setPurgeOpen(true); setPurgePreview(null); setPurgeError(''); setPurgePreviewLoading(true);
    try {
      const preview = await api.getStrict(`/attachments/${detail.id}/purge-rejected-batch`);
      setPurgePreview(preview);
    } catch (requestError) {
      setPurgeError(requestError?.message || '整改包范围读取失败，请重试');
    } finally {
      if (mountedRef.current) setPurgePreviewLoading(false);
    }
  };

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
      render: (_, item) => CAPTURE_LABELS[item.capture_source] || item.capture_source || '未记录',
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
    <Button onClick={() => navigate('/audit?tab=inspection')}>前往巡检质控</Button>
    <Button onClick={() => navigate('/audit?tab=workorder')}>前往工单审核</Button>
  </Space>;

  return (
    <WorkspacePage title="影像档案" subtitle="集中查询巡检与工单等业务留存的影像记录">
      <WorkspaceToolbar className="archive-toolbar" actions={<>
        <Segmented value={archiveMode} onChange={(nextMode) => {
          setDetail(null); writeUrl(applied, view, nextMode);
        }} options={[{ value: 'current', label: '当前档案' }, { value: 'history', label: '历史记录' }]} />
        <Button type="primary" icon={<SearchOutlined />} onClick={applyFilters}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={resetFilters}>重置</Button>
        <Segmented value={view} onChange={(next) => writeUrl(applied, next, archiveMode, page)}
          options={[{ value: 'table', icon: <FileTextOutlined />, label: '表格' },
            { value: 'grid', icon: <AppstoreOutlined />, label: '网格' }]} />
      </>}>
        <FilterField label="影像搜索"><Input aria-label="影像搜索" placeholder="搜索档案名称或描述" allowClear
          prefix={<SearchOutlined />} value={filters.keyword}
          onChange={event => setFilters(current => ({ ...current, keyword: event.target.value }))}
          onPressEnter={applyFilters} /></FilterField>
        <FilterField label="站点"><Select aria-label="站点" placeholder="全部站点" allowClear showSearch optionFilterProp="label"
          value={filters.site_id} options={sites.map(site => ({ value: site.id, label: site.name }))}
          onChange={value => setFilters(current => ({ ...current, site_id: value }))} /></FilterField>
        <FilterField label="业务来源"><Select aria-label="业务来源" placeholder="全部业务来源" allowClear
          value={filters.business_type} options={BUSINESS_OPTIONS}
          onChange={value => setFilters(current => ({ ...current, business_type: value }))} /></FilterField>
        <FilterField label="可信拍摄日期"><RangePicker aria-label="可信拍摄日期" value={filters.date_range}
          placeholder={['拍摄开始', '拍摄结束']}
          onChange={value => setFilters(current => ({ ...current, date_range: value }))} /></FilterField>
      </WorkspaceToolbar>

      {error && <WorkspaceEmpty type="error" description={error} onRefresh={load} />}
      {!error && <div className="archive-results"><Spin spinning={loading}>
        {!loading && items.length === 0 ? <WorkspaceEmpty
          type={Object.values(applied).some(Boolean) ? 'filtered' : 'empty'}
          description={archiveMode === 'current'
            ? '当前没有已完成业务审核且仍有效的影像；巡检照片在巡检质控审核，工单照片随工单审核。'
            : '当前没有驳回、作废、替换、待审或补充材料记录。'}
          onRefresh={load}>{emptyActions}</WorkspaceEmpty> : view === 'table' ? <WorkspaceTable rowKey="id" dataSource={items} columns={columns}
          loading={loading} fillHeight emptyType={Object.values(applied).some(Boolean) ? 'filtered' : 'empty'}
          onRefresh={load} scroll={{ x: 900, y: 'calc(100vh - 330px)' }}
          pagination={total > ARCHIVE_PAGE_SIZE ? { current: page, pageSize: ARCHIVE_PAGE_SIZE, total, showSizeChanger: false, disabled: loading,
            showTotal: value => `共 ${value} 条`, onChange: (next) => writeUrl(applied, view, archiveMode, next) } : false} /> :
          <div className="archive-grid-view">
          <div className="archive-grid-scroll" ref={gridRef} role="region" aria-label="影像档案网格" tabIndex={0}>
          <div className="archive-grid-items">{items.map(item =>
            <Card key={item.id} size="small" hoverable cover={<button type="button" onClick={() => setDetail(item)}
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
        footer={<Space>{rejectedPurgeEligibility(detail, user, purgeLoading).allowed &&
          <Button danger onClick={openRejectedPurge}>清理本次整改已驳回照片</Button>}
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
            <Descriptions.Item label="采集来源">{CAPTURE_LABELS[detail.capture_source] || detail.capture_source || '未记录'}</Descriptions.Item>
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
            <Descriptions.Item label="原始文件" span={2}>{detail.original_filename || detail.filename || '-'}</Descriptions.Item>
          </Descriptions>
        </>}
      </Modal>
      <Modal open={purgeOpen} title="清理本次整改已驳回照片"
        okText={purgePreview?.count ? `清理全部 ${purgePreview.count} 张` : '确认清理'}
        cancelText="返回" okButtonProps={{ danger: true, disabled: !purgePreview?.count || purgePreviewLoading }}
        confirmLoading={purgeLoading || purgePreviewLoading}
        closable={!purgeLoading && !purgePreviewLoading} maskClosable={!purgeLoading && !purgePreviewLoading} destroyOnHidden
        onOk={submitRejectedPurge} onCancel={() => { if (!purgeLoading) setPurgeOpen(false); }}>
        <Typography.Paragraph type="danger">
          {purgePreview?.count
            ? `将清理本次整改全部 ${purgePreview.count} 张已驳回照片。此操作不可恢复，仅保留文字删除摘要。`
            : '正在读取本次整改范围…'}
        </Typography.Paragraph>
        {purgeError && <Typography.Paragraph type="danger">{purgeError}</Typography.Paragraph>}
      </Modal>
    </WorkspacePage>
  );
}
