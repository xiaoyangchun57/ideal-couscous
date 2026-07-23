import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Table, Card, Input, Select, Button, Space, Tag, Badge,
  Row, Col, Typography, message, Modal, Switch,
  InputNumber, Tooltip, Divider, Form, Radio, Empty,
} from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  SearchOutlined,
  SettingOutlined,
  ReloadOutlined,
  ExperimentOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  UserOutlined,
  FileTextOutlined,
  LinkOutlined,
  AuditOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import {
  alertLevelColor, alertLevelLabel, alertStatusMap,
  CONCLUSION_OPTIONS,
} from '../../services/constants';
import { useTheme } from '../../hooks/useTheme';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import ThresholdRulesTab from './components/ThresholdRulesTab';

const reagentStatusColor = { 正常: 'green', 临期: 'orange', 低余量: 'red', 已过期: 'volcano', 未设置: 'default' };

const { Text, Title } = Typography;

// ---------------------------------------------------------------------------
// Status color / badge mapping
// ---------------------------------------------------------------------------
const statusColorMap = {
  pending: '#faad14',
  acknowledged: '#1890ff',
  resolved: '#52c41a',
};

const statusIconMap = {
  pending: <ExclamationCircleOutlined />,
  acknowledged: <ClockCircleOutlined />,
  resolved: <CheckCircleOutlined />,
};

// ---------------------------------------------------------------------------
// Alert severity labels (tiered display)
// ---------------------------------------------------------------------------
const alertSeverityLabel = {
  blue: '一般关注',
  yellow: '一般告警',
  orange: '较重告警',
  red: '紧急告警',
};

const alertSeverityTag = {
  blue: { color: '#38bdf8', label: 'IV级', desc: '一般关注' },
  yellow: { color: '#facc15', label: 'III级', desc: '一般告警' },
  orange: { color: '#fb923c', label: 'II级', desc: '较重告警' },
  red: { color: '#ef4444', label: 'I级', desc: '紧急告警' },
};

// ---------------------------------------------------------------------------
// Resolve reason options
// ---------------------------------------------------------------------------
const resolveReasonOptions = [
  { value: 'normal_deviation', label: '正常偏差', desc: '数据在正常波动范围内' },
  { value: 'manual_review', label: '人工复核', desc: '经人工核实确认无异常' },
  { value: 'false_alarm', label: '误报', desc: '传感器或系统误触发' },
  { value: 'equipment_maintenance', label: '设备维护', desc: '设备维护期间的正常现象' },
  { value: 'environmental_factor', label: '环境因素', desc: '天气、季节等环境因素影响' },
  { value: 'other', label: '其他', desc: '其他原因' },
];

// ---------------------------------------------------------------------------
// Date-range presets
// ---------------------------------------------------------------------------
const dateRangeOptions = [
  { label: '今日', value: 'today' },
  { label: '本周', value: 'week' },
  { label: '本月', value: 'month' },
];

// ---------------------------------------------------------------------------
// Helper: check if a date string falls within a named range
// ---------------------------------------------------------------------------
function isInDateRange(dateStr, range) {
  if (!dateStr || !range) return true;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return true;
  const now = new Date();

  if (range === 'today') {
    return (
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    );
  }
  if (range === 'week') {
    const weekAgo = new Date(now);
    weekAgo.setDate(weekAgo.getDate() - 7);
    return d >= weekAgo;
  }
  if (range === 'month') {
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  }
  return true;
}

// ---------------------------------------------------------------------------
// Default alert rules configuration
// ---------------------------------------------------------------------------
const defaultAlertRules = [
  {
    id: 'rule_data_gap',
    metric: 'data_gap',
    metricLabel: '数据缺失',
    description: '监测数据连续缺失超过设定时间触发告警',
    enabled: true,
    flowType: 'auto',
    thresholds: { blue: 30, yellow: 60, orange: 120, red: 240 },
    unit: '分钟',
  },
  {
    id: 'rule_data_freeze',
    metric: 'data_freeze',
    metricLabel: '数据冻结',
    description: '监测数据长时间保持不变（疑似传感器故障）',
    enabled: true,
    flowType: 'manual',
    thresholds: { blue: 60, yellow: 120, orange: 240, red: 480 },
    unit: '分钟',
  },
  {
    id: 'rule_data_spike',
    metric: 'data_spike',
    metricLabel: '数据突变',
    description: '监测数据短时间内变化幅度超过阈值',
    enabled: true,
    flowType: 'manual',
    thresholds: { blue: 15, yellow: 30, orange: 50, red: 80 },
    unit: '%',
  },
  {
    id: 'rule_device_status',
    metric: 'device_status',
    metricLabel: '设备离线',
    description: '设备心跳超时判定为离线状态',
    enabled: true,
    flowType: 'auto',
    thresholds: { blue: 10, yellow: 30, orange: 60, red: 120 },
    unit: '分钟',
  },
  {
    id: 'rule_arrival_rate',
    metric: 'arrival_rate',
    metricLabel: '到报率',
    description: '站点数据到报率低于设定阈值',
    enabled: true,
    flowType: 'manual',
    thresholds: { blue: 95, yellow: 90, orange: 80, red: 70 },
    unit: '%',
    isReversed: true,
  },
];

