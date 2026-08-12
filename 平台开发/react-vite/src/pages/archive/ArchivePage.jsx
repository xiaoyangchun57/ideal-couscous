import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App, Button, Card, Col, DatePicker, Descriptions, Image, Input, Modal,
  Row, Segmented, Select, Space, Spin, Typography,
} from 'antd';
import {
  AppstoreOutlined, DownloadOutlined, FileTextOutlined, PictureOutlined,
  ReloadOutlined, SearchOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useSearchParams } from 'react-router-dom';
import WorkspacePage, {
  FilterField, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar,
} from '../../components/WorkspacePage';
import { api } from '../../services/api';

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

export default function ArchivePage() {
  const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState(() => filtersFromParams(searchParams));
  const [applied, setApplied] = useState(() => filtersFromParams(searchParams));
  const [sites, setSites] = useState([]);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [view, setView] = useState(() => searchParams.get('view') === 'grid' ? 'grid' : 'table');
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    api.getStrict('/sites').then(data => setSites(Array.isArray(data) ? data : []))
      .catch(() => setSites([]));
  }, []);

  const writeUrl = useCallback((nextFilters, nextView = view) => {
    const params = new URLSearchParams();
    if (nextFilters.keyword) params.set('keyword', nextFilters.keyword);
    if (nextFilters.site_id) params.set('site_id', nextFilters.site_id);
    if (nextFilters.business_type) params.set('business_type', nextFilters.business_type);
    if (nextFilters.date_range?.[0] && nextFilters.date_range?.[1]) {
      params.set('date_from', nextFilters.date_range[0].format('YYYY-MM-DD'));
      params.set('date_to', nextFilters.date_range[1].format('YYYY-MM-DD'));
    }
    if (nextView === 'grid') params.set('view', 'grid');
    setSearchParams(params, { replace: true });
  }, [setSearchParams, view]);

  const load = useCallback(async (nextPage = 1) => {
    setLoading(true);
    const params = new URLSearchParams({
      page: String(nextPage), limit: '100', current_archive: '1',
    });
    if (applied.keyword) params.set('keyword', applied.keyword);
    if (applied.site_id) params.set('site_id', applied.site_id);
    if (applied.business_type) params.set('business_type', applied.business_type);
    if (applied.date_range?.[0] && applied.date_range?.[1]) {
      params.set('date_from', applied.date_range[0].format('YYYY-MM-DD'));
      params.set('date_to', applied.date_range[1].format('YYYY-MM-DD'));
    }
    try {
      const data = await api.getStrict(`/attachments?${params.toString()}`);
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(nextPage);
      setError('');
    } catch (requestError) {
      setError(requestError.message || '正式影像加载失败');
    } finally {
      setLoading(false);
    }
  }, [applied]);

  useEffect(() => { load(1); }, [load]);

  const applyFilters = () => {
    const next = { ...filters, keyword: filters.keyword.trim() };
    setFilters(next);
    setApplied(next);
    writeUrl(next);
  };

  const resetFilters = () => {
    const next = { ...emptyFilters };
    setFilters(next);
    setApplied(next);
    writeUrl(next);
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
    {
      title: '操作', width: 120,
      render: (_, item) => <Space><Button type="link" size="small" onClick={() => setDetail(item)}>详情</Button>
        <Button type="link" size="small" onClick={() => window.open(item.stored_path, '_blank')}>下载</Button></Space>,
    },
  ], []);

  return (
    <WorkspacePage title="正式影像档案" subtitle="仅展示当前有效、来源可信且内容审核通过的影像">
      <WorkspaceToolbar layout="stacked" actions={<>
        <Button type="primary" icon={<SearchOutlined />} onClick={applyFilters}>查询</Button>
        <Button icon={<ReloadOutlined />} onClick={resetFilters}>重置</Button>
        <Segmented value={view} onChange={(next) => { setView(next); writeUrl(applied, next); }}
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

      {error && <WorkspaceEmpty type="error" description={error} onRefresh={() => load(page)} />}
      {!error && <Spin spinning={loading}>
        {view === 'table' ? <WorkspaceTable rowKey="id" dataSource={items} columns={columns}
          loading={loading} emptyType={Object.values(applied).some(Boolean) ? 'filtered' : 'empty'}
          onRefresh={() => load(page)} scroll={{ x: 900, y: 'calc(100vh - 330px)' }}
          pagination={total > 100 ? { current: page, pageSize: 100, total, showSizeChanger: false,
            showTotal: value => `共 ${value} 条`, onChange: load } : false} /> :
          items.length ? <Row gutter={[12, 12]}>{items.map(item => <Col key={item.id} xs={24} sm={12} md={8} lg={6}>
            <Card size="small" hoverable cover={<button type="button" onClick={() => setDetail(item)}
              style={{ width: '100%', height: 170, padding: 0, border: 0, overflow: 'hidden', cursor: 'pointer' }}>
              <img src={item.stored_path} alt={displayTitle(item)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            </button>} actions={[
              <Button key="detail" type="text" icon={<PictureOutlined />} onClick={() => setDetail(item)}>详情</Button>,
              <Button key="download" type="text" icon={<DownloadOutlined />} onClick={() => window.open(item.stored_path, '_blank')}>下载</Button>,
            ]}>
              <Card.Meta title={displayTitle(item)} description={`${item.site_name || '未关联站点'} · ${item.taken_at || '-'}`} />
            </Card>
          </Col>)}</Row> : <WorkspaceEmpty type={Object.values(applied).some(Boolean) ? 'filtered' : 'empty'} onRefresh={() => load(1)} />}
      </Spin>}

      <Modal title={detail ? displayTitle(detail) : '影像详情'} open={Boolean(detail)}
        onCancel={() => setDetail(null)} width={760}
        footer={<Space><Button onClick={() => setDetail(null)}>关闭</Button>
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
            <Descriptions.Item label="内容审核">已通过{detail.reviewed_at ? ` · ${detail.reviewed_at}` : ''}</Descriptions.Item>
            <Descriptions.Item label="文件大小">{formatSize(detail.file_size)}</Descriptions.Item>
            <Descriptions.Item label="原始文件" span={2}>{detail.original_filename || detail.filename || '-'}</Descriptions.Item>
          </Descriptions>
        </>}
      </Modal>
    </WorkspacePage>
  );
}
