function selectExecutionSite(packages, selectedPlanId, preferredSiteId) {
  const list = Array.isArray(packages) ? packages : [];
  const preferredSiteKey = preferredSiteId == null ? null : String(preferredSiteId);
  const preferredPackage = preferredSiteId
    ? list.find((pkg) => (pkg.sites || []).some((site) => String(site.site_id) === preferredSiteKey))
    : null;
  const currentPackage = preferredPackage
    || list.find((pkg) => selectedPlanId != null && String(pkg.plan_id) === String(selectedPlanId))
    || list[0]
    || null;
  const sites = currentPackage ? currentPackage.sites || [] : [];
  const site = sites.find((item) => String(item.site_id) === preferredSiteKey)
    || sites[0]
    || null;
  return { currentPackage, site };
}

function photoRequirement(required, remotePhotos, localPhotos) {
  const expected = Math.max(0, Number(required) || 0);
  const captured = Math.max(0, Number(remotePhotos) || 0) + Math.max(0, Number(localPhotos) || 0);
  const missing = Math.max(0, expected - captured);
  return { required: expected, captured, missing, ready: missing === 0 };
}

module.exports = { selectExecutionSite, photoRequirement };
