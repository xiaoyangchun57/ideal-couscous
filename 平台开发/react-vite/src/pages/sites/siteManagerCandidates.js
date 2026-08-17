export function filterSiteManagerCandidates(rows) {
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (row?.status !== 'active') return false;
    const roles = Array.isArray(row.roles) && row.roles.length
      ? row.roles
      : [row?.role];
    return roles.includes('operator');
  });
}
