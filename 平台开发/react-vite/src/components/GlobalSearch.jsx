import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, List, Modal, Spin, Tag, Typography } from 'antd';
import { EnvironmentOutlined, FileSearchOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useTheme } from '../hooks/useTheme';
import { getSearchablePages } from '../config/navigation';
import { buildGlobalSearchPath } from '../utils/shellNavigation';

const { Text } = Typography;
const resultTypeLabels = {
  页面: '页面',
  site: '站点',
  workorder: '工单',
  device: '设备',
};

const resultIcons = {
  site: <EnvironmentOutlined />,
  workorder: <FileSearchOutlined />,
  device: <ToolOutlined />,
};

export default function GlobalSearch({ open, onClose }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { tokens } = useTheme();
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [records, setRecords] = useState([]);
  const [loadError, setLoadError] = useState('');
  const [searchVersion, setSearchVersion] = useState(0);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setRecords([]);
    setLoadError('');
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const keyword = query.trim();
    if (!keyword) {
      setRecords([]);
      setLoadError('');
      setLoading(false);
      return undefined;
    }
    let active = true;
    setRecords([]);
    setLoadError('');
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.getStrict(`/global-search?q=${encodeURIComponent(keyword)}`);
        if (active) setRecords(Array.isArray(data?.results) ? data.results : []);
      } catch (error) {
        if (active) setLoadError(error.message || '业务对象搜索失败');
      } finally {
        if (active) setLoading(false);
      }
    }, 220);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [open, query, searchVersion]);

  const results = useMemo(() => {
    const pages = getSearchablePages(user?.roles || [user?.role]).map((item) => ({ ...item, icon: <SearchOutlined /> }));
    const source = [...pages, ...records.map((item) => ({ ...item, icon: resultIcons[item.type] || <SearchOutlined /> }))];
    const keyword = query.trim().toLowerCase();
    if (!keyword) return pages.slice(0, 10);
    return source
      .filter((item) => `${item.title} ${item.subtitle || ''} ${item.type}`.toLowerCase().includes(keyword))
      .slice(0, 20);
  }, [query, records, user?.role, user?.roles]);

  const openResult = (item) => {
    onClose();
    navigate(item.path || buildGlobalSearchPath(item));
  };

  const activateResult = (event, item) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openResult(item);
    }
  };

  return (
    <Modal
      className="global-search-modal"
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
      <div className="global-search-results">
        <Spin spinning={loading}>
          {loadError && (
            <Alert
              type="warning"
              showIcon
              message="部分搜索结果未加载"
              description="页面入口仍可搜索，站点、工单和设备结果可能不完整。"
              action={<Button size="small" onClick={() => setSearchVersion((value) => value + 1)}>重试</Button>}
              style={{ marginBottom: 10 }}
            />
          )}
          {results.length === 0 && !loading ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配结果" style={{ paddingTop: 64 }} />
          ) : (
            <List
              dataSource={results}
              renderItem={(item) => (
                <List.Item
                  className="global-search-result"
                  role="button"
                  tabIndex={0}
                  aria-label={`${resultTypeLabels[item.type] || item.type}：${item.title}`}
                  onClick={() => openResult(item)}
                  onKeyDown={(event) => activateResult(event, item)}
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
      <div className="global-search-summary" style={{ color: tokens.colorTextTertiary, borderTopColor: tokens.colorBorderSecondary }}>
        {query.trim() ? `找到 ${results.length} 个结果` : `可访问页面 ${results.length} 个`}
      </div>
    </Modal>
  );
}
