const RESOURCE_STATE = {
  arrangement_required: '待重新安排资源',
  pending_approval: '资源待审批',
  ready: '资源已就绪',
};

function buildCheckinPayload(site, planId, coords, checkTime) {
  return {
    site_id: site.id,
    site_name: site.name,
    plan_id: planId,
    check_time: checkTime,
    lat: coords.lat,
    lng: coords.lng,
  };
}

function reworkResourcePresentation(pkg) {
  const state = pkg && pkg.resource_state ? pkg.resource_state : 'ready';
  return {
    isRework: Boolean(pkg && pkg.is_rework),
    state,
    label: RESOURCE_STATE[state] || '资源待处理',
    canRequest: Boolean(pkg && pkg.is_rework && state === 'arrangement_required'),
  };
}

module.exports = { buildCheckinPayload, reworkResourcePresentation };
