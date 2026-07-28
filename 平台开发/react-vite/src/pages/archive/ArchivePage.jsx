import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card, Input, Select, Button, Space, Tag, Row, Col,
  App, Modal, Typography, Spin, Empty, Image, DatePicker,
  Badge, Tooltip, Statistic, Table, Checkbox, Radio,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined,
  PictureOutlined, VideoCameraOutlined, FolderOpenOutlined,
  CalendarOutlined, EnvironmentOutlined, UserOutlined,
  FileTextOutlined, DownloadOutlined, InboxOutlined, RollbackOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import dayjs from 'dayjs';
import FilterBar from '../../components/FilterBar';
import { statusColors } from '../../theme/tokens';
import {
  pageRootStyle, tableCardStyle, tableCardBody,
  filterInputWidth, filterSelectWidth, filterSmallSelectWidth,
} from '../../services/pageStyles';
import {
  attachmentSourceTypeMap, attachmentCategoryColor, attachmentCategoryMap,
  attachmentReviewStatusMap, ATTACHMENT_SOURCE_OPTIONS, ATTACHMENT_CATEGORY_OPTIONS,
  ATTACHMENT_REVIEW_STATUS_OPTIONS,
} from '../../services/constants';

const { Text, Title } = Typography;
const { RangePicker } = DatePicker;

// 上传人渲染
const uploaderOf = (it) => it.uploader_name || '-';

// 审核状态标签（仅用 antd 预设色名，主题无关，符合规范 §7）
const ReviewTag = ({ it }) => {
  if (!it.review_required) return <Tag color="default">无需审核</Tag>;
  const map = attachmentReviewStatusMap;
  const color = it.review_status === 'pending' ? 'processing'
    : it.review_status === 'approved' ? 'green' : 'red';
  return <Tag color={color}>{map[it.review_status] || it.review_status}</Tag>;
};

