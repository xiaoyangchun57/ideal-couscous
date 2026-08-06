import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Table, Card, Input, Select, Button, Space, Tag, Badge, Dropdown,
  Row, Col, Typography, App as AntApp, Modal, Switch,
  InputNumber, Tooltip, Form, Radio, Empty, Tabs,
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
  EditOutlined,
  UserOutlined,
  LinkOutlined,
  MoreOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import {
  alertLevelColor, alertLevelLabel, alertStatusMap, alertStatusBadge,
  CONCLUSION_OPTIONS,
} from '../../services/constants';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';
import { useTableAutoHeight } from '../../hooks/useTableAutoHeight';
import { statusColors } from '../../theme/tokens';
import {
  pageRootStyle, cardStyleBase, tableCardStyle, tableCardBody,
  filterInputWidth, filterSelectWidth, filterSmallSelectWidth,
} from '../../services/pageStyles';
import ThresholdRulesTab from './components/ThresholdRulesTab';
import { StatusStrip, TableLongText, ToolbarMeta, WorkspaceEmpty, WorkspaceToolbar } from '../../components/WorkspacePage';

const reagentStatusColor = { 正常: 'green', 临期: 'orange', 低余量: 'red', 已过期: 'volcano', 未设置: 'default' };

const { Text, Title } = Typography;

// ---------------------------------------------------------------------------
// Status icon mapping（状态色统一走 constants.alertStatusBadge，禁页内硬编码）
// ---------------------------------------------------------------------------
const statusIconMap = {
  pending: <ExclamationCircleOutlined />,
  acknowledged: <ClockCircleOutlined />,
  resolved: <CheckCircleOutlined />,
};

