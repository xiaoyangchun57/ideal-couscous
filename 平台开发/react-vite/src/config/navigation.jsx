import {
  AlertOutlined,
  AuditOutlined,
  BarChartOutlined,
  CarOutlined,
  DashboardOutlined,
  EnvironmentOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  ScheduleOutlined,
  TeamOutlined,
  ToolOutlined,
} from '@ant-design/icons';

export const roleLabels = {
  admin: '系统管理员',
  operator: '运维人员',
  reviewer: '审核员',
};

export const routeMeta = {
  '/': { title: '运营驾驶舱', group: '运营总览', icon: <DashboardOutlined /> },
  '/alerts': { title: '告警与事件', group: '运营总览', icon: <AlertOutlined /> },
  '/analysis': { title: '数据分析', group: '查询与分析', icon: <BarChartOutlined /> },
  '/workorders': { title: '工单', group: '任务闭环', icon: <FileTextOutlined /> },
  '/plan-schedules': { title: '巡检计划', group: '任务闭环', icon: <ScheduleOutlined /> },
  '/audit': { title: '统一审核', group: '任务闭环', icon: <AuditOutlined /> },
  '/sites': { title: '站点全景', group: '站点与资产', icon: <EnvironmentOutlined /> },
  '/equipment': { title: '设备与物资', group: '站点与资产', icon: <ToolOutlined /> },
  '/vehicles': { title: '车辆', group: '站点与资产', icon: <CarOutlined /> },
  '/archive': { title: '影像与记录', group: '查询与分析', icon: <FolderOpenOutlined /> },
  '/evaluation': { title: '运营绩效', group: '查询与分析', icon: <BarChartOutlined /> },
  '/users': { title: '人员与权限', group: '系统设置', icon: <TeamOutlined /> },
};

const allowed = (requiredRoles, userRoles) => {
  if (!requiredRoles) return true;
  const roles = Array.isArray(userRoles) ? userRoles : [userRoles];
  return requiredRoles.some((role) => roles.includes(role));
};

// 导航按职责裁剪；接口权限仍由后端校验，避免把审核员带进资源调度等非本职流程。
const pageRoles = {
  '/alerts': ['admin', 'reviewer'],
  '/analysis': ['admin', 'reviewer'],
  '/workorders': ['admin', 'operator'],
  '/plan-schedules': ['admin', 'operator'],
  '/equipment': ['admin'],
  '/vehicles': ['admin'],
  '/audit': ['admin', 'reviewer'],
  '/evaluation': ['admin', 'reviewer'],
  '/sites': ['admin', 'reviewer'],
  '/archive': ['admin', 'reviewer'],
  '/users': ['admin'],
};

export function getNavigation(roles) {
  const groups = [
    {
      key: 'workspace',
      label: '运营总览',
      children: ['/', '/alerts'],
    },
    {
      key: 'monitoring',
      label: '任务闭环',
      children: ['/workorders', '/plan-schedules', '/audit'],
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
      children: ['/users'],
    },
  ];

  return groups
    .filter((group) => allowed(group.roles, roles))
    .map((group) => {
      const children = group.children.filter((path) => allowed(pageRoles[path], roles));
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

export function getSearchablePages(roles) {
  return Object.entries(routeMeta)
    .filter(([path]) => {
      return allowed(pageRoles[path], roles);
    })
    .map(([path, meta]) => ({ type: '页面', title: meta.title, subtitle: meta.group, path }));
}
