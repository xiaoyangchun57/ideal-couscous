import { useState, useEffect, useMemo } from 'react';
import { Table, Card, Select, Space, Typography, message, Statistic, Row, Col, Tag, Button, Dropdown } from 'antd';
import { DownloadOutlined, FileExcelOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';

const { Title, Text } = Typography;

const ROLE_CN = { admin: '管理员', manager: '主管', operator: '运维员', inspector: '审核员', viewer: '访客' };
const PERIOD_OPTS = [
  { value: 'month', label: '本月' },
  { value: '7d', label: '近7天' },
  { value: '30d', label: '近30天' },
  { value: 'quarter', label: '本季度' },
  { value: 'year', label: '本年度' },
];

// 达标率/闭环率通用配色
const rateColor = (r) => (r >= 80 ? 'green' : r >= 60 ? 'gold' : 'red');
const healthColor = (r) => (r >= 90 ? '#52c41a' : r >= 75 ? '#faad14' : '#f5222d');

export default function EvaluationPage() {
  const { tokens } = useTheme();
  const [period, setPeriod] = useState('month');
  const [health, setHealth] = useState(null);
  const [personnel, setPersonnel] = useState({ overview: null, list: [], period_label: '' });
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [h, p] = await Promise.all([
        api.get('/data/health?period=' + period),
        api.get('/evaluation/personnel?period=' + period),
      ]);
      setHealth(h || null);
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

  // xlsx 下载辅助：api 默认走 JSON，导出接口返回二进制，需单独 fetch
  const downloadExport = async (url, filename) => {
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

  // ===== 人员运维绩效（主区，多维指标）=====
  const personCols = [
    { title: '姓名', dataIndex: 'real_name', key: 'real_name', fixed: 'left', width: 90,
      render: (v) => <Text strong>{v}</Text> },
    { title: '角色', dataIndex: 'role', key: 'role', width: 80, render: (v) => ROLE_CN[v] || v },
    { title: '工单处理', dataIndex: 'wo_total', key: 'wo_total', width: 90,
      sorter: (a, b) => a.wo_total - b.wo_total },
    { title: '闭环数', dataIndex: 'wo_closed', key: 'wo_closed', width: 80 },
    { title: '闭环率', dataIndex: 'wo_closed_rate', key: 'wo_closed_rate', width: 90,
      sorter: (a, b) => a.wo_closed_rate - b.wo_closed_rate,
      render: (v) => <Tag color={rateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '平均响应(h)', dataIndex: 'response_hours', key: 'response_hours', width: 110,
      sorter: (a, b) => (a.response_hours ?? 1e9) - (b.response_hours ?? 1e9),
      render: (v) => (v === null || v === undefined ? <Text type="secondary">—</Text> : `${v}h`) },
    { title: '平均处理(天)', dataIndex: 'wo_avg_days', key: 'wo_avg_days', width: 110,
      render: (v) => (v === null || v === undefined ? <Text type="secondary">—</Text> : `${v}天`) },
    { title: 'SLA超时', dataIndex: 'sla_breach', key: 'sla_breach', width: 90,
      render: (v) => (Number(v) > 0 ? <Tag color="red">{v}</Tag> : <Text type="secondary">0</Text>) },
    { title: 'SLA达标率', dataIndex: 'on_time_rate', key: 'on_time_rate', width: 100,
      sorter: (a, b) => a.on_time_rate - b.on_time_rate,
      render: (v) => <Tag color={rateColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '巡检执行', dataIndex: 'insp_done', key: 'insp_done', width: 90 },
    { title: '巡检审核', dataIndex: 'insp_reviewed', key: 'insp_reviewed', width: 90 },
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
      render: (v) => <Tag color={healthColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate',
      render: (v) => <Tag color={healthColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '当前及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate',
      render: (v) => <Tag color={healthColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '缺失', dataIndex: 'missing', key: 'missing' },
    { title: '超限', dataIndex: 'over_limit', key: 'over_limit' },
  ];

  const siteCols = [
    { title: '站点', dataIndex: 'site_name', key: 'site_name' },
    { title: '负责人', dataIndex: 'manager', key: 'manager', render: (v) => v || '未分配' },
    { title: '完整性', dataIndex: 'completeness_rate', key: 'completeness_rate',
      render: (v) => <Tag color={healthColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate',
      render: (v) => <Tag color={healthColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
    { title: '当前及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate',
      render: (v) => <Tag color={healthColor(Number(v) || 0)}>{Number(v) || 0}%</Tag> },
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

  return (
    <div className="evaluation-page">
      <div className="evaluation-header">
        <Space align="baseline">
          <Title level={3} style={{ margin: 0 }}>人员评估</Title>
          {personnel.period_label && <Text type="secondary">考核期：{personnel.period_label}</Text>}
        </Space>
        <Space className="evaluation-actions" wrap>
          <Button icon={<DownloadOutlined />} onClick={() => downloadExport('/api/export/evaluation?period=' + period, `人员评估_${personnel.period_label || period}.xlsx`)}>
            导出评估表
          </Button>
          <Dropdown
            menu={{
              items: reportItems,
              onClick: ({ key }) => downloadExport('/api/export/ops-report?period=' + key, `运维报告_${key === 'quarter' ? '本季度' : '本年度'}.xlsx`),
            }}
          >
            <Button icon={<FileExcelOutlined />} type="primary">导出运维报告</Button>
          </Dropdown>
          <Text type="secondary">统计周期：</Text>
          <Select value={period} onChange={setPeriod} options={PERIOD_OPTS} style={{ width: 120 }} />
        </Space>
      </div>

      {/* 人员运维绩效人均概览 */}
      {personOverview && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
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
      <Card title="人员运维绩效（工单响应 / 处理时效 / SLA / 巡检）" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r) => r.id}
          loading={loading}
          columns={personCols}
          dataSource={personnel.list}
          pagination={false}
          size="small"
          scroll={{ x: 1040 }}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>

      {/* 数据健康度维度 */}
      {healthOverview && (
        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
          {healthOverview.map((o) => (
            <Col key={o.label} xs={12} sm={8} lg={Math.floor(24 / healthOverview.length)}>
              <Card size="small">
                <Statistic title={o.label} value={o.value} valueStyle={{ fontSize: 22 }} />
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Card title="负责人站点数据情况（排障参考，不直接计入个人绩效）" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r) => r.manager || 'x'}
          loading={loading}
          columns={managerCols}
          dataSource={byManager}
          pagination={false}
          size="small"
          scroll={{ x: 760 }}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>

      <Card title="各站点数据质量维度">
        <Table
          rowKey={(r) => r.site_id || r.site_name}
          loading={loading}
          columns={siteCols}
          dataSource={bySite}
          pagination={{ pageSize: 10 }}
          size="small"
          scroll={{ x: 760 }}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>
      <style>{`
        .evaluation-page { padding: 24px; }
        .evaluation-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
        .evaluation-actions { justify-content: flex-end; }
        @media (max-width: 639px) {
          .evaluation-page { padding: 16px 12px; }
          .evaluation-header { align-items: flex-start; }
          .evaluation-actions { width: 100%; justify-content: flex-start; }
          .evaluation-actions .ant-btn { padding-inline: 9px; }
        }
      `}</style>
    </div>
  );
}
