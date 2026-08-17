import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag, message } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import WorkspacePage from '../../components/WorkspacePage';
import { api } from '../../services/api';
import {
  inspectionConfigFormValues, inspectionItemAccessState, inspectionItemFormValues,
  inspectionItemRefreshNotice,
} from './inspectionSettingsForm';

const SITE_TYPES = [{ value: 'water_quality', label: '水质监测站' }];
const FREQUENCIES = [
  { value: 'weekly', label: '周检' }, { value: 'monthly', label: '月检' },
  { value: 'quarterly', label: '季检' }, { value: 'yearly', label: '年检' },
];

export default function InspectionSettingsPage() {
  const [templates, setTemplates] = useState([]);
  const [configs, setConfigs] = useState([]);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [templateSaving, setTemplateSaving] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [itemLoading, setItemLoading] = useState(false);
  const [itemError, setItemError] = useState('');
  const [itemSaving, setItemSaving] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [itemOpen, setItemOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [template, setTemplate] = useState(null);
  const [item, setItem] = useState(null);
  const [config, setConfig] = useState(null);
  const [templateForm] = Form.useForm();
  const [itemForm] = Form.useForm();
  const [configForm] = Form.useForm();
  const itemRequestRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ts, cs] = await Promise.all([
        api.getStrict('/inspection-v2/templates'), api.getStrict('/inspection-v2/configs'),
      ]);
      setTemplates(Array.isArray(ts) ? ts : []); setConfigs(Array.isArray(cs) ? cs : []);
    } catch (error) { message.error(error?.message || '检查项配置加载失败'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    load();
    return () => { itemRequestRef.current += 1; };
  }, [load]);

  const loadTemplateItems = useCallback(async templateId => {
    const requestId = ++itemRequestRef.current;
    setItemLoading(true); setItemError('');
    try {
      const rows = await api.getStrict(`/inspection-v2/templates/${templateId}/items`);
      if (requestId !== itemRequestRef.current) return false;
      setItems(Array.isArray(rows) ? rows : []);
      return true;
    } catch (error) {
      if (requestId !== itemRequestRef.current) return false;
      setItems([]);
      setItemError(error?.message || '检查项加载失败');
      return false;
    } finally {
      if (requestId === itemRequestRef.current) setItemLoading(false);
    }
  }, []);

  const closeTemplate = () => {
    if (templateSaving || itemSaving) return;
    itemRequestRef.current += 1;
    setItemLoading(false);
    setTemplateOpen(false);
  };

  const openTemplate = async (row = null) => {
    setTemplate(row); templateForm.resetFields(); setTemplateOpen(true);
    if (row) {
      templateForm.setFieldsValue({ template_name: row.template_name, category: row.category, frequency: row.frequency, description: row.description || '' });
      await loadTemplateItems(row.id);
    } else {
      setItems([]); setItemError(''); setItemLoading(false);
      templateForm.setFieldsValue({ frequency: 'monthly' });
    }
  };

  const saveTemplate = async () => {
    if (templateSaving) return;
    const values = await templateForm.validateFields();
    setTemplateSaving(true);
    try {
      if (template) await api.putStrict(`/inspection-v2/templates/${template.id}`, values);
      else await api.postStrict('/inspection-v2/templates', { ...values, items: [] });
      setTemplateOpen(false); await load(); message.success('模板已保存');
    } catch (error) { message.error(error?.message || '模板保存失败'); }
    finally { setTemplateSaving(false); }
  };

  const removeTemplate = async row => {
    try { await api.deleteStrict(`/inspection-v2/templates/${row.id}`); await load(); message.success('模板已删除'); }
    catch (error) { message.error(error?.message || '模板删除失败'); }
  };

  const openItem = row => {
    if (!template || !inspectionItemAccessState({
      loading: itemLoading, error: itemError, saving: itemSaving,
    }).canMutate) return;
    setItem(row || null); itemForm.resetFields();
    itemForm.setFieldsValue(inspectionItemFormValues(row, items.length));
    setItemOpen(true);
  };
  const saveItem = async () => {
    if (itemSaving) return;
    const values = await itemForm.validateFields();
    setItemSaving(true);
    try {
      const base = `/inspection-v2/templates/${template.id}/items`;
      if (item) await api.putStrict(`${base}/${item.id}`, values); else await api.postStrict(base, values);
      setItemOpen(false); message.success('检查项已保存');
      const refreshed = await loadTemplateItems(template.id);
      const notice = inspectionItemRefreshNotice(refreshed);
      if (notice) message.warning(notice);
      await load();
    } catch (error) { message.error(error?.message || '检查项保存失败'); }
    finally { setItemSaving(false); }
  };
  const removeItem = async row => {
    if (itemSaving) return;
    setItemSaving(true);
    try { await api.deleteStrict(`/inspection-v2/templates/${template.id}/items/${row.id}`); setItems(current => current.filter(x => x.id !== row.id)); await load(); message.success('检查项已删除'); }
    catch (error) { message.error(error?.message || '检查项删除失败'); }
    finally { setItemSaving(false); }
  };

  const openConfig = row => {
    setConfig(row || null); configForm.resetFields();
    configForm.setFieldsValue(inspectionConfigFormValues(row));
    setConfigOpen(true);
  };
  const saveConfig = async () => {
    if (configSaving) return;
    const values = await configForm.validateFields();
    setConfigSaving(true);
    try {
      if (config) await api.putStrict(`/inspection-v2/configs/${config.id}`, values); else await api.postStrict('/inspection-v2/configs', values);
      setConfigOpen(false); await load(); message.success('站点类型匹配已保存');
    } catch (error) { message.error(error?.message || '匹配配置保存失败'); }
    finally { setConfigSaving(false); }
  };
  const removeConfig = async row => {
    try { await api.deleteStrict(`/inspection-v2/configs/${row.id}`); await load(); message.success('匹配配置已删除'); }
    catch (error) { message.error(error?.message || '匹配配置删除失败'); }
  };

  return <WorkspacePage title="检查项设置" subtitle="管理员维护模板和站点类型匹配；模板变更只影响后续新建计划，历史计划保留快照。"
    primaryAction={<Space><Button icon={<ReloadOutlined />} onClick={load}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openTemplate()}>新增模板</Button></Space>}>
    <Alert type="info" showIcon message="检查项设置只影响后续新建计划，不会改写已生成的历史计划。" style={{ marginBottom: 12 }} />
    <Card title="检查项模板" size="small"><Table rowKey="id" loading={loading} pagination={false} dataSource={templates} columns={[
      { title: '模板名称', dataIndex: 'template_name' }, { title: '分类', dataIndex: 'category' },
      { title: '频次', dataIndex: 'frequency', render: value => FREQUENCIES.find(x => x.value === value)?.label || value }, { title: '检查项数', dataIndex: 'item_count' },
      { title: '操作', render: (_, row) => <Space><Button size="small" icon={<EditOutlined />} onClick={() => openTemplate(row)}>编辑</Button><Popconfirm title="删除模板及检查项？" onConfirm={() => removeTemplate(row)}><Button size="small" danger icon={<DeleteOutlined />} aria-label="删除模板" /></Popconfirm></Space> },
    ]} /></Card>
    <Card title="站点类型匹配" size="small" style={{ marginTop: 12 }} extra={<Button icon={<PlusOutlined />} onClick={() => openConfig()}>新增匹配</Button>}><Table rowKey="id" loading={loading} pagination={false} dataSource={configs} columns={[
      { title: '站点类型', dataIndex: 'site_type', render: value => SITE_TYPES.find(x => x.value === value)?.label || value }, { title: '模板', dataIndex: 'template_name' }, { title: '检查项数', dataIndex: 'item_count' }, { title: '状态', dataIndex: 'is_active', render: value => value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag> },
      { title: '操作', render: (_, row) => <Space><Button size="small" icon={<EditOutlined />} onClick={() => openConfig(row)}>编辑</Button><Popconfirm title="删除匹配配置？" onConfirm={() => removeConfig(row)}><Button size="small" danger icon={<DeleteOutlined />} aria-label="删除匹配" /></Popconfirm></Space> },
    ]} /></Card>

    <Modal title={template ? '编辑检查项模板' : '新增检查项模板'} open={templateOpen} onCancel={closeTemplate} onOk={saveTemplate} confirmLoading={templateSaving} okText="保存" destroyOnHidden width={760}><Form form={templateForm} layout="vertical"><Space.Compact block><Form.Item name="template_name" label="模板名称" rules={[{ required: true }]} style={{ flex: 1 }}><Input /></Form.Item><Form.Item name="category" label="模板分类" rules={[{ required: true }]} style={{ flex: 1, marginLeft: 8 }}><Input /></Form.Item><Form.Item name="frequency" label="适用频次" rules={[{ required: true }]} style={{ flex: 1, marginLeft: 8 }}><Select options={FREQUENCIES} /></Form.Item></Space.Compact><Form.Item name="description" label="模板说明"><Input.TextArea rows={2} /></Form.Item></Form>{template && <Card size="small" title="模板检查项" extra={<Button type="primary" size="small" icon={<PlusOutlined />} disabled={!inspectionItemAccessState({ loading: itemLoading, error: itemError, saving: itemSaving }).canMutate} onClick={() => openItem()}>新增检查项</Button>}>{itemError && <Alert type="error" showIcon message={itemError} action={<Button size="small" onClick={() => loadTemplateItems(template.id)}>重试</Button>} style={{ marginBottom: 8 }} />}<Table size="small" rowKey="id" loading={itemLoading} pagination={false} dataSource={items} columns={[{ title: '名称', dataIndex: 'item_name' }, { title: '分类', dataIndex: 'category' }, { title: '频次级别', dataIndex: 'frequency_level' }, { title: '照片', dataIndex: 'photo_required', render: value => value ? '需要' : '可选' }, { title: '操作', render: (_, row) => <Space><Button size="small" icon={<EditOutlined />} disabled={!inspectionItemAccessState({ loading: itemLoading, error: itemError, saving: itemSaving }).canMutate} onClick={() => openItem(row)}>编辑</Button><Popconfirm title="删除检查项？" disabled={!inspectionItemAccessState({ loading: itemLoading, error: itemError, saving: itemSaving }).canMutate} onConfirm={() => removeItem(row)}><Button size="small" danger disabled={!inspectionItemAccessState({ loading: itemLoading, error: itemError, saving: itemSaving }).canMutate} icon={<DeleteOutlined />} aria-label="删除检查项" /></Popconfirm></Space> }]} /></Card>}</Modal>
    <Modal title={item ? '编辑检查项' : '新增检查项'} open={itemOpen} onCancel={() => !itemSaving && setItemOpen(false)} onOk={saveItem} confirmLoading={itemSaving} okText="保存" destroyOnHidden><Form form={itemForm} layout="vertical"><Form.Item name="item_name" label="检查项名称" rules={[{ required: true }]}><Input /></Form.Item><Space.Compact block><Form.Item name="category" label="分类" style={{ flex: 1 }}><Input /></Form.Item><Form.Item name="frequency_level" label="频次级别" style={{ flex: 1, marginLeft: 8 }}><Select options={[{ value: 'high', label: '高' }, { value: 'mid', label: '中' }, { value: 'low', label: '低' }]} /></Form.Item></Space.Compact><Space.Compact block><Form.Item name="photo_required" label="需要照片" style={{ flex: 1 }}><Select options={[{ value: true, label: '需要' }, { value: false, label: '可选' }]} /></Form.Item><Form.Item name="max_photos" label="照片数量要求" style={{ flex: 1, marginLeft: 8 }}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Space.Compact><Space.Compact block><Form.Item name="need_review" label="需要内容审核" style={{ flex: 1 }}><Select options={[{ value: true, label: '需要' }, { value: false, label: '不需要' }]} /></Form.Item><Form.Item name="sort_order" label="显示顺序" style={{ flex: 1, marginLeft: 8 }}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Space.Compact><Form.Item name="inspection_standard" label="合格范围与检查提醒"><Input.TextArea rows={3} maxLength={500} showCount placeholder="填写现场判断时需要看到的合格范围或检查要求" /></Form.Item></Form></Modal>
    <Modal title="站点类型匹配" open={configOpen} onCancel={() => !configSaving && setConfigOpen(false)} onOk={saveConfig} confirmLoading={configSaving} okText="保存" destroyOnHidden><Form form={configForm} layout="vertical"><Form.Item name="site_type" label="站点类型" rules={[{ required: true }]}><Select options={SITE_TYPES} /></Form.Item><Form.Item name="template_id" label="检查项模板" rules={[{ required: true }]}><Select options={templates.map(x => ({ value: x.id, label: x.template_name }))} /></Form.Item><Form.Item name="device_types" label="适用设备类型"><Select mode="tags" placeholder="可选；不填表示全部设备" /></Form.Item><Form.Item name="is_active" label="启用"><Select options={[{ value: true, label: '启用' }, { value: false, label: '停用' }]} /></Form.Item><Form.Item name="remark" label="备注"><Input.TextArea rows={2} /></Form.Item></Form></Modal>
  </WorkspacePage>;
}
