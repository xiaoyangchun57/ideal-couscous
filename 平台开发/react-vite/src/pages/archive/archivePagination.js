export const ARCHIVE_PAGE_SIZE = 20;
export const ARCHIVE_PAGE_SIZES = [20, 50, 100];

export function archivePageNumber(value) {
  const page = Number(value);
  return Number.isSafeInteger(page) && page > 0 ? page : 1;
}

export function archivePageSize(value) {
  const pageSize = Number(value);
  return ARCHIVE_PAGE_SIZES.includes(pageSize) ? pageSize : ARCHIVE_PAGE_SIZE;
}

export function archiveLastPage(total, pageSize = ARCHIVE_PAGE_SIZE) {
  return Math.max(1, Math.ceil(total / archivePageSize(pageSize)));
}

export function archiveNavigationState(params) {
  const filters = new URLSearchParams();
  for (const key of ['keyword', 'site_id', 'business_type', 'date_from', 'date_to']) {
    if (params.get(key)) filters.set(key, params.get(key));
  }
  const page = archivePageNumber(params.get('page'));
  const pageSize = archivePageSize(params.get('page_size'));
  const archiveMode = params.get('scope') === 'history' ? 'history' : 'current';
  const request = new URLSearchParams(filters);
  request.set('page', String(page));
  request.set('limit', String(pageSize));
  if (archiveMode === 'current') request.set('current_archive', '1');
  else { request.set('history_archive', '1'); request.set('include_voided', '1'); }
  return {
    page, pageSize, archiveMode, view: params.get('view') === 'grid' ? 'grid' : 'table',
    filterQuery: filters.toString(), requestQuery: request.toString(),
  };
}