export default function ArchivePage() {
  const { tokens, isDark } = useTheme();
  const { user } = useAuth();
  const { modal, message } = App.useApp();
  const isAdmin = user?.role === 'admin';

  // 筛选条件
  const [filters, setFilters] = useState({
    site_id: undefined,
    category: undefined,
    source_type: undefined,
    review_status: undefined,
    keyword: '',
    date_range: null,
    archived: '',
    quickDate: undefined,
  });
  const [archiveVisible, setArchiveVisible] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [archiveReason, setArchiveReason] = useState('');
  const [sites, setSites] = useState([]);
  const [stats, setStats] = useState(null);
  const [list, setList] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [view, setView] = useState('grid'); // grid | table
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewItem, setPreviewItem] = useState(null);

  const [tableWrapRef, tableBodyHeight] = useTableAutoHeight({ headerOffset: 40, deps: [list.length] });

  // 加载站点列表
  useEffect(() => {
    api.get('/sites').then(data => { if (Array.isArray(data)) setSites(data); });
  }, []);

  // 加载统计
  useEffect(() => {
    api.get('/attachments/stats').then(setStats);
  }, []);

  // 加载附件列表
  const loadList = useCallback(async (p = 1) => {
    setLoading(true);
    const params = new URLSearchParams();
    params.set('page', p);
    params.set('limit', 30);
    if (filters.site_id) params.set('site_id', filters.site_id);
    if (filters.category) params.set('category', filters.category);
    if (filters.source_type) params.set('source_type', filters.source_type);
    if (filters.review_status) params.set('review_status', filters.review_status);
    if (filters.keyword) params.set('keyword', filters.keyword);
    if (filters.date_range && filters.date_range[0]) {
      params.set('date_from', filters.date_range[0].format('YYYY-MM-DD'));
      params.set('date_to', filters.date_range[1].format('YYYY-MM-DD'));
    }
    if (filters.archived !== '' && filters.archived !== undefined) {
      params.set('archived', filters.archived);
    }
    const data = await api.get(`/attachments?${params.toString()}`);
    if (data) {
      setList(data.items || []);
      setTotal(data.total || 0);
      setSelectedRowKeys(keys => keys.filter(k => (data.items || []).some(i => i.id === k)));
      setPage(p);
    }
    setLoading(false);
  }, [filters]);

  useEffect(() => { loadList(1); }, [loadList]);

  const refreshStats = () => api.get('/attachments/stats').then(setStats);

  // 批量归档
  const batchArchive = async () => {
    if (!selectedRowKeys.length) return;
    await Promise.all(selectedRowKeys.map(id => api.post(`/attachments/${id}/archive`, { archive_reason: '批量归档' })));
    message.success('已批量归档');
    setSelectedRowKeys([]);
    loadList(page); refreshStats();
  };

  // 打开预览
  const handlePreview = (item) => { setPreviewItem(item); setPreviewVisible(true); };

  // 打开归档弹窗（填归档原因）
  const openArchive = (item) => { setArchiveTarget(item); setArchiveReason(''); setArchiveVisible(true); };

  // 确认归档
  const confirmArchive = async () => {
    if (!archiveTarget) return;
    const res = await api.post(`/attachments/${archiveTarget.id}/archive`, { archive_reason: archiveReason });
    if (res && res.success) {
      message.success('已归档');
      setArchiveVisible(false); setArchiveTarget(null);
      setPreviewItem(it => it ? { ...it, archived: 1 } : it);
      loadList(page); refreshStats();
    } else {
      message.error((res && res.error) || '归档失败');
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
        const res = await api.post(`/attachments/${item.id}/unarchive`);
        if (res && res.success) {
          message.success('已取消归档');
          setPreviewItem(it => it ? { ...it, archived: 0 } : it);
          loadList(page); refreshStats();
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
        </Space>
      ),
    },
    {
      title: '关联信息', key: 'association', width: 160, ellipsis: true,
      render: (_, it) => (
        <div>
          <Text ellipsis style={{ display: 'block', fontSize: 12 }}>{it.site_name || '未关联站点'}</Text>
          <Text type="secondary" ellipsis style={{ display: 'block', fontSize: 11 }}>{uploaderOf(it)}</Text>
        </div>
      ),
    },
    { title: '拍摄时间', dataIndex: 'taken_at', key: 'taken_at', width: 120,
      render: (v) => v || '—' },
    {
      title: '状态', key: 'status', width: 104,
      render: (_, it) => (
        <Space direction="vertical" size={2}>
          <ReviewTag it={it} />
          {it.archived ? <Tag color="green">已归档</Tag> : <Tag>未归档</Tag>}
        </Space>
      ),
    },
    {
      title: '操作', key: 'op', width: isAdmin ? 128 : 64,
      render: (_, it) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => handlePreview(it)}>预览</Button>
          {isAdmin && (it.archived
            ? <Button type="link" size="small" onClick={() => handleUnarchive(it)}>取消归档</Button>
            : <Button type="link" size="small" onClick={() => openArchive(it)}>归档</Button>)}
        </Space>
      ),
    },
  ];

  const rowSelection = isAdmin ? {
    selectedRowKeys,
    onChange: (keys) => setSelectedRowKeys(keys),
  } : undefined;

  return (
    <div style={pageRootStyle}>
      {/* 统计卡片：响应式，避免小屏挤压 */}
      {stats && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic title="影像总数" value={stats.total || 0} prefix={<PictureOutlined />}
                valueStyle={{ color: tokens.colorPrimary }} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic title="分类数" value={Object.keys(stats.by_category || {}).length} prefix={<FolderOpenOutlined />} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic title="关联工单" value={(stats.by_source && stats.by_source.workorder) || 0} prefix={<FileTextOutlined />} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic title="关联巡检" value={(stats.by_source && stats.by_source.inspection) || 0} prefix={<EnvironmentOutlined />} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic title="已归档" value={stats.archived || 0} prefix={<InboxOutlined />}
                valueStyle={{ color: (stats.archived || 0) > 0 ? statusColors.info[isDark ? 'dark' : 'light'] : statusColors.success[isDark ? 'dark' : 'light'] }} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6} lg={4}>
            <Card size="small">
              <Statistic title="待审核" value={stats.review_pending || 0} prefix={<PictureOutlined />}
                valueStyle={{ color: (stats.review_pending || 0) > 0 ? statusColors.warning[isDark ? 'dark' : 'light'] : statusColors.success[isDark ? 'dark' : 'light'] }} />
            </Card>
          </Col>
        </Row>
      )}

      {/* 筛选栏 + 工具 */}
      <FilterBar
        style={{ marginBottom: 12 }}
        extra={(
          <Space size={8} wrap>
            <Button type="primary" icon={<SearchOutlined />} onClick={() => loadList(1)}>搜索</Button>
            <Button icon={<ReloadOutlined />} onClick={() => {
              setFilters({ site_id: undefined, category: undefined, source_type: undefined, review_status: undefined, keyword: '', date_range: null, archived: '', quickDate: undefined });
            }}>重置</Button>
            <Radio.Group value={view} onChange={e => setView(e.target.value)} optionType="button" buttonStyle="solid" size="small">
              <Radio.Button value="grid"><AppstoreOutlined /> 网格</Radio.Button>
              <Radio.Button value="table"><FileTextOutlined /> 表格</Radio.Button>
            </Radio.Group>
          </Space>
        )}
      >
        <Input placeholder="搜索文件名或描述..." prefix={<SearchOutlined />}
          value={filters.keyword} onChange={e => setFilters(f => ({ ...f, keyword: e.target.value }))}
          onPressEnter={() => loadList(1)} style={{ width: filterInputWidth }} allowClear />
        <Select placeholder="选择站点" allowClear showSearch optionFilterProp="label" style={{ width: filterSelectWidth }}
          value={filters.site_id} onChange={v => setFilters(f => ({ ...f, site_id: v }))}
          options={sites.map(s => ({ value: s.id, label: s.name }))} />
        <Select placeholder="分类" allowClear style={{ width: filterSelectWidth }}
          value={filters.category} onChange={v => setFilters(f => ({ ...f, category: v }))}
          options={ATTACHMENT_CATEGORY_OPTIONS} />
        <Select placeholder="审核状态" allowClear style={{ width: filterSmallSelectWidth }}
          value={filters.review_status} onChange={v => setFilters(f => ({ ...f, review_status: v }))}
          options={ATTACHMENT_REVIEW_STATUS_OPTIONS} />
        <Select placeholder="归档状态" style={{ width: filterSmallSelectWidth }}
          value={filters.archived} onChange={v => setFilters(f => ({ ...f, archived: v }))}
          options={[{ value: '', label: '全部状态' }, { value: '0', label: '未归档' }, { value: '1', label: '已归档' }]} />
        <RangePicker value={filters.date_range} onChange={v => setFilters(f => ({ ...f, date_range: v }))}
          placeholder={['开始日期', '结束日期']} />
      </FilterBar>

      {/* 快捷日期 */}
      <div style={{ marginBottom: 12, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        {['今日', '昨日', '近7天', '近30天', '本月'].map(label => (
          <Button key={label} size="small" type={filters.quickDate === label ? 'primary' : 'default'}
            onClick={() => {
              let start = null, end = null;
              const today = dayjs();
              switch (label) {
                case '今日': start = today.startOf('day'); end = today.endOf('day'); break;
                case '昨日': start = today.subtract(1, 'day').startOf('day'); end = today.subtract(1, 'day').endOf('day'); break;
                case '近7天': start = today.subtract(6, 'day').startOf('day'); end = today.endOf('day'); break;
                case '近30天': start = today.subtract(29, 'day').startOf('day'); end = today.endOf('day'); break;
                case '本月': start = today.startOf('month'); end = today.endOf('month'); break;
                default: break;
              }
              setFilters(f => ({ ...f, quickDate: label, date_range: start && end ? [start, end] : null }));
            }}>
            {label}
          </Button>
        ))}
        {filters.quickDate && (
          <Button size="small" type="text" onClick={() => setFilters(f => ({ ...f, quickDate: undefined, date_range: null }))}>清除快捷</Button>
        )}
      </div>

      {/* 批量操作条 */}
      {selectedRowKeys.length > 0 && (
        <div style={{ marginBottom: 12, padding: '8px 12px', background: tokens.colorFillSecondary, borderRadius: tokens.borderRadius, display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
          <Text strong>已选 {selectedRowKeys.length} 项</Text>
          <Button size="small" icon={<InboxOutlined />} onClick={batchArchive} disabled={!isAdmin}>批量归档</Button>
          <Button size="small" type="text" onClick={() => setSelectedRowKeys([])}>取消选择</Button>
        </div>
      )}

      {/* 图片网格 / 表格 */}
      <Spin spinning={loading}>
        {list.length === 0 && !loading && (
          <Empty description="暂无影像资料" style={{ marginTop: 80 }} />
        )}
        {view === 'grid' ? (
          <Row gutter={[12, 12]}>
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
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          overflow: 'hidden', cursor: 'pointer', position: 'relative',
                        }}
                        onClick={() => handlePreview(item)}
                      >
                        {isAdmin && <Checkbox
                          checked={checked}
                          onClick={e => e.stopPropagation()}
                          onChange={e => {
                            setSelectedRowKeys(keys => e.target.checked
                              ? [...new Set([...keys, item.id])]
                              : keys.filter(k => k !== item.id));
                          }}
                          style={{ position: 'absolute', top: 8, left: 8, zIndex: 2 }}
                        />}
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
                      </div>
                    }
                    actions={[
                      <Button key="view" type="text" size="small" title="查看"
                        icon={<SearchOutlined />} onClick={() => handlePreview(item)} />,
                      <Button key="download" type="text" size="small" title="下载"
                        icon={<DownloadOutlined />} onClick={() => window.open(item.stored_path, '_blank')} />,
                      isAdmin && (item.archived
                        ? <Button key="unarchive" type="text" size="small" title="取消归档"
                            icon={<RollbackOutlined />} onClick={() => handleUnarchive(item)} />
                        : <Button key="archive" type="text" size="small" title="归档"
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
                            <span>{item.taken_at?.slice(0, 10) || '—'}</span>
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
          <Card style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
            <div ref={tableWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <Table
                rowKey="id" dataSource={list} columns={columns} size="small"
                rowSelection={rowSelection} pagination={false}
                scroll={tableBodyHeight ? { y: tableBodyHeight } : undefined}
              />
            </div>
          </Card>
        )}

        {/* 分页 */}
        {total > 30 && (
          <div style={{ textAlign: 'center', marginTop: 24 }}>
            <Button disabled={page <= 1} onClick={() => loadList(page - 1)} style={{ marginRight: 8 }}>上一页</Button>
            <Text style={{ margin: '0 16px' }}>{page} / {Math.ceil(total / 30)}</Text>
            <Button disabled={page >= Math.ceil(total / 30)} onClick={() => loadList(page + 1)}>下一页</Button>
          </div>
        )}
      </Spin>

      {/* 预览弹窗 */}
      <Modal
        title={previewItem?.description || previewItem?.filename || '附件预览'}
        open={previewVisible}
        onCancel={() => { setPreviewVisible(false); setPreviewItem(null); }}
        footer={null}
        width={800}
        destroyOnClose
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
                <Col span={12}><Text type="secondary">关联站点：</Text><Text>{previewItem.site_name || '-'}</Text></Col>
                <Col span={12}><Text type="secondary">拍摄时间：</Text><Text>{previewItem.taken_at || '-'}</Text></Col>
                <Col span={12}><Text type="secondary">上传人：</Text><Text>{uploaderOf(previewItem)}</Text></Col>
                <Col span={12}><Text type="secondary">审核状态：</Text><ReviewTag it={previewItem} /></Col>
                <Col span={12}><Text type="secondary">归档状态：</Text>{previewItem.archived ? <Tag color="green">已归档</Tag> : <Tag>未归档</Tag>}</Col>
                <Col span={24}><Text type="secondary">描述：</Text><Text>{previewItem.description || '-'}</Text></Col>
                {previewItem.gps_lat && (
                  <Col span={24}><Text type="secondary">GPS位置：</Text><Text>{previewItem.gps_lat?.toFixed(6)}, {previewItem.gps_lng?.toFixed(6)}</Text></Col>
                )}
                <Col span={24}><Text type="secondary">上传时间：</Text><Text>{previewItem.created_at}</Text></Col>
              </Row>
            </Card>
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <Space>
                <Button type="primary" icon={<DownloadOutlined />} onClick={() => window.open(previewItem.stored_path, '_blank')}>下载文件</Button>
                {previewItem.archived
                  ? <Button icon={<RollbackOutlined />} onClick={() => { setPreviewVisible(false); handleUnarchive(previewItem); }}>取消归档</Button>
                  : <Button icon={<InboxOutlined />} onClick={() => { setPreviewVisible(false); openArchive(previewItem); }}>归档</Button>}
              </Space>
            </div>
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
        destroyOnClose
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
    </div>
  );
}
