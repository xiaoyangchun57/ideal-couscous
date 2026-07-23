import { useEffect, useMemo, useState } from 'react';
import { Empty, Input, List, Modal, Spin, Tag, Typography } from 'antd';
import { EnvironmentOutlined, FileSearchOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useTheme } from '../hooks/useTheme';
import { getSearchablePages } from '../config/navigation';

const { Text } = Typography;
const resultTypeLabels = {
  页面: '页面',
  站点: '站点',
  工单: '工单',
  设备: '设备',
};

function asArray(payload, keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

export default function GlobalSearch({ open, onClose }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { tokens } = useTheme();
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [records, setRecords] = useState([]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    let active = true;
    setLoading(true);
    Promise.all([api.get('/sites'), api.get('/workorders'), api.get('/devices')])
      .then(([siteData, workorderData, deviceData]) => {
        if (!active) return;
        const sites = asArray(siteData, ['sites', 'data']).map((item) => ({
          type: '站点',
          title: item.name || item.site_name || `站点 ${item.id}`,
          subtitle: item.code || item.site_code || item.type_name || '站点资料',
          path: `/sites?archive=${item.id}`,
          icon: <EnvironmentOutlined />,
        }));
        const workorders = asArray(workorderData, ['workorders', 'items', 'data']).map((item) => ({
          type: '工单',
          title: item.title || item.content || item.description || `工单 ${item.id}`,
          subtitle: item.order_no || item.work_order_no || item.site_name || '工单详情',
          path: `/workorders?search=${encodeURIComponent(item.order_no || item.work_order_no || item.id)}`,
          icon: <FileSearchOutlined />,
        }));
        const devices = asArray(deviceData, ['devices', 'items', 'data']).map((item) => ({
          type: '设备',
          title: item.name || item.device_name || `设备 ${item.id}`,
          subtitle: item.code || item.device_code || item.site_name || '设备台账',
          path: `/equipment?search=${encodeURIComponent(item.code || item.device_code || item.name || item.id)}`,
          icon: <ToolOutlined />,
        }));
        setRecords([...sites, ...workorders, ...devices]);
      })
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [open]);

  const results = useMemo(() => {
    const pages = getSearchablePages(user?.role).map((item) => ({ ...item, icon: <SearchOutlined /> }));
    const source = [...pages, ...records];
    const keyword = query.trim().toLowerCase();
    if (!keyword) return pages.slice(0, 10);
    return source
      .filter((item) => `${item.title} ${item.subtitle || ''} ${item.type}`.toLowerCase().includes(keyword))
      .slice(0, 20);
  }, [query, records, user?.role]);

  const openResult = (item) => {
    onClose();
    navigate(item.path);
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width={620}
      title="全局搜索"
      destroyOnHidden
      styles={{ body: { paddingTop: 8 } }}
    >
      <Input
        autoFocus
        allowClear
        size="large"
        prefix={<SearchOutlined style={{ color: tokens.colorTextTertiary }} />}
        placeholder="搜索页面、站点、工单或设备"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div style={{ minHeight: 280, maxHeight: 440, overflowY: 'auto', marginTop: 12 }}>
        <Spin spinning={loading && records.length === 0}>
          {results.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配结果" style={{ paddingTop: 64 }} />
          ) : (
            <List
              dataSource={results}
              renderItem={(item) => (
                <List.Item
                  className="global-search-result"
                  onClick={() => openResult(item)}
                  style={{ cursor: 'pointer', padding: '10px 12px', borderRadius: 6 }}
                >
                  <List.Item.Meta
                    avatar={<span style={{ color: tokens.colorPrimary, fontSize: 16 }}>{item.icon}</span>}
                    title={<Text strong>{item.title}</Text>}
                    description={item.subtitle}
                  />
                  <Tag bordered={false}>{resultTypeLabels[item.type] || '其他'}</Tag>
                </List.Item>
              )}
            />
          )}
        </Spin>
      </div>
      <div style={{ color: tokens.colorTextTertiary, fontSize: 12, paddingTop: 10, borderTop: `1px solid ${tokens.colorBorderSecondary}` }}>
        输入关键词筛选，点击结果直接前往
      </div>
    </Modal>
  );
}
