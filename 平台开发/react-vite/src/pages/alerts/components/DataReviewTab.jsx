import { useState, useEffect } from 'react';
import {
  Table, Card, Button, Space, Tag, Typography, Row, Col, Statistic,
  message, Modal, Select, Input, Empty, Tooltip, Form, Radio, Popconfirm,
  Drawer, Descriptions,
} from 'antd';
import {
  ReloadOutlined, ThunderboltOutlined, ExperimentOutlined,
  CheckOutlined, CloseOutlined, AuditOutlined, FundProjectionScreenOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { api } from '../../../services/api';
import { metricMap, CONCLUSION_OPTIONS } from '../../../services/constants';
import EChart from '../../../components/EChart';

const { Text, Title } = Typography;

// 状态映射
const STATUS_MAP = {
  pending: { label: 'L1 待审', color: 'default' },
  auto_reviewed: { label: 'L2 待审', color: 'blue' },
  smart_reviewed: { label: 'L3 待审', color: 'purple' },
  manual_reviewed: { label: '已复核', color: 'orange' },
  archived: { label: '已归档', color: 'green' },
};

const AUTO_RESULT_MAP = {
  pass: { label: '通过', color: 'green' },
  reject: { label: '驳回', color: 'red' },
  suspicious: { label: '疑似', color: 'orange' },
};

const SMART_RESULT_MAP = {
  pass: { label: '正常', color: 'green' },
  suspicious: { label: '疑似异常', color: 'orange' },
};

const MANUAL_RESULT_MAP = {
  approved: { label: '已核准', color: 'green' },
  rejected: { label: '已驳回', color: 'red' },
};

const DEVICE_STATUS_MAP = {
  online: '在线',
  offline: '离线',
  unknown: '未知',
};

const MAINTENANCE_STATUS_MAP = {
  pending: '待处理',
  in_progress: '处理中',
  completed: '已完成',
  cancelled: '已取消',
};

const APPROVE_CONCLUSIONS = [
  { value: 'normal_data', label: '数据正常' },
  { value: 'false_alarm', label: '系统误判' },
  { value: 'normal_deviation', label: '正常偏差' },
  { value: 'other', label: '其他' },
];

const REJECT_CONCLUSIONS = [
  { value: 'equipment_failure', label: '设备故障' },
  { value: 'sensor_abnormal', label: '传感器异常' },
  { value: 'field_check_required', label: '需现场核查' },
  { value: 'environmental_factor', label: '环境因素' },
  { value: 'data_distortion', label: '数据失真' },
  { value: 'other', label: '其他' },
];

// 默认审核级别筛选（与后端 level 参数一致）
const LEVEL_OPTIONS = [
  { value: '', label: '全部' },
  { value: '1', label: 'L1 待审/已审' },
  { value: '2', label: 'L1→L2' },
  { value: '3', label: 'L2→L3 人工复核' },
];

export default function DataReviewTab({ tokens, isDark }) {
  const [reviews, setReviews] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState({ by_status: {}, by_metric: [], by_site: [], total: 0, pass_rate: 0 });
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [filters, setFilters] = useState({ level: '', status: '', metric: '', page: 1, per_page: 50 });
  const [reviewModal, setReviewModal] = useState({ open: false, mode: 'batch', items: [], action: 'approve', reason: '', conclusion: '' });
  const [codeModal, setCodeModal] = useState(false);
  const [anomalyCodes, setAnomalyCodes] = useState([]);
  const [traceDrawer, setTraceDrawer] = useState({ open: false, chain: null, loading: false });
  const [trendDrawer, setTrendDrawer] = useState({ open: false, data: null, loading: false, metric: '', site_id: null, site_name: '' });
  const [trendSel, setTrendSel] = useState({ site_id: null, metric: '' });
  const [reviewer] = useState(() => {
    try { return JSON.parse(localStorage.getItem('auth_user') || '{}').id || 1; }
    catch { return 1; }
  });

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.level) params.set('level', filters.level);
      if (filters.status) params.set('status', filters.status);
      if (filters.metric) params.set('metric', filters.metric);
      params.set('page', filters.page);
      params.set('per_page', filters.per_page);
      const list = await api.get('/data-reviews?' + params.toString());
      setReviews(list.items || []);
      setTotal(list.total || 0);
    } catch (e) {
      message.error('加载数据审核列表失败：' + e.message);
    } finally {
      setLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      const data = await api.get('/data-reviews/stats');
      setStats(data);
    } catch (e) { /* 静默 */ }
  };

  const loadCodes = async () => {
    try { setAnomalyCodes(await api.get('/anomaly-codes') || []); }
    catch (e) { /* 静默 */ }
  };

  // 打开编码参考时加载
  useEffect(() => { if (codeModal) loadCodes(); }, [codeModal]);

  useEffect(() => { load(); loadStats(); }, [filters.page, filters.level, filters.status, filters.metric]);

  // 溯源链
  const openTrace = async (review) => {
    setTraceDrawer({ open: true, chain: null, loading: true });
    try {
      const data = await api.get(`/anomaly-traceability/chain?review_id=${review.id}`);
      setTraceDrawer({ open: true, chain: data, loading: false });
    } catch (e) {
      message.error('加载溯源链失败');
      setTraceDrawer({ open: false, chain: null, loading: false });
    }
  };

  // 趋势预测：根据统计数据自动选「数据量最大的站点+指标」，并支持下拉切换
  const openTrend = async () => {
    setTrendDrawer({ open: true, data: null, loading: true, metric: '', site_id: null, site_name: '' });
    try {
      const stats = await api.get('/data-reviews/stats');
      const topMetric = stats?.by_metric?.[0]?.metric || 'ph';
      const topSite = stats?.by_site?.[0] || null;
      const sid = topSite?.site_id || 274;
      const sname = topSite?.site_name || `站点${sid}`;
      setTrendSel({ site_id: sid, metric: topMetric });
      await fetchTrend(sid, topMetric, sname);
    } catch (e) {
      message.error('加载趋势数据失败');
      setTrendDrawer({ open: false, data: null, loading: false, metric: '', site_id: null, site_name: '' });
    }
  };

  const fetchTrend = async (sid, m, sname) => {
    setTrendDrawer(d => ({ ...d, loading: true }));
    try {
      const data = await api.get(`/prediction/trend?site_id=${sid}&metric=${m}&hours=48&forecast_steps=12`);
      setTrendDrawer({ open: true, data, loading: false, metric: m, site_id: sid, site_name: sname || `站点${sid}` });
    } catch (e) {
      message.error('加载趋势数据失败');
      setTrendDrawer(d => ({ ...d, loading: false, data: null }));
    }
  };

  const onTrendChange = async (patch) => {
    const next = { ...trendSel, ...patch };
    setTrendSel(next);
    const sid = next.site_id || 274;
    const m = next.metric || 'ph';
    const sname = (stats.by_site || []).find(s => s.site_id === sid)?.site_name || `站点${sid}`;
    await fetchTrend(sid, m, sname);
  };

  const openReview = (mode, items, action = 'approve') => {
    setReviewModal({ open: true, mode, items, action, reason: '', conclusion: '' });
  };

  // 提交人工复核
  const submitReview = async () => {
    const { mode, items, action, reason, conclusion } = reviewModal;
    try {
      if (mode === 'single' && items.length === 1) {
        await api.post(`/data-reviews/${items[0].id}/manual-review`, { action, reviewer_id: reviewer, reason, conclusion });
      } else {
        await api.post('/data-reviews/batch-manual-review', {
          ids: items.map(i => i.id), action, reviewer_id: reviewer, reason, conclusion,
        });
      }
      message.success(`已${action === 'approve' ? '核准' : '驳回'} ${items.length} 条`);
      setReviewModal({ open: false, mode: 'batch', items: [], action: 'approve', reason: '', conclusion: '' });
      setSelectedRowKeys([]);
      load(); loadStats();
    } catch (e) { message.error(e.message); }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 50 },
    { title: '站点', width: 130, render: (_, r) => (
      <Text strong style={{ fontSize: 12 }}>{r.site_name}</Text>
    )},
    { title: '指标', dataIndex: 'metric', width: 80, render: (v) => <Tag style={{ fontSize: 11 }}>{metricMap[v] || v}</Tag> },
    { title: '值', dataIndex: 'value', width: 55, render: (v) => v == null ? <Text type="secondary">-</Text> : Number(v)?.toFixed?.(3) ?? v },
    { title: '时间', dataIndex: 'recorded_at', width: 120, render: (v) => <Text style={{ fontSize: 11 }}>{v}</Text> },
    { title: '状态', dataIndex: 'status', width: 80, render: (v) => {
      const s = STATUS_MAP[v] || { label: v, color: 'default' };
      return <Tag color={s.color} style={{ borderRadius: 4, fontSize: 11 }}>{s.label}</Tag>;
    }},
    { title: 'L1', dataIndex: 'auto_result', width: 60, render: (v, r) => {
      if (!v) return <Text type="secondary" style={{ fontSize: 11 }}>-</Text>;
      const s = AUTO_RESULT_MAP[v];
      return <Tag color={s.color} style={{ borderRadius: 4, fontSize: 11 }}>{s.label}</Tag>;
    }},
    { title: 'Z值', dataIndex: 'smart_score', width: 50, render: (v) => {
      if (v == null) return <Text style={{ fontSize: 11 }}>-</Text>;
      const color = v > 3 ? 'red' : v > 2 ? 'orange' : 'green';
      return <Tag color={color} style={{ borderRadius: 4, fontSize: 11 }}>{v}</Tag>;
    }},
    { title: 'L2', dataIndex: 'smart_result', width: 60, render: (v) => {
      if (!v) return <Text style={{ fontSize: 11 }}>-</Text>;
      const s = SMART_RESULT_MAP[v];
      return <Tag color={s.color} style={{ borderRadius: 4, fontSize: 11 }}>{s.label}</Tag>;
    }},
    { title: 'L3', dataIndex: 'manual_result', width: 60, render: (v, r) => {
      if (!v) return <Text style={{ fontSize: 11 }}>-</Text>;
      const s = MANUAL_RESULT_MAP[v];
      return <Tag color={s.color} style={{ borderRadius: 4, fontSize: 11 }}>{s.label}</Tag>;
    }},
    { title: '操作', width: 110, render: (_, r) => (
      <Space size={0}>
      {r.status === 'smart_reviewed' || r.status === 'manual_reviewed' ? (
        <>
          <Button size="small" type="link" icon={<CheckOutlined />} onClick={() => openReview('single', [r], 'approve')} style={{ fontSize: 11, color: '#52c41a' }}>
            通过
          </Button>
          <Button size="small" type="link" icon={<CloseOutlined />} onClick={() => openReview('single', [r], 'reject')} style={{ fontSize: 11, color: '#ff4d4f' }}>
            驳回
          </Button>
        </>
      ) : <Text type="secondary" style={{ fontSize: 11 }}>-</Text>}
      {r.auto_result && r.auto_reason ? (
        <Tooltip title="查看溯源链">
          <Button size="small" type="link" icon={<ExperimentOutlined />} onClick={() => openTrace(r)} style={{ fontSize: 11 }}>
            溯源
          </Button>
        </Tooltip>
      ) : null}
      </Space>
    )},
  ];

  return (
    <>
    <style>{`
      .review-scroll-table .ant-table-body { scrollbar-width: none; -ms-overflow-style: none; }
      .review-scroll-table .ant-table-body::-webkit-scrollbar { display: none; }
    `}</style>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%', overflow: 'hidden' }}>
      {/* 统计卡片 */}
      <Row gutter={[12, 12]}>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic title="待处理总数" value={stats.total || 0} prefix={<AuditOutlined />} valueStyle={{ color: tokens.colorPrimary }} />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic
              title="L1 待审"
              value={stats.by_status?.pending || 0}
              valueStyle={{ color: '#8c8c8c' }}
            />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic
              title="L2 待审"
              value={stats.by_status?.auto_reviewed || 0}
              valueStyle={{ color: '#1890ff' }}
            />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic
              title="L3 待审"
              value={(stats.by_status?.smart_reviewed || 0) + (stats.by_status?.manual_reviewed || 0)}
              valueStyle={{ color: '#fa8c16' }}
            />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic
              title="已归档"
              value={stats.by_status?.archived || 0}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic
              title="归档通过率"
              value={stats.pass_rate || 0}
              suffix="%"
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 操作工具栏 */}
      <Card bodyStyle={{ padding: 12 }}>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={() => { load(); loadStats(); }}>刷新</Button>
          <Text type="secondary">后台每 10 分钟自动完成 L1 仪器审核 + L2 智能审核（统计过程控制 Z 分数）</Text>
          <div style={{ flex: 1 }} />
          <Popconfirm
            title={`确认批量核准 ${selectedRowKeys.length} 条？`}
            disabled={selectedRowKeys.length === 0}
            onConfirm={() => openReview('batch', reviews.filter(r => selectedRowKeys.includes(r.id)))}
            okText="确认"
            cancelText="取消"
          >
            <Button type="primary" disabled={selectedRowKeys.length === 0} icon={<CheckOutlined />}>
              批量核准 ({selectedRowKeys.length})
            </Button>
          </Popconfirm>
          <Popconfirm
            title={`确认批量驳回 ${selectedRowKeys.length} 条？`}
            disabled={selectedRowKeys.length === 0}
            onConfirm={() => {
              setReviewModal({
                open: true, mode: 'batch',
                items: reviews.filter(r => selectedRowKeys.includes(r.id)),
                action: 'reject', reason: '', conclusion: '',
              });
            }}
            okText="确认"
            cancelText="取消"
          >
            <Button danger disabled={selectedRowKeys.length === 0} icon={<CloseOutlined />}>
              批量驳回 ({selectedRowKeys.length})
            </Button>
          </Popconfirm>
          <Button icon={<FileTextOutlined />} onClick={() => setCodeModal(true)}>编码参考</Button>
          <Button icon={<ExperimentOutlined />} onClick={openTrend}>趋势预测</Button>
        </Space>
      </Card>

      {/* 筛选 */}
      <Card bodyStyle={{ padding: 12 }}>
        <Space wrap>
          <span>审核级别：</span>
          <Select
            value={filters.level || undefined}
            onChange={v => setFilters(f => ({ ...f, level: v || '', page: 1 }))}
            options={LEVEL_OPTIONS}
            style={{ width: 180 }}
            placeholder="全部"
            allowClear
          />
          <span>状态：</span>
          <Select
            value={filters.status || undefined}
            onChange={v => setFilters(f => ({ ...f, status: v || '', page: 1 }))}
            options={Object.entries(STATUS_MAP).map(([k, v]) => ({ value: k, label: v.label }))}
            style={{ width: 150 }}
            placeholder="全部"
            allowClear
          />
          <span>指标：</span>
          <Input
            value={filters.metric}
            onChange={e => setFilters(f => ({ ...f, metric: e.target.value, page: 1 }))}
            placeholder="如 pH / 氨氮"
            style={{ width: 150 }}
            allowClear
          />
        </Space>
      </Card>

      {/* 审核列表 */}
      <Card bodyStyle={{ padding: 0 }} style={{ flex: 1, overflow: 'hidden' }}>
        <div style={{ height: '100%', overflow: 'hidden', scrollbarWidth: 'none', msOverflowStyle: 'none' }}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={reviews}
          loading={loading}
          className="review-scroll-table"
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
          }}
          pagination={{
            current: filters.page,
            pageSize: filters.per_page,
            total,
            showSizeChanger: true,
            pageSizeOptions: ['20', '50', '100'],
            onChange: (page, pageSize) => setFilters(f => ({ ...f, page, per_page: pageSize })),
          }}
          scroll={{ y: 'calc(100vh - 460px)' }}
          size="small"
          locale={{ emptyText: <Empty description="暂无审核数据" /> }}
        />
        </div>
      </Card>

      {/* 人工复核弹窗 */}
      <Modal
        open={reviewModal.open}
        title={reviewModal.action === 'approve' ? '确认数据正常' : '确认数据异常'}
        onCancel={() => setReviewModal(m => ({ ...m, open: false }))}
        onOk={submitReview}
        okText={reviewModal.action === 'approve' ? '确认通过' : '确认驳回'}
        cancelText="取消"
        okButtonProps={reviewModal.action === 'reject' ? { danger: true } : { type: 'primary' }}
        width={520}
      >
        <Form layout="vertical">
          <Form.Item label={`将对 ${reviewModal.items.length} 条数据进行${reviewModal.action === 'approve' ? '核准' : '驳回'}操作`}>
            <Text type="secondary">{reviewModal.action === 'approve'
              ? '确认该数据无异常，操作后将归档为已审核。'
              : '确认该数据存在异常，操作后将归档并写入复核原因。'}</Text>
          </Form.Item>
          <Form.Item label={reviewModal.action === 'approve' ? '核准意见（可选）' : '驳回原因（必填）'}>
            <Input.TextArea
              rows={3}
              value={reviewModal.reason}
              onChange={e => setReviewModal(m => ({ ...m, reason: e.target.value }))}
              placeholder={reviewModal.action === 'approve' ? '如：与历史数据一致' : '请说明驳回原因'}
            />
          </Form.Item>
          <Form.Item label="处置结论">
            <Select
              placeholder={reviewModal.action === 'approve' ? '选择正常结论（可选）' : '选择异常结论（可选）'}
              allowClear
              options={reviewModal.action === 'approve' ? APPROVE_CONCLUSIONS : REJECT_CONCLUSIONS}
              value={reviewModal.conclusion}
              onChange={e => setReviewModal(m => ({ ...m, conclusion: e }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 异常编码参考 */}
      <Modal open={codeModal} onCancel={() => setCodeModal(false)} title="异常编码参考" footer={null} width={860}
        styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}>
        <Table
          dataSource={anomalyCodes}
          rowKey="code"
          pagination={false}
          size="small"
          className="review-scroll-table"
          scroll={{ x: 820, y: 460 }}
          columns={[
            { title: '编码', dataIndex: 'code', width: 80, fixed: 'left', render: v => <Text code strong>{v}</Text> },
            { title: '类别', dataIndex: 'category', width: 80, render: v => <Tag>{v === 'quality' ? '质量' : v === 'equipment' ? '设备' : '自定义'}</Tag> },
            { title: '严重度', dataIndex: 'severity', width: 70, render: v => {
              const c = v === 'critical' ? 'red' : v === 'warning' ? 'orange' : 'blue';
              return <Tag color={c}>{v === 'critical' ? '严重' : v === 'warning' ? '警告' : '提示'}</Tag>;
            }},
            { title: '标题', dataIndex: 'title', width: 120, ellipsis: false },
            { title: '说明', dataIndex: 'description', width: 220, ellipsis: false },
            { title: '处理建议', dataIndex: 'suggestion', width: 260, ellipsis: false },
          ]}
        />
      </Modal>

      {/* 溯源链 Drawer */}
      <Drawer open={traceDrawer.open} onClose={() => setTraceDrawer({ open: false, chain: null, loading: false })}
        title="异常溯源链" width={640} loading={traceDrawer.loading}>
        {traceDrawer.chain && (
          <>
          {traceDrawer.chain.review && (
            <Card size="small" title="原始数据" style={{ marginBottom: 12 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="站点">{traceDrawer.chain.review.site_name || '-'}</Descriptions.Item>
                <Descriptions.Item label="指标">{metricMap[traceDrawer.chain.review.metric] || traceDrawer.chain.review.metric}</Descriptions.Item>
                <Descriptions.Item label="值">{traceDrawer.chain.review.value}</Descriptions.Item>
                <Descriptions.Item label="时间">{traceDrawer.chain.review.recorded_at}</Descriptions.Item>
                <Descriptions.Item label="状态">{STATUS_MAP[traceDrawer.chain.review.status]?.label || traceDrawer.chain.review.status}</Descriptions.Item>
                <Descriptions.Item label="L1结果">
                  {traceDrawer.chain.review.auto_reason ? <Text code style={{ fontSize: 11 }}>{traceDrawer.chain.review.auto_reason}</Text> : '-'}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          {traceDrawer.chain.device && (
            <Card size="small" title="设备状态（最近）" style={{ marginBottom: 12 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="在线状态">{DEVICE_STATUS_MAP[traceDrawer.chain.device.status] || traceDrawer.chain.device.status || '-'}</Descriptions.Item>
                <Descriptions.Item label="电压">{traceDrawer.chain.device.voltage || '-'}</Descriptions.Item>
                <Descriptions.Item label="设备型号">{traceDrawer.chain.device.device_model || '-'}</Descriptions.Item>
                <Descriptions.Item label="最后更新">{traceDrawer.chain.device.last_data_time || <Text type="secondary">无数据</Text>}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          {traceDrawer.chain.maintenance && (
            <Card size="small" title="最近维护记录" style={{ marginBottom: 12 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="维护类型">{traceDrawer.chain.maintenance.type || '-'}</Descriptions.Item>
                <Descriptions.Item label="状态">{MAINTENANCE_STATUS_MAP[traceDrawer.chain.maintenance.status] || traceDrawer.chain.maintenance.status || '-'}</Descriptions.Item>
                <Descriptions.Item label="时间">{traceDrawer.chain.maintenance.created_at || '-'}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          {traceDrawer.chain.nearby_sites && traceDrawer.chain.nearby_sites.length > 0 && (
            <Card size="small" title="周边站点同期值（空间验证）" style={{ marginBottom: 12 }}>
              <Table dataSource={traceDrawer.chain.nearby_sites} rowKey={(_,i)=>i} pagination={false} size="small"
                columns={[
                  { title: '站点', dataIndex: 'name', width: 100 },
                  { title: '值', dataIndex: 'value', width: 60 },
                  { title: '时间', dataIndex: 'recorded_at', width: 140 },
                ]}
              />
            </Card>
          )}
          {traceDrawer.chain.trace && (
            <Card size="small" title="人工复核意见" style={{ marginBottom: 12 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="故障真因">{traceDrawer.chain.trace.root_cause || <Text type="secondary">待填写</Text>}</Descriptions.Item>
                <Descriptions.Item label="现象描述">{traceDrawer.chain.trace.symptom || <Text type="secondary">待填写</Text>}</Descriptions.Item>
                <Descriptions.Item label="处置措施">{traceDrawer.chain.trace.treatment || <Text type="secondary">待填写</Text>}</Descriptions.Item>
                <Descriptions.Item label="影响评估">{traceDrawer.chain.trace.impact || <Text type="secondary">待填写</Text>}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          {traceDrawer.chain.codes && traceDrawer.chain.codes.length > 0 && (
            <Card size="small" title="异常编码参考" style={{ marginBottom: 12 }}>
              <Table dataSource={traceDrawer.chain.codes} rowKey="code" pagination={false} size="small"
                columns={[
                  { title: '编码', dataIndex: 'code', width: 70 },
                  { title: '标题', dataIndex: 'title', width: 80 },
                  { title: '处理建议', dataIndex: 'suggestion' },
                ]}
              />
            </Card>
          )}
          </>
        )}
      </Drawer>

      {/* 趋势预测 Drawer */}
      <Drawer open={trendDrawer.open} onClose={() => setTrendDrawer({ open: false, data: null, loading: false })}
        title={`趋势预测：${metricMap[trendDrawer.metric] || trendDrawer.metric}（${trendDrawer.site_name || `站点${trendDrawer.site_id}`}）`} width={760} loading={trendDrawer.loading}>
        <Space wrap style={{ marginBottom: 12 }}>
          <span>站点：</span>
          <Select
            value={trendSel.site_id || undefined}
            onChange={v => onTrendChange({ site_id: v })}
            style={{ width: 180 }}
            showSearch optionFilterProp="label" placeholder="选择站点"
            options={(stats.by_site || []).map(s => ({ value: s.site_id, label: `${s.site_name}（${s.n || 0}）` }))}
          />
          <span>指标：</span>
          <Select
            value={trendSel.metric || undefined}
            onChange={v => onTrendChange({ metric: v })}
            style={{ width: 160 }}
            showSearch optionFilterProp="label" placeholder="选择指标"
            options={(stats.by_metric || []).map(m => ({ value: m.metric, label: metricMap[m.metric] || m.metric }))}
          />
        </Space>
        {trendDrawer.data && (
          <EChart option={{
            tooltip: { trigger: 'axis' },
            legend: { data: ['实际值', '移动平均', '预测', '上限', '下限'], top: 0 },
            grid: { left: 50, right: 20, top: 40, bottom: 30 },
            xAxis: { type: 'time' },
            yAxis: { type: 'value', name: trendDrawer.metric },
            series: [
              { name: '实际值', type: 'line', data: (trendDrawer.data.actual || []).map(v => [v.time, v.value]),
                symbol: 'circle', symbolSize: 3, lineStyle: { width: 1 }, color: '#1890ff' },
              { name: '移动平均', type: 'line', data: (trendDrawer.data.moving_average || []).map(v => [v.time, v.value]),
                symbol: 'none', lineStyle: { width: 2, type: 'dashed' }, color: '#722ed1' },
              { name: '预测', type: 'line', data: (trendDrawer.data.forecast || []).map(v => [v.time, v.value]),
                symbol: 'diamond', symbolSize: 6, lineStyle: { width: 2 }, color: '#f5222d' },
              { name: '上限', type: 'line', data: (trendDrawer.data.upper || []).map(v => [v.time, v.value]),
                symbol: 'none', lineStyle: { width: 1, type: 'dotted' }, color: '#ff4d4f' },
              { name: '下限', type: 'line', data: (trendDrawer.data.lower || []).map(v => [v.time, v.value]),
                symbol: 'none', lineStyle: { width: 1, type: 'dotted' }, color: '#52c41a' },
            ],
            dataZoom: [{ type: 'inside', start: 0, end: 100 }],
          }} style={{ height: 420 }} />
        )}
        {trendDrawer.data && (
          <Card size="small" style={{ marginTop: 12 }}>
            <Descriptions size="small" column={4}>
              <Descriptions.Item label="数据点">{trendDrawer.data.metadata?.data_points || '-'}</Descriptions.Item>
              <Descriptions.Item label="斜率">{trendDrawer.data.slope || '-'}</Descriptions.Item>
              <Descriptions.Item label="R²">{trendDrawer.data.r_squared || '-'}</Descriptions.Item>
              <Descriptions.Item label="预测步数">{trendDrawer.data.metadata?.forecast_steps || '-'}</Descriptions.Item>
            </Descriptions>
          </Card>
        )}
      </Drawer>
      </div>
    </>
  );
}
