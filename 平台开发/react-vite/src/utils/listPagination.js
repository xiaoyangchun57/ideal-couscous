export function listLastPage(total, pageSize) {
  const safeTotal = Math.max(0, Number(total) || 0);
  const safePageSize = Math.max(1, Number(pageSize) || 1);
  return Math.max(1, Math.ceil(safeTotal / safePageSize));
}

export function visiblePageRowCount(dataLength, currentPage, pageSize) {
  const safeLength = Math.max(0, Number(dataLength) || 0);
  const safePage = Math.max(1, Number(currentPage) || 1);
  const safePageSize = Math.max(1, Number(pageSize) || 1);
  if (safeLength <= safePageSize) return safeLength;
  return Math.max(0, Math.min(safePageSize, safeLength - ((safePage - 1) * safePageSize)));
}
