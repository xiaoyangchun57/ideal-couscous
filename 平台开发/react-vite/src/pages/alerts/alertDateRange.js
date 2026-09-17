export const ALERT_DATE_RANGE_OPTIONS = [
  { label: '今日', value: 'today' },
  { label: '本周', value: 'week' },
  { label: '本月', value: 'month' },
  { label: '已加载历史', value: 'all' },
];

function nonNegativeInteger(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0;
}

export function alertListCoverage(loadedCount, reportedTotal) {
  const loaded = nonNegativeInteger(loadedCount);
  const total = Math.max(loaded, nonNegativeInteger(reportedTotal));
  const missing = Math.max(0, total - loaded);
  return {
    loaded,
    total,
    missing,
    truncated: missing > 0,
    truncationText: missing > 0
      ? `统计共 ${total} 条，当前列表已加载 ${loaded} 条；另有 ${missing} 条暂不可在 Web 打开，需后端分页契约支持。`
      : '',
  };
}

export function isAlertInDateRange(dateStr, range, now = new Date()) {
  if (!dateStr || !range || range === 'all') return true;
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return true;
  if (range === 'today') {
    return date.getFullYear() === now.getFullYear()
      && date.getMonth() === now.getMonth()
      && date.getDate() === now.getDate();
  }
  if (range === 'week') {
    const weekAgo = new Date(now);
    weekAgo.setDate(weekAgo.getDate() - 7);
    return date >= weekAgo;
  }
  if (range === 'month') {
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth();
  }
  return true;
}
