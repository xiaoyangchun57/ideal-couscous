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

export function canApprovePhotoReview(photos, riskAcknowledged, options = {}) {
  return getRiskyPhotoIds(photos, options).length === 0 || Boolean(riskAcknowledged);
}

export function getRiskAcknowledgementLabel(count) {
  return `已核对 ${Number(count) || 0} 张风险影像`;
}
