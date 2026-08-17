export const applyPlanVehicleSelection = (current, vehicleId) => {
  if (!current) return current;
  const normalizedVehicleId = vehicleId || null;
  return {
    ...current,
    vehicle_id: normalizedVehicleId,
    vehicle_days: normalizedVehicleId ? current.vehicle_days : {},
    vehicle_exception_reason: '',
  };
};

export const buildPlanValidationPayload = (schedule) => ({
  user_id: schedule?.user_id,
  schedule_type: schedule?.schedule_type,
  period_start: schedule?.period_start,
  period_end: schedule?.period_end,
  plan_data: schedule?.plan_data || {},
  vehicle_id: schedule?.vehicle_id || null,
  vehicle_days: schedule?.vehicle_days || {},
  vehicle_exception_reason: schedule?.vehicle_exception_reason || '',
  exclude_schedule_id: schedule?.id,
});
