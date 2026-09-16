export const ARCHIVE_PAGE_SIZE = 100;

export function archivePageNumber(value) {
  const page = Number(value);
  return Number.isSafeInteger(page) && page > 0 ? page : 1;
}

export function archiveLastPage(total) {
  return Math.max(1, Math.ceil(total / ARCHIVE_PAGE_SIZE));
}

export function archiveNavigationState(params) {
  const filters = new URLSearchParams();
  for (const key of ['keyword', 'site_id', 'business_type', 'date_from', 'date_to']) {
    if (params.get(key)) filters.set(key, params.get(key));
  }
  const page = archivePageNumber(params.get('page'));
  const archiveMode = params.get('scope') === 'history' ? 'history' : 'current';
  const request = new URLSearchParams(filters);
  request.set('page', String(page));
  request.set('limit', String(ARCHIVE_PAGE_SIZE));
  if (archiveMode === 'current') request.set('current_archive', '1');
  else { request.set('history_archive', '1'); request.set('include_voided', '1'); }
  return {
    page, archiveMode, view: params.get('view') === 'grid' ? 'grid' : 'table',
    filterQuery: filters.toString(), requestQuery: request.toString(),
  };
}
