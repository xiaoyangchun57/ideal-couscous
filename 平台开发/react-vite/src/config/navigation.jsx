import {
  AlertOutlined,
  AuditOutlined,
  BarChartOutlined,
  CameraOutlined,
  CarOutlined,
  DashboardOutlined,
  EnvironmentOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  ScheduleOutlined,
  TeamOutlined,
  ToolOutlined,
} from '@ant-design/icons';

export const roleLabels = {
  admin: '系统管理员',
  manager: '运维主管',
  operator: '运维人员',
  inspector: '审核员',
  reviewer: '审核员',
  viewer: '只读用户',
};

export const routeMeta = {
  '/': { title: '信息中心', group: '工作台', icon: <DashboardOutlined /> },
  '/alerts': { title: '预警中心', group: '监控告警', icon: <AlertOutlined /> },
  '/analysis': { title: '统计分析', group: '监控告警', icon: <BarChartOutlined /> },
  '/workorders': { title: '工单管理', group: '运维作业', icon: <FileTextOutlined /> },
  '/plan-schedules': { title: '计划调度', group: '运维作业', icon: <ScheduleOutlined /> },
  '/equipment': { title: '设备管理', group: '运维作业', icon: <ToolOutlined /> },
  '/vehicles': { title: '车辆管理', group: '运维作业', icon: <CarOutlined /> },
  '/reagents': { title: '试剂主数据', group: '运维作业', icon: <ExperimentOutlined /> },
  '/reports': { title: '异常上报', group: '运维作业', icon: <AlertOutlined /> },
  '/audit': { title: '待办审核', group: '审核中心', icon: <AuditOutlined /> },
  '/batch-review': { title: '照片审核', group: '审核中心', icon: <CameraOutlined /> },
  '/evaluation': { title: '人员评估', group: '审核中心', icon: <BarChartOutlined /> },
  '/sites': { title: '站点管理', group: '基础资料', icon: <EnvironmentOutlined /> },
  '/archive': { title: '影像档案', group: '基础资料', icon: <FolderOpenOutlined /> },
  '/users': { title: '人员管理', group: '系统管理', icon: <TeamOutlined /> },
};

const allowed = (roles, role) => !roles || roles.includes(role);

// 导航按职责裁剪；接口权限仍由后端校验，避免把审核员带进资源调度等非本职流程。
const pageRoles = {
  '/alerts': ['admin', 'manager', 'reviewer', 'inspector'],
  '/analysis': ['admin', 'manager', 'reviewer', 'inspector'],
  '/workorders': ['admin', 'manager', 'operator'],
  '/plan-schedules': ['admin', 'manager', 'operator'],
  '/equipment': ['admin', 'manager'],
  '/vehicles': ['admin', 'manager'],
  '/reagents': ['admin', 'manager'],
  '/reports': ['admin', 'manager', 'operator'],
  '/audit': ['admin', 'manager', 'reviewer', 'inspector'],
  '/batch-review': ['admin', 'manager', 'reviewer', 'inspector'],
  '/evaluation': ['admin', 'manager', 'reviewer', 'inspector'],
  '/sites': ['admin', 'manager', 'reviewer', 'inspector'],
  '/archive': ['admin', 'manager', 'reviewer', 'inspector'],
  '/users': ['admin'],
};

export function getNavigation(role) {
  const groups = [
    {
      key: 'workspace',
      label: '工作台',
      children: ['/'],
    },
    {
      key: 'monitoring',
      label: '监控告警',
      children: ['/alerts', '/analysis'],
    },
    {
      key: 'operations',
      label: '运维作业',
      children: ['/workorders', '/plan-schedules', '/equipment', '/vehicles', '/reagents', '/reports'],
    },
    {
      key: 'review',
      label: '审核中心',
      roles: ['admin', 'manager', 'reviewer', 'inspector'],
      children: ['/audit', '/batch-review', '/evaluation'],
    },
    {
      key: 'assets',
      label: '基础资料',
      children: ['/sites', '/archive'],
    },
    {
      key: 'system',
      label: '系统管理',
      roles: ['admin'],
      children: ['/users'],
    },
  ];

  return groups
    .filter((group) => allowed(group.roles, role))
    .map((group) => {
      const children = group.children.filter((path) => allowed(pageRoles[path], role));
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

export function getSearchablePages(role) {
  return Object.entries(routeMeta)
    .filter(([path]) => {
      return allowed(pageRoles[path], role);
    })
    .map(([path, meta]) => ({ type: '页面', title: meta.title, subtitle: meta.group, path }));
}
