const normalizedRoles = (roles) => new Set((Array.isArray(roles) ? roles : [roles]).filter(Boolean));

const hasAnyRole = (roles, allowed) => {
  const current = normalizedRoles(roles);
  return allowed.some((role) => current.has(role));
};

export function buildGlobalSearchPath(item) {
  const identifier = encodeURIComponent(item?.identifier ?? item?.id ?? '');
  switch (item?.type) {
    case 'site':
      return identifier ? `/sites?archive=${identifier}` : '/sites';
    case 'workorder':
      return identifier ? `/workorders?search=${identifier}` : '/workorders';
    case 'device':
      return identifier ? `/equipment?q=${identifier}` : '/equipment';
    default:
      return item?.path || '/';
  }
}

export function getNotificationTarget(item, roles) {
  const sourceId = item?.source_id ? encodeURIComponent(item.source_id) : '';
  switch (item?.source_type) {
    case 'workorder':
      return hasAnyRole(roles, ['admin', 'operator'])
        ? (sourceId ? `/workorders?search=${sourceId}` : '/workorders')
        : null;
    case 'workorder_review':
      return hasAnyRole(roles, ['admin', 'reviewer'])
        ? (sourceId ? `/audit?tab=workorder&order=${sourceId}` : '/audit?tab=workorder')
        : null;
    case 'inspection':
    case 'inspection_review':
      return hasAnyRole(roles, ['admin', 'operator']) ? '/plan-schedules' : null;
    case 'photo_review':
      return hasAnyRole(roles, ['admin', 'reviewer']) ? '/audit?tab=photo' : null;
    case 'data_review':
      return hasAnyRole(roles, ['admin', 'reviewer']) ? '/audit?tab=data' : null;
    case 'parts_request':
      return hasAnyRole(roles, ['admin']) ? '/audit?tab=parts' : null;
    case 'spare_part_request':
      return hasAnyRole(roles, ['admin']) ? '/audit?tab=spareparts' : null;
    case 'vehicle_application':
      return hasAnyRole(roles, ['admin']) ? '/audit?tab=vehicle' : null;
    case 'plan_schedule':
      return hasAnyRole(roles, ['admin', 'operator'])
        ? (sourceId ? `/plan-schedules?schedule=${sourceId}` : '/plan-schedules')
        : null;
    case 'user_work_transfer':
      return hasAnyRole(roles, ['admin', 'operator']) ? '/?view=operations' : null;
    case 'manual_report':
      return hasAnyRole(roles, ['admin', 'reviewer']) ? '/reports' : null;
    case 'alert':
      return hasAnyRole(roles, ['admin', 'reviewer']) ? '/alerts' : null;
    case 'reagent_qc':
      return hasAnyRole(roles, ['admin']) ? '/equipment' : null;
    default:
      return null;
  }
}
