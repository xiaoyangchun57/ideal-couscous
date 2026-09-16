export const ALERT_DATE_RANGE_OPTIONS = [
  { label: '今日', value: 'today' },
  { label: '本周', value: 'week' },
  { label: '本月', value: 'month' },
  { label: '全部历史', value: 'all' },
];

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
