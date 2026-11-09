import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Collapse, Image, Space, Spin, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../services/api';
import WorkspacePage from '../../components/WorkspacePage';

const { Text, Title } = Typography;

export default function OriginalInspectionView() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const target = useRef(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const planId = params.get('original_plan');
  const siteId = params.get('original_site');
  const itemId = params.get('original_item');
  const attachmentId = params.get('original_attachment');

  useEffect(() => {
    let cancelled = false;
    const attachmentPath = attachmentId ? `/${encodeURIComponent(attachmentId)}` : '';
    api.getStrict(`/evaluation/original-inspection/${encodeURIComponent(planId)}/${encodeURIComponent(siteId)}/${encodeURIComponent(itemId)}${attachmentPath}`)
      .then(data => { if (!cancelled) setResult(data); })
      .catch(err => { if (!cancelled) setError(err.message || '原巡检加载失败'); });
    return () => { cancelled = true; };
  }, [planId, siteId, itemId, attachmentId]);

  useEffect(() => {
    if (!result) return undefined;
    const timer = setTimeout(() => {
      target.current?.scrollIntoView({ block: 'center' });
      target.current?.focus({ preventScroll: true });
    }, 150);
    return () => clearTimeout(timer);
  }, [result]);

  const groups = new Map();
  (result?.items || []).forEach(item => {
    if (!groups.has(item.site_id)) groups.set(item.site_id, { name: item.site_name, items: [] });
    groups.get(item.site_id).items.push(item);
  });
  return <WorkspacePage title="原巡检记录" toolbar={<Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>{attachmentId ? '返回照片明细' : '返回人员绩效'}</Button>}>
    {error && <Alert type="error" message={error} />}
    {!result && !error && <Spin />}
    {result && <>
      <Title level={5}>{result.plan.plan_name || `巡检 #${result.plan.id}`}</Title>
      <Text type="secondary">执行记录 #{result.plan.id}</Text>
      <Collapse defaultActiveKey={[siteId]} items={[...groups.entries()].map(([id, group]) => ({
        key: String(id), label: group.name || `站点 #${id}`,
        children: <Collapse defaultActiveKey={[itemId]} items={group.items.map(item => ({
          key: String(item.id), label: item.item_name || `检查项 #${item.id}`,
          children: <Space direction="vertical" style={{ width: '100%' }} ref={!attachmentId && Number(item.id) === Number(itemId) ? target : undefined} tabIndex={!attachmentId && Number(item.id) === Number(itemId) ? -1 : undefined}>
            <Text>结果：{item.result === 'normal' ? '正常' : item.result === 'abnormal' ? '异常' : '未填写'}</Text>
            <Text>检查时间：{item.completed_at || item.check_time || '未记录'}</Text>
            <Text>现场说明：{item.remark || '无'}</Text>
            <Image.PreviewGroup>{(item.evidence_attachments || []).map(photo => <div key={photo.id}
              ref={Number(photo.id) === Number(attachmentId) ? target : undefined}
              tabIndex={Number(photo.id) === Number(attachmentId) ? -1 : undefined}
              style={{ display: 'inline-block', padding: 6, margin: 4, border: Number(photo.id) === Number(attachmentId) ? '2px solid #1677ff' : '2px solid transparent' }}>
              <Image src={photo.display_url} width={120} alt={photo.filename || '巡检照片'} />
              <div><Text>{photo.filename || `照片 #${photo.id}`}</Text></div>
              {photo.reject_reason && <div><Text>质量意见：{photo.reject_reason}</Text></div>}
            </div>)}</Image.PreviewGroup>
          </Space>,
        }))} />,
      }))} />
    </>}
  </WorkspacePage>;
}
