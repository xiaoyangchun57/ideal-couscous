function approveItemIdsForPhotoSelection(itemIds, attachmentDetails, rejectedPhotoIds) {
  const rejectedPhotos = new Set((rejectedPhotoIds || []).map(String));
  const rejectedItems = new Set((attachmentDetails || [])
    .filter((photo) => rejectedPhotos.has(String(photo.id)) && photo.item_id !== undefined && photo.item_id !== null)
    .map((photo) => String(photo.item_id)));
  return Array.from(new Set(itemIds || [])).filter((itemId) => !rejectedItems.has(String(itemId)));
}

function getRiskyPhotoIds(photos, rejectedPhotoIds) {
  const rejected = new Set((rejectedPhotoIds || []).map(String));
  return Array.from(new Set((photos || [])
    .filter(photo => photo && photo.id !== undefined && photo.id !== null)
    .filter(photo => !rejected.has(String(photo.id)))
    .filter(photo => Boolean(
      photo.is_flagged || photo.flag_reason || photo.flag_rule || photo.duplicate_of_id
    ))
    .map(photo => photo.id)));
}

module.exports = { approveItemIdsForPhotoSelection, getRiskyPhotoIds };
