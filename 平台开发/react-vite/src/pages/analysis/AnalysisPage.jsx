import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Alert, Button, Card, Typography, Spin, Row, Col,
  Table,
} from 'antd';
import EChart from '../../components/EChart';
import {
  ArrowUpOutlined, ArrowDownOutlined, DashboardOutlined,
  CheckCircleOutlined, FieldTimeOutlined, WarningOutlined,
  MinusOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { pageRootStyle } from '../../services/pageStyles';
import WorkspacePage, { WorkspaceEmpty } from '../../components/WorkspacePage';

const { Title, Text } = Typography;
const ANALYSIS_CHART_HEIGHT = 'clamp(260px, calc(100vh - 520px), 560px)';

function KpiCard({ title, value, suffix, prefix, trend, trendValue, icon, color, tokens }) {
  const isUp = trend === 'up';
  const isDown = trend === 'down';
  const trendColor = isUp ? tokens.colorSuccess : isDown ? tokens.colorError : tokens.colorTextTertiary;
  const trendIcon = isUp ? <ArrowUpOutlined /> : isDown ? <ArrowDownOutlined /> : <MinusOutlined />;

  return (
    <Card
      style={{ borderRadius: 8, height: '100%', border: `1px solid ${tokens.summaryBorder}`, boxShadow: tokens.summaryShadow }}
      styles={{ body: { padding: '20px 24px' } }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <Text style={{ color: tokens.colorTextSecondary, fontSize: 13, display: 'block', marginBottom: 8 }}>
            {title}
          </Text>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 28, fontWeight: 700, color: tokens.colorText, lineHeight: 1.2 }}>
              {prefix}{value}
            </span>
            {suffix && (
              <Text style={{ color: tokens.colorTextTertiary, fontSize: 14 }}>{suffix}</Text>
            )}
          </div>
          {trendValue != null && (
            <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ color: trendColor, fontSize: 13, fontWeight: 500 }}>
                {trendIcon} {trendValue}
              </span>
              <Text style={{ color: tokens.colorTextTertiary, fontSize: 12 }}>较上期</Text>
            </div>
          )}
        </div>
        <div style={{
          width: 44, height: 44, borderRadius: 8,
          background: color === tokens.colorWarning ? tokens.colorWarningBg : `${color}18`,
          border: `1px solid ${color === tokens.colorWarning ? tokens.colorWarningBorder : `${color}24`}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 20, color,
        }}>
          {icon}
        </div>
      </div>
    </Card>
  );
}

// ---------- Chart Components ----------

function ArrivalTrendChart({ tokens, arrival }) {
  const days = (arrival || []).map((d) => d.date.slice(5)); // MM-DD
  const rates = (arrival || []).map((d) => d.rate);
  const option = {
    tooltip: {
      trigger: 'axis', backgroundColor: tokens.colorBgElevated, borderColor: tokens.colorBorder,
      textStyle: { color: tokens.colorText, fontSize: 12 },
      valueFormatter: (v) => (v == null ? '无数据' : `${v}%`),
    },
    grid: { left: 48, right: 16, top: 24, bottom: 24 },
    xAxis: { type: 'category', data: days, axisLine: { lineStyle: { color: tokens.colorBorder } }, axisLabel: { color: tokens.colorTextSecondary, fontSize: 11 } },
    yAxis: { type: 'value', min: 0, max: 100, axisLine: { show: false }, splitLine: { lineStyle: { color: tokens.colorBorderSecondary } }, axisLabel: { color: tokens.colorTextSecondary, fontSize: 11, formatter: '{value}%' } },
    series: [{
      name: '到报率', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6,
      connectNulls: false, data: rates,
      lineStyle: { color: tokens.colorPrimary, width: 2 }, itemStyle: { color: tokens.colorPrimary },
      areaStyle: { color: tokens.colorPrimary, opacity: 0.12 },
    }],
  };
  return (
    <Card title="数据到报趋势（近7日）" style={{ borderRadius: 8, height: '100%', border: `1px solid ${tokens.colorBorderSecondary}` }} styles={{ body: { padding: '12px 16px 8px' } }}>
      <EChart option={option} style={{ height: ANALYSIS_CHART_HEIGHT }} />
    </Card>
  );
}

function WorkOrderAnalysisChart({ tokens, woStats }) {
  const statuses = ['待处理', '已受理', '已派发', '处理中', '待审核', '已完成'];
  const keys = ['pending', 'accepted', 'dispatched', 'in_progress', 'reviewing', 'closed'];
  const data = keys.map(k => woStats?.by_status?.[k] || 0);
  const colors = [tokens.colorWarning, tokens.colorInfo, tokens.colorPrimary, tokens.colorInfo, tokens.colorSuccess];
  const option = {
    tooltip: { trigger: 'axis', backgroundColor: tokens.colorBgElevated, borderColor: tokens.colorBorder, textStyle: { color: tokens.colorText, fontSize: 12 } },
    grid: { left: 48, right: 16, top: 24, bottom: 24 },
    xAxis: { type: 'category', data: statuses, axisLine: { lineStyle: { color: tokens.colorBorder } }, axisLabel: { color: tokens.colorTextSecondary, fontSize: 11 } },
    yAxis: { type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: tokens.colorBorderSecondary } }, axisLabel: { color: tokens.colorTextSecondary, fontSize: 11 } },
    series: [{
      type: 'bar', barWidth: '50%', data: data.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: [4, 4, 0, 0] } })),
    }],
  };
  return (
    <Card title="工单处理分析" style={{ borderRadius: 8, height: '100%', border: `1px solid ${tokens.colorBorderSecondary}` }} styles={{ body: { padding: '12px 16px 8px' } }}>
      <EChart option={option} style={{ height: ANALYSIS_CHART_HEIGHT }} />
    </Card>
  );
}

function DeviceStatusChart({ tokens, devices }) {
  const total = devices?.total || 0;
  const online = devices?.online || 0;
  const offline = devices?.offline || 0;
  const maintenance = devices?.maintenance ?? devices?.fault ?? 0;
  const data = [
    { value: online, name: '在线', itemStyle: { color: tokens.colorSuccess } },
    { value: offline, name: '离线', itemStyle: { color: tokens.colorError } },
    { value: maintenance, name: '维护中', itemStyle: { color: tokens.colorWarning } },
    { value: Math.max(0, total - online - offline - maintenance), name: '其他状态', itemStyle: { color: tokens.colorInfo } },
  ];
  const option = {
    tooltip: { trigger: 'item', backgroundColor: tokens.colorBgElevated, borderColor: tokens.colorBorder, textStyle: { color: tokens.colorText, fontSize: 12 }, formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 8, top: 'center', textStyle: { color: tokens.colorTextSecondary, fontSize: 12 }, itemWidth: 12, itemHeight: 12 },
    series: [{
      type: 'pie', radius: ['42%', '68%'], center: ['38%', '52%'], avoidLabelOverlap: false,
      label: { show: false }, emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold', color: tokens.colorText } },
      data,
    }],
  };
  return (
    <Card title="设备状态分布" style={{ borderRadius: 8, height: '100%', border: `1px solid ${tokens.colorBorderSecondary}` }} styles={{ body: { padding: '12px 16px 8px' } }}>
      <EChart option={option} style={{ height: ANALYSIS_CHART_HEIGHT }} />
    </Card>
  );
}

function InspectionTrendChart({ tokens, inspection }) {
  const months = (inspection || []).map((m) => `${m.month.slice(5)}月`);
  const rates = (inspection || []).map((m) => m.rate);
  const option = {
    tooltip: {
      trigger: 'axis', backgroundColor: tokens.colorBgElevated, borderColor: tokens.colorBorder,
      textStyle: { color: tokens.colorText, fontSize: 12 },
      valueFormatter: (v) => (v == null ? '无数据' : `${v}%`),
    },
    grid: { left: 48, right: 16, top: 24, bottom: 24 },
    xAxis: { type: 'category', data: months, axisLine: { lineStyle: { color: tokens.colorBorder } }, axisLabel: { color: tokens.colorTextSecondary, fontSize: 11 } },
    yAxis: { type: 'value', min: 0, max: 100, axisLine: { show: false }, splitLine: { lineStyle: { color: tokens.colorBorderSecondary } }, axisLabel: { color: tokens.colorTextSecondary, fontSize: 11, formatter: '{value}%' } },
    series: [{
      type: 'line', smooth: true, symbol: 'circle', symbolSize: 6,
      connectNulls: false, data: rates,
      lineStyle: { color: tokens.colorInfo, width: 2 },
      itemStyle: { color: tokens.colorInfo },
      areaStyle: { color: tokens.colorInfo, opacity: 0.12 },
    }],
  };
  return (
    <Card title="巡检完成率趋势（近12月）" style={{ borderRadius: 8, height: '100%', border: `1px solid ${tokens.colorBorderSecondary}` }} styles={{ body: { padding: '12px 16px 8px' } }}>
      <EChart option={option} style={{ height: ANALYSIS_CHART_HEIGHT }} />
    </Card>
  );
}

export default function AnalysisPage() {
  const { tokens } = useTheme();
  const [dashboard, setDashboard] = useState(null);
  const [inspStats, setInspStats] = useState(null);
  const [woStats, setWoStats] = useState(null);
  const [arrivalSummary, setArrivalSummary] = useState(null);
  const [trends, setTrends] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadErrors, setLoadErrors] = useState([]);

  // ---------- Data fetching ----------

  const loadAnalysis = useCallback(async () => {
    setLoading(true);
    const requests = [
      ['运营概览', api.getStrict('/dashboard/summary'), setDashboard],
      ['巡检统计', api.getStrict('/inspections/statistics'), setInspStats],
      ['工单统计', api.getStrict('/workorders/statistics'), setWoStats],
      ['到报汇总', api.getStrict('/data/arrival/summary'), setArrivalSummary],
      ['趋势数据', api.getStrict('/analysis/trends'), setTrends],
    ];
    const results = await Promise.allSettled(requests.map(([, promise]) => promise));
    const errors = [];
    results.forEach((result, index) => {
      const [label, , setter] = requests[index];
      if (result.status === 'fulfilled') setter(result.value || {});
      else errors.push(`${label}：${result.reason?.message || '加载失败'}`);
    });
    setLoadErrors(errors);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadAnalysis();
  }, [loadAnalysis]);

  // ---------- KPI derivation ----------

  const hasArrivalSample = (arrivalSummary?.by_metric || []).length > 0;
  const arrivalRate = hasArrivalSample ? arrivalSummary.total_avg : null;

  let closureRate = null;
  if (woStats?.total && woStats.total > 0) {
    closureRate = Math.round((woStats.by_status?.closed || 0) / woStats.total * 1000) / 10;
  }

  // Inspection completion rate
  let inspectionRate = null;
  if (inspStats?.total_tasks && inspStats.total_tasks > 0) {
    inspectionRate = Math.round(inspStats.completed_tasks / inspStats.total_tasks * 1000) / 10;
  } else if (dashboard?.inspections?.total && dashboard.inspections.total > 0) {
    inspectionRate = Math.round(dashboard.inspections.completed / dashboard.inspections.total * 1000) / 10;
  }

  const deviceTotal = Number(trends?.devices?.total) || 0;
  const deviceOfflineRate = deviceTotal > 0
    ? Math.round(((Number(trends?.devices?.offline) || 0) / deviceTotal) * 1000) / 10
    : null;

  // ---------- Benchmark table ----------

  const benchmark = (arrivalSummary?.by_metric || []).map((m, idx) => ({
    id: m.metric || idx,
    name: m.metric || '-',
    site_count: m.site_count || 0,
    throughput_rate: m.avg_rate ?? null,
    below_threshold: m.below_threshold ?? 0,
  }));

  const benchmarkColumns = [
    {
      title: '数据类型',
      dataIndex: 'name',
      key: 'name',
      width: 140,
      ellipsis: true,
      render: (text) => <Text strong>{text || '-'}</Text>,
    },
    {
      title: '站点数',
      dataIndex: 'site_count',
      key: 'site_count',
      width: 90,
      align: 'center',
      sorter: (a, b) => (a.site_count || 0) - (b.site_count || 0),
    },
    {
      title: '数据到报率',
      dataIndex: 'throughput_rate',
      key: 'throughput_rate',
      width: 120,
      align: 'center',
      sorter: (a, b) => (a.throughput_rate || 0) - (b.throughput_rate || 0),
      render: (val) => val != null ? (
        <Text style={{ color: val >= 95 ? tokens.colorSuccess : val >= 85 ? tokens.colorWarning : tokens.colorError }}>
          {val}%
        </Text>
      ) : '-',
    },
    {
      title: '低于阈值站点',
      dataIndex: 'below_threshold',
      key: 'below_threshold',
      width: 120,
      align: 'center',
      render: (value) => value || 0,
    },
  ];

  const hasArrivalTrend = (trends?.arrival || []).some((item) => item?.rate != null);
  const hasInspectionTrend = (trends?.inspection || []).some((item) => Number(item?.total) > 0);
  const hasWorkOrders = Number(woStats?.total) > 0;
  const hasDevices = deviceTotal > 0;
  const hasAnySample = hasArrivalTrend || hasInspectionTrend || hasWorkOrders || hasDevices;
  const unavailableSources = useMemo(() => loadErrors.join('；'), [loadErrors]);

  if (!loading && loadErrors.length > 0 && !hasAnySample) {
    return (
      <WorkspacePage title="数据分析" subtitle="基于已采集的运维和监测样本进行趋势复盘。">
        <WorkspaceEmpty
          type="error"
          description={`${unavailableSources}。当前不能判断是否无样本。`}
          onRefresh={loadAnalysis}
        />
      </WorkspacePage>
    );
  }

  if (!loading && !hasAnySample) {
    return (
      <WorkspacePage title="数据分析" subtitle="基于已采集的运维和监测样本进行趋势复盘。">
        <WorkspaceEmpty type="sample" description="尚无监测到报、巡检、工单或设备统计样本。" onRefresh={loadAnalysis} />
      </WorkspacePage>
    );
  }

  // ---------- Render ----------

  return (
    <div style={{ ...pageRootStyle, overflowY: 'auto' }}>
      {/* Header */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <Title level={4} style={{ margin: 0, color: tokens.colorText }}>数据分析</Title>
          <Text type="secondary">到报趋势近 7 日，巡检趋势近 12 月；工单和设备为当前授权范围累计状态。</Text>
        </div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={loadAnalysis}>刷新</Button>
      </div>

      {loadErrors.length > 0 && <Alert type="warning" showIcon message="部分统计未更新" description={unavailableSources} style={{ marginBottom: 12 }} />}
      {!hasArrivalSample && <Alert type="info" showIcon message="监测到报数据尚无样本" description="本页仍展示已有的工单、巡检和设备事实；到报率及参数对比不据此给出 0 分或推测结论。" style={{ marginBottom: 12, padding: '8px 12px' }} />}

      {/* KPI Cards */}
      <Spin spinning={loading}>
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          <Col xs={24} sm={12} lg={6} flex="1">
            <KpiCard
              title="今日数据到报率" value={arrivalRate ?? '无样本'} suffix={arrivalRate == null ? '' : '%'}
              icon={<DashboardOutlined />} color={tokens.colorPrimary}
              tokens={tokens}
            />
          </Col>
          <Col xs={24} sm={12} lg={6} flex="1">
            <KpiCard
              title="工单闭环率" value={closureRate ?? '无样本'} suffix={closureRate == null ? '' : '%'}
              icon={<CheckCircleOutlined />} color={tokens.colorSuccess}
              tokens={tokens}
            />
          </Col>
          <Col xs={24} sm={12} lg={6} flex="1">
            <KpiCard
              title="巡检完成率" value={inspectionRate ?? '无样本'} suffix={inspectionRate == null ? '' : '%'}
              icon={<FieldTimeOutlined />} color={tokens.colorInfo}
              tokens={tokens}
            />
          </Col>
          <Col xs={24} sm={12} lg={6} flex="1">
            <KpiCard
              title="设备离线率" value={deviceOfflineRate ?? '无样本'} suffix={deviceOfflineRate == null ? '' : '%'}
              icon={<WarningOutlined />} color={tokens.colorWarning}
              tokens={tokens}
            />
          </Col>
        </Row>
      </Spin>

      {/* Charts */}
      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        {hasArrivalTrend && <Col xs={24} lg={12}>
          <ArrivalTrendChart tokens={tokens} arrival={trends?.arrival} />
        </Col>}
        {hasWorkOrders && <Col xs={24} lg={12}>
          <WorkOrderAnalysisChart tokens={tokens} woStats={woStats} />
        </Col>}
        {hasDevices && <Col xs={24} lg={12}>
          <DeviceStatusChart tokens={tokens} devices={trends?.devices} />
        </Col>}
        {hasInspectionTrend && <Col xs={24} lg={12}>
          <InspectionTrendChart tokens={tokens} inspection={trends?.inspection} />
        </Col>}
      </Row>

      {/* Benchmark Table */}
      {benchmark.length > 0 && <Card
        title="监测参数到报情况"
        style={{ borderRadius: 8, border: `1px solid ${tokens.colorBorderSecondary}` }}
        styles={{ body: { padding: 0 } }}
      >
        <div style={{ maxHeight: 480, overflow: 'auto' }}>
          <style>{`.hide-scrollbar::-webkit-scrollbar{display:none}.hide-scrollbar{-ms-overflow-style:none;scrollbar-width:none}`}</style>
          <div className="hide-scrollbar">
            <Table
              columns={benchmarkColumns}
              dataSource={benchmark}
              rowKey={(r) => r.id || r.name}
              loading={loading}
              pagination={false}
              locale={{ emptyText: <WorkspaceEmpty type="sample" description="今日没有可比较的参数到报样本" /> }}
              size="middle"
            />
          </div>
        </div>
      </Card>}
    </div>
  );
}
