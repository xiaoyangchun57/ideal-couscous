import { useEffect, useState } from 'react';
import { Button, Col, Form, Input, InputNumber, Modal, Popconfirm, Row, Select, Space, Switch, Tag, Tooltip, Typography, message } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../../../services/api';
import { metricMap } from '../../../services/constants';
import { useAuth } from '../../../hooks/useAuth';
import { StatusStrip, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar } from '../../../components/WorkspacePage';

const { Text } = Typography;
const RULE_TYPE_LABEL = { static: '静态阈值', spc: 'SPC 动态', historical: '历史基线', correlated: '关联阈值' };
const RULE_TYPE_COLOR = { static: 'blue', spc: 'purple', historical: 'green', correlated: 'orange' };
const RULE_TYPE_OPTIONS = [
  { value: 'static', label: '静态阈值' },
  { value: 'spc', label: 'SPC 动态' },
  { value: 'historical', label: '历史基线（暂不可用）', disabled: true },
  { value: 'correlated', label: '关联阈值（暂不可用）', disabled: true },
];
const METRIC_OPTIONS = [
  { value: 'ph', label: 'pH' }, { value: 'cod', label: 'COD' }, { value: 'ammonia', label: '氨氮' }, { value: 'total_phosphorus', label: '总磷' },
  { value: 'total_nitrogen', label: '总氮' }, { value: 'dissolved_oxygen', label: '溶解氧' }, { value: 'turbidity', label: '浊度' }, { value: 'water_temp', label: '水温' },
];
const SCOPE_OPTIONS = [{ value: 'global', label: '全局' }, { value: 'metric', label: '按指标' }, { value: 'site', label: '按站点' }];
const SEVERITY_OPTIONS = [{ value: 'info', label: '提示', color: 'blue' }, { value: 'warning', label: '警告', color: 'orange' }, { value: 'critical', label: '严重', color: 'red' }];

