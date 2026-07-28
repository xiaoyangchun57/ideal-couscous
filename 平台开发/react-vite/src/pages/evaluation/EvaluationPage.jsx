import { useState, useEffect, useMemo } from 'react';
import { Table, Card, Select, Space, Typography, message, Statistic, Row, Col, Tag, Button, Dropdown } from 'antd';
import { DownloadOutlined, FileExcelOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import { evaluationRateColor } from '../../services/constants';
import FilterBar from '../../components/FilterBar';
import {
  pageRootStyle, tableCardStyle, tableCardBody, filterSmallSelectWidth,
} from '../../services/pageStyles';

const { Title, Text } = Typography;

const ROLE_CN = { admin: '管理员', manager: '主管', operator: '运维员', inspector: '审核员', viewer: '访客' };
const PERIOD_OPTS = [
  { value: 'month', label: '本月' },
  { value: '7d', label: '近7天' },
  { value: '30d', label: '近30天' },
  { value: 'quarter', label: '本季度' },
  { value: 'year', label: '本年度' },
];

export default function EvaluationPage() {
  const { tokens, isDark } = useTheme();
  const [siteWrapRef, siteBodyH] = useTableAutoHeight();
  const [period, setPeriod] = useState('month');
  const [health, setHealth] = useState(null);
  const [personnel, setPersonnel] = useState({ overview: null, list: [], period_label: '' });
  const [baseline, setBaseline] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [h, p, b] = await Promise.all([
        api.get('/data/health?period=' + period),
        api.get('/evaluation/personnel?period=' + period),
        api.get('/operations/baseline?period=' + period),
      ]);
      setHealth(h || null);
      setBaseline(b && !b.error ? b : null);
      // 兼容旧数组格式与新对象格式
      if (Array.isArray(p)) setPersonnel({ overview: null, list: p, period_label: '' });
      else setPersonnel({ overview: p?.overview || null, list: p?.list || [], period_label: p?.period_label || '' });
    } catch (e) {
      message.error('加载失败：' + (e.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [period]);

  // 报表自助埋点：进入评估/报表页记一次"打开"，导出时记"导出"
  useEffect(() => { api.track('report.opened', {}); }, []);

  // xlsx 下载辅助：api 默认走 JSON，导出接口返回二进制，需单独 fetch
  const downloadExport = async (url, filename, reportType) => {
    if (reportType) api.track('report.exported', { report_type: reportType });
    try {
      const token = (() => { try { return localStorage.getItem('water_ops_token') || ''; } catch { return ''; } })();
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error('导出失败：' + res.status);
      const blob = await res.blob();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    } catch (e) {
      message.error(e.message || '导出失败');
    }
  };

  const reportItems = [
    { key: 'quarter', label: '本季度运维报告' },
    { key: 'year', label: '本年度运维报告' },
  ];

  // 主表只保留管理者扫描绩效所需的五列；明细指标在单元格内成组呈现，避免横向滚动。
  const personCols = [
    { title: '人员', key: 'person', width: 150,
      render: (_, r) => <><Text strong>{r.real_name || '未命名'}</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{ROLE_CN[r.role] || r.role || '—'}</Text></> },
    { title: '工单量', dataIndex: 'wo_total', key: 'workorders', width: 130,
      sorter: (a, b) => (a.wo_total || 0) - (b.wo_total || 0),
      render: (_, r) => <><Text strong>{r.wo_total || 0} 件</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>已闭环 {r.wo_closed || 0} 件</Text></> },
    { title: '闭环与 SLA', key: 'quality', width: 180,
      render: (_, r) => <Space size={[4, 4]} wrap><Tag color={evaluationRateColor(Number(r.wo_closed_rate) || 0)}>闭环 {Number(r.wo_closed_rate) || 0}%</Tag><Tag color={evaluationRateColor(Number(r.on_time_rate) || 0)}>SLA {Number(r.on_time_rate) || 0}%</Tag>{Number(r.sla_breach) > 0 && <Tag color="red">超时 {r.sla_breach}</Tag>}</Space> },
    { title: '响应与处置', key: 'duration', width: 160,
      render: (_, r) => <><Text>{r.response_hours == null ? '响应 —' : `响应 ${r.response_hours}h`}</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{r.wo_avg_days == null ? '处置 —' : `平均处置 ${r.wo_avg_days} 天`}</Text></> },
    { title: '巡检', key: 'inspection', width: 130,
      render: (_, r) => <><Text>执行 {r.insp_done || 0} 项</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>审核 {r.insp_reviewed || 0} 项</Text></> },
  ];

  // 人员绩效人均概览卡
  const personOverview = useMemo(() => {
    const o = personnel.overview;
    if (!o) return null;
    return [
      { label: '在岗人数', value: o.staff_count ?? 0 },
      { label: '工单总量', value: o.wo_total ?? 0 },
      { label: '整体闭环率', value: (o.closed_rate ?? 0) + '%' },
      { label: 'SLA达标率', value: (o.on_time_rate ?? 0) + '%' },
      { label: '平均响应', value: o.avg_response_hours == null ? '—' : o.avg_response_hours + 'h' },
      { label: '巡检完成', value: o.insp_done ?? 0 },
    ];
  }, [personnel]);

  // ===== 数据健康度（作为评估的一个维度）=====
  const managerCols = [
    { title: '负责人', dataIndex: 'manager', key: 'manager', render: (v) => <Text strong>{v || '未分配'}</Text> },
    { title: '负责站点', dataIndex: 'site_count', key: 'site_count' },
    { title: '完整性', dataIndex: 'completeness_rate', key: 'completeness_rate',
      render: (v) => <Tag color={evaluationRateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate',
      render: (v) => <Tag color={evaluationRateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '当前及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate',
      render: (v) => <Tag color={evaluationRateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '缺失', dataIndex: 'missing', key: 'missing' },
    { title: '超限', dataIndex: 'over_limit', key: 'over_limit' },
  ];

  const siteCols = [
    { title: '站点', dataIndex: 'site_name', key: 'site_name', ellipsis: true },
    { title: '负责人', dataIndex: 'manager', key: 'manager', ellipsis: true, render: (v) => v || '未分配' },
    { title: '完整性', dataIndex: 'completeness_rate', key: 'completeness_rate',
      render: (v) => <Tag color={evaluationRateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate',
      render: (v) => <Tag color={evaluationRateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '当前及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate',
      render: (v) => <Tag color={evaluationRateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '缺失', dataIndex: 'missing', key: 'missing' },
    { title: '超限', dataIndex: 'over_limit', key: 'over_limit' },
  ];

  const byManager = useMemo(() => (health && health.by_manager) || [], [health]);
  const bySite = useMemo(() => (health && health.by_site) || [], [health]);

  const healthOverview = useMemo(() => {
    if (!health || !health.total) return null;
    const t = health.total;
    return [
      { label: '应报总数', value: t.expected || 0 },
      { label: '完整性', value: (t.completeness_rate ?? 0) + '%' },
      { label: '有效性', value: (t.validity_rate ?? 0) + '%' },
      { label: '当前及时性', value: (t.timeliness_rate ?? 0) + '%' },
      { label: '缺失 / 超限', value: `${t.missing || 0} / ${t.over_limit || 0}` },
    ];
  }, [health]);

  const baselineItems = useMemo(() => {
    if (!baseline) return [];
    const metrics = baseline.north_star || {};
    const labelMap = {
      inspection_coverage: '巡检覆盖率',
      work_order_online_closure_rate: '工单线上闭环率',
      alert_online_handling_rate: '告警处置线上率',
      review_online_completion_rate: '审核线上完成率',
    };
    return Object.entries(metrics).map(([key, metric]) => ({
      key,
      label: labelMap[key] || key,
      value: metric.value == null ? '待采集' : `${metric.value}%`,
      detail: metric.value == null ? '当前周期无有效样本' : `${metric.numerator}/${metric.denominator} 个样本`,
      color: metric.value == null ? 'default' : 'blue',
    }));
  }, [baseline]);

  return (
    <div style={pageRootStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16, flexShrink: 0 }}>
        <Space align="baseline">
          <Title level={3} style={{ margin: 0 }}>人员评估</Title>
          {personnel.period_label && <Text type="secondary">考核期：{personnel.period_label}</Text>}
        </Space>
      </div>
      <FilterBar
        style={{ marginBottom: 16 }}
        extra={(
          <Space wrap>
            <Button icon={<DownloadOutlined />} onClick={() => downloadExport('/api/export/evaluation?period=' + period, `人员评估_${personnel.period_label || period}.xlsx`, 'evaluation')}>
              导出评估表
            </Button>
            <Dropdown
              menu={{
                items: reportItems,
                onClick: ({ key }) => downloadExport('/api/export/ops-report?period=' + key, `运维报告_${key === 'quarter' ? '本季度' : '本年度'}.xlsx`, key),
              }}
            >
              <Button icon={<FileExcelOutlined />} type="primary">导出运维报告</Button>
            </Dropdown>
          </Space>
        )}
      >
        <Text type="secondary">统计周期：</Text>
        <Select value={period} onChange={setPeriod} options={PERIOD_OPTS} style={{ width: filterSmallSelectWidth }} />
      </FilterBar>

      {baselineItems.length > 0 && (
        <Card title="运营基线（仅记录当前值，不设目标）" style={{ marginBottom: 16, flexShrink: 0 }}>
          <Row gutter={[12, 12]}>
            {baselineItems.map((item) => (
              <Col key={item.key} xs={12} sm={12} lg={6}>
                <div style={{ minHeight: 94, padding: 12, border: `1px solid ${tokens.colorBorder}`, borderRadius: 6 }}>
                  <Text type="secondary">{item.label}</Text>
                  <div><Text strong style={{ fontSize: 24, lineHeight: 1.5 }}>{item.value}</Text></div>
                  <Tag color={item.color}>{item.detail}</Tag>
                </div>
              </Col>
            ))}
          </Row>
          <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
            离线闭环、审核耗时、报表自助和行动队列指标仍在采集，基线形成后再纳入月度复盘。
          </Text>
        </Card>
      )}

      {/* 人员运维绩效人均概览 */}
      {personOverview && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16, flexShrink: 0 }}>
          {personOverview.map((o) => (
            <Col key={o.label} xs={12} sm={8} lg={4}>
              <Card size="small">
                <Statistic title={o.label} value={o.value} valueStyle={{ fontSize: 22 }} />
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {/* 人员运维绩效（主区） */}
      <Card title="人员运维绩效（工单响应 / 处理时效 / SLA / 巡检）" style={{ marginBottom: 16, flexShrink: 0 }}>
        <Table
          rowKey={(r) => r.id}
          loading={loading}
          columns={personCols}
          dataSource={personnel.list}
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>

      {/* 数据健康度维度 */}
      {healthOverview && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16, flexShrink: 0 }}>
          {healthOverview.map((o) => (
            <Col key={o.label} xs={12} sm={8} lg={Math.floor(24 / healthOverview.length)}>
              <Card size="small">
                <Statistic title={o.label} value={o.value} valueStyle={{ fontSize: 22 }} />
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Card title="负责人站点数据情况（排障参考，不直接计入个人绩效）" style={{ marginBottom: 16, flexShrink: 0 }}>
        <Table
          rowKey={(r) => r.manager || 'x'}
          loading={loading}
          columns={managerCols}
          dataSource={byManager}
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>

      <Card title="各站点数据质量维度" style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
        <div ref={siteWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <Table
            rowKey={(r) => r.site_id || r.site_name}
            loading={loading}
            columns={siteCols}
            dataSource={bySite}
            pagination={false}
            size="small"
            scroll={siteBodyH ? { y: siteBodyH } : undefined}
            locale={{ emptyText: '暂无数据' }}
          />
        </div>
      </Card>
    </div>
  );
}
