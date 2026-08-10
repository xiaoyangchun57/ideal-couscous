function planScheduleDetailUrl(sourceId) {
  const id = String(sourceId == null ? '' : sourceId).trim();
  return /^[1-9]\d*$/.test(id)
    ? '/pages/plan-detail/plan-detail?id=' + id
    : '/pages/plan/plan';
}

module.exports = { planScheduleDetailUrl };
