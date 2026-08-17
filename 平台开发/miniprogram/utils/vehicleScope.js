function myVehicleQuery(scope, user, options) {
  const userId = Number(user && user.id);
  return Object.assign({}, options || {}, {
    scope,
    // Never let a missing local profile turn an administrator's "我的" view into all users' data.
    applicant_id: Number.isInteger(userId) && userId > 0 ? userId : 0,
  });
}

function isReturnedUse(item) {
  return !!(item && (item.returned_at || item.status === 'returned'));
}

function activeUseFromRows(rows) {
  return (rows || []).find(item => !isReturnedUse(item)) || null;
}

function effectiveVehicleCanReturn(item, today) {
  if (item && item.can_return !== undefined) return !!item.can_return;
  const tripEnd = item && item.end_at ? String(item.end_at).slice(0, 10) : '';
  const isPlanTrip = String((item && item.reason) || '').indexOf('巡检计划#') >= 0;
  return !isPlanTrip || !tripEnd || tripEnd <= today || (item && item.vehicle_status === 'restricted');
}

module.exports = { myVehicleQuery, isReturnedUse, activeUseFromRows, effectiveVehicleCanReturn };
