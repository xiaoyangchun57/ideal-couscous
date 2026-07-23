import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Breadcrumb,
  Button,
  Dropdown,
  Drawer,
  Empty,
  Layout,
  List,
  Menu,
  Popover,
  Space,
  Spin,
  Typography,
} from 'antd';
import {
  BellOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  SearchOutlined,
  SunOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import GlobalSearch from '../components/GlobalSearch';
import { getNavigation, roleLabels, routeMeta } from '../config/navigation';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useTheme } from '../hooks/useTheme';
import './AppShell.css';

const { Header, Content, Sider } = Layout;
const { Text } = Typography;

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { isDark, toggleTheme, tokens } = useTheme();
  const [collapsed, setCollapsed] = useState(() => window.innerWidth < 1180);
  const [isCompact, setIsCompact] = useState(() => window.innerWidth < 640);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifs, setNotifs] = useState([]);
  const [unread, setUnread] = useState(0);
  const [notifLoading, setNotifLoading] = useState(false);

  const selectedKey = `/${location.pathname.split('/')[1] || ''}`;
  const currentMeta = routeMeta[selectedKey] || routeMeta['/'];
  const navItems = useMemo(() => getNavigation(user?.role), [user?.role]);

  const loadNotifs = useCallback(async () => {
    if (document.hidden) return;
    setNotifLoading(true);
    try {
      const data = await api.get('/notifications');
      setNotifs(data?.notifications || []);
      setUnread(data?.unread_count || 0);
    } finally {
      setNotifLoading(false);
    }
  }, []);

  useEffect(() => {
    loadNotifs();
    const timer = setInterval(loadNotifs, 30000);
    const onVisibilityChange = () => { if (!document.hidden) loadNotifs(); };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [loadNotifs]);

  useEffect(() => {
    const onShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', onShortcut);
    return () => window.removeEventListener('keydown', onShortcut);
  }, []);

  useEffect(() => {
    const onResize = () => setIsCompact(window.innerWidth < 640);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const markAllRead = async () => {
    await api.put('/notifications/read-all');
    setNotifs((items) => items.map((item) => ({ ...item, is_read: 1 })));
    setUnread(0);
  };

  const markOneRead = async (id) => {
    await api.put(`/notifications/${id}/read`);
    setNotifs((items) => items.map((item) => item.id === id ? { ...item, is_read: 1 } : item));
    setUnread((count) => Math.max(0, count - 1));
  };

  const notificationContent = (
    <div className="notification-panel">
      <div className="notification-panel__header" style={{ borderColor: tokens.colorBorder }}>
        <div>
          <Text strong>通知中心</Text>
          <div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{unread} 条未读</div>
        </div>
        <Button type="link" size="small" onClick={markAllRead} disabled={unread === 0}>全部已读</Button>
      </div>
      <div className="notification-panel__body">
        <Spin spinning={notifLoading}>
          {notifs.length === 0 ? (
            <Empty description="暂无通知" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 28 }} />
          ) : (
            <List
              dataSource={notifs}
              renderItem={(item) => (
                <List.Item
                  className="notification-item"
                  onClick={() => !item.is_read && markOneRead(item.id)}
                  style={{ opacity: item.is_read ? 0.62 : 1 }}
                >
                  <List.Item.Meta
                    title={<span style={{ fontSize: 13, fontWeight: item.is_read ? 400 : 600 }}>{item.title}</span>}
                    description={(
                      <div>
                        <div style={{ color: tokens.colorTextSecondary }}>{item.content}</div>
                        <div style={{ color: tokens.colorTextTertiary, marginTop: 3 }}>{item.created_at}</div>
                      </div>
                    )}
                  />
                  {!item.is_read && <Badge status="processing" />}
                </List.Item>
              )}
            />
          )}
        </Spin>
      </div>
    </div>
  );

  const userMenuItems = [{
    key: 'logout',
    icon: <LogoutOutlined />,
    label: '退出登录',
    onClick: () => {
      logout();
      navigate('/login');
    },
  }];

  return (
    <Layout className="app-shell" style={{ background: tokens.colorBgLayout }}>
      <Sider
        className="app-sidebar"
        width={220}
        collapsedWidth={72}
        collapsed={collapsed}
        theme={isDark ? 'dark' : 'light'}
        style={{ background: tokens.navBg, borderColor: tokens.colorBorder, display: isCompact ? 'none' : undefined }}
      >
        <button className="brand" type="button" onClick={() => navigate('/')} aria-label="返回信息中心">
          <span className="brand__mark">水</span>
          {!collapsed && (
            <span className="brand__copy">
              <strong>水文智慧运维</strong>
              <small>运营管理平台</small>
            </span>
          )}
        </button>

        <div className="sidebar-menu-wrap">
          <Menu
            mode="inline"
            inlineCollapsed={collapsed}
            selectedKeys={[selectedKey]}
            items={navItems}
            onClick={({ key }) => navigate(key)}
            style={{ background: 'transparent', borderInlineEnd: 0 }}
          />
        </div>

        <div className="sidebar-footer" style={{ borderColor: tokens.colorBorder }}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? '展开导航' : '收起导航'}
          >
            {!collapsed && '收起导航'}
          </Button>
        </div>
      </Sider>

      <Layout className="app-main" style={{ background: tokens.colorBgLayout }}>
        <Header className="app-header" style={{ background: tokens.navBg, borderColor: tokens.colorBorder }}>
          {isCompact && <Button type="text" icon={<MenuUnfoldOutlined />} onClick={() => setMobileNavOpen(true)} aria-label="打开导航" />}
          <div className="page-location">
            <Breadcrumb
              items={[
                { title: currentMeta.group },
                { title: currentMeta.title },
              ]}
            />
          </div>

          <button className="search-trigger" type="button" onClick={() => setSearchOpen(true)} style={{ borderColor: tokens.colorBorder }}>
            <SearchOutlined />
            <span>搜索站点、工单、设备或页面</span>
          </button>

          <Space className="header-actions" size={8}>
            <Popover
              content={notificationContent}
              trigger="click"
              open={notifOpen}
              onOpenChange={setNotifOpen}
              placement="bottomRight"
            >
              <Badge count={unread} size="small" offset={[-2, 2]}>
                <Button type="text" icon={<BellOutlined />} aria-label="通知中心" />
              </Badge>
            </Popover>
            <Button
              type="text"
              icon={isDark ? <SunOutlined /> : <MoonOutlined />}
              onClick={toggleTheme}
              aria-label={isDark ? '切换浅色模式' : '切换深色模式'}
            />
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight" trigger={['click']}>
              <button className="user-chip" type="button" style={{ background: tokens.colorPrimaryBg, borderColor: tokens.colorBorder }}>
                <UserOutlined />
                <span className="user-chip__name">{user?.name || user?.username || '--'}</span>
                <span className="user-chip__role">{roleLabels[user?.role] || '用户'}</span>
              </button>
            </Dropdown>
          </Space>
        </Header>

        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>

      <GlobalSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
      <Drawer title="功能导航" placement="left" width={260} open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)} styles={{ body: { padding: 0, background: tokens.navBg } }}>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={navItems}
          onClick={({ key }) => { navigate(key); setMobileNavOpen(false); }}
          style={{ background: 'transparent', borderInlineEnd: 0 }} />
      </Drawer>
    </Layout>
  );
}
