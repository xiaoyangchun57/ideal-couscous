export function auditItemKey(item) {
  return item ? `${item.source_type || ''}:${item.id ?? ''}` : '';
}

export function nextQueueItem(previousQueue, currentItem, refreshedItems, getKey = auditItemKey) {
  const currentKey = getKey(currentItem);
  const currentIndex = previousQueue.findIndex((item) => getKey(item) === currentKey);
  if (currentIndex < 0) return null;

  const refreshedByKey = new Map(refreshedItems.map((item) => [getKey(item), item]));
  return previousQueue
    .slice(currentIndex + 1)
    .map((item) => refreshedByKey.get(getKey(item)))
    .find(Boolean) || null;
}
