import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, DatePicker, Drawer, Dropdown, Image, Modal, Select, Space, Table, Tag, Typography, message } from 'antd';
import dayjs from 'dayjs';
import { ArrowLeftOutlined, CloseOutlined, DownloadOutlined, FileExcelOutlined, LeftOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../services/api';
import { evaluationRateColor, orderStatusMap } from '../../services/constants';
import WorkspacePage, {
  FilterField, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar,
  formatTablePaginationTotal, standardListPagination,
} from '../../components/WorkspacePage';
import { useTheme } from '../../hooks/useTheme';
import './EvaluationPage.css';

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
const CONFIG_STATUS = {
  configured: { label: '已配置', color: 'green' },
  metrics_unconfigured: { label: '参数未配置', color: 'orange' },
  monitoring_disabled: { label: '未启用监测', color: 'default' },
};

const renderRate = (value) => value == null
  ? <Text type="secondary">无样本</Text>
  : <Tag color={evaluationRateColor(Number(value))}>{Number(value)}%</Tag>;

const opsReportFilename = () => '水质运维工作报告.docx';
const evaluationScroll = () => document.querySelector('.evaluation-page');

const queryForPeriod = (params) => {
  const requested = params.get('period') || 'month';
  const period = PERIOD_KEYS.has(requested) ? requested : 'month';
  const month = params.get('month') || '';
  const historical = period === 'month' && /^\d{4}-\d{2}$/.test(month) && month !== dayjs().format('YYYY-MM') ? month : '';
  return historical
    ? `period=custom&start=${historical}-01&end=${dayjs(`${historical}-01`).endOf('month').format('YYYY-MM-DD')}`
    : `period=${period}`;
};

const beijingTime = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`;
};

export default function EvaluationPage() {
  const { tokens } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const periodParam = searchParams.get('period') || 'month';
  const period = PERIOD_KEYS.has(periodParam) ? periodParam : 'month';
  const month = searchParams.get('month') || '';
  const historicalMonth = period === 'month' && /^\d{4}-\d{2}$/.test(month) && month !== dayjs().format('YYYY-MM') ? month : '';
  const periodQuery = queryForPeriod(searchParams);
  const [selectedPerson, setSelectedPerson] = useState(location.state?.restorePhoto?.selectedPerson ?? location.state?.restoreRecords?.selectedPerson ?? null);
  const [siteMode, setSiteMode] = useState(location.state?.restorePhoto?.siteMode || location.state?.restoreRecords?.siteMode || 'all');
  const [selectedSite, setSelectedSite] = useState(null);
  const [factorDetail, setFactorDetail] = useState(null);
  const [recordDetail, setRecordDetail] = useState(null);
  const [health, setHealth] = useState(null);
  const [personnel, setPersonnel] = useState({ overview: null, list: [], period_label: '', scope_label: '' });
  const [personLoading, setPersonLoading] = useState(true);
  const [healthLoading, setHealthLoading] = useState(true);
  const [loadErrors, setLoadErrors] = useState({});
  const loading = personLoading || healthLoading;
  const [exporting, setExporting] = useState('');
  const [photoDetail, setPhotoDetail] = useState(null);
  const photoRequest = useRef(0);
  const loadRequest = useRef(0);
  const recordRequest = useRef(0);
  const factorRequest = useRef(0);
  const restored = useRef(false);
  const siteSection = useRef(null);
  const siteOpener = useRef(null);
  const siteDetailHeading = useRef(null);
  const siteDetailScroll = useRef(null);
  const siteFactorScroll = useRef(0);
  const selectedSiteId = selectedSite?.site_id;
  const selectedMetric = factorDetail?.metric?.metric;
  useEffect(() => {
    if (!selectedSiteId) return;
    siteDetailScroll.current?.scrollTo({ top: selectedMetric ? 0 : siteFactorScroll.current });
    siteDetailHeading.current?.focus({ preventScroll: true });
  }, [selectedSiteId, selectedMetric]);
  useEffect(() => {
    factorRequest.current += 1;
    setFactorDetail(null);
    setSelectedSite(null);
  }, [selectedPerson, siteMode]);
  useEffect(() => () => { photoRequest.current += 1; recordRequest.current += 1; factorRequest.current += 1; loadRequest.current += 1; }, []);
  const openOriginalInspection = useCallback((photo) => {
    const restore = { person: photoDetail.person, selectedPeriod: photoDetail.selectedPeriod,
      month: photoDetail.month, photoId: photo.attachment_id, scrollY: evaluationScroll()?.scrollTop || 0,
      selectedPerson, siteMode };
    navigate(`${location.pathname}${location.search}`, { replace: true, state: { restorePhoto: restore } });
    const params = new URLSearchParams({ original_plan: photo.plan_id, original_site: photo.site_id,
      original_item: photo.item_id, original_attachment: photo.attachment_id });
    navigate(`/plan-schedules?${params}`);
  }, [location.pathname, location.search, navigate, photoDetail, selectedPerson, siteMode]);
  const openPhotoDetail = useCallback(async (person, selectedPeriod = period, month = historicalMonth) => {
    const requestId = ++photoRequest.current;
    setPhotoDetail({ person, selectedPeriod, month, loading: true, photos: [], error: '' });
    try {
      const query = month ? `month=${encodeURIComponent(month)}` : `period=${encodeURIComponent(selectedPeriod)}`;
      const detail = await api.getStrict(`/evaluation/personnel/${person.id}/photo-quality?${query}`);
      if (!detail || !Array.isArray(detail.photos) || typeof detail.deduction !== 'number') throw new Error('照片明细数据不完整，请重试');
      if (requestId !== photoRequest.current) return;
      setPhotoDetail({ person, selectedPeriod, month, loading: false, photos: detail.photos, deduction: detail.deduction, start: detail.start, end: detail.end, error: '' });
    } catch (error) {
      if (requestId === photoRequest.current) setPhotoDetail({ person, selectedPeriod, month, loading: false, photos: [], error: error.message || '明细加载失败' });
    }
  }, [period, historicalMonth]);

  useEffect(() => {
    const restore = location.state?.restorePhoto;
    if (!restore || restored.current) return;
    restored.current = true;
    openPhotoDetail(restore.person, restore.selectedPeriod, restore.month);
  }, [location.state, openPhotoDetail]);

  useEffect(() => {
    if (!photoDetail || photoDetail.loading || !location.state?.restorePhoto?.photoId) return;
    const timer = setTimeout(() => document.getElementById(`quality-photo-${location.state.restorePhoto.photoId}`)?.scrollIntoView({ block: 'center' }), 150);
    return () => clearTimeout(timer);
  }, [location.state, photoDetail]);

  const updateQuery = useCallback((patch) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([key, value]) => {
      if (value && !((key === 'period' && value === 'month') || (key === 'view' && value === 'people'))) next.set(key, value);
      else next.delete(key);
    });
    if (queryForPeriod(next) !== periodQuery) {
      loadRequest.current += 1;
      recordRequest.current += 1;
      factorRequest.current += 1;
    }
    setSearchParams(next, { replace: true });
  }, [periodQuery, searchParams, setSearchParams]);

  const load = useCallback(async () => {
    const requestId = ++loadRequest.current;
    recordRequest.current += 1;
    factorRequest.current += 1;
    setSelectedSite(null);
    setFactorDetail(null);
    setRecordDetail(null);
    setPersonLoading(true);
    setHealthLoading(true);
    setLoadErrors({});
    setHealth(null);
    setPersonnel({ overview: null, list: [], period_label: '', scope_label: '' });
    const healthRequest = api.getStrict(`/data/health?scope=report&${periodQuery}`)
      .then(data => { if (requestId === loadRequest.current) setHealth(data || null); })
      .catch(error => { if (requestId === loadRequest.current) setLoadErrors(prev => ({ ...prev, health: `数据质量：${error.message || '加载失败'}` })); })
      .finally(() => { if (requestId === loadRequest.current) setHealthLoading(false); });
    const personnelRequest = api.getStrict(`/evaluation/personnel?${periodQuery}`)
      .then(data => {
        if (requestId !== loadRequest.current) return;
        setPersonnel(Array.isArray(data)
          ? { overview: null, list: data, period_label: '', scope_label: '' }
          : { overview: data?.overview || null, list: data?.list || [], period_label: data?.period_label || '', scope_label: data?.scope_label || '', start: data?.start, end: data?.end });
      })
      .catch(error => { if (requestId === loadRequest.current) setLoadErrors(prev => ({ ...prev, personnel: `人员绩效：${error.message || '加载失败'}` })); })
      .finally(() => { if (requestId === loadRequest.current) setPersonLoading(false); });
    await Promise.all([healthRequest, personnelRequest]);
  }, [periodQuery]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!loading && ['managers', 'sites'].includes(searchParams.get('view'))) {
      siteSection.current?.scrollIntoView({ block: 'start' });
    }
  }, [loading, searchParams]);
  useEffect(() => {
    recordRequest.current += 1;
    factorRequest.current += 1;
    setSelectedSite(null);
    setFactorDetail(null);
    setRecordDetail(null);
  }, [periodQuery]);
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

  const openRecords = useCallback(async (person, kind) => {
    factorRequest.current += 1;
    setFactorDetail(null);
    setSelectedSite(null);
    const requestId = ++recordRequest.current;
    setRecordDetail({ person, kind, loading: true });
    try {
      const data = await api.getStrict(`/evaluation/personnel/${person.id}/records?${periodQuery}&kind=${kind}`);
      if (requestId === recordRequest.current) setRecordDetail({ person, kind, ...data });
    } catch (error) {
      if (requestId === recordRequest.current) setRecordDetail({ person, kind, error: error.message || '明细加载失败' });
    }
  }, [periodQuery]);

  const restoredRecords = useRef(false);
  useEffect(() => {
    const restore = location.state?.restoreRecords;
    if (!restore || restoredRecords.current) return;
    restoredRecords.current = true;
    openRecords(restore.person, restore.kind);
  }, [location.state, openRecords]);

  const openInspectionRecord = useCallback((row) => {
    navigate(`${location.pathname}${location.search}`, { replace: true, state: {
      restoreRecords: { person: recordDetail.person, kind: recordDetail.kind, selectedPerson, siteMode, scrollY: evaluationScroll()?.scrollTop || 0 },
    } });
    navigate(`/plan-schedules?${new URLSearchParams({ original_plan: row.plan_id, original_site: row.site_id, original_item: row.id })}`);
  }, [location.pathname, location.search, navigate, recordDetail, selectedPerson, siteMode]);

  const openWorkOrder = useCallback((orderNo) => {
    navigate(`${location.pathname}${location.search}`, { replace: true, state: {
      restoreRecords: { person: recordDetail.person, kind: recordDetail.kind, selectedPerson, siteMode, scrollY: evaluationScroll()?.scrollTop || 0 },
    } });
    navigate(`/workorders?order=${encodeURIComponent(orderNo)}`);
  }, [location.pathname, location.search, navigate, recordDetail, selectedPerson, siteMode]);

  useEffect(() => {
    const restore = location.state?.restorePhoto || location.state?.restoreRecords;
    if (loading || !restore) return;
    const frame = requestAnimationFrame(() => {
      const container = evaluationScroll();
      if (container) container.scrollTop = restore.scrollY || 0;
    });
    return () => cancelAnimationFrame(frame);
  }, [loading, location.state]);

  const openFactor = useCallback(async (site, metric) => {
    const requestId = ++factorRequest.current;
    setFactorDetail({ site, metric, loading: true });
    try {
      const query = new URLSearchParams(`scope=report&${periodQuery}&site_id=${site.site_id}&metric=${encodeURIComponent(metric.metric)}`);
      const data = await api.getStrict(`/data/health/records?${query}`);
      if (requestId === factorRequest.current) setFactorDetail({ ...data, site, metric });
    } catch (error) {
      if (requestId === factorRequest.current) setFactorDetail({ site, metric, error: error.message || '因子明细加载失败' });
    }
  }, [periodQuery]);

  const openSite = useCallback((site, opener) => {
    if (opener) siteOpener.current = opener;
    siteFactorScroll.current = 0;
    recordRequest.current += 1;
    setRecordDetail(null);
    factorRequest.current += 1;
    setFactorDetail(null);
    setSelectedSite(site);
  }, []);
  const closeSite = useCallback(() => {
    factorRequest.current += 1;
    setFactorDetail(null);
    setSelectedSite(null);
    if (siteOpener.current?.isConnected) siteOpener.current.focus({ preventScroll: true });
  }, []);
  const returnToFactors = () => {
    factorRequest.current += 1;
    setFactorDetail(null);
  };

  const siteCols = useMemo(() => [
    { title: '站点', dataIndex: 'site_name', key: 'site_name', render: (value, row) => <Button className="evaluation-site-name" type="link" aria-pressed={selectedSiteId === row.site_id} onClick={event => openSite(row, event.currentTarget)}>{value}</Button> },
    { title: '负责人', dataIndex: 'manager', key: 'manager', ellipsis: true, render: (value) => value || '未分配' },
    { title: '监测配置', dataIndex: 'configuration_status', key: 'configuration_status', render: (value) => { const status = CONFIG_STATUS[value] || { label: value || '未知', color: 'default' }; return <Tag color={status.color}>{status.label}</Tag>; } },
    { title: '应报', dataIndex: 'expected', key: 'expected' },
    { title: '实到', dataIndex: 'actual', key: 'actual' },
    { title: '完整性', dataIndex: 'completeness_rate', key: 'completeness_rate', render: renderRate },
    { title: '有效性', dataIndex: 'validity_rate', key: 'validity_rate', render: renderRate },
    { title: '及时性', dataIndex: 'timeliness_rate', key: 'timeliness_rate', render: renderRate },
    { title: '缺失', dataIndex: 'missing', key: 'missing' },
    { title: '超限', dataIndex: 'over_limit', key: 'over_limit' },
    { title: '', key: 'details', render: (_, row) => <Button type="link" onClick={event => openSite(row, event.currentTarget)}>因子与记录</Button> },
  ], [openSite, selectedSiteId]);

  const byManager = useMemo(() => health?.by_manager || [], [health]);
  const bySite = useMemo(() => health?.by_site || [], [health]);
  const visibleSites = bySite.filter(site => siteMode === 'unassigned' ? !site.responsible_people?.length :
    siteMode === 'person' ? site.responsible_people?.some(person => person.id === selectedPerson) : true);
  const selectedSiteIndex = visibleSites.findIndex(site => site.site_id === selectedSiteId);
  const hasAnyLoadedData = personnel.list.length > 0 || byManager.length > 0 || bySite.length > 0;
  const closedSlaBreach = personnel.list.reduce((sum, item) => sum + (Number(item.closed_sla_breach) || 0), 0);
  const openOverdue = personnel.list.reduce((sum, item) => sum + (Number(item.open_overdue) || 0), 0);
  const statusItems = [
    { key: 'staff', label: '在岗人员', value: personnel.overview?.staff_count ?? personnel.list.length, always: true },
    { key: 'workorders', label: '本周期工单', value: personnel.overview?.work_order_attribution_incomplete ? '归属待核对' : personnel.overview?.wo_total || 0, always: true },
    { key: 'closed-breach', label: '已关单超时', value: personnel.overview?.work_order_attribution_incomplete ? '归属待核对' : closedSlaBreach, color: closedSlaBreach ? '#cf1322' : undefined, always: true },
    { key: 'open-overdue', label: '开放已逾期', value: personnel.overview?.work_order_attribution_incomplete ? '归属待核对' : openOverdue, color: openOverdue ? tokens.colorWarning : undefined, always: true },
    { key: 'inspection', label: '已完成巡检项', value: personnel.overview?.insp_done || 0, always: true },
  ];

  const toolbar = <WorkspaceToolbar actions={<>
    <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
    <Button icon={<DownloadOutlined />} loading={exporting === 'evaluation'} disabled={Boolean(exporting)} onClick={() => downloadExport(`/export/evaluation?${periodQuery}`, `人员客观指标明细_${personnel.period_label || period}.xlsx`, 'evaluation')}>下载人员指标明细</Button>
    <Dropdown menu={{ items: [
      { key: 'report-quarter', label: '本季度工作报告（Word）' },
      { key: 'report-year', label: '本年度工作报告（Word）' },
      { key: 'details-quarter', label: '本季度详细数据附件（Excel）' },
      { key: 'details-year', label: '本年度详细数据附件（Excel）' },
    ], onClick: ({ key }) => {
      const [kind, periodKey] = key.split('-');
      downloadExport(`/export/ops-report${kind === 'details' ? '-details' : ''}?period=${periodKey}`,
        kind === 'details' ? `水质运维详细数据_${periodKey}.xlsx` : opsReportFilename(), key);
    } }} disabled={Boolean(exporting)}>
      <Button type="primary" icon={<FileExcelOutlined />} loading={exporting.startsWith('report-') || exporting.startsWith('details-')}>下载工作报告</Button>
    </Dropdown>
  </>}>
    <FilterField label="统计周期">
      <Select aria-label="选择运营绩效统计周期" value={period} onChange={(value) => updateQuery({ period: value, month: '' })} options={PERIOD_OPTS} style={{ width: 160 }} />
    </FilterField>
    {period === 'month' && <FilterField label="统计月份"><DatePicker picker="month" allowClear={false} disabledDate={value => value.isAfter(dayjs(), 'month')} value={dayjs(`${month || dayjs().format('YYYY-MM')}-01`)} onChange={(_, value) => updateQuery({ month: value })} /></FilterField>}
    {personnel.scope_label && <Text type="secondary">统计范围：{personnel.scope_label}</Text>}
  </WorkspaceToolbar>;

  const errorMessages = Object.values(loadErrors);
  if (!loading && errorMessages.length === 2 && !hasAnyLoadedData) {
    return <WorkspacePage className="evaluation-page" title="运营绩效" toolbar={toolbar}><WorkspaceEmpty type="error" description={errorMessages.join('；')} onRefresh={load} /></WorkspacePage>;
  }

  return <WorkspacePage
    className="evaluation-page"
    title="运营绩效"
    subtitle={personnel.start ? `考核周期：${personnel.start.slice(0, 10)} 至 ${personnel.end.slice(0, 10)}` : undefined}
    statusItems={personLoading || !personnel.overview ? [] : statusItems}
    toolbar={toolbar}
  >
    {errorMessages.length > 0 && <Alert type="warning" showIcon message="部分数据未更新" description={errorMessages.join('；')} style={{ marginBottom: 8 }} />}
    <div className={`evaluation-layout${recordDetail ? ' evaluation-layout--detail' : ''}`} style={{
      '--evaluation-text': tokens.colorText,
      '--evaluation-secondary': tokens.colorTextSecondary,
      '--evaluation-border': tokens.colorBorder,
      '--evaluation-primary': tokens.colorPrimary,
      '--evaluation-error': tokens.colorError,
      '--evaluation-surface': tokens.panelBg,
      '--evaluation-detail-surface': tokens.colorBgElevated,
      '--evaluation-shadow': tokens.shadowCard,
    }}>
    <div className="evaluation-main">
    <Typography.Title level={4}>人员绩效</Typography.Title>
    <div className="evaluation-people">
      {personnel.list.map(person => {
        const assigned = bySite.filter(site => site.responsible_people?.some(owner => owner.id === person.id));
        const unconfigured = assigned.filter(site => site.configuration_status !== 'configured').length;
        const calculable = assigned.filter(site => site.configuration_status === 'configured' && site.expected > 0);
        const ambiguous = person.work_order_attribution === 'ambiguous';
        return <Card key={person.id} className="evaluation-person" size="small" onClick={() => { setSelectedPerson(person.id); setSiteMode('person'); }}
          tabIndex={0} onKeyDown={event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); setSelectedPerson(person.id); setSiteMode('person'); } }}
          aria-label={`${person.real_name || '未命名'}，${siteMode === 'person' && selectedPerson === person.id ? '已选中' : '查看负责站点'}`}
          style={{ cursor: 'pointer', borderColor: siteMode === 'person' && selectedPerson === person.id ? tokens.colorPrimary : undefined }}>
          <div className="evaluation-person-head"><div><strong>{person.real_name || '未命名'}</strong> <Text type="secondary">{ROLE_CN[person.role] || person.role}</Text></div></div>
          <div className="evaluation-metrics">
            <div className="evaluation-metric">{ambiguous ? <><span className="evaluation-metric-title">工单闭环</span><strong className="evaluation-metric-empty">归属待核对</strong></> : <button className="evaluation-metric-main" onClick={event => { event.stopPropagation(); openRecords(person, 'workorders'); }}><span className="evaluation-metric-title">工单闭环</span><strong className="evaluation-metric-value">{person.wo_closed}<small> / {person.wo_total} 份</small></strong>{person.wo_total > 0 && <span className="evaluation-metric-note">闭环率 {Math.round(person.wo_closed / person.wo_total * 100)}%</span>}</button>}</div>
            <div className="evaluation-metric"><button className="evaluation-metric-main" onClick={event => { event.stopPropagation(); openRecords(person, 'inspection_done'); }}><span className="evaluation-metric-title">巡检执行</span><strong className="evaluation-metric-value">{person.insp_done}<small> 项</small></strong></button><button className="evaluation-metric-note evaluation-link" onClick={event => { event.stopPropagation(); openRecords(person, 'inspection_reviewed'); }}>审核 {person.insp_reviewed} 项</button></div>
            <div className="evaluation-metric"><span className="evaluation-metric-title">已关单按时</span><strong className={person.on_time_rate == null || ambiguous ? 'evaluation-metric-empty' : 'evaluation-metric-value'}>{ambiguous ? '归属待核对' : person.on_time_rate == null ? '无样本' : <>{person.on_time_rate}<small> %</small></>}</strong>{!ambiguous && person.on_time_rate != null && <span className="evaluation-metric-note">超时 {person.closed_sla_breach || 0} · 逾期 {person.open_overdue || 0}</span>}</div>
            <div className="evaluation-metric"><button className="evaluation-metric-main" onClick={event => { event.stopPropagation(); openPhotoDetail(person); }}><span className="evaluation-metric-title">照片扣分</span><strong className={`evaluation-metric-value${Number(person.photo_quality_deduction) > 0 ? ' evaluation-metric-value--error' : ''}`}>{person.photo_quality_deduction || 0}<small> 分</small></strong></button></div>
          </div>
          <div className="evaluation-person-secondary"><span>响应 {ambiguous ? '待核对' : person.response_hours == null ? '无样本' : `${person.response_hours} 小时`}</span><span>处置 {ambiguous ? '待核对' : person.wo_avg_days == null ? '无样本' : `${person.wo_avg_days} 天`}</span></div>
          <div className="evaluation-person-sites"><strong>本人负责站点</strong><span>{health ? `${assigned.length} 站` : healthLoading ? '加载中' : '数据不可用'}</span><div>{health ? `${calculable.length ? `缺失 ${calculable.reduce((n, site) => n + site.missing, 0)} · 超限 ${calculable.reduce((n, site) => n + site.over_limit, 0)}` : '无可计算样本'} · 未配置或未启用 ${unconfigured} 站` : healthLoading ? '正在读取站点数据' : '站点数据读取失败'}</div></div>
        </Card>;
      })}
    </div>
    <Space ref={siteSection} wrap style={{ margin: '24px 0 12px' }}><Typography.Title level={4} style={{ margin: 0 }}>站点数据质量</Typography.Title>
      <Button onClick={() => { setSiteMode('all'); setSelectedPerson(null); }}>全部站点</Button>
      <Button onClick={() => { setSiteMode('unassigned'); setSelectedPerson(null); }}>无有效负责人</Button>
      {siteMode === 'person' && <Text>当前人员：{personnel.list.find(person => person.id === selectedPerson)?.real_name}</Text>}
    </Space>
    <WorkspaceTable rowKey="site_id" loading={healthLoading} columns={siteCols} dataSource={visibleSites} emptyType="sample" onRefresh={load} pagination={standardListPagination()} scroll={{ x: 920 }} rowClassName={row => row.site_id === selectedSiteId ? 'evaluation-site-row--selected' : ''} />
    </div>
    {selectedSite && <aside className="evaluation-site-panel" aria-labelledby="evaluation-site-title" onKeyDown={event => {
      if (event.key === 'Escape' && !event.defaultPrevented && !event.target.closest('.ant-select')) { event.stopPropagation(); if (factorDetail) returnToFactors(); else closeSite(); }
    }}>
      <header className="evaluation-site-panel__header">
        <div className="evaluation-site-panel__title">
          <h2 id="evaluation-site-title" ref={siteDetailHeading} tabIndex={-1}>{selectedSite.site_name}{factorDetail ? ` · ${factorDetail.metric.label || factorDetail.metric.metric}` : ' · 因子'}</h2>
          <Button type="text" icon={<CloseOutlined />} aria-label="关闭站点详情" onClick={closeSite} />
        </div>
        <div className="evaluation-site-panel__navigation">
          <Select aria-label="切换站点" showSearch optionFilterProp="label" value={selectedSiteId} options={visibleSites.map(site => ({ value: site.site_id, label: site.site_name }))} onChange={value => { const site = visibleSites.find(item => item.site_id === value); if (site) openSite(site); }} />
          <Button icon={<LeftOutlined />} aria-label="上一站点" disabled={selectedSiteIndex <= 0} onClick={() => openSite(visibleSites[selectedSiteIndex - 1])} />
          <Button icon={<RightOutlined />} aria-label="下一站点" disabled={selectedSiteIndex < 0 || selectedSiteIndex >= visibleSites.length - 1} onClick={() => openSite(visibleSites[selectedSiteIndex + 1])} />
        </div>
      </header>
      <div className="evaluation-site-panel__content" ref={siteDetailScroll}>
        <div className="evaluation-site-panel__context">
          <Text>负责人：{selectedSite.manager || '未分配'}</Text>
          {personnel.start && <Text type="secondary">{personnel.start.slice(0, 10)} — {personnel.end.slice(0, 10)}</Text>}
        </div>
        {!factorDetail ? <>
          <div className="evaluation-site-panel__rates"><span>完整性 {renderRate(selectedSite.completeness_rate)}</span><span>有效性 {renderRate(selectedSite.validity_rate)}</span><span>及时性 {renderRate(selectedSite.timeliness_rate)}</span></div>
          <Table className="evaluation-site-panel__table" rowKey="metric" size="small" tableLayout="fixed" dataSource={selectedSite.metrics || []} pagination={false} locale={{ emptyText: <WorkspaceEmpty type="sample" /> }} columns={[
            { title: '因子', dataIndex: 'label', render: (value, row) => <Button type="link" onClick={() => { siteFactorScroll.current = siteDetailScroll.current?.scrollTop || 0; openFactor(selectedSite, row); }}>{value || row.metric}</Button> },
            { title: '应报', dataIndex: 'expected', width: 52 }, { title: '实到', dataIndex: 'actual', width: 52 },
            { title: '缺失', dataIndex: 'missing', width: 52 }, { title: '超限', dataIndex: 'over_limit', width: 52 },
            { title: '最近记录', dataIndex: 'latest_at', width: 128, render: value => <span className="evaluation-site-panel__time">{beijingTime(value)}</span> },
          ]} />
        </> : <section aria-label="因子记录" aria-busy={factorDetail.loading ? 'true' : 'false'}>
          <div className="evaluation-site-panel__factor-heading"><Button icon={<ArrowLeftOutlined />} onClick={returnToFactors}>因子列表</Button><strong>{factorDetail.metric.label || factorDetail.metric.metric}</strong></div>
          {factorDetail.error ? <Alert type="error" message={factorDetail.error} action={<Button onClick={() => openFactor(factorDetail.site, factorDetail.metric)}>重试</Button>} /> : <Table className="evaluation-site-panel__table" key={`${selectedSiteId}-${selectedMetric}`} size="small" tableLayout="fixed" loading={factorDetail.loading} rowKey={row => `${row.endpoint_id}-${row.instrument_asset_code || ''}-${row.scheduled_at}`} dataSource={factorDetail.records || []} pagination={standardListPagination({ pageSize: undefined, defaultPageSize: 20, showTotal: formatTablePaginationTotal })} onChange={() => siteDetailScroll.current?.scrollTo({ top: 0 })} locale={{ emptyText: <WorkspaceEmpty type="sample" /> }} columns={[
            { title: '应报时间', dataIndex: 'scheduled_at', render: beijingTime },
            { title: '状态', width: 64, render: (_, row) => row.missing ? '缺失' : row.over_limit ? '超限' : '实到' },
            { title: '观测时间', render: (_, row) => beijingTime(row.observation?.observed_at) },
            { title: '数值', render: (_, row) => row.observation ? `${row.observation.standard_value} ${row.observation.standard_unit || ''}` : '—' },
          ]} />}
        </section>}
      </div>
    </aside>}
    {recordDetail && <aside className="evaluation-detail-host" aria-label="明细">
    <Drawer getContainer={false} rootStyle={{ position: 'absolute' }} title={`${recordDetail?.person?.real_name || ''} · ${recordDetail?.kind === 'workorders' ? '工单' : recordDetail?.kind === 'inspection_done' ? '本人执行检查项' : '本人审核检查项'}`} open={Boolean(recordDetail)} onClose={() => { recordRequest.current += 1; setRecordDetail(null); }} width="100%" mask={false}>
      {recordDetail?.loading && <p>正在加载明细…</p>}
      {recordDetail?.error && <Alert type="error" message={recordDetail.error} />}
      {recordDetail?.records && <><p>范围：{recordDetail.start} 至 {recordDetail.end}</p><WorkspaceTable rowKey="id" tableLayout="fixed" dataSource={recordDetail.records} pagination={standardListPagination()} columns={recordDetail.kind === 'workorders' ? [
        { title: '工单', dataIndex: 'order_no', render: value => <Button type="link" onClick={() => openWorkOrder(value)}>{value}</Button> }, { title: '标题', dataIndex: 'title' }, { title: '状态', dataIndex: 'status', render: value => orderStatusMap[value] || value || '—' }, { title: '创建时间', dataIndex: 'created_at' }, { title: '关单时间', dataIndex: 'resolved_at' },
      ] : [{ title: '计划', dataIndex: 'plan_name' }, { title: '检查项', dataIndex: 'item_name', render: (value, row) => <Button type="link" onClick={() => openInspectionRecord(row)}>{value}</Button> }, { title: '结果', dataIndex: 'result', render: value => value === 'normal' ? '正常' : value === 'abnormal' ? '异常' : '未填写' }, { title: '完成时间', dataIndex: 'completed_at' }, { title: '审核时间', dataIndex: 'review_time' }]} /></>}
    </Drawer>
    </aside>}
    </div>
    <Modal title={`${photoDetail?.person?.real_name || ''} · 照片质量扣分明细`} open={Boolean(photoDetail)} footer={null}
      onCancel={() => { photoRequest.current += 1; setPhotoDetail(null); }}>
      <Space wrap><Text>检查月份</Text><DatePicker picker="month" value={photoDetail?.month ? dayjs(`${photoDetail.month}-01`) : null} placeholder="原查询范围" onChange={(_, value) => openPhotoDetail(photoDetail.person, photoDetail.selectedPeriod, value || '')} /></Space>
      {!photoDetail?.loading && !photoDetail?.error && <p>范围：{photoDetail?.start} 至 {photoDetail?.end}</p>}
      {photoDetail?.loading && <p>正在加载照片明细…</p>}
      {photoDetail?.error && <Alert type="error" message={photoDetail.error} action={<Button onClick={() => openPhotoDetail(photoDetail.person, photoDetail.selectedPeriod, photoDetail.month)}>重试</Button>} />}
      {!photoDetail?.loading && !photoDetail?.error && <p>本范围扣分：{photoDetail?.deduction}</p>}
      {!photoDetail?.loading && !photoDetail?.error && photoDetail?.photos.length === 0 && <p>{photoDetail.month ? '该检查月份暂无已确认扣分照片' : '该范围暂无已确认扣分照片'}</p>}
      <Image.PreviewGroup>{photoDetail?.photos.map(photo => <div id={`quality-photo-${photo.attachment_id}`} key={photo.attachment_id} style={{ marginBottom: 16 }}>
        <Image src={photo.display_url} width={120} alt={photo.item_name || '不合格照片'} />
        <p>检查时间：{photo.completed_at || '未知'} · 归属月份：{photo.score_month || '未知'}</p>
        <p>{photo.item_name} · 扣{photo.deduction}分</p>
        <p>{photo.reason}</p>
        <Button type="link" onClick={() => openOriginalInspection(photo)}>查看原巡检</Button>
      </div>)}</Image.PreviewGroup>
    </Modal>
  </WorkspacePage>;
}
