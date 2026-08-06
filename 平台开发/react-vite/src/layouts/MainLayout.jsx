import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
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
  Segmented,
  Space,
  Spin,
  Typography,
  message,
} from 'antd';
import {
  BellOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  SearchOutlined,
  SunOutlined,
  TeamOutlined,
  EnvironmentOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import GlobalSearch from '../components/GlobalSearch';
import { getNavigation, roleLabels, routeMeta } from '../config/navigation';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useTheme } from '../hooks/useTheme';
import { getNotificationTarget } from '../utils/shellNavigation';
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
  const [notifError, setNotifError] = useState('');
  const [notifView, setNotifView] = useState('current');
  const [markingAllRead, setMarkingAllRead] = useState(false);

  const selectedKey = `/${location.pathname.split('/')[1] || ''}`;
  const currentMeta = routeMeta[selectedKey] || { group: '页面导航', title: '页面不存在' };
  const isCockpit = selectedKey === '/';
  const cockpitView = new URLSearchParams(location.search).get('view') === 'sites' ? 'sites' : 'operations';
  const navItems = useMemo(() => getNavigation(user?.roles || [user?.role]), [user?.role, user?.roles]);
  const userRoles = useMemo(() => user?.roles?.length ? user.roles : [user?.role], [user?.role, user?.roles]);
  const userRoleLabel = useMemo(() => {
    const roles = user?.roles?.length ? user.roles : [user?.role];
    return roles.filter(Boolean).map((role) => roleLabels[role] || role).join(' / ') || '用户';
  }, [user?.role, user?.roles]);

  const changeCockpitView = (view) => {
    navigate(view === 'sites' ? '/?view=sites' : '/', { replace: true });
  };

  const loadNotifs = useCallback(async () => {
    if (document.hidden) return;
    setNotifLoading(true);
    try {
      const data = await api.getStrict('/notifications');
      setNotifs(data?.notifications || []);
      setUnread(data?.unread_count || 0);
      setNotifError('');
    } catch (error) {
      setNotifError(error.message || '通知加载失败');
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
    if (markingAllRead || unread === 0) return;
    setMarkingAllRead(true);
    try {
      await api.putStrict('/notifications/read-all');
      setNotifs((items) => items.map((item) => ({ ...item, is_read: 1 })));
      setUnread(0);
      message.success('当前通知已全部标为已读');
    } catch (error) {
      message.error(error.message || '通知状态更新失败，请重试');
    } finally {
      setMarkingAllRead(false);
    }
  };

  const markOneRead = async (id) => {
    await api.putStrict(`/notifications/${id}/read`);
    setNotifs((items) => items.map((item) => item.id === id ? { ...item, is_read: 1 } : item));
    setUnread((count) => Math.max(0, count - 1));
  };

  const openNotification = async (item) => {
    if (item.is_stale) return;
    if (!item.is_read) {
      try { await markOneRead(item.id); } catch (error) {
        message.error(error.message || '通知状态更新失败，请重试');
      }
    }
    const target = getNotificationTarget(item, userRoles);
    if (target) {
      setNotifOpen(false);
      navigate(target);
    } else {
      message.info('该通知没有可用的网页详情，请按通知内容处理');
    }
  };

  const handleNotifOpenChange = (open) => {
    setNotifOpen(open);
    if (open) loadNotifs();
  };

  const currentNotifs = useMemo(() => notifs.filter((item) => !item.is_stale), [notifs]);
  const historyNotifs = useMemo(() => notifs.filter((item) => item.is_stale), [notifs]);
  const visibleNotifs = notifView === 'history' ? historyNotifs : currentNotifs;

  const notificationContent = (
    <div className="notification-panel">
      <div className="notification-panel__header" style={{ borderColor: tokens.colorBorder }}>
        <div>
          <Text strong>通知中心</Text>
          <div style={{ fontSize: 12, color: tokens.colorTextTertiary }}>{unread} 条未读</div>
        </div>
        <Button type="link" size="small" onClick={markAllRead} loading={markingAllRead} disabled={unread === 0}>全部已读</Button>
      </div>
      <Segmented
        className="notification-panel__switch"
        block
        size="small"
        value={notifView}
        onChange={setNotifView}
        options={[
          { value: 'current', label: `当前通知 ${currentNotifs.length}` },
          { value: 'history', label: `历史通知 ${historyNotifs.length}` },
        ]}
      />
      <div className="notification-panel__body">
        <Spin spinning={notifLoading && notifs.length === 0}>
          {notifError && (
            <Alert
              type="warning"
              showIcon
              message="通知加载失败"
              description={notifs.length ? '当前保留上次成功加载的通知，未读数量可能不是最新结果。' : '当前不能判断是否有新通知，请检查网络后重试。'}
              action={<Button size="small" onClick={loadNotifs}>重试</Button>}
              style={{ margin: 12 }}
            />
          )}
          {visibleNotifs.length === 0 && !notifLoading ? (
            <Empty description={notifView === 'history' ? '暂无历史通知' : '暂无当前通知'} image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 28 }} />
          ) : (
            <List
              dataSource={visibleNotifs}
              renderItem={(item) => {
                const interactive = !item.is_stale;
                return <List.Item
                  className={`notification-item${interactive ? ' notification-item--interactive' : ''}`}
                  role={interactive ? 'button' : undefined}
                  tabIndex={interactive ? 0 : undefined}
                  aria-label={interactive ? `通知：${item.title}` : undefined}
                  onClick={interactive ? () => openNotification(item) : undefined}
                  onKeyDown={interactive ? (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      openNotification(item);
                    }
                  } : undefined}
                  style={{ opacity: item.is_stale ? 0.64 : item.is_read ? 0.72 : 1 }}
                >
                  <List.Item.Meta
                    title={<span style={{ fontSize: 13, fontWeight: item.is_read ? 400 : 600 }}>
                      {item.is_stale && <Badge status="default" text="历史" style={{ marginRight: 6 }} />}
                      {item.title}
                    </span>}
                    description={(
                      <div>
                        <div style={{ color: tokens.colorTextSecondary }}>{item.content}</div>
                        {item.is_stale && item.current_status && (
                          <div style={{ color: tokens.colorTextSecondary, marginTop: 3 }}>
                            当前计划状态：{{ approved: '已通过', rejected: '已退回', archived: '已归档', deleted: '已删除' }[item.current_status] || item.current_status}
                          </div>
                        )}
                        <div style={{ color: tokens.colorTextTertiary, marginTop: 3 }}>{item.created_at}</div>
                      </div>
                    )}
                  />
                  {!item.is_read && <Badge status="processing" />}
                </List.Item>;
              }}
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
    onClick: async () => {
      await logout();
      navigate('/login', { replace: true });
    },
  }];

  return (
    <Layout className="app-shell" style={{ background: tokens.colorBgLayout }}>
      <a className="skip-link" href="#main-content">跳到主内容</a>
      <div className="sr-only" role="status" aria-live="polite">{currentMeta.title}页面已打开</div>
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
              <strong>水质智慧运维</strong>
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
          <div className={`page-location${isCockpit ? ' page-location--cockpit' : ''}`}>
            {isCockpit ? (
              <Segmented
                className="header-view-switcher"
                size="small"
                value={cockpitView}
                onChange={changeCockpitView}
                options={[
                  { value: 'operations', label: '今日运维', icon: <TeamOutlined /> },
                  { value: 'sites', label: '站点监测', icon: <EnvironmentOutlined /> },
                ]}
              />
            ) : (
              <Breadcrumb
                items={[
                  { title: currentMeta.group },
                  { title: currentMeta.title },
                ]}
              />
            )}
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
              onOpenChange={handleNotifOpenChange}
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
                <span className="user-chip__name">{user?.real_name || user?.name || user?.login_name || user?.username || '--'}</span>
                <span className="user-chip__role">{userRoleLabel}</span>
              </button>
            </Dropdown>
          </Space>
        </Header>

        <Content className="app-content" id="main-content" role="main" tabIndex={-1}>
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
