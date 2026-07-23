import { useState, useEffect } from 'react';
import { Upload, Button, Select, Input, Space, message } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import {
  ATTACHMENT_SOURCE_OPTIONS, ATTACHMENT_CATEGORY_OPTIONS,
} from '../services/constants';

/**
 * 统一附件/影像上传组件（智能收口）
 * - 走 /api/upload/attachment：自动按水印文字/文件名/描述做分类识别，
 *   命中 photo_requirements 且配置需审核时自动进入「影像审核」队列；
 *   流程外类型（试剂/车辆/养护）未配置需求项，仅归档不参与审核。
 * - 业务页可预置 sourceType / siteId / category 实现「就近上传」，影像档案则全选。
 */
export default function AttachmentUpload({
  sourceType, sourceId, siteId, category,
  buttonText = '上传资料',
  accept = '.png,.jpg,.jpeg,.gif,.webp',
  maxCount = 5, multiple = true, onSuccess,
}) {
  const { user } = useAuth();
  const [localSource, setLocalSource] = useState(sourceType || undefined);
  const [localSite, setLocalSite] = useState(siteId || undefined);
  const [localCategory, setLocalCategory] = useState(category || undefined);
  const [description, setDescription] = useState('');
  const [sites, setSites] = useState([]);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    api.get('/sites').then(d => { if (Array.isArray(d)) setSites(d); });
  }, []);

  const curSource = sourceType || localSource;
  const curSite = siteId || localSite;
  const curCategory = category || localCategory;

  const data = {
    source_type: curSource || '',
    source_id: sourceId || 0,
    site_id: curSite || '',
    category: curCategory || '',
    description: description || '',
    uploader_id: user?.id || 1,
    uploader_name: user?.name || user?.username || '运维人员',
  };

  const beforeUpload = () => {
    if (!curSource) { message.warning('请先选择来源类型'); return Upload.LIST_IGNORE; }
    return true;
  };

  const onChange = (info) => {
    if (info.file.status === 'uploading') { setUploading(true); return; }
    if (info.file.status === 'done') {
      setUploading(false);
      message.success(`「${info.file.name}」上传成功`);
      setDescription('');
      if (onSuccess) onSuccess(info.file.response);
    } else if (info.file.status === 'error') {
      setUploading(false);
      message.error(`「${info.file.name}」上传失败，请重试`);
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {!sourceType && (
        <Select placeholder="来源类型（必选）" style={{ width: '100%' }}
          value={localSource} onChange={setLocalSource}
          options={ATTACHMENT_SOURCE_OPTIONS} />
      )}
      {!siteId && (
        <Select placeholder="关联站点（选填）" showSearch allowClear style={{ width: '100%' }}
          optionFilterProp="label" value={localSite} onChange={setLocalSite}
          options={sites.map(s => ({ value: s.id, label: s.name }))} />
      )}
      {!category && (
        <Select placeholder="分类（选填，留空由系统按水印智能识别）" allowClear style={{ width: '100%' }}
          value={localCategory} onChange={setLocalCategory}
          options={ATTACHMENT_CATEGORY_OPTIONS} />
      )}
      <Input placeholder="描述 / 水印说明（选填，用于智能识别分类）"
        value={description} onChange={e => setDescription(e.target.value)} />
      <Upload
        action="/api/upload/attachment"
        data={data}
        accept={accept}
        listType="text"
        multiple={multiple}
        maxCount={maxCount}
        beforeUpload={beforeUpload}
        onChange={onChange}
        disabled={uploading}
      >
        <Button icon={<UploadOutlined />} loading={uploading}>{buttonText}</Button>
      </Upload>
    </Space>
  );
}
