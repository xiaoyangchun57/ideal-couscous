import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Form, Image, Input, Modal, Popconfirm, Select, Space, Tag, Tooltip, Typography, message,
} from 'antd';
import { CheckOutlined, EyeOutlined, InboxOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../../services/api';
import { useAuth } from '../../hooks/useAuth';
import { useTheme } from '../../hooks/useTheme';
import { reportStatusMap } from '../../services/constants';
import { filterSelectWidth } from '../../services/pageStyles';
import WorkspacePage, {
  FilterField, TableLongText, ToolbarMeta, WorkspaceEmpty, WorkspaceTable, WorkspaceToolbar,
} from '../../components/WorkspacePage';

const { Text } = Typography;

const REPORT_TYPE = {
  sensory: '感官异常',
  equipment: '设备异常',
  environment: '环境异常',
  violation: '违规操作',
  pollution: '污染事件',
};

function reportPhotos(record) {
  if (Array.isArray(record?.photo_urls)) return record.photo_urls.filter(Boolean);
  try { return JSON.parse(record?.photo_urls || '[]').filter(Boolean); } catch { return []; }
}

export default function ReportsPage() {
  const { user } = useAuth();
  const { tokens } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const [allReports, setAllReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [evidenceRecord, setEvidenceRecord] = useState(null);
  const [verifyTarget, setVerifyTarget] = useState(null);
  const [verifySaving, setVerifySaving] = useState(false);
  const [archiveSavingId, setArchiveSavingId] = useState(null);
  const [verifyForm] = Form.useForm();
  const roles = user?.roles?.length ? user.roles : [user?.role];
  const canManage = roles.includes('admin');
  const filterStatus = searchParams.get('status') || '';
  const filterType = searchParams.get('type') || '';

  const updateQuery = useCallback((patch) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getStrict('/manual-reports');
      setAllReports(Array.isArray(data) ? data : []);
      setLoadError('');
    } catch (error) {
      setLoadError(error.message || '异常闭环加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const list = useMemo(() => allReports.filter((record) => (
    (!filterStatus || record.status === filterStatus)
    && (!filterType || record.report_type === filterType)
  )), [allReports, filterStatus, filterType]);

  const statusSummary = useMemo(() => Object.keys(reportStatusMap).map((status) => ({
    status,
    ...reportStatusMap[status],
    count: allReports.filter((item) => item.status === status).length,
  })).filter((item) => item.count > 0), [allReports]);

  const openVerify = useCallback((record) => {
    verifyForm.resetFields();
    setVerifyTarget(record);
  }, [verifyForm]);

  const submitVerify = useCallback(async () => {
    if (!verifyTarget || verifySaving) return;
    try {
      const values = await verifyForm.validateFields();
      setVerifySaving(true);
      await api.postStrict(`/manual-reports/${verifyTarget.id}/verify`, { note: values.note.trim() });
      message.success(`已核实“${verifyTarget.site_name || '异常上报'}”，关联工单继续处置`);
      setVerifyTarget(null);
      await load();
    } catch (error) {
      if (!error?.errorFields) message.error(`核实失败：${error.message}`);
    } finally {
      setVerifySaving(false);
    }
  }, [load, verifyForm, verifySaving, verifyTarget]);

  const archive = useCallback(async (record) => {
    if (archiveSavingId) return;
    setArchiveSavingId(record.id);
    try {
      await api.postStrict(`/manual-reports/${record.id}/archive`, {});
      message.success(`“${record.site_name || '异常上报'}”已归档`);
      await load();
    } catch (error) {
      message.error(`归档失败：${error.message}`);
    } finally {
      setArchiveSavingId(null);
    }
  }, [archiveSavingId, load]);

  const columns = useMemo(() => [
    {
      title: '异常', key: 'context', width: 180, ellipsis: true, render: (_, record) => <>
        <Tag color="orange">{REPORT_TYPE[record.report_type] || record.report_type || '异常'}</Tag>
        <Text strong ellipsis style={{ display: 'block', marginTop: 4 }}>{record.site_name || '未关联站点'}</Text>
      </>,
    },
    { title: '现场描述', dataIndex: 'description', width: 320,
      render: (value) => <TableLongText value={value} /> },
    {
      title: '现场证据', key: 'evidence', width: 108, render: (_, record) => {
        const photos = reportPhotos(record);
        return photos.length
          ? <Button aria-label={`查看${record.site_name || '异常上报'}异常上报#${record.id}的${photos.length}张现场照片`} size="small" icon={<EyeOutlined />} onClick={() => setEvidenceRecord(record)}>照片 {photos.length}</Button>
          : <Text type="secondary">缺失</Text>;
      },
    },
    {
      title: '上报信息', key: 'reported', width: 160, render: (_, record) => <>
        <Text>{record.reporter_name || '—'}</Text>
        <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{record.reported_at || '—'}</Text>
        {record.gps_lat && <Tooltip title={`定位：${Number(record.gps_lat).toFixed(5)}, ${Number(record.gps_lng).toFixed(5)}`}><Text type="secondary" style={{ fontSize: 12 }}>已附定位</Text></Tooltip>}
      </>,
    },
    {
      title: '关联工单', dataIndex: 'order_no', width: 150, render: (value, record) => value
        ? <Link aria-label={`查看${record.site_name || '异常上报'}的关联工单${value}`} to={`/workorders?search=${encodeURIComponent(value)}`}>{value}</Link>
        : <Text type="secondary">未生成</Text>,
    },
    {
      title: '闭环状态', dataIndex: 'status', width: 138, render: (value) => {
        const status = reportStatusMap[value] || { label: value || '未知', color: 'default' };
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
    ...(canManage ? [{
      title: '操作', key: 'actions', width: 118, fixed: 'right', render: (_, record) => <Space size={4}>
        {record.status === 'dispatched' && <Button aria-label={`核实${record.site_name || '异常上报'}异常上报#${record.id}`} size="small" icon={<CheckOutlined />} onClick={() => openVerify(record)}>核实</Button>}
        {record.status === 'resolved' && <Popconfirm
          title={`归档“${record.site_name || '异常上报'}”？`}
          description="归档后该记录退出待办，但仍保留在历史记录中，且无法在本页撤销。"
          okText="确认归档"
          cancelText="取消"
          onConfirm={() => archive(record)}
        >
          <Button aria-label={`归档${record.site_name || '异常上报'}异常上报#${record.id}`} size="small" icon={<InboxOutlined />} loading={archiveSavingId === record.id}>归档</Button>
        </Popconfirm>}
        {!['dispatched', 'resolved'].includes(record.status) && <Text type="secondary">—</Text>}
      </Space>,
    }] : []),
  ], [archive, archiveSavingId, canManage, openVerify]);

  const hasFilters = Boolean(filterStatus || filterType);

  return (
    <WorkspacePage
      title="异常闭环"
      subtitle="核实现场异常证据，跟踪派生工单直至归档"
      statusItems={statusSummary.map((item) => ({ key: item.status, label: item.label, value: item.count }))}
      toolbar={<WorkspaceToolbar actions={<Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}>
        <FilterField label="状态">
          <Select aria-label="按异常闭环状态筛选" value={filterStatus || undefined} onChange={(value) => updateQuery({ status: value || '' })} placeholder="全部状态" allowClear style={{ width: filterSelectWidth }}
            options={Object.entries(reportStatusMap).map(([key, value]) => ({ value: key, label: value.label }))} />
        </FilterField>
        <FilterField label="异常类型">
          <Select aria-label="按异常类型筛选" value={filterType || undefined} onChange={(value) => updateQuery({ type: value || '' })} placeholder="全部类型" allowClear style={{ width: filterSelectWidth }}
            options={Object.entries(REPORT_TYPE).map(([key, value]) => ({ value: key, label: value }))} />
        </FilterField>
        {hasFilters && <ToolbarMeta label="当前结果">已筛选 {list.length} 条</ToolbarMeta>}
      </WorkspaceToolbar>}
    >
      {loadError && allReports.length > 0 && <Alert
        type="warning"
        showIcon
        message="列表未更新"
        description={`${loadError}。当前保留上次成功加载的 ${allReports.length} 条记录。`}
        action={<Button size="small" onClick={load}>重新加载</Button>}
        style={{ marginBottom: 8 }}
      />}
      {loadError && !loading && allReports.length === 0 ? (
        <WorkspaceEmpty type="error" description={`${loadError}，当前不能判断是否无记录。`} onRefresh={load} />
      ) : (
        <WorkspaceTable
          rowKey="id"
          columns={columns}
          dataSource={list}
          loading={loading}
          onRefresh={load}
          pagination={list.length > 10 ? { pageSize: 10, size: 'small', showSizeChanger: false } : false}
          emptyType={hasFilters ? 'filtered' : 'empty'}
          scroll={{ x: canManage ? 1050 : 900, y: 'calc(100vh - 390px)', scrollToFirstRowOnChange: true }}
        />
      )}

      <Modal
        open={!!verifyTarget}
        title={verifyTarget ? `核实异常 · ${verifyTarget.site_name || '未关联站点'}` : '核实异常'}
        okText="确认核实"
        cancelText="取消"
        onOk={submitVerify}
        onCancel={() => { if (!verifySaving) setVerifyTarget(null); }}
        confirmLoading={verifySaving}
        destroyOnHidden
      >
        {verifyTarget && <>
          <Text type="secondary">核实只确认上报内容与现场证据，不会替代工单处置或直接关单。</Text>
          <div style={{ marginTop: 12, padding: 12, border: `1px solid ${tokens.colorBorder}`, borderRadius: 6, background: tokens.colorFillAlter }}>
            <Text strong>{REPORT_TYPE[verifyTarget.report_type] || '现场异常'}</Text>
            <Text style={{ display: 'block', marginTop: 4 }}>{verifyTarget.description || '未填写现场描述'}</Text>
          </div>
          <Form form={verifyForm} layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item name="note" label="核实说明" rules={[
              { required: true, whitespace: true, message: '请填写核实依据或结论' },
              { max: 500, message: '核实说明不能超过 500 字' },
            ]}>
              <Input.TextArea rows={4} maxLength={500} showCount placeholder="例如：已核对现场照片和上报描述，情况属实，继续按关联工单处置。" />
            </Form.Item>
          </Form>
        </>}
      </Modal>

      <Modal
        open={!!evidenceRecord}
        title={evidenceRecord ? `现场证据 · ${evidenceRecord.site_name || '异常上报'}` : '现场证据'}
        footer={null}
        onCancel={() => setEvidenceRecord(null)}
        width={760}
        destroyOnHidden
      >
        <Image.PreviewGroup>
          <Space wrap size={12}>
            {evidenceRecord && reportPhotos(evidenceRecord).map((url, index) => (
              <Image key={url} width={180} height={135} style={{ objectFit: 'cover', borderRadius: 6 }} src={url} alt={`${evidenceRecord.site_name || '异常上报'}现场照片 ${index + 1}`} />
            ))}
          </Space>
        </Image.PreviewGroup>
      </Modal>
    </WorkspacePage>
  );
}
