function count(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
}

function homeSummary(summary) {
  if (!summary) return null;
  const pending = count(summary.pending_items);
  const rework = count(summary.rework_items);
  return {
    sites: count(summary.total_sites),
    todo: pending + rework,
    workorders: count(summary.pending_workorders),
    alerts: count(summary.pending_alerts) + count(summary.abnormal_items),
  };
}

function homeSite(site) {
  const pending = count(site && site.pending_items);
  const rework = count(site && site.rework_items);
  let todoText = `${pending} 项待检`;
  if (pending && rework) todoText = `待检 ${pending} · 需补拍 ${rework}`;
  else if (rework) todoText = `${rework} 项需补拍`;
  return Object.assign({}, site || {}, {
    pending_items: pending,
    rework_items: rework,
    todo_text: todoText,
  });
}

function homeSiteSelection(site) {
  const value = site || {};
  return {
    siteId: value.site_id == null ? null : value.site_id,
    planId: value.target_plan_id == null ? null : value.target_plan_id,
    itemId: value.target_item_id == null ? null : value.target_item_id,
  };
}

module.exports = { homeSummary, homeSite, homeSiteSelection };
