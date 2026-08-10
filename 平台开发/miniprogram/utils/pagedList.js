function normalizePagedList(response) {
  if (Array.isArray(response)) {
    return { items: response, total: response.length, hasMore: false, page: 1, limit: response.length };
  }
  const body = response || {};
  const items = Array.isArray(body.items) ? body.items : [];
  const total = Number(body.total);
  return {
    items,
    total: Number.isFinite(total) ? total : items.length,
    hasMore: !!body.has_more,
    page: Number(body.page) || 1,
    limit: Number(body.limit) || items.length,
  };
}

function appendPagedItems(existing, response) {
  const page = normalizePagedList(response);
  return Object.assign({}, page, { items: (existing || []).concat(page.items) });
}

function hasMoreFromResponse(response, itemCount, pageSize) {
  if (response && typeof response.has_more === 'boolean') return response.has_more;
  return Number(itemCount || 0) >= Number(pageSize || 1);
}

function appendDistinctById(existing, incoming) {
  const seen = new Set();
  return (existing || []).concat(incoming || []).filter(item => {
    if (!item || item.id === undefined || item.id === null) return true;
    const key = String(item.id);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

module.exports = { normalizePagedList, appendPagedItems, hasMoreFromResponse, appendDistinctById };
