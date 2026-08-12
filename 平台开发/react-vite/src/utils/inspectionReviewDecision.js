export function approveItemIdsForPhotoSelection(itemIds, attachmentDetails, rejectedPhotoIds) {
  const rejectedPhotos = new Set((rejectedPhotoIds || []).map(String));
  const rejectedItems = new Set((attachmentDetails || [])
    .filter((photo) => rejectedPhotos.has(String(photo.id)) && photo.item_id !== undefined && photo.item_id !== null)
    .map((photo) => String(photo.item_id)));
  return Array.from(new Set(itemIds || [])).filter((itemId) => !rejectedItems.has(String(itemId)));
}

export function photoRejectionNeedsReason(attachmentIds, rejectedPhotoIds) {
  return (attachmentIds || []).length > 0 && (rejectedPhotoIds || []).length > 0;
}

export function canSubmitPhotoReview(attachmentIds, rejectedPhotoIds, comment) {
  return !photoRejectionNeedsReason(attachmentIds, rejectedPhotoIds) || Boolean(String(comment || '').trim());
}

export function getRiskyPhotoIds(photos, options = {}) {
  const includeMissingCaptureTime = Boolean(options.includeMissingCaptureTime);
  const rejectedPhotoIds = new Set((options.rejectedPhotoIds || []).map(String));
  return Array.from(new Set((photos || [])
    .filter((photo) => photo && photo.id !== undefined && photo.id !== null)
    .filter((photo) => !rejectedPhotoIds.has(String(photo.id)))
    .filter((photo) => Boolean(
      photo.is_flagged
      || photo.flag_reason
      || photo.flag_rule
      || photo.duplicate_of_id
      || (includeMissingCaptureTime && !photo.taken_at),
    ))
    .map((photo) => photo.id)));
}

export function getUnqualifiedPhotoIds(photos, rejectedPhotoIds = []) {
  const rejected = new Set((rejectedPhotoIds || []).map(String));
  return Array.from(new Set((photos || [])
    .filter((photo) => photo && photo.id !== undefined && photo.id !== null)
    .filter((photo) => !rejected.has(String(photo.id)))
    .filter((photo) => photo.evidence_qualification !== 'qualified')
    .map((photo) => photo.id)));
}

export function failedApprovalPhotoIds(photos, rejectedPhotoIds, failedPhotoIds) {
  const rejected = new Set((rejectedPhotoIds || []).map(String));
  const failed = new Set((failedPhotoIds || []).map(String));
  return (photos || []).filter((photo) => photo && !rejected.has(String(photo.id)) && failed.has(String(photo.id))).map((photo) => photo.id);
}

export function reviewPhotoGridStyle(count) {
  return { display: 'grid', gridTemplateColumns: count <= 1 ? 'minmax(0, 1fr)' : 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 };
}
