function positiveInteger(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export async function resolveReworkScheduleId(reworkPlanValue, loadInspectionPlan) {
  const reworkPlanId = positiveInteger(reworkPlanValue);
  if (!reworkPlanId) return null;
  const inspectionPlan = await loadInspectionPlan(reworkPlanId);
  return positiveInteger(inspectionPlan?.plan_schedule_id);
}

export function replaceReworkWithSchedule(searchParams, scheduleId) {
  const next = new URLSearchParams(searchParams);
  next.delete('rework_plan');
  const normalizedScheduleId = positiveInteger(scheduleId);
  if (normalizedScheduleId) next.set('schedule', String(normalizedScheduleId));
  else next.delete('schedule');
  return next;
}

export function mergePlanEditSites(siteRows, schedule, authorizedIds = null) {
  const permitted = authorizedIds instanceof Set ? authorizedIds : null;
  const currentSites = Array.isArray(siteRows) ? siteRows : siteRows?.sites || [];
  const byId = new Map(currentSites
    .filter(site => !permitted || permitted.has(Number(site.id)))
    .map(site => [Number(site.id), {
      ...site,
      id: Number(site.id),
      historical_dates: [],
      is_historical_ended: false,
    }]));
  const baseline = schedule?.previous_plan_data && typeof schedule.previous_plan_data === 'object'
    ? schedule.previous_plan_data
    : schedule?.plan_data || {};
  const historicalDates = new Map();
  Object.entries(baseline).forEach(([date, day]) => {
    (Array.isArray(day?.sites) ? day.sites : []).forEach(rawSiteId => {
      const siteId = Number(rawSiteId);
      if (!Number.isInteger(siteId) || siteId <= 0 || (permitted && !permitted.has(siteId))) return;
      if (!historicalDates.has(siteId)) historicalDates.set(siteId, new Set());
      historicalDates.get(siteId).add(date);
    });
  });
  historicalDates.forEach((dates, siteId) => {
    const current = byId.get(siteId);
    if (current) {
      current.historical_dates = [...dates].sort();
      return;
    }
    const archived = schedule?.site_map?.[siteId] || schedule?.site_map?.[String(siteId)] || {};
    byId.set(siteId, {
      ...archived,
      id: siteId,
      name: archived.name || `站点 #${siteId}`,
      historical_dates: [...dates].sort(),
      is_historical_ended: true,
    });
  });
  return [...byId.values()];
}

export function planEditSitesForDate(sites, date) {
  return (sites || []).filter(site => !site.is_historical_ended
    || (site.historical_dates || []).includes(date));
}
