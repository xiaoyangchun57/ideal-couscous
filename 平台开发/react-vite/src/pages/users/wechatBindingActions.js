export async function finishWechatBindingUnbind({ isCurrentUser, logout, redirectToLogin, refreshUsers }) {
  if (isCurrentUser) {
    await logout();
    redirectToLogin();
    return 'logged_out';
  }
  await refreshUsers();
  return 'refreshed';
}
