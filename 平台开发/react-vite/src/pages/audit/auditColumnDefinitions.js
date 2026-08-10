const PLAN_COLUMNS = ['plan', 'executor', 'route_sites', 'vehicle', 'spare_parts', 'submit_time', 'action'];
const VEHICLE_COLUMNS = ['applicant_vehicle', 'use_time', 'destination_reason', 'related_work', 'submit_time', 'action'];
const PARTS_COLUMNS = ['part', 'request_type', 'request', 'submit_time', 'action'];
const DEFAULT_COLUMNS = ['content', 'site', 'photos', 'submit_time', 'action'];
const ADMIN_TABS = ['data', 'inspection', 'plan', 'workorder', 'parts', 'vehicle', 'photo'];
const REVIEWER_TABS = ['data', 'inspection', 'workorder', 'photo'];

export function getAuditAllowedTabs(roles) {
  const currentRoles = new Set((Array.isArray(roles) ? roles : [roles]).filter(Boolean));
  if (currentRoles.has('admin')) return ADMIN_TABS;
  if (currentRoles.has('reviewer')) return REVIEWER_TABS;
  return [];
}

export function getAuditColumnProfile(sourceTypes = []) {
  if (sourceTypes.length !== 1) return 'default';
  if (sourceTypes[0] === 'plan_schedule') return 'plan';
  if (sourceTypes[0] === 'vehicle_application') return 'vehicle';
  if (['parts_request', 'spare_part_request'].includes(sourceTypes[0])) return 'parts';
  return 'default';
}

export function getAuditColumnKeys(sourceTypes = []) {
  const profile = getAuditColumnProfile(sourceTypes);
  if (profile === 'plan') return PLAN_COLUMNS;
  if (profile === 'vehicle') return VEHICLE_COLUMNS;
  if (profile === 'parts') return PARTS_COLUMNS;
  return DEFAULT_COLUMNS;
}
