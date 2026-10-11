import { HoverGifIcon } from '../components/HoverGifIcon';

export const roleLabels = {
  admin: '系统管理员',
  operator: '运维人员',
  reviewer: '审核员',
};

const NavIcon = ({ name }) => <HoverGifIcon name={name} size={18} folder="nav" mode="parent" style={{ marginRight: 10 }} />;

export const routeMeta = {
  '/': { title: '运营驾驶舱', group: '运营总览', icon: <NavIcon name="globe" /> },
  '/alerts': { title: '告警与事件', group: '运营总览', icon: <NavIcon name="warning-triangle" /> },
  '/analysis': { title: '数据分析', group: '查询与分析', icon: <NavIcon name="bar-chart" /> },
  '/workorders': { title: '工单', group: '任务闭环', icon: <NavIcon name="article" /> },
  '/plan-schedules': { title: '巡检计划', group: '任务闭环', icon: <NavIcon name="calendar-dots" /> },
  '/inspection-settings': { title: '检查项设置', group: '系统管理', icon: <NavIcon name="book-open" /> },
  '/reports': { title: '异常闭环', group: '任务闭环', icon: <NavIcon name="check-circle-empty" /> },
  '/audit': { title: '统一审核', group: '任务闭环', icon: <NavIcon name="list-rules" /> },
  '/sites': { title: '站点全景', group: '站点与资产', icon: <NavIcon name="location-pin" /> },
  '/sites/data-access': { title: '接入观察', group: '站点与资产', icon: <NavIcon name="api" /> },
  '/equipment': { title: '设备与物资', group: '站点与资产', icon: <NavIcon name="wrench" /> },
  '/vehicles': { title: '车辆', group: '站点与资产', icon: <NavIcon name="car" /> },
  '/archive': { title: '影像与记录', group: '查询与分析', icon: <NavIcon name="image-mountain" /> },
  '/evaluation': { title: '运营绩效', group: '查询与分析', icon: <NavIcon name="trending-up" /> },
  '/users': { title: '人员与权限', group: '系统设置', icon: <NavIcon name="avatar" /> },
  '/reagents': { title: '试剂主数据', group: '系统设置', icon: <NavIcon name="layers" /> },
};

const allowed = (requiredRoles, userRoles) => {
  if (!requiredRoles) return true;
  const roles = Array.isArray(userRoles) ? userRoles : [userRoles];
  return requiredRoles.some((role) => roles.includes(role));
};

// 导航按职责裁剪；接口权限仍由后端校验，避免把审核员带进资源调度等非本职流程。
export const pageRoles = {
  '/alerts': ['admin', 'reviewer'],
  '/analysis': ['admin', 'reviewer'],
  '/workorders': ['admin', 'operator'],
  '/plan-schedules': ['admin', 'operator'],
  '/inspection-settings': ['admin'],
  '/reports': ['admin', 'reviewer'],
  '/equipment': ['admin'],
  '/vehicles': ['admin'],
  '/audit': ['admin', 'reviewer'],
  '/evaluation': ['admin', 'reviewer'],
  '/sites': ['admin', 'reviewer', 'operator'],
  '/sites/data-access': ['admin'],
  '/archive': ['admin', 'reviewer'],
  '/users': ['admin'],
  '/reagents': ['admin'],
};

export function getNavigation(roles, capabilities = {}) {
  const groups = [
    {
      key: 'workspace',
      label: '运营总览',
      children: ['/', '/alerts'],
    },
    {
      key: 'monitoring',
      label: '任务闭环',
      children: ['/workorders', '/plan-schedules', '/reports', '/audit'],
    },
    {
      key: 'operations',
      label: '站点与资产',
      children: ['/sites', '/equipment', '/vehicles'],
    },
    {
      key: 'analysis',
      label: '查询与分析',
      children: ['/analysis', '/archive', '/evaluation'],
    },
    {
      key: 'system',
      label: '系统管理',
      roles: ['admin'],
      children: ['/users', '/reagents', '/inspection-settings'],
    },
  ];

  return groups
    .filter((group) => allowed(group.roles, roles))
    .map((group) => {
      const children = group.children.filter((path) => allowed(pageRoles[path], roles)
        && (path !== '/sites/data-access' || capabilities.station_monitoring_public === true));
      return {
        type: 'group',
        key: group.key,
        label: group.label,
        children: children.map((path) => ({
        key: path,
        icon: routeMeta[path].icon,
        label: routeMeta[path].title,
        })),
      };
    })
    .filter((group) => group.children.length > 0);
}

export function getSearchablePages(roles, capabilities = {}) {
  return Object.entries(routeMeta)
    .filter(([path]) => {
      return allowed(pageRoles[path], roles)
        && (path !== '/sites/data-access' || capabilities.station_monitoring_public === true);
    })
    .map(([path, meta]) => ({ type: '页面', title: meta.title, subtitle: meta.group, path }));
}