const alertSeverityTag = {
  blue: { color: alertLevelColor.blue, label: 'IV级', desc: '一般关注' },
  yellow: { color: alertLevelColor.yellow, label: 'III级', desc: '一般告警' },
  orange: { color: alertLevelColor.orange, label: 'II级', desc: '较重告警' },
  red: { color: alertLevelColor.red, label: 'I级', desc: '紧急告警' },
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
// Component: Alert Rule Engine Tab
// ---------------------------------------------------------------------------
function AlertRuleEngineTab({ tokens, isDark, canManage }) {
  const { message, modal } = AntApp.useApp();
  const [rules, setRules] = useState([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState('');
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState(null);
  const [editSaving, setEditSaving] = useState(false);
  const [simModalOpen, setSimModalOpen] = useState(false);
  const [simForm, setSimForm] = useState({ ruleId: null, siteId: null, value: 0 });
  const [sites, setSites] = useState([]);
  const [sitesLoading, setSitesLoading] = useState(false);
  const [sitesError, setSitesError] = useState('');
  const [simLoading, setSimLoading] = useState(false);
  const [escalationConfig, setEscalationConfig] = useState([]);
  const [escalationError, setEscalationError] = useState('');
  const [disableTarget, setDisableTarget] = useState(null);
  const [disableReason, setDisableReason] = useState('');
  const [toggleSaving, setToggleSaving] = useState('');

  const loadSites = useCallback(async () => {
    setSitesLoading(true);
    try {
      const data = await api.getStrict('/sites');
      if (Array.isArray(data)) setSites(data);
      setSitesError('');
    } catch (error) {
      setSitesError(error.message || '站点选项加载失败');
    } finally {
      setSitesLoading(false);
    }
  }, []);

  const loadEscalation = useCallback(async () => {
    try {
      const data = await api.getStrict('/alert-escalation-config');
      if (Array.isArray(data)) setEscalationConfig(data);
      setEscalationError('');
    } catch (error) {
      setEscalationError(error.message || '升级配置加载失败');
    }
  }, []);

  const loadRules = useCallback(async () => {
    setRulesLoading(true);
    try {
      const data = await api.getStrict('/alert-rules');
      if (Array.isArray(data)) setRules(data);
      setRulesError('');
    } catch (error) {
      setRulesError(error.message || '告警规则加载失败');
    } finally {
      setRulesLoading(false);
    }
  }, []);

  useEffect(() => { loadSites(); }, [loadSites]);
  useEffect(() => { loadEscalation(); }, [loadEscalation]);
  useEffect(() => { loadRules(); }, [loadRules]);

  // Toggle rule enabled
  const saveToggle = useCallback(async (rule, checked, reason = '') => {
    if (toggleSaving) return;
    setToggleSaving(rule.id);
    try {
      await api.putStrict(`/alert-rules/${rule.id}`, {
        enabled: checked,
        ...(reason ? { disable_reason: reason } : {}),
      });
      setRules((prev) => prev.map((item) => item.id === rule.id ? { ...item, enabled: checked } : item));
      message.success(checked ? `已启用“${rule.metricLabel}”规则` : `已停用“${rule.metricLabel}”规则`);
      setDisableTarget(null);
      setDisableReason('');
    } catch (error) {
      message.error(error.message || '保存失败，请重试');
    } finally {
      setToggleSaving('');
    }
  }, [message, toggleSaving]);

  const handleToggle = useCallback((rule, checked) => {
    if (!canManage) return;
    if (!checked) {
      setDisableTarget(rule);
      setDisableReason('');
      return;
    }
    modal.confirm({
      title: `启用“${rule.metricLabel}”规则？`,
      content: '启用后，新进入系统的数据将按该规则判断并可能产生告警。',
      okText: '确认启用',
      cancelText: '取消',
      onOk: () => saveToggle(rule, true),
    });
  }, [canManage, modal, saveToggle]);

  // Edit rule thresholds
  const handleEdit = useCallback((rule) => {
    if (!canManage) return;
    setEditingRule({ ...rule, thresholds: { ...rule.thresholds } });
    setEditModalOpen(true);
  }, [canManage]);

  const handleSaveEdit = useCallback(async () => {
    if (!editingRule || editSaving) return;
    setEditSaving(true);
    try {
      await api.putStrict(`/alert-rules/${editingRule.id}`, { thresholds: editingRule.thresholds });
      setRules((prev) => prev.map((r) => r.id === editingRule.id ? editingRule : r));
      setEditModalOpen(false);
      setEditingRule(null);
      message.success('规则阈值已保存');
    } catch (error) {
      message.error(error.message || '保存失败，请重试');
    } finally {
      setEditSaving(false);
    }
  }, [editingRule, editSaving]);

  // Simulate trigger
  const handleSimulate = useCallback(() => {
    if (!canManage) return;
    if (sitesError) {
      message.error('站点选项尚未加载，恢复后才能模拟触发');
      return;
    }
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
    api.postStrict('/alerts/simulate', payload).then((result) => {
      message.success(`模拟告警已触发：${alertLevelLabel[level]}（测试记录 #${result.id}）`);
      setSimModalOpen(false);
      setSimForm({ ruleId: null, siteId: null, value: 0 });
    }).catch((error) => {
      message.error(error.message || '模拟告警触发失败，请检查规则和站点后重试');
    }).finally(() => setSimLoading(false));
  }, [canManage, message, rules, simForm, sites, sitesError]);

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
          loading={toggleSaving === r.id}
          disabled={!canManage}
          aria-label={`${r.enabled ? '停用' : '启用'}${r.metricLabel}规则`}
          onChange={(checked) => handleToggle(r, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      align: 'center',
      render: (_, r) => (
        <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} disabled={!canManage} aria-label={`编辑${r.metricLabel}规则`}>
          编辑
        </Button>
      ),
    },
  ], [tokens, handleToggle, handleEdit, toggleSaving, canManage]);

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 2 }}>
      {/* Rule Engine Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Text style={{ color: tokens.colorTextSecondary, fontSize: 13 }}>
            配置告警触发规则，设定各级别阈值，支持模拟触发验证
          </Text>
        </div>
        <Space>
          <Tooltip title={canManage ? '创建带测试标记的模拟告警' : '仅管理员可模拟触发'}>
            <Button
              type="primary"
              icon={<ExperimentOutlined />}
              onClick={() => setSimModalOpen(true)}
              style={{ borderRadius: 8 }}
              disabled={!canManage}
            >
              模拟触发
            </Button>
          </Tooltip>
        </Space>
      </div>

      {/* Rule Table */}
      {rulesError && rules.length > 0 && <Alert
        type="warning"
        showIcon
        message="规则列表未更新"
        description={`${rulesError}。当前保留上次成功加载的配置。`}
        action={<Button size="small" onClick={loadRules}>重新加载</Button>}
        style={{ marginBottom: 8 }}
      />}
      {rulesError && !rulesLoading && rules.length === 0 ? <WorkspaceEmpty
        type="error"
        description="告警规则加载失败，当前不能确认服务器中的实际配置。"
        onRefresh={loadRules}
      /> : <Table
        rowKey="id"
        columns={ruleColumns}
        dataSource={rules}
        loading={rulesLoading}
        pagination={false}
        scroll={{ y: 'calc(100vh - 380px)' }}
        size="small"
        style={{ borderRadius: 12, overflow: 'hidden' }}
      />}

      {/* Edit Threshold Modal */}
      <Modal
        title={`编辑规则 - ${editingRule?.metricLabel || ''}`}
        open={editModalOpen}
        onOk={handleSaveEdit}
        onCancel={() => { setEditModalOpen(false); setEditingRule(null); }}
        okText="保存"
        cancelText="取消"
        confirmLoading={editSaving}
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

      <Modal
        title={`停用“${disableTarget?.metricLabel || ''}”规则`}
        open={Boolean(disableTarget)}
        onCancel={() => { if (!toggleSaving) { setDisableTarget(null); setDisableReason(''); } }}
        onOk={() => saveToggle(disableTarget, false, disableReason.trim())}
        okText="确认停用"
        okButtonProps={{ danger: true, disabled: !disableReason.trim() }}
        cancelText="取消"
        confirmLoading={Boolean(toggleSaving)}
        destroyOnHidden
      >
        <Text>停用后，该规则不再对新数据触发告警；已有告警和工单不会自动关闭。</Text>
        <Form.Item label="停用原因" required style={{ marginTop: 16, marginBottom: 0 }}>
          <Input.TextArea
            value={disableReason}
            onChange={(event) => setDisableReason(event.target.value)}
            placeholder="请说明停用原因，便于后续追溯"
            maxLength={200}
            showCount
            autoSize={{ minRows: 3, maxRows: 5 }}
          />
        </Form.Item>
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
        <Alert
          type="warning"
          showIcon
          message="此操作会创建真实的测试告警记录"
          description="记录带“模拟”标记并进入告警链路，可能影响当前列表和统计。仅用于受控验证，验证后应按测试记录编号完成清理。"
          style={{ marginTop: 8, marginBottom: 16 }}
        />
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
              loading={sitesLoading}
              disabled={Boolean(sitesError)}
              options={sites.map((s) => ({
                value: s.id,
                label: `${s.name} (${s.code || '-'})`,
              }))}
            />
            {sitesError && <Alert
              type="error"
              showIcon
              message="站点选项加载失败"
              description={sitesError}
              action={<Button size="small" onClick={loadSites}>重试</Button>}
              style={{ marginTop: 8 }}
            />}
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
              <Text style={{ fontSize: 12, color: tokens.colorTextSecondary }}>当前触发级别：</Text>
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
        {escalationError ? <WorkspaceEmpty
          type="error"
          description="告警升级配置加载失败，当前表格并非空配置。"
          onRefresh={loadEscalation}
        /> : <Table
          dataSource={escalationConfig}
          rowKey="level"
          pagination={false}
          size="small"
          columns={[
            { title: '告警级别', dataIndex: 'level', width: 116, render: v => {
              return <Tag color={alertLevelColor[v] || 'default'}>{alertLevelLabel[v] || v}</Tag>;
            }},
            { title: '升级时限', dataIndex: 'sla_minutes', width: 100, render: v => `${v} 分钟` },
            { title: '自动建单', dataIndex: 'auto_workorder', width: 124, render: v => v
              ? <Tag color="success">自动建单</Tag>
              : <Tooltip title="蓝色关注仅发送提醒；若超时未处理，升级至黄色预警后才自动建单，避免轻微波动产生无效工单。"><Tag>仅提醒</Tag></Tooltip> },
            { title: '通知方式', dataIndex: 'notify_type', width: 108, render: v => ({ app: '应用内提醒', sms: '短信通知', phone: '电话通知' }[v] || v || '未设置') },
            { title: '升级目标', dataIndex: 'escalate_to_level', width: 116, render: v => v && v !== 'None' ? <Tag color={alertLevelColor[v] || 'default'}>{alertLevelLabel[v] || v}</Tag> : '最高级别' },
            { title: '说明', dataIndex: 'description', width: 300,
              render: value => <TableLongText value={value} /> },
          ]}
        />}
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

