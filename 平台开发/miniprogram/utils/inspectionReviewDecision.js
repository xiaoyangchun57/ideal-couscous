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

function getUnqualifiedPhotoIds(photos, rejectedPhotoIds) {
  const rejected = new Set((rejectedPhotoIds || []).map(String));
  return Array.from(new Set((photos || [])
    .filter(photo => photo && photo.id !== undefined && photo.id !== null)
    .filter(photo => !rejected.has(String(photo.id)))
    .filter(photo => photo.evidence_qualification !== 'qualified')
    .map(photo => photo.id)));
}

function groupReviewPhotosByItem(reviewPhotos) {
  const groups = [];
  const groupByKey = new Map();
  (Array.isArray(reviewPhotos) ? reviewPhotos : []).forEach(photo => {
    const source = photo || {};
    const rawItemId = source.item_id;
    const hasItemId = rawItemId !== undefined && rawItemId !== null && String(rawItemId).trim() !== '';
    const itemId = hasItemId ? rawItemId : null;
    const key = hasItemId ? 'item:' + String(rawItemId) : 'unknown';
    let group = groupByKey.get(key);
    if (!group) {
      group = {
        key,
        itemId,
        itemLabel: hasItemId ? '' : '关联检查项暂不可用',
        photos: [],
      };
      groupByKey.set(key, group);
      groups.push(group);
    }
    if (hasItemId && !group.itemLabel && String(source.itemLabel || '').trim()) {
      group.itemLabel = source.itemLabel;
    }
    group.photos.push(source);
  });
  return groups.map(group => Object.assign({}, group, {
    itemLabel: group.itemLabel || '检查项待确认',
  }));
}

module.exports = {
  approveItemIdsForPhotoSelection,
  getRiskyPhotoIds,
  getUnqualifiedPhotoIds,
  groupReviewPhotosByItem,
};
