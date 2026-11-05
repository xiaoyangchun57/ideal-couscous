export function toggleArchiveDownloadItem(selectedIds, attachmentId, checked) {
  const ids = new Set((selectedIds || []).map(Number).filter(Number.isSafeInteger));
  const id = Number(attachmentId);
  if (!Number.isSafeInteger(id) || id <= 0) return [...ids];
  if (checked) ids.add(id);
  else ids.delete(id);
  return [...ids];
}

export function toggleArchiveDownloadPage(selectedIds, pageIds, checked) {
  const ids = new Set((selectedIds || []).map(Number).filter(Number.isSafeInteger));
  for (const rawId of pageIds || []) {
    const id = Number(rawId);
    if (!Number.isSafeInteger(id) || id <= 0) continue;
    if (checked) ids.add(id);
    else ids.delete(id);
  }
  return [...ids];
}

export function archiveDownloadFilterSelection(applied, archiveMode) {
  const filters = { archive_mode: archiveMode === 'history' ? 'history' : 'current' };
  for (const key of ['keyword', 'site_id', 'business_type']) {
    if (applied?.[key] !== undefined && applied?.[key] !== null && applied?.[key] !== '') {
      filters[key] = applied[key];
    }
  }
  if (applied?.date_range?.[0] && applied?.date_range?.[1]) {
    filters.date_from = applied.date_range[0].format('YYYY-MM-DD');
    filters.date_to = applied.date_range[1].format('YYYY-MM-DD');
  }
  return { mode: 'filter', filters };
}

export function archiveDownloadFailureSummary(failures) {
  const rows = Array.isArray(failures) ? failures : [];
  if (!rows.length) return '';
  const counts = new Map();
  for (const row of rows) {
    const reason = row?.reason || '状态已变化';
    counts.set(reason, (counts.get(reason) || 0) + 1);
  }
  return [...counts.entries()].map(([reason, count]) => `${reason} ${count} 条`).join('；');
}

export function archiveDownloadScopeKey(archiveMode, filterQuery) {
  return `${archiveMode === 'history' ? 'history' : 'current'}|${filterQuery || ''}`;
}

export function nextArchiveDownloadSelectionRequest(previous, scopeKey) {
  return { id: Number(previous?.id || 0) + 1, scopeKey };
}

export function isCurrentArchiveDownloadSelectionRequest(request, current, currentScopeKey, mounted = true) {
  return Boolean(mounted
    && request?.id === current?.id
    && request?.scopeKey === current?.scopeKey
    && request?.scopeKey === currentScopeKey);
}