const ALERT_TABS = PRIMARY_TABS.flatMap((primary) => primary.children.map((child) => ({
  key: child.key,
  label: child.label,
})));

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function AlertsPage() {
  const { message } = AntApp.useApp();
  const { user } = useAuth();
  const { tokens, isDark } = useTheme();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const roles = user?.roles?.length ? user.roles : [user?.role];
  const canManage = roles.includes('admin');
  const requestedTab = searchParams.get('tab') || 'alerts';
  const activeTab = ALERT_TABS.some((tab) => tab.key === requestedTab) ? requestedTab : 'alerts';
  const searchText = searchParams.get('search') || '';
  const statusFilter = searchParams.get('status') || null;
  const levelFilter = searchParams.get('level') || null;
  const requestedRange = searchParams.get('range') || 'today';
  const dateRange = dateRangeOptions.some((option) => option.value === requestedRange) ? requestedRange : 'today';

  const updateQuery = useCallback((patch) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  // ---- State ---------------------------------------------------------------
  const [allAlerts, setAllAlerts] = useState([]);       // full list from backend
  const [counts, setCounts] = useState({ total: 0, pending: 0, acknowledged: 0, resolved: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [alertsWrapRef, alertsH] = useTableAutoHeight({ headerOffset: 54 });
  const [reagentWrapRef, reagentH] = useTableAutoHeight({ headerOffset: 48 });

  // 试剂预警（跨站剩余可用天数/低余量）
  const [reagentList, setReagentList] = useState([]);
  const [reagentLoading, setReagentLoading] = useState(false);
  const [reagentError, setReagentError] = useState('');
  const loadReagentOverview = useCallback(async () => {
    setReagentLoading(true);
    try {
      const d = await api.getStrict('/reagent-overview');
      setReagentList(Array.isArray(d?.items) ? d.items : []);
      setReagentError('');
    } catch (e) {
      setReagentError(e.message || '物资预警加载失败');
    } finally {
      setReagentLoading(false);
    }
  }, []);
  useEffect(() => { if (activeTab === 'reagent') loadReagentOverview(); }, [activeTab, loadReagentOverview]);

  const resetFilters = useCallback(() => {
    updateQuery({ search: '', status: '', level: '', range: '' });
  }, [updateQuery]);

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

  // Convert confirm modal state (replaces Modal.confirm for React 19 compat)
  const [convertModalOpen, setConvertModalOpen] = useState(false);
  const [convertTarget, setConvertTarget] = useState(null);
  const [convertLoading, setConvertLoading] = useState(false);

  // Batch confirm modal state (replaces Modal.confirm for React 19 compat)
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchAction, setBatchAction] = useState(null);
  const [batchLabel, setBatchLabel] = useState('');
  const [batchReason, setBatchReason] = useState('');
  const [batchRemark, setBatchRemark] = useState('');

  // ---- Fetching (backend returns a plain array) ----------------------------
  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const [data, statistics] = await Promise.all([
        api.getStrict('/alerts?limit=500'),
        api.getStrict('/alerts/statistics'),
      ]);
      const list = Array.isArray(data) ? data : [];
      setAllAlerts(list);
      setCounts({
        total: Number(statistics?.total || 0),
        pending: Number(statistics?.by_status?.pending || 0),
        acknowledged: Number(statistics?.by_status?.acknowledged || 0),
        resolved: Number(statistics?.by_status?.resolved || 0),
      });
      setError(null);
    } catch (fetchError) {
      setError(fetchError.message || '加载告警数据失败，请检查网络后重试');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // 兼容旧入口：人工异常应从现场执行包发起，网页端仅提供证据审阅与闭环。
  useEffect(() => {
    if (searchParams.get('source') === 'manual') {
      navigate('/reports', { replace: true });
    }
  }, [searchParams, navigate]);

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

  const alertObjectLabel = useCallback((record) => (
    `${record.site_name || record.site_code || '未关联站点'}告警#${record.id}`
  ), []);

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
      await api.postStrict(`/alerts/${resolveTarget.id}/resolve`, {
        reason: values.reason,
        remark: values.remark || '',
        conclusion: values.conclusion,
      });
      message.success(`告警「${resolveTarget.site_name || resolveTarget.id}」已办结`);
      setResolveModalOpen(false);
      setResolveTarget(null);
      fetchAlerts();
    } catch (error) {
      if (!error?.errorFields) message.error(error.message || '操作失败，请重试');
    } finally {
      setActionLoading((prev) => ({ ...prev, [resolveTarget?.id]: false }));
    }
  }, [resolveTarget, resolveForm, fetchAlerts]);

  const handleAcknowledge = useCallback(async (record) => {
    setActionLoading((prev) => ({ ...prev, [record.id]: true }));
    try {
      await api.postStrict(`/alerts/${record.id}/acknowledge`, {});
      message.success(`告警「${record.site_name || record.id}」已确认`);
      fetchAlerts();
    } catch (error) {
      message.error(error.message || '确认失败，请重试');
    } finally {
      setActionLoading((prev) => ({ ...prev, [record.id]: false }));
    }
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
      await api.postStrict(`/alerts/${urgeTarget.id}/urge`, {
        supervisor: values.supervisor,
        opinion: values.opinion || '',
        deadline: values.deadline || '',
      });
      message.success(`已对告警「${urgeTarget.site_name || urgeTarget.id}」发起督办`);
      setUrgeModalOpen(false);
      setUrgeTarget(null);
      fetchAlerts();
    } catch (error) {
      if (!error?.errorFields) message.error(error.message || '督办失败，请重试');
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
    try {
      const result = await api.postStrict(`/alerts/${convertTarget.id}/confirm-convert`, {});
      const orderNo = result.order_no || '';
      message.success(orderNo ? `已成功转为工单 ${orderNo}` : '已成功转为工单');
      setConvertModalOpen(false);
      setConvertTarget(null);
      fetchAlerts();
      // Navigate to work orders page filtered by the new order
      if (orderNo) {
        setTimeout(() => navigate(`/workorders?search=${orderNo}`), 500);
      }
    } catch (error) {
      message.error(error.message || '转工单失败，请重试');
    } finally {
      setConvertLoading(false);
    }
  }, [convertTarget, fetchAlerts, navigate]);

  // ---- Batch actions (POST via batch endpoint) -----------------------------
  const runBatch = useCallback((action, label) => {
    if (selectedRowKeys.length === 0) return;
    setBatchAction(action);
    setBatchLabel(label);
    setBatchReason('');
    setBatchRemark('');
    setBatchModalOpen(true);
  }, [selectedRowKeys]);

  const handleBatchConfirm = useCallback(async () => {
    if (!batchAction || selectedRowKeys.length === 0) return;
    if (batchAction === 'resolve' && !batchReason) {
      message.warning('请选择批量办结原因');
      return;
    }
    if (batchAction === 'urge' && !batchRemark.trim()) {
      message.warning('请填写批量督办要求');
      return;
    }
    setBatchLoading(true);
    try {
      const result = await api.postStrict('/alerts/batch', {
        ids: selectedRowKeys,
        action: batchAction,
        ...(batchReason ? { reason: batchReason } : {}),
        ...(batchRemark.trim() ? { remark: batchRemark.trim() } : {}),
      });
      const completed = Number(result.count ?? selectedRowKeys.length);
      const skipped = Number(result.skipped || 0);
      message.success(`批量${batchLabel}完成 ${completed} 条${skipped ? `，跳过 ${skipped} 条状态不适用记录` : ''}`);
      setSelectedRowKeys([]);
      setBatchModalOpen(false);
      setBatchAction(null);
      fetchAlerts();
    } catch (error) {
      message.error(error.message || `批量${batchLabel}失败，请重试`);
    } finally {
      setBatchLoading(false);
    }
  }, [batchAction, batchLabel, batchReason, batchRemark, selectedRowKeys, fetchAlerts, message]);

  const handleBatchResolve = useCallback(() => {
    runBatch('resolve', '办结');
  }, [runBatch]);

  const handleBatchUrge = useCallback(() => {
    runBatch('urge', '督办');
  }, [runBatch]);

  const handleBatchConvert = useCallback(() => {
    runBatch('convert', '转工单');
  }, [runBatch]);

  // ---- Table columns -------------------------------------------------------
  const columns = useMemo(() => [
    {
      title: '站点 & 等级',
      key: 'site_level',
      width: 168,
      render: (_, record) => {
        const severity = alertSeverityTag[record.level] || { color: tokens.colorTextTertiary, label: '?', desc: '未知' };
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
      width: 360,
      render: (text) => <TableLongText value={text} />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status) => (
        <Tag
          icon={statusIconMap[status]}
          color={alertStatusBadge[status] || 'default'}
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
            <Button
              type="link"
              size="small"
              icon={<LinkOutlined />}
              aria-label={`查看${record.site_name || '该告警'}的关联工单${record.related_order_no || ''}`}
              onClick={() => navigate(`/workorders?search=${encodeURIComponent(record.related_order_no || '')}`)}
            >
              {record.related_order_no || '查看关联工单'}
            </Button>
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

        if (!canManage) return <Text type="secondary">只读</Text>;

        const moreItems = [
          ...(record.status === 'pending' ? [{ key: 'resolve', label: '办结' }] : []),
          { key: 'urge', label: '督办' },
          { key: 'convert', label: '转工单' },
        ];

        const onMoreAction = ({ key }) => {
          if (key === 'resolve') handleResolve(record);
          if (key === 'urge') handleUrge(record);
          if (key === 'convert') handleConvert(record);
        };

        return (
          <Space.Compact size="small">
            {record.status === 'pending' && (
              <Button
                type="primary"
                loading={isLoading}
                onClick={() => handleAcknowledge(record)}
                aria-label={`受理${alertObjectLabel(record)}`}
              >
                受理
              </Button>
            )}
            {record.status === 'acknowledged' && <Button
              type="primary"
              loading={isLoading}
              onClick={() => handleResolve(record)}
              aria-label={`办结${alertObjectLabel(record)}`}
            >办结</Button>}
            <Dropdown menu={{ items: moreItems, onClick: onMoreAction }} trigger={['click']} disabled={isLoading}>
              <Button icon={<MoreOutlined />} aria-label={`${alertObjectLabel(record)}的更多操作`} />
            </Dropdown>
          </Space.Compact>
        );
      },
    },
  ], [tokens, actionLoading, handleAcknowledge, handleResolve, handleUrge, handleConvert, canManage, navigate, alertObjectLabel]);

  // ---- Row selection -------------------------------------------------------
  const rowSelection = useMemo(() => ({
    selectedRowKeys,
    onChange: (keys) => setSelectedRowKeys(keys),
    getCheckboxProps: (record) => ({
      disabled: record.status === 'resolved' || Boolean(record.related_order_no),
      'aria-label': `选择${alertObjectLabel(record)}`,
    }),
  }), [selectedRowKeys, alertObjectLabel]);

  // ---- Styles --------------------------------------------------------------
  const cardStyle = cardStyleBase(tokens, isDark);
  const infoColor = statusColors.info[isDark ? 'dark' : 'light'];

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
      icon: <ExclamationCircleOutlined style={{ fontSize: 16, color: tokens.colorWarning }} />,
      color: tokens.colorWarning,
    },
    {
      title: '处理中',
      value: counts.acknowledged,
      icon: <ClockCircleOutlined style={{ fontSize: 16, color: tokens.colorInfo }} />,
      color: tokens.colorInfo,
    },
    {
      title: '已办结',
      value: counts.resolved,
      icon: <CheckCircleOutlined style={{ fontSize: 16, color: tokens.colorSuccess }} />,
      color: tokens.colorSuccess,
    },
  ], [counts, tokens.colorPrimary, tokens.colorWarning, tokens.colorInfo, tokens.colorSuccess]);

  // ---- Render --------------------------------------------------------------
  return (
    <div style={pageRootStyle}>
      {/* Page Header */}
      <div style={{ marginBottom: 4, flexShrink: 0 }}>
        <Title level={4} style={{ margin: 0, color: tokens.colorText }}>告警与事件</Title>

        {/* Single line tab layer for mutually exclusive alert views. */}
        <Tabs
          type="line"
          activeKey={activeTab}
          items={ALERT_TABS}
          onChange={(key) => updateQuery({ tab: key === 'alerts' ? '' : key })}
          style={{ marginTop: 12 }}
        />
      </div>

      {/* Tab Content */}
      <div className="workspace-embedded-page" style={{ overflow: 'hidden' }}>
        {activeTab === 'alerts' ? (
          <>
            <StatusStrip items={statCards.map((item) => ({ key: item.title, label: item.title, value: item.value, color: item.color }))} />

            {/* Filter Bar */}
            <WorkspaceToolbar
              actions={(
                <Space>
                  <Button icon={<ReloadOutlined />} onClick={resetFilters}>
                    重置
                  </Button>
                  <Button onClick={fetchAlerts} loading={loading} style={{ borderRadius: 8 }}>
                    刷新
                  </Button>
                </Space>
              )}
            >
              <Input
                placeholder="搜索站点名称或告警内容..."
                prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
                allowClear
                value={searchText}
                onChange={(e) => updateQuery({ search: e.target.value })}
                onPressEnter={fetchAlerts}
                style={{ width: filterInputWidth, borderRadius: 8 }}
                aria-label="搜索站点名称或告警内容"
              />
              <Select
                placeholder="告警状态"
                allowClear
                value={statusFilter}
                onChange={(val) => updateQuery({ status: val || '' })}
                style={{ width: filterSelectWidth }}
                aria-label="按告警状态筛选"
                      options={Object.entries(alertStatusMap).map(([value, label]) => ({
                        value,
                        label,
                      }))}
                    />
              <Select
                placeholder="告警等级"
                allowClear
                value={levelFilter}
                onChange={(val) => updateQuery({ level: val || '' })}
                style={{ width: filterSelectWidth }}
                aria-label="按告警等级筛选"
                      options={[
                        { value: 'red', label: 'I级 紧急告警' },
                        { value: 'orange', label: 'II级 较重告警' },
                        { value: 'yellow', label: 'III级 一般告警' },
                        { value: 'blue', label: 'IV级 一般关注' },
                      ]}
                    />
              <Select
                value={dateRange}
                onChange={(value) => updateQuery({ range: value === 'today' ? '' : value })}
                style={{ width: filterSmallSelectWidth }}
                aria-label="按告警时间范围筛选"
                      options={dateRangeOptions}
                    />
                    {(statusFilter || levelFilter || dateRange || searchText) && (
                      <ToolbarMeta label={statusFilter || levelFilter || searchText || dateRange !== 'today' ? '筛选结果' : '当前范围'}>
                        {filteredAlerts.length} 条
                      </ToolbarMeta>
                    )}
            </WorkspaceToolbar>

            {/* Batch Operations Bar */}
            {selectedRowKeys.length > 0 && (
              <div
                style={{
                  marginTop: 0,
                  padding: '6px 10px',
                  borderRadius: 6,
                  background: `${infoColor}14`,
                  border: `1px solid ${infoColor}40`,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  flexShrink: 0,
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

            {incidentGroups.length > 0 && (
              <Card
                title="事件聚合"
                extra={<Text type="secondary" style={{ fontSize: 12 }}>同类型告警按 30 分钟窗口归并</Text>}
                style={{ ...cardStyle, marginTop: 0 }}
                styles={{ body: { padding: '6px 12px' } }}
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
            {error && allAlerts.length > 0 && <Alert
              type="warning"
              showIcon
              message="告警列表未更新"
              description={`${error}。当前保留上次成功加载的 ${allAlerts.length} 条告警。`}
              action={<Button size="small" onClick={fetchAlerts}>重新加载</Button>}
              style={{ marginTop: 0 }}
            />}

            <Card style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
              {/* Error State */}
              {error && allAlerts.length === 0 && (
                <div
                  style={{
                    padding: '32px 24px',
                    textAlign: 'center',
                  }}
                >
                  <ExclamationCircleOutlined style={{ fontSize: 40, color: tokens.colorError, marginBottom: 12 }} />
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
              {(!error || allAlerts.length > 0) && !loading && filteredAlerts.length === 0 && (
                <WorkspaceEmpty type={searchText || statusFilter || levelFilter ? 'filtered' : 'empty'} onRefresh={fetchAlerts} />
              )}
              {(!error || allAlerts.length > 0) && (loading || filteredAlerts.length > 0) && (
                <div ref={alertsWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Table
                  rowKey="id"
                  columns={columns}
                  dataSource={filteredAlerts}
                  loading={loading}
                  rowSelection={canManage ? rowSelection : undefined}
                  pagination={{ pageSize: 10, size: 'small', showSizeChanger: false }}
                  scroll={alertsH ? { y: alertsH } : undefined}
                  size="small"
                  style={{ borderRadius: 12, overflow: 'hidden' }}
                />
                </div>
              )}
            </Card>
          </>
        ) : activeTab === 'rules' ? (
          <AlertRuleEngineTab tokens={tokens} isDark={isDark} canManage={canManage} />
        ) : activeTab === 'thresholds' ? (
          <ThresholdRulesTab tokens={tokens} isDark={isDark} />
        ) : activeTab === 'reagent' ? (
          <div className="workspace-embedded-page">
            <StatusStrip items={[
              { key: 'expired', label: '已过期', value: reagentList.filter((item) => item.status === '已过期').length, color: tokens.colorError },
              { key: 'near_expiry', label: '临期', value: reagentList.filter((item) => item.status === '临期').length, color: tokens.colorWarning },
              { key: 'low_stock', label: '低余量', value: reagentList.filter((item) => item.status === '低余量').length, color: tokens.colorError },
            ]} />
            <WorkspaceToolbar actions={<Button icon={<ReloadOutlined />} onClick={loadReagentOverview} loading={reagentLoading}>刷新</Button>}>
              <Text type="secondary">共 {reagentList.length} 条需要关注的站点试剂</Text>
            </WorkspaceToolbar>
            <Card style={{ ...tableCardStyle(tokens, isDark), marginTop: 0 }} styles={{ body: tableCardBody }}>
              <div ref={reagentWrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                {reagentError && !reagentLoading ? <WorkspaceEmpty
                  type="error"
                  description="物资预警加载失败，当前不能判断是否暂无临期或低余量试剂。"
                  onRefresh={loadReagentOverview}
                /> : <Table
                  dataSource={reagentList}
                  rowKey={(r) => `${r.site_id}-${r.reagent_id}`}
                  loading={reagentLoading}
                  pagination={{ pageSize: 10, size: 'small', showSizeChanger: false }}
                  size="small"
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
                        : <Text strong style={{ color: v <= 0 ? tokens.colorError : v <= 7 ? tokens.colorWarning : tokens.colorSuccess }}>{v} 天</Text>,
                    },
                    {
                      title: '余量', dataIndex: 'current_qty', width: 100,
                      render: (v) => v == null ? '—' : Number(v).toFixed(2),
                    },
                    {
                      title: '操作', key: 'op', width: 120,
                      render: (_, r) => (
                        <Button
                          aria-label={`查看${r.site_name || '未命名站点'}的${r.reagent_name || '试剂'}详情`}
                          size="small"
                          type="link"
                          onClick={() => navigate(`/sites?archive=${r.site_id}`)}
                        >查看站点</Button>
                      ),
                    },
                  ]}
                />}
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
            <div style={{ padding: '10px 14px', borderRadius: 8, background: tokens.colorPrimaryBg, marginBottom: 16 }}>
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
            <div style={{ padding: '10px 14px', borderRadius: 8, background: `${statusColors.warning[isDark ? 'dark' : 'light']}0F`, marginBottom: 16 }}>
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
        onCancel={() => { setBatchModalOpen(false); setBatchAction(null); setBatchReason(''); setBatchRemark(''); }}
        okText="确认"
        cancelText="取消"
        confirmLoading={batchLoading}
        width={420}
      >
        <div style={{ padding: '12px 0' }}>
          <Text>确认对选中的 <Text strong>{selectedRowKeys.length}</Text> 条告警执行「{batchLabel}」操作？</Text>
          {batchAction === 'resolve' && <div style={{ marginTop: 16 }}>
            <Text strong>办结原因</Text>
            <Select
              aria-label="选择批量办结原因"
              placeholder="请选择办结原因"
              value={batchReason || undefined}
              onChange={setBatchReason}
              options={resolveReasonOptions.map(({ value, label }) => ({ value, label }))}
              style={{ width: '100%', marginTop: 8 }}
            />
          </div>}
          {batchAction === 'urge' && <div style={{ marginTop: 16 }}>
            <Text strong>督办要求</Text>
            <Input.TextArea
              aria-label="填写批量督办要求"
              value={batchRemark}
              onChange={(event) => setBatchRemark(event.target.value)}
              placeholder="请说明本批告警的处理要求和时限"
              maxLength={300}
              showCount
              rows={3}
              style={{ marginTop: 8 }}
            />
          </div>}
          {batchAction === 'convert' && <Alert
            type="warning"
            showIcon
            message="每条告警将分别生成一张工单"
            description="生成后不能在本页撤销，请确认所选告警均需要现场处置。"
            style={{ marginTop: 16 }}
          />}
        </div>
      </Modal>

    </div>
  );
}
