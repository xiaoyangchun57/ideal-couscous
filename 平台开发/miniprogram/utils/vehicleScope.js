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
  // Return authority is a server fact. Local dates and text cannot safely
  // represent plan completion, replacement vehicles, or restricted returns.
  return !!(item && item.can_return);
}

module.exports = { myVehicleQuery, isReturnedUse, activeUseFromRows, effectiveVehicleCanReturn };
