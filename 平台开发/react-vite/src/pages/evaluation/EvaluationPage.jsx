import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Dropdown, Select, Space, Tabs, Tag, Typography, message } from 'antd';
import { DownloadOutlined, FileExcelOutlined, ReloadOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../services/api';
import { evaluationRateColor } from '../../services/constants';
import WorkspacePage, {
  FilterField, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar,
} from '../../components/WorkspacePage';
import { useTheme } from '../../hooks/useTheme';

const { Text } = Typography;

const ROLE_CN = { admin: '管理员', manager: '主管', operator: '运维人员', reviewer: '审核员', inspector: '审核员', viewer: '访客' };
const PERIOD_OPTS = [
  { value: 'month', label: '本月' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'quarter', label: '本季度' },
  { value: 'year', label: '本年度' },
];
const PERIOD_KEYS = new Set(PERIOD_OPTS.map((item) => item.value));
const VIEW_KEYS = new Set(['people', 'managers', 'sites']);
const CONFIG_STATUS = {
  configured: { label: '已配置', color: 'green' },
  metrics_unconfigured: { label: '参数未配置', color: 'orange' },
  monitoring_disabled: { label: '未启用监测', color: 'default' },
};

const renderRate = (value) => value == null
  ? <Text type="secondary">无样本</Text>
  : <Tag color={evaluationRateColor(Number(value))}>{Number(value)}%</Tag>;

const opsReportFilename = (period) => {
  const now = new Date();
  const year = now.getFullYear();
  return period === 'quarter'
    ? `运维报告_${year}年第${Math.floor(now.getMonth() / 3) + 1}季度.xlsx`
    : `运维报告_${year}年度.xlsx`;
};

export default function EvaluationPage() {
  const { tokens } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const periodParam = searchParams.get('period') || 'month';
  const period = PERIOD_KEYS.has(periodParam) ? periodParam : 'month';
  const viewParam = searchParams.get('view') || 'people';
  const activeView = VIEW_KEYS.has(viewParam) ? viewParam : 'people';
  const [health, setHealth] = useState(null);
  const [personnel, setPersonnel] = useState({ overview: null, list: [], period_label: '', scope_label: '' });
  const [loading, setLoading] = useState(true);
  const [loadErrors, setLoadErrors] = useState([]);
  const [exporting, setExporting] = useState('');

  const updateQuery = useCallback((patch) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([key, value]) => {
      if (value && !((key === 'period' && value === 'month') || (key === 'view' && value === 'people'))) next.set(key, value);
      else next.delete(key);
    });
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const load = useCallback(async () => {
    setLoading(true);
    const requests = [
      ['数据质量', api.getStrict(`/data/health?period=${period}`)],
      ['人员绩效', api.getStrict(`/evaluation/personnel?period=${period}`)],
    ];
    const results = await Promise.allSettled(requests.map(([, promise]) => promise));
    const errors = [];
    if (results[0].status === 'fulfilled') setHealth(results[0].value || null);
    else errors.push(`数据质量：${results[0].reason?.message || '加载失败'}`);
    if (results[1].status === 'fulfilled') {
      const data = results[1].value;
      setPersonnel(Array.isArray(data)
        ? { overview: null, list: data, period_label: '', scope_label: '' }
        : { overview: data?.overview || null, list: data?.list || [], period_label: data?.period_label || '', scope_label: data?.scope_label || '' });
    } else errors.push(`人员绩效：${results[1].reason?.message || '加载失败'}`);
    setLoadErrors(errors);
    setLoading(false);
  }, [period]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.track('report.opened', {}); }, []);

  const downloadExport = useCallback(async (url, fallbackFilename, reportType) => {
    if (exporting) return;
    setExporting(reportType);
    try {
      const result = await api.downloadStrict(url);
      const objectUrl = URL.createObjectURL(result.blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = result.filename || fallbackFilename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
      await api.track('report.exported', { report_type: reportType });
      message.success(`${link.download} 已开始下载`);
    } catch (error) {
      message.error(error.message || '导出失败，未生成文件');
    } finally {
      setExporting('');
    }
  }, [exporting]);

  const personCols = useMemo(() => [
    { title: '人员', key: 'person', width: 150, render: (_, row) => <><Text strong>{row.real_name || '未命名'}</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{ROLE_CN[row.role] || row.role || '-'}</Text></> },
    { title: '工单', key: 'workorders', width: 120, sorter: (a, b) => (a.wo_total || 0) - (b.wo_total || 0), render: (_, row) => <><Text strong>{row.wo_total || 0} 份</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>已闭环 {row.wo_closed || 0} 份</Text></> },
    {
      title: '闭环与 SLA', key: 'quality', width: 250, render: (_, row) => <Space size={[4, 4]} wrap>
        {Number(row.wo_total) > 0 ? <Tag color={evaluationRateColor(Number(row.wo_closed_rate) || 0)}>闭环 {Number(row.wo_closed_rate) || 0}%</Tag> : <Text type="secondary">无工单样本</Text>}
        {Number(row.closed_sla_sample) > 0 ? <Tag color={evaluationRateColor(Number(row.on_time_rate) || 0)}>已关单按时 {Number(row.on_time_rate) || 0}%</Tag> : <Text type="secondary">无已关单 SLA 样本</Text>}
        {Number(row.closed_sla_breach) > 0 && <Tag color="red">已关单超时 {row.closed_sla_breach}</Tag>}
        {Number(row.open_overdue) > 0 && <Tag color="orange">开放已逾期 {row.open_overdue}</Tag>}
      </Space>,
    },
    { title: '响应与处置', key: 'duration', width: 170, render: (_, row) => <><Text>{row.response_hours == null ? '无到站签到样本' : `平均响应 ${row.response_hours}h`}</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{row.wo_avg_days == null ? '无已关单处置样本' : `平均处置 ${row.wo_avg_days} 天`}</Text></> },
    { title: '巡检', key: 'inspection', width: 130, render: (_, row) => <><Text>执行 {row.insp_done || 0} 项</Text><Text type="secondary" style={{ display: 'block', fontSize: 12 }}>审核 {row.insp_reviewed || 0} 项</Text></> },
  ], []);

  const managerCols = useMemo(() => [
    { title: '负责人', dataIndex: 'manager', key: 'manager', render: (value) => <Text strong>{value || '未分配'}</Text> },
    { title: '已配置 / 负责站点', key: 'configured', render: (_, row) => `${row.configured_site_count || 0} / ${row.site_count || 0}` },
    { title: '应报', dataIndex: 'expected', key: 'expected' },
    { title: '实到', dataIndex: 'actual', key: 'actual' },
    { title: '完整性', dataIndex: 'completeness_rate', key: 'completeness_rate', render: renderRate },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate', render: renderRate },
    { title: '及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate', render: renderRate },
    { title: '缺失', dataIndex: 'missing', key: 'missing' },
    { title: '超限', dataIndex: 'over_limit', key: 'over_limit' },
  ], []);

  const siteCols = useMemo(() => [
    { title: '站点', dataIndex: 'site_name', key: 'site_name', ellipsis: true },
    { title: '负责人', dataIndex: 'manager', key: 'manager', ellipsis: true, render: (value) => value || '未分配' },
    { title: '监测配置', dataIndex: 'configuration_status', key: 'configuration_status', render: (value) => { const status = CONFIG_STATUS[value] || { label: value || '未知', color: 'default' }; return <Tag color={status.color}>{status.label}</Tag>; } },
    { title: '应报', dataIndex: 'expected', key: 'expected' },
    { title: '实到', dataIndex: 'actual', key: 'actual' },
    { title: '完整性', dataIndex: 'completeness_rate', key: 'completeness_rate', render: renderRate },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate', render: renderRate },
    { title: '及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate', render: renderRate },
    { title: '缺失', dataIndex: 'missing', key: 'missing' },
    { title: '超限', dataIndex: 'over_limit', key: 'over_limit' },
  ], []);

  const byManager = useMemo(() => health?.by_manager || [], [health]);
  const bySite = useMemo(() => health?.by_site || [], [health]);
  const hasPersonnelActivity = personnel.list.some((row) => Number(row.wo_total) > 0 || Number(row.insp_done) > 0 || Number(row.insp_reviewed) > 0);
  const hasMonitoringSamples = Number(health?.total?.actual) > 0;
  const unconfiguredSites = Number(health?.total?.unconfigured_site_count) || 0;
  const hasAnyLoadedData = personnel.list.length > 0 || byManager.length > 0 || bySite.length > 0;
  const closedSlaBreach = personnel.list.reduce((sum, item) => sum + (Number(item.closed_sla_breach) || 0), 0);
  const openOverdue = personnel.list.reduce((sum, item) => sum + (Number(item.open_overdue) || 0), 0);
  const statusItems = [
    { key: 'staff', label: '在岗人员', value: personnel.overview?.staff_count ?? personnel.list.length, always: true },
    { key: 'workorders', label: '本周期工单', value: personnel.overview?.wo_total || 0, always: true },
    { key: 'closed-breach', label: '已关单超时', value: closedSlaBreach, color: closedSlaBreach ? '#cf1322' : undefined, always: true },
    { key: 'open-overdue', label: '开放已逾期', value: openOverdue, color: openOverdue ? tokens.colorWarning : undefined, always: true },
    { key: 'inspection', label: '已完成巡检', value: personnel.overview?.insp_done || 0, always: true },
  ];

  const toolbar = <WorkspaceToolbar actions={<>
    <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
    <Button icon={<DownloadOutlined />} loading={exporting === 'evaluation'} disabled={Boolean(exporting)} onClick={() => downloadExport(`/export/evaluation?period=${period}`, `人员评估_${personnel.period_label || period}.xlsx`, 'evaluation')}>导出评估</Button>
    <Dropdown menu={{ items: [{ key: 'quarter', label: '本季度运维报告' }, { key: 'year', label: '本年度运维报告' }], onClick: ({ key }) => downloadExport(`/export/ops-report?period=${key}`, opsReportFilename(key), key) }} disabled={Boolean(exporting)}>
      <Button type="primary" icon={<FileExcelOutlined />} loading={exporting === 'quarter' || exporting === 'year'}>导出运维报告</Button>
    </Dropdown>
  </>}>
    <FilterField label="统计周期">
      <Select aria-label="选择运营绩效统计周期" value={period} onChange={(value) => updateQuery({ period: value })} options={PERIOD_OPTS} style={{ width: 160 }} />
    </FilterField>
    {personnel.scope_label && <Text type="secondary">统计范围：{personnel.scope_label}</Text>}
  </WorkspaceToolbar>;

  const periodRule = '已关闭工单按关单时间归期，未关闭工单按创建时间归期；SLA 达标率只统计已关单且配置截止时间的样本';
  if (!loading && loadErrors.length > 0 && !hasAnyLoadedData) {
    return <WorkspacePage title="运营绩效" subtitle={periodRule} toolbar={toolbar}><WorkspaceEmpty type="error" description={`${loadErrors.join('；')}。当前不能判断是否无记录。`} onRefresh={load} /></WorkspacePage>;
  }

  const tabs = [
    {
      key: 'people', label: `人员绩效 ${personnel.list.length}`,
      children: <WorkspaceTable rowKey={(row) => row.id} loading={loading} columns={personCols} dataSource={personnel.list} emptyType="sample" onRefresh={load} scroll={{ x: 820, y: 'calc(100vh - 410px)' }} />,
    },
    {
      key: 'managers', label: `负责人数据质量 ${byManager.length}`,
      children: <WorkspaceTable rowKey={(row) => row.manager || 'unassigned'} loading={loading} columns={managerCols} dataSource={byManager} emptyType="sample" onRefresh={load} scroll={{ x: 900, y: 'calc(100vh - 410px)' }} />,
    },
    {
      key: 'sites', label: `站点数据质量 ${bySite.length}`,
      children: <WorkspaceTable rowKey={(row) => row.site_id || row.site_name} loading={loading} columns={siteCols} dataSource={bySite} emptyType="sample" onRefresh={load} pagination={bySite.length > 20 ? { pageSize: 20, size: 'small', showSizeChanger: false } : false} scroll={{ x: 920, y: 'calc(100vh - 450px)' }} />,
    },
  ];

  return <WorkspacePage
    title="运营绩效"
    subtitle={personnel.period_label ? `考核周期：${personnel.period_label}；${periodRule}` : periodRule}
    statusItems={statusItems}
    toolbar={toolbar}
  >
    {loadErrors.length > 0 && <Alert type="warning" showIcon message="部分数据未更新" description={loadErrors.join('；')} style={{ marginBottom: 8 }} />}
    {activeView === 'people' && !hasPersonnelActivity && <Alert type="info" showIcon message="本周期没有人员执行样本" description="在岗人员仍保留在名单中；无工单或巡检记录不等同于绩效为 0 分。" style={{ marginBottom: 8, padding: '8px 12px' }} />}
    {activeView !== 'people' && unconfiguredSites > 0 && <Alert type="info" showIcon message={`${unconfiguredSites} 个站点没有可用的应报参数配置`} description="未启用监测或没有历史参数依据的站点不计算应报、缺失和完整性，不使用全局阈值参数代替站点配置。" style={{ marginBottom: 8, padding: '8px 12px' }} />}
    {activeView !== 'people' && unconfiguredSites === 0 && !hasMonitoringSamples && <Alert type="info" showIcon message="本周期未接收到监测样本" description="已配置站点按应报量计算缺失；有效性和及时性在无实到样本时保持“无样本”。" style={{ marginBottom: 8, padding: '8px 12px' }} />}
    <Tabs className="workspace-tabs" activeKey={activeView} onChange={(value) => updateQuery({ view: value })} items={tabs} />
  </WorkspacePage>;
}
