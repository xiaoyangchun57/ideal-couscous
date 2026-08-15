function rolesForUser(user) {
  const value = user || {};
  if (Array.isArray(value.roles) && value.roles.length) return value.roles;
  return value.role ? [value.role] : [];
}

function canReview(user) {
  const roles = rolesForUser(user);
  return roles.includes('admin') || roles.includes('reviewer');
}

function loadReviewTodoCount(user, loadPending) {
  if (!canReview(user)) return Promise.resolve(0);
  return Promise.resolve()
    .then(() => loadPending())
    .then(rows => (Array.isArray(rows) ? rows.length : 0));
}

module.exports = { rolesForUser, canReview, loadReviewTodoCount };
