function selectExecutionSite(packages, selectedPlanId, preferredSiteId) {
  const list = Array.isArray(packages) ? packages : [];
  const preferredSiteKey = preferredSiteId == null ? null : String(preferredSiteId);
  const selectedPackage = list.find(
    (pkg) => selectedPlanId != null && String(pkg.plan_id) === String(selectedPlanId),
  );
  const selectedPackageAtSite = selectedPackage && preferredSiteKey != null
    && (selectedPackage.sites || []).some((site) => String(site.site_id) === preferredSiteKey)
    ? selectedPackage
    : null;
  const preferredPackage = preferredSiteKey != null
    ? list.find((pkg) => (pkg.sites || []).some((site) => String(site.site_id) === preferredSiteKey))
    : null;
  const currentPackage = selectedPackageAtSite
    || preferredPackage
    || selectedPackage
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

function inspectionPhotoProgress(categories) {
  let required = 0;
  let current = 0;
  (Array.isArray(categories) ? categories : []).forEach((category) => {
    (category.items || []).forEach((item) => {
      const itemRequired = Math.max(0, Number(item.required_photos) || 0);
      required += itemRequired;
      if (item.evidence_status === 'supplement_required') return;
      if (item.evidence_status === 'effective' && Number(item.review_status || 0) === 2) {
        current += itemRequired;
        return;
      }
      if (item.effective_evidence_count != null) {
        current += Math.max(0, Number(item.effective_evidence_count) || 0);
        return;
      }
      let legacyPhotos = [];
      try { legacyPhotos = item.photo_urls ? JSON.parse(item.photo_urls) : []; } catch (error) { legacyPhotos = []; }
      current += Array.isArray(legacyPhotos) ? legacyPhotos.length : 0;
    });
  });
  return { req: required, taken: current, missing: Math.max(0, required - current) };
}

module.exports = { selectExecutionSite, photoRequirement, inspectionPhotoProgress };
