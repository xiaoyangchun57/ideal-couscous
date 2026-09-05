let unreadRevision = 0;

function invalidateUnreadCount() {
  unreadRevision += 1;
  return unreadRevision;
}

function currentUnreadRevision() {
  return unreadRevision;
}

function authoritativeUnreadCount(response) {
  if (!response || response.count === null || response.count === undefined || response.count === '') return null;
  const count = Number(response.count);
  return Number.isInteger(count) && count >= 0 ? count : null;
}

module.exports = {
  authoritativeUnreadCount,
  currentUnreadRevision,
  invalidateUnreadCount,
};
