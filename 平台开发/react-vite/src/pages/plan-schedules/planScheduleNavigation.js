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
