function completeLoginSession(actions, token, user, sites) {
  actions.setAuth(token, user, sites);
  if (actions.bindWechat) actions.bindWechat();
  try {
    if (actions.refreshBadge) {
      const refresh = actions.refreshBadge();
      if (refresh && typeof refresh.catch === 'function') refresh.catch(() => {});
    }
  } catch (error) {
    // Badge refresh is best-effort and must never block a successful login.
  }
  actions.navigateHome();
}

module.exports = { completeLoginSession };
