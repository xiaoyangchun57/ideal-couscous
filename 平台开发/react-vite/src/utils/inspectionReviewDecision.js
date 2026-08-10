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