// ---------------------------------------------------------------------------
// Component: Alert Rule Engine Tab
// ---------------------------------------------------------------------------
function AlertRuleEngineTab({ tokens, isDark }) {
  const [rules, setRules] = useState([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState(null);
  const [simModalOpen, setSimModalOpen] = useState(false);
  const [simForm, setSimForm] = useState({ ruleId: null, siteId: null, value: 0 });
  const [sites, setSites] = useState([]);
  const [simLoading, setSimLoading] = useState(false);
  const [escalationConfig, setEscalationConfig] = useState([]);

  // Fetch sites for simulation dropdown
  useEffect(() => {
    api.get('/sites').then((data) => {
      if (Array.isArray(data)) setSites(data);
    });
  }, []);

  // Fetch escalation config
  useEffect(() => {
    api.get('/alert-escalation-config').then(data => {
      if (Array.isArray(data)) setEscalationConfig(data);
    }).catch(() => {});
  }, []);

  // Fetch alert rules from backend (persisted config)
  useEffect(() => {
    setRulesLoading(true);
    api.get('/alert-rules').then(data => {
      if (Array.isArray(data)) setRules(data);
    }).catch(() => {
      // Fallback to defaults if backend not available
      setRules(defaultAlertRules);
    }).finally(() => setRulesLoading(false));
  }, []);

  // Toggle rule enabled
  const handleToggle = useCallback((ruleId, checked) => {
    api.put(`/alert-rules/${ruleId}`, { enabled: checked }).then(() => {
      setRules((prev) => prev.map((r) => r.id === ruleId ? { ...r, enabled: checked } : r));
      message.success(checked ? '规则已启用' : '规则已停用');
    }).catch(() => {
      message.error('保存失败，请重试');
    });
  }, []);

  // Edit rule thresholds
  const handleEdit = useCallback((rule) => {
    setEditingRule({ ...rule, thresholds: { ...rule.thresholds } });
    setEditModalOpen(true);
  }, []);

  const handleSaveEdit = useCallback(() => {
    if (!editingRule) return;
    api.put(`/alert-rules/${editingRule.id}`, { thresholds: editingRule.thresholds }).then(() => {
      setRules((prev) => prev.map((r) => r.id === editingRule.id ? editingRule : r));
      setEditModalOpen(false);
      setEditingRule(null);
      message.success('规则阈值已保存');
    }).catch(() => {
      message.error('保存失败，请重试');
    });
  }, [editingRule]);

  // Simulate trigger
  const handleSimulate = useCallback(() => {
    if (!simForm.ruleId || !simForm.siteId) {
      message.warning('请选择规则和站点');
      return;
    }
    setSimLoading(true);
    const rule = rules.find((r) => r.id === simForm.ruleId);
    const site = sites.find((s) => s.id === Number(simForm.siteId));
    // Determine level based on value vs thresholds
    let level = 'blue';
    const val = simForm.value;
    if (rule.isReversed) {
      if (val <= rule.thresholds.red) level = 'red';
      else if (val <= rule.thresholds.orange) level = 'orange';
      else if (val <= rule.thresholds.yellow) level = 'yellow';
    } else {
      if (val >= rule.thresholds.red) level = 'red';
      else if (val >= rule.thresholds.orange) level = 'orange';
      else if (val >= rule.thresholds.yellow) level = 'yellow';
    }
    // Post to backend to create alert
    const payload = {
      site_id: simForm.siteId,
      metric: rule.metric,
      value: simForm.value,
      level,
      message: `[模拟] ${site?.name || '未知站点'} ${rule.metricLabel} ${val}${rule.unit}，触发${alertLevelLabel[level]}`,
    };
    api.post('/alerts/simulate', payload).then((result) => {
      if (result && !result.error) {
        message.success(`模拟告警已触发：${alertLevelLabel[level]}`);
        setSimModalOpen(false);
        setSimForm({ ruleId: null, siteId: null, value: 0 });
      } else {
        message.info('模拟触发成功（演示模式）');
        setSimModalOpen(false);
      }
    }).catch(() => {
      message.info('模拟触发成功（演示模式）');
      setSimModalOpen(false);
    }).finally(() => setSimLoading(false));
  }, [simForm, rules, sites]);

  // Rule table columns
  const ruleColumns = useMemo(() => [
    {
      title: '规则名称',
      key: 'name',
      width: 180,
      render: (_, r) => (
        <div>
          <Text strong style={{ display: 'block', marginBottom: 2 }}>{r.metricLabel}</Text>
          <Text style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{r.description}</Text>
        </div>
      ),
    },
    {
      title: '触发模式',
      dataIndex: 'flowType',
      key: 'flowType',
      width: 100,
      align: 'center',
      render: (ft) => (
        <Tag color={ft === 'auto' ? 'blue' : 'orange'} style={{ borderRadius: 4 }}>
          {ft === 'auto' ? '自动处置' : '人工审核'}
        </Tag>
      ),
    },
    {
      title: '蓝色关注',
      key: 'blue',
      width: 110,
      align: 'center',
      render: (_, r) => (
        <span style={{ color: alertLevelColor.blue, fontWeight: 600 }}>
          {r.thresholds.blue}{r.unit}
        </span>
      ),
    },
    {
      title: '黄色警示',
      key: 'yellow',
      width: 110,
      align: 'center',
      render: (_, r) => (
        <span style={{ color: alertLevelColor.yellow, fontWeight: 600 }}>
          {r.thresholds.yellow}{r.unit}
        </span>
      ),
    },
    {
      title: '橙色预警',
      key: 'orange',
      width: 110,
      align: 'center',
      render: (_, r) => (
        <span style={{ color: alertLevelColor.orange, fontWeight: 600 }}>
          {r.thresholds.orange}{r.unit}
        </span>
      ),
    },
    {
      title: '红色警报',
      key: 'red',
      width: 110,
      align: 'center',
      render: (_, r) => (
        <span style={{ color: alertLevelColor.red, fontWeight: 600 }}>
          {r.thresholds.red}{r.unit}
        </span>
      ),
    },
    {
      title: '启用',
      key: 'enabled',
      width: 70,
      align: 'center',
      render: (_, r) => (
        <Switch
          size="small"
          checked={r.enabled}
          onChange={(checked) => handleToggle(r.id, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      align: 'center',
      render: (_, r) => (
        <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)}>
          编辑
        </Button>
      ),
    },
  ], [tokens, handleToggle, handleEdit]);

  return (
    <div>
      {/* Rule Engine Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>
            配置告警触发规则，设定各级别阈值，支持模拟触发验证
          </Text>
        </div>
        <Space>
          <Button
            type="primary"
            icon={<ExperimentOutlined />}
            onClick={() => setSimModalOpen(true)}
            style={{ borderRadius: 8 }}
          >
            模拟触发
          </Button>
        </Space>
      </div>

      {/* Rule Table */}
      <Table
        rowKey="id"
        columns={ruleColumns}
        dataSource={rules}
        loading={rulesLoading}
        pagination={false}
        scroll={{ y: 'calc(100vh - 380px)' }}
        size="small"
        style={{ borderRadius: 12, overflow: 'hidden' }}
      />

      {/* Edit Threshold Modal */}
      <Modal
        title={`编辑规则 - ${editingRule?.metricLabel || ''}`}
        open={editModalOpen}
        onOk={handleSaveEdit}
        onCancel={() => { setEditModalOpen(false); setEditingRule(null); }}
        okText="保存"
        cancelText="取消"
        width={520}
      >
        {editingRule && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
            <div style={{ padding: '12px 16px', borderRadius: 8, background: isDark ? 'rgba(0,200,180,0.06)' : 'rgba(0,0,0,0.02)' }}>
              <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>{editingRule.description}</Text>
            </div>
            <Row gutter={16}>
              <Col span={12}>
                <div style={{ marginBottom: 8 }}>
                  <Tag color={alertLevelColor.blue} style={{ borderRadius: 4 }}>蓝色关注</Tag>
                </div>
                <InputNumber
                  value={editingRule.thresholds.blue}
                  onChange={(val) => setEditingRule({ ...editingRule, thresholds: { ...editingRule.thresholds, blue: val } })}
                  style={{ width: '100%' }}
                  min={0}
                  addonAfter={editingRule.unit}
                />
              </Col>
              <Col span={12}>
                <div style={{ marginBottom: 8 }}>
                  <Tag color={alertLevelColor.yellow} style={{ borderRadius: 4 }}>黄色警示</Tag>
                </div>
                <InputNumber
                  value={editingRule.thresholds.yellow}
                  onChange={(val) => setEditingRule({ ...editingRule, thresholds: { ...editingRule.thresholds, yellow: val } })}
                  style={{ width: '100%' }}
                  min={0}
                  addonAfter={editingRule.unit}
                />
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={12}>
                <div style={{ marginBottom: 8 }}>
                  <Tag color={alertLevelColor.orange} style={{ borderRadius: 4 }}>橙色预警</Tag>
                </div>
                <InputNumber
                  value={editingRule.thresholds.orange}
                  onChange={(val) => setEditingRule({ ...editingRule, thresholds: { ...editingRule.thresholds, orange: val } })}
                  style={{ width: '100%' }}
                  min={0}
                  addonAfter={editingRule.unit}
                />
              </Col>
              <Col span={12}>
                <div style={{ marginBottom: 8 }}>
                  <Tag color={alertLevelColor.red} style={{ borderRadius: 4 }}>红色警报</Tag>
                </div>
                <InputNumber
                  value={editingRule.thresholds.red}
                  onChange={(val) => setEditingRule({ ...editingRule, thresholds: { ...editingRule.thresholds, red: val } })}
                  style={{ width: '100%' }}
                  min={0}
                  addonAfter={editingRule.unit}
                />
              </Col>
            </Row>
            {editingRule.isReversed && (
              <div style={{ padding: '8px 12px', borderRadius: 6, background: isDark ? 'rgba(250,173,20,0.08)' : 'rgba(250,173,20,0.06)', border: '1px solid rgba(250,173,20,0.2)' }}>
                <Text style={{ fontSize: 12, color: tokens.colorWarning }}>
                  注意：该指标为反向阈值，数值越低告警级别越高
                </Text>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Simulate Trigger Modal */}
      <Modal
        title="模拟触发告警"
        open={simModalOpen}
        onOk={handleSimulate}
        onCancel={() => setSimModalOpen(false)}
        okText="触发"
        cancelText="取消"
        confirmLoading={simLoading}
        width={480}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
          <div>
            <div style={{ marginBottom: 6 }}>
              <Text strong style={{ fontSize: 13 }}>选择规则</Text>
            </div>
            <Select
              placeholder="请选择告警规则"
              style={{ width: '100%' }}
              value={simForm.ruleId}
              onChange={(val) => setSimForm({ ...simForm, ruleId: val })}
              options={rules.filter((r) => r.enabled).map((r) => ({
                value: r.id,
                label: `${r.metricLabel} (${r.flowType === 'auto' ? '自动' : '人工'})`,
              }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 6 }}>
              <Text strong style={{ fontSize: 13 }}>选择站点</Text>
            </div>
            <Select
              placeholder="请选择站点"
              style={{ width: '100%' }}
              showSearch
              optionFilterProp="label"
              value={simForm.siteId}
              onChange={(val) => setSimForm({ ...simForm, siteId: val })}
              options={sites.map((s) => ({
                value: s.id,
                label: `${s.name} (${s.code || '-'})`,
              }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 6 }}>
              <Text strong style={{ fontSize: 13 }}>触发值</Text>
            </div>
            <InputNumber
              placeholder="请输入模拟触发值"
              style={{ width: '100%' }}
              value={simForm.value}
              onChange={(val) => setSimForm({ ...simForm, value: val || 0 })}
              min={0}
              addonAfter={simForm.ruleId ? (rules.find((r) => r.id === simForm.ruleId)?.unit || '') : ''}
            />
          </div>
          {simForm.ruleId && (
            <div style={{ padding: '10px 14px', borderRadius: 8, background: isDark ? 'rgba(0,200,180,0.06)' : 'rgba(0,0,0,0.02)' }}>
              <Text style={{ fontSize: 12, color: tokens.colorTextSecondary }}>
                {(() => {
                  const rule = rules.find((r) => r.id === simForm.ruleId);
                  if (!rule) return '';
                  const val = simForm.value;
                  let level = '未触发';
                  let color = tokens.colorTextTertiary;
                  if (rule.isReversed) {
                    if (val <= rule.thresholds.red) { level = '红色警报'; color = alertLevelColor.red; }
                    else if (val <= rule.thresholds.orange) { level = '橙色预警'; color = alertLevelColor.orange; }
                    else if (val <= rule.thresholds.yellow) { level = '黄色警示'; color = alertLevelColor.yellow; }
                    else if (val <= rule.thresholds.blue) { level = '蓝色关注'; color = alertLevelColor.blue; }
                  } else {
                    if (val >= rule.thresholds.red) { level = '红色警报'; color = alertLevelColor.red; }
                    else if (val >= rule.thresholds.orange) { level = '橙色预警'; color = alertLevelColor.orange; }
                    else if (val >= rule.thresholds.yellow) { level = '黄色警示'; color = alertLevelColor.yellow; }
                    else if (val >= rule.thresholds.blue) { level = '蓝色关注'; color = alertLevelColor.blue; }
                  }
                  return `当前触发级别：`;
                })()}
              </Text>
              {(() => {
                const rule = rules.find((r) => r.id === simForm.ruleId);
                if (!rule) return null;
                const val = simForm.value;
                let level = null;
                if (rule.isReversed) {
                  if (val <= rule.thresholds.red) level = { label: '红色警报', color: alertLevelColor.red };
                  else if (val <= rule.thresholds.orange) level = { label: '橙色预警', color: alertLevelColor.orange };
                  else if (val <= rule.thresholds.yellow) level = { label: '黄色警示', color: alertLevelColor.yellow };
                  else if (val <= rule.thresholds.blue) level = { label: '蓝色关注', color: alertLevelColor.blue };
                } else {
                  if (val >= rule.thresholds.red) level = { label: '红色警报', color: alertLevelColor.red };
                  else if (val >= rule.thresholds.orange) level = { label: '橙色预警', color: alertLevelColor.orange };
                  else if (val >= rule.thresholds.yellow) level = { label: '黄色警示', color: alertLevelColor.yellow };
                  else if (val >= rule.thresholds.blue) level = { label: '蓝色关注', color: alertLevelColor.blue };
                }
                return level ? <Tag color={level.color} style={{ marginLeft: 8, fontWeight: 600 }}>{level.label}</Tag> : null;
              })()}
            </div>
          )}
        </div>
      </Modal>

      {/* 分级告警升级配置 */}
      <div style={{ marginTop: 24 }}>
        <Title level={5} style={{ color: tokens.colorText }}>告警升级配置</Title>
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          系统每 5 分钟扫描未处理告警，超过 SLA 时长自动升级至下一级并自动生成工单
        </Text>
        <Table
          dataSource={escalationConfig}
          rowKey="level"
          pagination={false}
          size="small"
          columns={[
            { title: '级别', dataIndex: 'level', width: 80, render: v => {
              const colorMap = {blue:'#1890ff',yellow:'#faad14',orange:'#fa8c16',red:'#f5222d'};
              return <Tag color={colorMap[v] || 'default'}>{v === 'blue' ? '蓝' : v === 'yellow' ? '黄' : v === 'orange' ? '橙' : '红'}</Tag>;
            }},
            { title: 'SLA（分钟）', dataIndex: 'sla_minutes', width: 100 },
            { title: '自动工单', dataIndex: 'auto_workorder', width: 80, render: v => v ? '✅' : '❌' },
            { title: '通知方式', dataIndex: 'notify_type', width: 100 },
            { title: '升级目标', dataIndex: 'escalate_to_level', width: 80, render: v => v && v !== 'None' ? v : '—' },
            { title: '说明', dataIndex: 'description', ellipsis: true },
          ]}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Primary (一级) tab config — 3 个一级 tab，各自挂载二级子 tab
// ---------------------------------------------------------------------------
const PRIMARY_TABS = [
  {
    key: 'events',
    icon: <AlertOutlined />,
    label: '告警事件',
    defaultLeaf: 'alerts',
    children: [
      { key: 'alerts', label: '监测告警' },
      { key: 'reagent', label: '物资预警' },
    ],
  },
  {
    key: 'rules',
    icon: <SettingOutlined />,
    label: '规则配置',
    defaultLeaf: 'rules',
    children: [
      { key: 'rules', label: '规则引擎' },
      { key: 'thresholds', label: '阈值规则' },
    ],
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function AlertsPage() {
  const { tokens, isDark } = useTheme();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState('alerts');
  const [primaryTab, setPrimaryTab] = useState('events');

  // 切换一级 tab：同步把 activeTab 设为该一级下的默认叶子 key
  const handlePrimaryTab = useCallback((key) => {
    setPrimaryTab(key);
    const pt = PRIMARY_TABS.find((p) => p.key === key);
    if (pt) setActiveTab(pt.defaultLeaf);
  }, []);
  const currentPrimary = PRIMARY_TABS.find((p) => p.key === primaryTab);


  // ---- State ---------------------------------------------------------------
  const [allAlerts, setAllAlerts] = useState([]);       // full list from backend
  const [counts, setCounts] = useState({ total: 0, pending: 0, acknowledged: 0, resolved: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sites, setSites] = useState([]);
  const [alertsWrapRef, alertsH] = useTableAutoHeight({ headerOffset: 54 });
  const [reagentWrapRef, reagentH] = useTableAutoHeight({ headerOffset: 48 });

  // 试剂预警（跨站剩余可用天数/低余量）
  const [reagentList, setReagentList] = useState([]);
  const [reagentLoading, setReagentLoading] = useState(false);
  const loadReagentOverview = useCallback(async () => {
    setReagentLoading(true);
    try {
      const d = await api.get('/reagent-overview');
      setReagentList(Array.isArray(d?.items) ? d.items : []);
    } catch (e) {
      setReagentList([]);
    } finally {
      setReagentLoading(false);
    }
  }, []);
  useEffect(() => { if (activeTab === 'reagent') loadReagentOverview(); }, [activeTab, loadReagentOverview]);

  const [searchText, setSearchText] = useState(searchParams.get('search') || '');
  const [statusFilter, setStatusFilter] = useState(null);   // null = all
  const [dateRange, setDateRange] = useState('today');
  const [levelFilter, setLevelFilter] = useState(null);     // null = all levels

  const resetFilters = useCallback(() => {
    setSearchText('');
    setStatusFilter(null);
    setLevelFilter(null);
    setDateRange('today');
  }, []);

  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [actionLoading, setActionLoading] = useState({});    // { [alertId]: true }
  const [batchLoading, setBatchLoading] = useState(false);

  // Resolve modal state
  const [resolveModalOpen, setResolveModalOpen] = useState(false);
  const [resolveTarget, setResolveTarget] = useState(null);
  const [resolveForm] = Form.useForm();

  // Urge modal state
  const [urgeModalOpen, setUrgeModalOpen] = useState(false);
  const [urgeTarget, setUrgeTarget] = useState(null);
  const [urgeForm] = Form.useForm();

  // Manual report state
  const [manualAlertOpen, setManualAlertOpen] = useState(false);
  const [manualForm] = Form.useForm();

  // Convert confirm modal state (replaces Modal.confirm for React 19 compat)
  const [convertModalOpen, setConvertModalOpen] = useState(false);
  const [convertTarget, setConvertTarget] = useState(null);
  const [convertLoading, setConvertLoading] = useState(false);

  // Batch confirm modal state (replaces Modal.confirm for React 19 compat)
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchAction, setBatchAction] = useState(null);
  const [batchLabel, setBatchLabel] = useState('');

  // ---- Fetching (backend returns a plain array) ----------------------------
  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    setError(null);

    const data = await api.get('/alerts');

    if (!data) {
      setError('加载告警数据失败，请检查网络后重试');
      setAllAlerts([]);
    } else {
      const list = Array.isArray(data) ? data : [];
      setAllAlerts(list);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    fetchAlerts();
    api.get('/sites').then(d => setSites(Array.isArray(d) ? d : [])).catch(() => {});
  }, [fetchAlerts]);

  // Sync search from URL params when navigating from cockpit
  useEffect(() => {
    const urlSearch = searchParams.get('search') || '';
    setSearchText(urlSearch);
  }, [searchParams]);

  // ---- Compute counts from the full list -----------------------------------
  useEffect(() => {
    const pending = allAlerts.filter((a) => a.status === 'pending').length;
    const acknowledged = allAlerts.filter((a) => a.status === 'acknowledged').length;
    const resolved = allAlerts.filter((a) => a.status === 'resolved').length;
    setCounts({
      total: allAlerts.length,
      pending,
      acknowledged,
      resolved,
    });
  }, [allAlerts]);

  // ---- Client-side filtering -----------------------------------------------
  const filteredAlerts = useMemo(() => {
    let list = allAlerts;

    // Status filter
    if (statusFilter) {
      list = list.filter((a) => a.status === statusFilter);
    }

    // Level filter
    if (levelFilter) {
      list = list.filter((a) => a.level === levelFilter);
    }

    // Date range filter
    if (dateRange) {
      list = list.filter((a) => isInDateRange(a.created_at, dateRange));
    }

    // Search text filter (site_name, site_code, message)
    if (searchText.trim()) {
      const q = searchText.trim().toLowerCase();
      list = list.filter(
        (a) =>
          (a.site_name && a.site_name.toLowerCase().includes(q)) ||
          (a.site_code && a.site_code.toLowerCase().includes(q)) ||
          (a.message && a.message.toLowerCase().includes(q)),
      );
    }

    return list;
  }, [allAlerts, statusFilter, levelFilter, dateRange, searchText]);

  // 管理视角先看事件簇，明细表仍保留用于逐条追溯和处置。
  const incidentGroups = useMemo(() => {
    const groups = new Map();
    filteredAlerts.forEach((alert) => {
      if (alert.status === 'resolved') return;
      const timestamp = new Date(String(alert.created_at || '').replace(' ', 'T')).getTime();
      const bucket = Number.isFinite(timestamp) ? Math.floor(timestamp / 1800000) : alert.id;
      const metric = alert.metric || alert.event_type || 'unknown';
      const key = `${metric}:${bucket}`;
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          metric,
          alerts: 0,
          sites: new Set(),
          orders: new Set(),
          latestAt: alert.created_at,
          level: alert.level,
        });
      }
      const group = groups.get(key);
      group.alerts += 1;
      if (alert.site_id || alert.site_name) group.sites.add(alert.site_id || alert.site_name);
      if (alert.related_order_no) group.orders.add(alert.related_order_no);
      if (alert.created_at > group.latestAt) group.latestAt = alert.created_at;
      if (['red', 'orange', 'yellow', 'blue'].indexOf(alert.level) < ['red', 'orange', 'yellow', 'blue'].indexOf(group.level)) {
        group.level = alert.level;
      }
    });
    return Array.from(groups.values())
      .map((group) => ({ ...group, siteCount: group.sites.size, orderCount: group.orders.size }))
      .sort((a, b) => b.alerts - a.alerts || String(b.latestAt).localeCompare(String(a.latestAt)));
  }, [filteredAlerts]);

  const incidentLabel = (metric) => ({
    device_status: '设备离线事件',
    data_gap: '数据中断事件',
  }[metric] || '水质异常事件');

  // ---- Single-row actions (all POST) ---------------------------------------
  const handleResolve = useCallback((record) => {
    setResolveTarget(record);
    resolveForm.resetFields();
    setResolveModalOpen(true);
  }, [resolveForm]);

  const handleResolveSubmit = useCallback(async () => {
    if (!resolveTarget) return;
    try {
      const values = await resolveForm.validateFields();
      setActionLoading((prev) => ({ ...prev, [resolveTarget.id]: true }));
      const result = await api.post(`/alerts/${resolveTarget.id}/resolve`, {
        reason: values.reason,
        remark: values.remark || '',
        conclusion: values.conclusion,
      });
      if (result && !result.error) {
        message.success(`告警「${resolveTarget.site_name || resolveTarget.id}」已办结`);
        setResolveModalOpen(false);
        setResolveTarget(null);
        fetchAlerts();
      } else {
        message.error(result?.error || '操作失败，请重试');
      }
    } catch {
      // validation error
    } finally {
      setActionLoading((prev) => ({ ...prev, [resolveTarget?.id]: false }));
    }
  }, [resolveTarget, resolveForm, fetchAlerts]);

  const handleAcknowledge = useCallback(async (record) => {
    setActionLoading((prev) => ({ ...prev, [record.id]: true }));
    const result = await api.post(`/alerts/${record.id}/acknowledge`, {});
    if (result && !result.error) {
      message.success(`告警「${record.site_name || record.id}」已确认`);
      fetchAlerts();
    } else {
      message.error(result?.error || '确认失败，请重试');
    }
    setActionLoading((prev) => ({ ...prev, [record.id]: false }));
  }, [fetchAlerts]);

  const handleUrge = useCallback((record) => {
    setUrgeTarget(record);
    urgeForm.resetFields();
    setUrgeModalOpen(true);
  }, [urgeForm]);

  const handleUrgeSubmit = useCallback(async () => {
    if (!urgeTarget) return;
    try {
      const values = await urgeForm.validateFields();
      setActionLoading((prev) => ({ ...prev, [urgeTarget.id]: true }));
      const result = await api.post(`/alerts/${urgeTarget.id}/urge`, {
        supervisor: values.supervisor,
        opinion: values.opinion || '',
        deadline: values.deadline || '',
      });
      if (result && !result.error) {
        message.success(`已对告警「${urgeTarget.site_name || urgeTarget.id}」发起督办`);
        setUrgeModalOpen(false);
        setUrgeTarget(null);
        fetchAlerts();
      } else {
        message.error(result?.error || '督办失败，请重试');
      }
    } catch {
      // validation error
    } finally {
      setActionLoading((prev) => ({ ...prev, [urgeTarget?.id]: false }));
    }
  }, [urgeTarget, urgeForm, fetchAlerts]);

  const handleConvert = useCallback((record) => {
    setConvertTarget(record);
    setConvertModalOpen(true);
  }, []);

  const handleConvertConfirm = useCallback(async () => {
    if (!convertTarget) return;
    setConvertLoading(true);
    const result = await api.post(`/alerts/${convertTarget.id}/confirm-convert`, {});
    if (result && !result.error) {
      const orderNo = result.order_no || '';
      message.success(orderNo ? `已成功转为工单 ${orderNo}` : '已成功转为工单');
      fetchAlerts();
      // Navigate to work orders page filtered by the new order
      if (orderNo) {
        setTimeout(() => navigate(`/workorders?search=${orderNo}`), 500);
      }
    } else {
      message.error(result?.error || '转工单失败，请重试');
    }
    setConvertLoading(false);
    setConvertModalOpen(false);
    setConvertTarget(null);
  }, [convertTarget, fetchAlerts, navigate]);

  // ---- Batch actions (POST via batch endpoint) -----------------------------
  const runBatch = useCallback((action, label) => {
    if (selectedRowKeys.length === 0) return;
    setBatchAction(action);
    setBatchLabel(label);
    setBatchModalOpen(true);
  }, [selectedRowKeys]);

  const handleBatchConfirm = useCallback(async () => {
    if (!batchAction || selectedRowKeys.length === 0) return;
    setBatchLoading(true);
    const result = await api.post('/alerts/batch', {
      ids: selectedRowKeys,
      action: batchAction,
    });
    if (result && !result.error) {
      message.success(`批量${batchLabel}成功，共 ${selectedRowKeys.length} 条`);
    } else {
      message.warning(result?.error || `批量${batchLabel}失败，请重试`);
    }
    setSelectedRowKeys([]);
    setBatchLoading(false);
    setBatchModalOpen(false);
    setBatchAction(null);
    fetchAlerts();
  }, [batchAction, batchLabel, selectedRowKeys, fetchAlerts]);

  const handleBatchResolve = useCallback(() => {
    runBatch('resolve', '办结');
  }, [runBatch]);

  const handleBatchUrge = useCallback(() => {
    runBatch('urge', '督办');
  }, [runBatch]);

  const handleBatchConvert = useCallback(() => {
    runBatch('confirm-convert', '转工单');
  }, [runBatch]);

  // ---- Table columns -------------------------------------------------------
  const columns = useMemo(() => [
    {
      title: '站点 & 等级',
      key: 'site_level',
      width: 220,
      render: (_, record) => {
        const severity = alertSeverityTag[record.level] || { color: '#999', label: '?', desc: '未知' };
        return (
          <div>
            <Text strong style={{ color: tokens.colorText, display: 'block', marginBottom: 4 }}>
              {record.site_name || record.site_code || '-'}
            </Text>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <Tag
                color={severity.color}
                style={{ fontSize: 11, borderRadius: 4, fontWeight: 600, margin: 0 }}
              >
                {severity.label}
              </Tag>
              <Text style={{ fontSize: 12, color: severity.color, fontWeight: 500 }}>
                {severity.desc}
              </Text>
            </div>
          </div>
        );
      },
    },
    {
      title: '告警信息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (text) => (
        <Text style={{ color: tokens.colorText }} title={text}>
          {text || '-'}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status) => (
        <Tag
          icon={statusIconMap[status]}
          color={statusColorMap[status] || '#999'}
          style={{ borderRadius: 4 }}
        >
          {alertStatusMap[status] || status || '未知'}
        </Tag>
      ),
    },
    {
      title: '告警时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      sorter: (a, b) => new Date(a.created_at) - new Date(b.created_at),
      render: (text) => (
        <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>
          {text ? new Date(text).toLocaleString('zh-CN') : '-'}
        </Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, record) => {
        const isLoading = !!actionLoading[record.id];
        const isResolved = record.status === 'resolved';
        const isConverted = record.flow_status === 'converted' || record.related_order_no;

        // If converted to work order, show linked work order info
        if (isConverted) {
          return (
            <div>
              <Tag
                icon={<LinkOutlined />}
                color="blue"
                style={{ borderRadius: 4, marginBottom: 4, cursor: 'pointer' }}
                onClick={() => navigate(`/workorders?search=${record.related_order_no || ''}`)}
              >
                {record.related_order_no || '已转工单'}
              </Tag>
              <div style={{ fontSize: 11, color: tokens.colorTextTertiary }}>
                点击查看工单详情
              </div>
            </div>
          );
        }

        // If resolved, show resolved status
        if (isResolved) {
          return (
            <Tag icon={<CheckCircleOutlined />} color="success" style={{ borderRadius: 4 }}>
              已办结
            </Tag>
          );
        }

        return (
          <Space size={4} wrap>
            <Button
              type="link"
              size="small"
              loading={isLoading}
              onClick={() => handleResolve(record)}
              style={{ color: tokens.colorSuccess || '#52c41a' }}
            >
              办结
            </Button>
            <Button
              type="link"
              size="small"
              loading={isLoading}
              onClick={() => handleUrge(record)}
              style={{ color: tokens.colorWarning || '#faad14' }}
            >
              督办
            </Button>
            <Button
              type="link"
              size="small"
              loading={isLoading}
              onClick={() => handleConvert(record)}
            >
              转工单
            </Button>
          </Space>
        );
      },
    },
  ], [tokens, actionLoading, handleResolve, handleUrge, handleConvert]);

  // ---- Row selection -------------------------------------------------------
  const rowSelection = useMemo(() => ({
    selectedRowKeys,
    onChange: (keys) => setSelectedRowKeys(keys),
  }), [selectedRowKeys]);

  // ---- Styles --------------------------------------------------------------
  const cardStyle = useMemo(() => ({
    borderRadius: 12,
    background: isDark
      ? 'linear-gradient(135deg, rgba(12,28,52,0.85), rgba(8,20,42,0.9))'
      : '#ffffff',
    border: `1px solid ${tokens.colorBorder}`,
    boxShadow: isDark
      ? '0 2px 12px rgba(0,0,0,0.3)'
      : '0 2px 8px rgba(0,0,0,0.06)',
  }), [isDark, tokens.colorBorder]);

  // ---- Stat cards config ---------------------------------------------------
  const statCards = useMemo(() => [
    {
      title: '告警总数',
      value: counts.total,
      icon: <AlertOutlined style={{ fontSize: 16, color: tokens.colorPrimary }} />,
      color: tokens.colorPrimary,
    },
    {
      title: '待处理',
      value: counts.pending,
      icon: <ExclamationCircleOutlined style={{ fontSize: 16, color: '#faad14' }} />,
      color: '#faad14',
    },
    {
      title: '处理中',
      value: counts.acknowledged,
      icon: <ClockCircleOutlined style={{ fontSize: 16, color: '#1890ff' }} />,
      color: '#1890ff',
    },
    {
      title: '已办结',
      value: counts.resolved,
      icon: <CheckCircleOutlined style={{ fontSize: 16, color: '#52c41a' }} />,
      color: '#52c41a',
    },
  ], [counts, tokens.colorPrimary]);

  // ---- Render --------------------------------------------------------------
  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 24 }}>
      {/* Page Header */}
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, flexShrink: 0 }}>
        <Title level={4} style={{ margin: 0, color: tokens.colorText }}>告警管理中心</Title>

        {/* 一级 Tab Bar */}
        <div style={{ display: 'flex', gap: 4, background: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)', borderRadius: 8, padding: 3 }}>
          {PRIMARY_TABS.map((tab) => (
            <Button
              key={tab.key}
              type={primaryTab === tab.key ? 'primary' : 'text'}
              size="small"
              icon={tab.icon}
              onClick={() => handlePrimaryTab(tab.key)}
              style={{
                borderRadius: 6,
                fontWeight: primaryTab === tab.key ? 600 : 400,
                background: primaryTab === tab.key ? tokens.colorPrimary : 'transparent',
                color: primaryTab === tab.key ? '#fff' : tokens.colorTextSecondary,
              }}
            >
              {tab.label}
            </Button>
          ))}
        </div>
      </div>

      {/* 二级子 Tab Bar（数据审核无子 tab） */}
      {currentPrimary?.children && (
        <div style={{ marginBottom: 16, display: 'flex', gap: 4, alignSelf: 'flex-start', background: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)', borderRadius: 8, padding: 3, flexShrink: 0 }}>
          {currentPrimary.children.map((leaf) => (
            <Button
              key={leaf.key}
              type={activeTab === leaf.key ? 'primary' : 'text'}
              size="small"
              onClick={() => setActiveTab(leaf.key)}
              style={{
                borderRadius: 6,
                fontWeight: activeTab === leaf.key ? 600 : 400,
                background: activeTab === leaf.key ? tokens.colorPrimary : 'transparent',
                color: activeTab === leaf.key ? '#fff' : tokens.colorTextSecondary,
              }}
            >
              {leaf.label}
            </Button>
          ))}
        </div>
      )}

      {/* Tab Content */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {activeTab === 'alerts' ? (
          <>
            {/* Keep alert status visible without pushing the working list below the fold. */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 1,
              marginBottom: 12, border: `1px solid ${tokens.colorBorder}`, borderRadius: 6,
              background: tokens.colorBorder, overflow: 'hidden', flexShrink: 0,
            }}>
              {statCards.map((item) => (
                <div key={item.title} style={{ padding: '8px 12px', background: tokens.colorBgContainer, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: tokens.colorTextSecondary, fontSize: 12, whiteSpace: 'nowrap' }}>
                    {item.icon}{item.title}
                  </div>
                  <div style={{ color: item.color, fontWeight: 650, fontSize: 20, lineHeight: 1.25, marginTop: 2 }}>{item.value}</div>
                </div>
              ))}
            </div>

            {/* Filter Bar */}
            <div style={{
              padding: '8px 0', borderTop: `1px solid ${tokens.colorBorder}`,
              borderBottom: `1px solid ${tokens.colorBorder}`, flexShrink: 0,
            }}>
              <Row gutter={[12, 12]} align="middle">
                <Col flex="auto">
                  <Space wrap size={12}>
                    <Input
                      placeholder="搜索站点名称或告警内容..."
                      prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
                      allowClear
                      value={searchText}
                      onChange={(e) => setSearchText(e.target.value)}
                      onPressEnter={fetchAlerts}
                      style={{ width: 280, borderRadius: 8 }}
                    />
                    <Select
                      placeholder="告警状态"
                      allowClear
                      value={statusFilter}
                      onChange={(val) => setStatusFilter(val ?? null)}
                      style={{ width: 140 }}
                      options={Object.entries(alertStatusMap).map(([value, label]) => ({
                        value,
                        label,
                      }))}
                    />
                    <Select
                      placeholder="告警等级"
                      allowClear
                      value={levelFilter}
                      onChange={(val) => setLevelFilter(val ?? null)}
                      style={{ width: 140 }}
                      options={[
                        { value: 'red', label: 'I级 紧急告警' },
                        { value: 'orange', label: 'II级 较重告警' },
                        { value: 'yellow', label: 'III级 一般告警' },
                        { value: 'blue', label: 'IV级 一般关注' },
                      ]}
                    />
                    <Select
                      value={dateRange}
                      onChange={setDateRange}
                      style={{ width: 120 }}
                      options={dateRangeOptions}
                    />
                    {(statusFilter || levelFilter || dateRange || searchText) && (
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        已筛选 {filteredAlerts.length} 条结果
                      </Text>
                    )}
                  </Space>
                </Col>
                <Col>
                  <Space>
                    <Button icon={<ReloadOutlined />} onClick={resetFilters}>
                      重置
                    </Button>
                    <Button icon={<PlusOutlined />} onClick={() => setManualAlertOpen(true)} type="primary" ghost>
                      人工上报
                    </Button>
                    <Button
                      onClick={fetchAlerts}
                      loading={loading}
                      style={{ borderRadius: 8 }}
                    >
                      刷新
                    </Button>
                  </Space>
                </Col>
              </Row>

              {/* Batch Operations Bar */}
              {selectedRowKeys.length > 0 && (
                <div
                  style={{
                    marginTop: 8,
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: isDark
                      ? 'rgba(24, 144, 255, 0.08)'
                      : 'rgba(24, 144, 255, 0.06)',
                    border: `1px solid ${isDark ? 'rgba(24,144,255,0.25)' : 'rgba(24,144,255,0.2)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                  }}
                >
                  <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>
                    已选择 <Badge count={selectedRowKeys.length} style={{ backgroundColor: tokens.colorPrimary }} /> 条告警
                  </Text>
                  <Space size={8}>
                    <Button
                      size="small"
                      type="primary"
                      loading={batchLoading}
                      onClick={handleBatchResolve}
                      icon={<CheckCircleOutlined />}
                    >
                      批量办结
                    </Button>
                    <Button
                      size="small"
                      loading={batchLoading}
                      onClick={handleBatchUrge}
                      icon={<ClockCircleOutlined />}
                    >
                      批量督办
                    </Button>
                    <Button
                      size="small"
                      loading={batchLoading}
                      onClick={handleBatchConvert}
                      icon={<AlertOutlined />}
                    >
                      批量转工单
                    </Button>
                    <Button
                      size="small"
                      type="text"
                      onClick={() => setSelectedRowKeys([])}
                    >
                      取消选择
                    </Button>
                  </Space>
                </div>
              )}
            </div>

            {incidentGroups.length > 0 && (
              <Card
                title="事件聚合"
                extra={<Text type="secondary" style={{ fontSize: 12 }}>同类型告警按 30 分钟窗口归并</Text>}
                style={{ ...cardStyle, marginTop: 8 }}
                bodyStyle={{ padding: '6px 12px' }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {incidentGroups.slice(0, 4).map((group) => (
                    <div key={group.key} style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 32 }}>
                      <Tag color={alertLevelColor[group.level] || 'orange'}>{incidentLabel(group.metric)}</Tag>
                      {group.siteCount >= 5 && <Tag color="red">跨站异常</Tag>}
                      <Text strong style={{ flex: 1 }}>
                        {group.siteCount} 个站点 · {group.alerts} 条告警
                      </Text>
                      <Text type="secondary">关联工单 {group.orderCount}</Text>
                      <Text type="secondary" style={{ minWidth: 136, textAlign: 'right' }}>{group.latestAt}</Text>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Alerts Table */}
            <Card
              style={{ ...cardStyle, marginTop: 8, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}
              bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            >
              {/* Error State */}
              {error && (
                <div
                  style={{
                    padding: '32px 24px',
                    textAlign: 'center',
                  }}
                >
                  <ExclamationCircleOutlined style={{ fontSize: 40, color: '#ff4d4f', marginBottom: 12 }} />
                  <div>
                    <Text style={{ color: tokens.colorError, fontSize: 14 }}>{error}</Text>
                  </div>
                  <Button
                    type="primary"
                    style={{ marginTop: 16, borderRadius: 8 }}
                    onClick={fetchAlerts}
                  >
                    重新加载
                  </Button>
                </div>
              )}

              {/* Table (also handles loading + empty states natively) */}
              {!error && (
                <div ref={alertsWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Table
                  rowKey="id"
                  columns={columns}
                  dataSource={filteredAlerts}
                  loading={loading}
                  rowSelection={rowSelection}
                  pagination={false}
                  scroll={alertsH ? { y: alertsH } : undefined}
                  locale={{
                    emptyText: (
                      <div style={{ padding: '40px 0' }}>
                        <CheckCircleOutlined style={{ fontSize: 40, color: '#52c41a', marginBottom: 12 }} />
                        <div>
                          <Text style={{ color: tokens.colorTextTertiary }}>
                            当前筛选条件下暂无告警记录
                          </Text>
                        </div>
                      </div>
                    ),
                  }}
                  size="middle"
                  style={{ borderRadius: 12, overflow: 'hidden' }}
                />
                </div>
              )}
            </Card>
          </>
        ) : activeTab === 'rules' ? (
          <AlertRuleEngineTab tokens={tokens} isDark={isDark} />
        ) : activeTab === 'thresholds' ? (
          <ThresholdRulesTab tokens={tokens} isDark={isDark} />
        ) : activeTab === 'reagent' ? (
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <Card
              style={{ ...cardStyle, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}
              bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
            >
              <div ref={reagentWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Table
                  dataSource={reagentList}
                  rowKey={(r) => `${r.site_id}-${r.reagent_id}`}
                  loading={reagentLoading}
                  pagination={false}
                  size="middle"
                  scroll={reagentH ? { y: reagentH } : undefined}
                  style={{ borderRadius: 12, overflow: 'hidden' }}
                  locale={{ emptyText: <Empty description="暂无临期/低余量试剂" /> }}
                  columns={[
                    { title: '站点', dataIndex: 'site_name', width: 160 },
                    { title: '试剂', dataIndex: 'reagent_name', width: 160 },
                    {
                      title: '状态', dataIndex: 'status', width: 100,
                      render: (s) => <Tag color={reagentStatusColor[s] || 'default'}>{s}</Tag>,
                    },
                    {
                      title: '剩余可用天数', dataIndex: 'remaining_days', width: 140,
                      render: (v) => v == null ? '—'
                        : <Text strong style={{ color: v <= 0 ? '#ff4d4f' : v <= 7 ? '#faad14' : '#52c41a' }}>{v} 天</Text>,
                    },
                    {
                      title: '余量', dataIndex: 'current_qty', width: 100,
                      render: (v) => v == null ? '—' : Number(v).toFixed(2),
                    },
                    {
                      title: '操作', key: 'op', width: 120,
                      render: (_, r) => (
                        <Button size="small" type="link" onClick={() => navigate(`/sites?archive=${r.site_id}`)}>去站点详情</Button>
                      ),
                    },
                  ]}
                />
              </div>
            </Card>
          </div>
        ) : null}
      </div>

      {/* Resolve Modal */}
      <Modal
        title={
          <Space>
            <CheckCircleOutlined style={{ color: tokens.colorSuccess }} />
            <span>告警办结</span>
          </Space>
        }
        open={resolveModalOpen}
        onOk={handleResolveSubmit}
        onCancel={() => { setResolveModalOpen(false); setResolveTarget(null); }}
        okText="确认办结"
        cancelText="取消"
        confirmLoading={resolveTarget ? !!actionLoading[resolveTarget.id] : false}
        width={520}
      >
        {resolveTarget && (
          <div style={{ marginTop: 16 }}>
            <div style={{ padding: '10px 14px', borderRadius: 8, background: isDark ? 'rgba(0,200,180,0.06)' : 'rgba(0,0,0,0.02)', marginBottom: 16 }}>
              <Text style={{ fontSize: 13, color: tokens.colorTextSecondary }}>
                站点：<Text strong>{resolveTarget.site_name || '-'}</Text>
                <br />
                告警：{resolveTarget.message || '-'}
              </Text>
            </div>
            <Form form={resolveForm} layout="vertical">
              <Form.Item
                name="reason"
                label="办结原因"
                rules={[{ required: true, message: '请选择办结原因' }]}
              >
                <Radio.Group style={{ width: '100%' }}>
                  {resolveReasonOptions.map((opt) => (
                    <Radio.Button key={opt.value} value={opt.value} style={{ marginBottom: 8 }}>
                      {opt.label}
                    </Radio.Button>
                  ))}
                </Radio.Group>
              </Form.Item>
              <Form.Item name="remark" label="备注说明">
                <Input.TextArea rows={3} placeholder="可选：补充说明..." />
              </Form.Item>
              <Form.Item name="conclusion" label="现场结论">
                <Select placeholder="选择处置结论" options={CONCLUSION_OPTIONS} allowClear />
              </Form.Item>
            </Form>
          </div>
        )}
      </Modal>

      {/* Urge Modal */}
      <Modal
        title={
          <Space>
            <ClockCircleOutlined style={{ color: tokens.colorWarning }} />
            <span>发起督办</span>
          </Space>
        }
        open={urgeModalOpen}
        onOk={handleUrgeSubmit}
        onCancel={() => { setUrgeModalOpen(false); setUrgeTarget(null); }}
        okText="确认督办"
        cancelText="取消"
        confirmLoading={urgeTarget ? !!actionLoading[urgeTarget.id] : false}
        width={480}
      >
        {urgeTarget && (
          <div style={{ marginTop: 16 }}>
            <div style={{ padding: '10px 14px', borderRadius: 8, background: isDark ? 'rgba(250,173,20,0.06)' : 'rgba(250,173,20,0.04)', marginBottom: 16 }}>
              <Text style={{ fontSize: 13, color: tokens.colorTextSecondary }}>
                站点：<Text strong>{urgeTarget.site_name || '-'}</Text>
                <br />
                告警：{urgeTarget.message || '-'}
              </Text>
            </div>
            <Form form={urgeForm} layout="vertical">
              <Form.Item
                name="supervisor"
                label="督办人"
                rules={[{ required: true, message: '请输入督办人姓名' }]}
              >
                <Input prefix={<UserOutlined />} placeholder="请输入督办人姓名" />
              </Form.Item>
              <Form.Item
                name="opinion"
                label="督办意见"
                rules={[{ required: true, message: '请输入督办意见' }]}
              >
                <Input.TextArea rows={3} placeholder="请输入督办意见和要求..." />
              </Form.Item>
              <Form.Item name="deadline" label="要求完成期限">
                <Input placeholder="例如：24小时内、本周五前" />
              </Form.Item>
            </Form>
          </div>
        )}
      </Modal>

      {/* Convert Confirm Modal */}
      <Modal
        title={
          <Space>
            <ExclamationCircleOutlined style={{ color: tokens.colorPrimary }} />
            <span>转为工单</span>
          </Space>
        }
        open={convertModalOpen}
        onOk={handleConvertConfirm}
        onCancel={() => { setConvertModalOpen(false); setConvertTarget(null); }}
        okText="确认"
        cancelText="取消"
        confirmLoading={convertLoading}
        width={420}
      >
        {convertTarget && (
          <div style={{ padding: '12px 0' }}>
            <Text>确认将告警「<Text strong>{convertTarget.site_name || convertTarget.id}</Text>」转为工单？</Text>
          </div>
        )}
      </Modal>

      {/* Batch Confirm Modal */}
      <Modal
        title={
          <Space>
            <ExclamationCircleOutlined style={{ color: tokens.colorWarning }} />
            <span>批量{batchLabel}</span>
          </Space>
        }
        open={batchModalOpen}
        onOk={handleBatchConfirm}
        onCancel={() => { setBatchModalOpen(false); setBatchAction(null); }}
        okText="确认"
        cancelText="取消"
        confirmLoading={batchLoading}
        width={420}
      >
        <div style={{ padding: '12px 0' }}>
          <Text>确认对选中的 <Text strong>{selectedRowKeys.length}</Text> 条告警执行「{batchLabel}」操作？</Text>
        </div>
      </Modal>

      {/* 人工上报 Modal */}
      <Modal open={manualAlertOpen} title="人工上报" onCancel={() => setManualAlertOpen(false)} okText="提交" cancelText="取消" onOk={async () => {
        try {
          const v = await manualForm.validateFields();
          v.reporter_id = 1;
          await api.post('/manual-reports', v);
          message.success('已上报，告警+工单已生成');
          setManualAlertOpen(false);
          manualForm.resetFields();
          fetchAlerts();
        } catch (e) { message.error('提交失败：' + e.message); }
      }} okText="提交" width={520} destroyOnClose>
        <Form form={manualForm} layout="vertical" initialValues={{ report_type: 'sensory' }}>
          <Form.Item name="report_type" label="类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'sensory', label: '感官异常' }, { value: 'equipment', label: '设备异常' },
              { value: 'environment', label: '环境异常' }, { value: 'violation', label: '违规操作' },
              { value: 'pollution', label: '污染事件' },
            ]} />
          </Form.Item>
          <Form.Item name="site_id" label="关联站点">
            <Select showSearch optionFilterProp="label" allowClear placeholder="可选"
              options={sites.map(s => ({ value: s.id, label: s.name }))} />
          </Form.Item>
          <Form.Item name="description" label="现场描述" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="请详细描述异常情况" />
          </Form.Item>
          <Form.Item name="photo_urls" label="照片链接（每行一个）">
            <Input.TextArea rows={2} placeholder="https://..." />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
