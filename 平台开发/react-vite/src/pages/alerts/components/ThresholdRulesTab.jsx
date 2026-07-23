import { useState, useEffect } from 'react';
import {
  Table, Card, Button, Space, Tag, Typography, Row, Col, Statistic,
  Modal, Form, Select, InputNumber, message, Switch, Empty, Popconfirm, Tooltip,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined,
  ExperimentOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { api } from '../../../services/api';
import { metricMap } from '../../../services/constants';
import { useAuth } from '../../../hooks/useAuth';

const { Text, Title } = Typography;

// 规则类型说明
const RULE_TYPE_LABEL = {
  static: '静态阈值',
  spc: 'SPC 动态',
  historical: '历史基线',
  correlated: '关联阈值',
};

const RULE_TYPE_COLOR = {
  static: 'blue',
  spc: 'purple',
  historical: 'green',
  correlated: 'orange',
};

const METRIC_OPTIONS = [
  { value: 'ph', label: 'pH' },
  { value: 'cod', label: 'COD' },
  { value: 'ammonia', label: '氨氮' },
  { value: 'total_phosphorus', label: '总磷' },
  { value: 'total_nitrogen', label: '总氮' },
  { value: 'dissolved_oxygen', label: '溶解氧' },
  { value: 'turbidity', label: '浊度' },
  { value: 'water_temp', label: '水温' },
];

const SCOPE_OPTIONS = [
  { value: 'global', label: '全局' },
  { value: 'metric', label: '按指标' },
  { value: 'site', label: '按站点' },
];

const SEVERITY_OPTIONS = [
  { value: 'info', label: '提示', color: 'blue' },
  { value: 'warning', label: '警告', color: 'orange' },
  { value: 'critical', label: '严重', color: 'red' },
];

export default function ThresholdRulesTab({ tokens, isDark }) {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [rules, setRules] = useState([]);
  const [stats, setStats] = useState({ total: 0, by_type: {} });
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.get('/threshold-rules');
      setRules(Array.isArray(data) ? data : []);
      const byType = (data || []).reduce((acc, r) => {
        acc[r.rule_type] = (acc[r.rule_type] || 0) + 1;
        return acc;
      }, {});
      setStats({ total: (data || []).length, by_type: byType });
    } catch (e) {
      message.error('加载阈值规则失败：' + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ rule_type: 'static', scope: 'metric', severity: 'warning' });
    setModalOpen(true);
  };

  const onEdit = (r) => {
    setEditing(r);
    form.setFieldsValue({
      name: r.name, scope: r.scope, metric: r.metric, rule_type: r.rule_type,
      severity: r.severity, min: r.conditions?.min, max: r.conditions?.max,
      mean: r.conditions?.mean, std: r.conditions?.std,
    });
    setModalOpen(true);
  };

  const onSubmit = async () => {
    const v = await form.validateFields();
    let conditions = {};
    if (v.rule_type === 'static') {
      conditions = { min: v.min, max: v.max };
    } else if (v.rule_type === 'spc') {
      conditions = { mean: v.mean, std: v.std, ucl: (v.mean || 0) + 3 * (v.std || 1), lcl: (v.mean || 0) - 3 * (v.std || 1) };
    }
    const payload = {
      name: v.name, scope: v.scope, metric: v.metric, rule_type: v.rule_type,
      severity: v.severity, conditions,
    };
    try {
      if (editing) {
        // 暂不支持 update（API 未实现），提示
        message.warning('编辑接口待实现，请删除后重建');
        return;
      }
      await api.post('/threshold-rules', payload);
      message.success('已创建阈值规则');
      setModalOpen(false);
      load();
    } catch (e) {
      message.error('保存失败：' + e.message);
    }
  };

  const columns = [
    { title: '规则名称', dataIndex: 'name', width: 200, render: (v, r) => (
      <Space direction="vertical" size={0}>
        <Text strong>{v}</Text>
        {r.scope === 'metric' && <Text type="secondary" style={{ fontSize: 12 }}>指标：{metricMap[r.metric] || r.metric}</Text>}
        {r.scope === 'site' && <Text type="secondary" style={{ fontSize: 12 }}>站点 ID：{r.site_id}</Text>}
        {r.scope === 'global' && <Text type="secondary" style={{ fontSize: 12 }}>全局规则</Text>}
      </Space>
    )},
    { title: '规则类型', dataIndex: 'rule_type', width: 100, render: (v) => <Tag color={RULE_TYPE_COLOR[v]} style={{ borderRadius: 4, fontSize: 11 }}>{RULE_TYPE_LABEL[v] || v}</Tag> },
    { title: '告警级别', dataIndex: 'severity', width: 80, render: (v) => {
      const s = SEVERITY_OPTIONS.find(x => x.value === v);
      return <Tag color={s?.color || 'default'} style={{ borderRadius: 4, fontSize: 11 }}>{s?.label || v}</Tag>;
    }},
    { title: '阈值条件', width: 280, render: (_, r) => {
      const c = r.conditions || {};
      if (r.rule_type === 'static') {
        return <Text>{c.min ?? '-'} ~ {c.max ?? '-'}</Text>;
      } else if (r.rule_type === 'spc') {
        return <Text>μ={c.mean ?? '-'} σ={c.std ?? '-'} ({c.lcl?.toFixed?.(2) ?? '-'} ~ {c.ucl?.toFixed?.(2) ?? '-'})</Text>;
      }
      return <Text type="secondary">复杂规则</Text>;
    }},
    { title: '已启用', dataIndex: 'enabled', width: 70, render: (v) => <Switch size="small" checked={!!v} disabled /> },
    { title: '操作', width: 120, fixed: 'right', render: (_, r) => (
      <Space>
        <Tooltip title={isAdmin ? '编辑' : '仅管理员可操作'}>
          <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(r)} disabled={!isAdmin} />
        </Tooltip>
        <Popconfirm title="确认删除此规则？" onConfirm={async () => {
          try {
            // 删除 API 未实现，提示
            message.warning('删除接口待实现');
          } catch (e) { message.error(e.message); }
        }} disabled={!isAdmin}>
          <Button size="small" icon={<DeleteOutlined />} danger disabled={!isAdmin} />
        </Popconfirm>
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
      {/* 顶部统计 */}
      <Row gutter={[12, 12]}>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic title="总规则数" value={stats.total} prefix={<ExperimentOutlined />} valueStyle={{ color: tokens.colorPrimary }} />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic title="静态阈值" value={stats.by_type.static || 0} valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic title="统计过程控制动态" value={stats.by_type.spc || 0} valueStyle={{ color: '#722ed1' }} />
          </Card>
        </Col>
        <Col flex="1">
          <Card bodyStyle={{ padding: '12px 16px' }}>
            <Statistic title="关联/基线" value={(stats.by_type.correlated || 0) + (stats.by_type.historical || 0)} valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>
      </Row>

      {/* 工具栏 */}
      <Card bodyStyle={{ padding: 12 }}>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate} disabled={!isAdmin}>新增阈值规则</Button>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Text type="secondary" style={{ marginLeft: 12 }}>
            当前采用 GB 3838-2002 III类水标准作为缺省静态阈值；可在此扩展 统计过程控制 / 历史基线 / 关联规则。
          </Text>
        </Space>
      </Card>

      {/* 表格 */}
      <Card bodyStyle={{ padding: 0 }}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={rules}
          loading={loading}
          className="review-scroll-table"
          pagination={false}
          scroll={{ y: 'calc(100vh - 420px)' }}
          size="small"
          locale={{ emptyText: <Empty description="暂无阈值规则，请点击右上角新增" /> }}
        />
      </Card>

      {/* 新增/编辑弹窗 */}
      <Modal
        open={modalOpen}
        title={editing ? '编辑阈值规则' : '新增阈值规则'}
        onCancel={() => setModalOpen(false)}
        onOk={onSubmit}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名称" rules={[{ required: true, message: '请输入名称' }]}>
            <input className="ant-input ant-input-outlined css-dev-only-do-not-override-1adbn6x" style={{ width: '100%', padding: '4px 11px', border: `1px solid ${tokens.colorBorder}`, borderRadius: 6, background: tokens.colorBgContainer, color: tokens.colorText }} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="scope" label="作用范围" rules={[{ required: true }]}>
                <Select options={SCOPE_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="metric" label="适用指标">
                <Select options={METRIC_OPTIONS} allowClear placeholder="作用范围=全局时可留空" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
                <Select options={Object.entries(RULE_TYPE_LABEL).map(([k, v]) => ({ value: k, label: v }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="severity" label="告警级别" rules={[{ required: true }]}>
                <Select options={SEVERITY_OPTIONS.map(s => ({ value: s.value, label: s.label }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item shouldUpdate={(p, c) => p.rule_type !== c.rule_type} noStyle>
            {({ getFieldValue }) => {
              const t = getFieldValue('rule_type');
              if (t === 'static') {
                return (
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item name="min" label="下限值"><InputNumber style={{ width: '100%' }} step={0.1} placeholder="可选" /></Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="max" label="上限值"><InputNumber style={{ width: '100%' }} step={0.1} placeholder="可选" /></Form.Item>
                    </Col>
                  </Row>
                );
              }
              if (t === 'spc') {
                return (
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item name="mean" label="均值 μ" rules={[{ required: true }]}><InputNumber style={{ width: '100%' }} step={0.1} /></Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="std" label="标准差 σ" rules={[{ required: true }]}><InputNumber style={{ width: '100%' }} step={0.01} min={0} /></Form.Item>
                    </Col>
                  </Row>
                );
              }
              return <Text type="secondary">该规则类型的条件配置开发中</Text>;
            }}
          </Form.Item>
        </Form>
      </Modal>
      </div>
    </>
  );
}