export default function ThresholdRulesTab() {
  const { user } = useAuth();
  const isAdmin = (user?.roles || [user?.role]).includes('admin');
  const [rules, setRules] = useState([]);
  const [sites, setSites] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form] = Form.useForm();
  const scope = Form.useWatch('scope', form);
  const ruleType = Form.useWatch('rule_type', form);

  const load = async () => {
    setLoading(true);
    try {
      const [ruleRows, siteRows] = await Promise.all([
        api.getStrict('/threshold-rules'),
        api.getStrict('/sites'),
      ]);
      setRules(ruleRows || []);
      setSites(Array.isArray(siteRows) ? siteRows : []);
      setLoadError('');
    }
    catch (error) { setLoadError(error.message || '阈值规则加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ rule_type: 'static', scope: 'metric', severity: 'warning' });
    setModalOpen(true);
  };
  const openEdit = (rule) => {
    setEditing(rule);
    form.setFieldsValue({ name: rule.name, scope: rule.scope, site_id: rule.site_id, metric: rule.metric, rule_type: rule.rule_type, severity: rule.severity, min: rule.conditions?.min, max: rule.conditions?.max, mean: rule.conditions?.mean, std: rule.conditions?.std });
    setModalOpen(true);
  };
  const submit = async () => {
    if (submitting) return;
    const values = await form.validateFields();
    const conditions = values.rule_type === 'static' ? { min: values.min, max: values.max } : values.rule_type === 'spc' ? { mean: values.mean, std: values.std, ucl: (values.mean || 0) + 3 * (values.std || 1), lcl: (values.mean || 0) - 3 * (values.std || 1) } : {};
    setSubmitting(true);
    try {
      const payload = { name: values.name, scope: values.scope, site_id: values.site_id, metric: values.metric, rule_type: values.rule_type, severity: values.severity, conditions };
      if (editing) await api.putStrict(`/threshold-rules/${editing.id}`, payload);
      else await api.postStrict('/threshold-rules', payload);
      message.success(editing ? '阈值规则已更新' : '已创建阈值规则');
      setModalOpen(false);
      load();
    } catch (error) { message.error(error.message || '保存失败，请重试'); }
    finally { setSubmitting(false); }
  };

  const remove = async (rule) => {
    try {
      await api.deleteStrict(`/threshold-rules/${rule.id}`);
      message.success(`已删除规则“${rule.name}”`);
      load();
    } catch (error) {
      message.error(error.message || '删除失败，请重试');
    }
  };

  const stats = rules.reduce((result, rule) => ({ ...result, [rule.rule_type]: (result[rule.rule_type] || 0) + 1 }), { total: rules.length });
  const columns = [
    { title: '规则名称', dataIndex: 'name', width: 220, render: (value, rule) => <Space direction="vertical" size={0}><Text strong>{value}</Text><Text type="secondary" style={{ fontSize: 12 }}>{rule.scope === 'metric' ? `指标：${metricMap[rule.metric] || rule.metric}` : rule.scope === 'site' ? `站点 ID：${rule.site_id}` : '全局规则'}</Text></Space> },
    { title: '规则类型', dataIndex: 'rule_type', width: 110, render: (value) => <Tag color={RULE_TYPE_COLOR[value]}>{RULE_TYPE_LABEL[value] || value}</Tag> },
    { title: '告警级别', dataIndex: 'severity', width: 100, render: (value) => { const level = SEVERITY_OPTIONS.find((item) => item.value === value); return <Tag color={level?.color || 'default'}>{level?.label || value}</Tag>; } },
    { title: '阈值条件', key: 'conditions', render: (_, rule) => { const condition = rule.conditions || {}; if (rule.rule_type === 'static') return `${condition.min ?? '-'} ~ ${condition.max ?? '-'}`; if (rule.rule_type === 'spc') return `均值=${condition.mean ?? '-'}，标准差=${condition.std ?? '-'}`; return <Text type="secondary">复杂规则</Text>; } },
    { title: '已启用', dataIndex: 'enabled', width: 90, render: (value) => <Switch size="small" checked={Boolean(value)} disabled /> },
    { title: '操作', key: 'actions', width: 120, fixed: 'right', render: (_, rule) => <Space><Tooltip title={isAdmin ? '编辑' : '仅管理员可操作'}><Button aria-label={`编辑规则${rule.name}`} size="small" icon={<EditOutlined />} onClick={() => openEdit(rule)} disabled={!isAdmin} /></Tooltip><Popconfirm title={`确认删除规则“${rule.name}”？`} description="删除后不再参与告警判断，且无法撤销。" onConfirm={() => remove(rule)} disabled={!isAdmin}><Button aria-label={`删除规则${rule.name}`} size="small" icon={<DeleteOutlined />} danger disabled={!isAdmin} /></Popconfirm></Space> },
  ];

  return <div className="workspace-embedded-page">
    <StatusStrip items={[{ key: 'total', label: '已配置规则', value: stats.total }, { key: 'static', label: '静态阈值', value: stats.static || 0 }, { key: 'spc', label: '动态规则', value: stats.spc || 0 }]} />
    <WorkspaceToolbar actions={<Space><Button icon={<ReloadOutlined />} onClick={load}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!isAdmin}>新增阈值规则</Button></Space>}><Text type="secondary">默认静态阈值采用 GB 3838-2002 III 类水标准；扩展规则可在此维护。</Text></WorkspaceToolbar>
    {loadError && !loading
      ? <WorkspaceEmpty type="error" description="阈值规则加载失败，当前不能确认是否尚未配置。" onRefresh={load} />
      : <WorkspaceTable rowKey="id" columns={columns} dataSource={rules} loading={loading} />}
    <Modal open={modalOpen} title={editing ? '编辑阈值规则' : '新增阈值规则'} onCancel={() => setModalOpen(false)} onOk={submit} confirmLoading={submitting} okText="保存" cancelText="取消" width={560} destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="规则名称" rules={[{ required: true, message: '请输入名称' }]}><Input /></Form.Item>
        <Row gutter={12}><Col span={12}><Form.Item name="scope" label="作用范围" rules={[{ required: true, message: '请选择作用范围' }]}><Select options={SCOPE_OPTIONS} onChange={() => form.setFieldsValue({ site_id: undefined, metric: undefined })} /></Form.Item></Col><Col span={12}>{scope === 'site' ? <Form.Item name="site_id" label="适用站点" rules={[{ required: true, message: '请选择站点' }]}><Select showSearch optionFilterProp="label" options={sites.map((site) => ({ value: site.id, label: site.name }))} placeholder="请选择站点" /></Form.Item> : <Form.Item name="metric" label="适用指标" rules={scope === 'metric' ? [{ required: true, message: '请选择指标' }] : []}><Select options={METRIC_OPTIONS} allowClear disabled={scope === 'global'} placeholder={scope === 'global' ? '全局规则无需指标' : '请选择指标'} /></Form.Item>}</Col></Row>
        {scope === 'site' && <Form.Item name="metric" label="限定指标（可选）"><Select options={METRIC_OPTIONS} allowClear placeholder="留空表示站点全部指标" /></Form.Item>}
        <Row gutter={12}><Col span={12}><Form.Item name="rule_type" label="规则类型" rules={[{ required: true, message: '请选择规则类型' }]}><Select options={RULE_TYPE_OPTIONS} /></Form.Item></Col><Col span={12}><Form.Item name="severity" label="告警级别" rules={[{ required: true, message: '请选择告警级别' }]}><Select options={SEVERITY_OPTIONS.map(({ value, label }) => ({ value, label }))} /></Form.Item></Col></Row>
        {ruleType === 'static' && <Row gutter={12}><Col span={12}><Form.Item name="min" label="下限值" dependencies={['max']} rules={[({ getFieldValue }) => ({ validator(_, value) { const max = getFieldValue('max'); if (value == null && max == null) return Promise.reject(new Error('上限和下限至少填写一项')); if (value != null && max != null && value >= max) return Promise.reject(new Error('下限必须小于上限')); return Promise.resolve(); } })]}><InputNumber style={{ width: '100%' }} step={0.1} /></Form.Item></Col><Col span={12}><Form.Item name="max" label="上限值" dependencies={['min']} rules={[({ getFieldValue }) => ({ validator(_, value) { const min = getFieldValue('min'); if (value == null && min == null) return Promise.reject(new Error('上限和下限至少填写一项')); if (value != null && min != null && value <= min) return Promise.reject(new Error('上限必须大于下限')); return Promise.resolve(); } })]}><InputNumber style={{ width: '100%' }} step={0.1} /></Form.Item></Col></Row>}
        {ruleType === 'spc' && <Row gutter={12}><Col span={12}><Form.Item name="mean" label="均值" rules={[{ required: true, message: '请输入均值' }]}><InputNumber style={{ width: '100%' }} step={0.1} /></Form.Item></Col><Col span={12}><Form.Item name="std" label="标准差" rules={[{ required: true, message: '请输入标准差' }]}><InputNumber style={{ width: '100%' }} step={0.01} min={0.01} /></Form.Item></Col></Row>}
      </Form>
    </Modal>
  </div>;
}
