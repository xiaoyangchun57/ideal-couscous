const CLEANUP_FACT_DEFINITIONS = [
  ['locations', '现场定位'],
  ['checkins', '签到'],
  ['photos', '照片'],
  ['reviews', '审核'],
  ['attachments', '附件'],
  ['notifications', '通知'],
  ['execution_records', '关联执行记录'],
  ['resource_records', '资源记录'],
];

export function cleanupCandidateFactRows(activityFacts) {
  const facts = activityFacts && typeof activityFacts === 'object' ? activityFacts : {};
  return CLEANUP_FACT_DEFINITIONS.map(([key, label]) => {
    const parsed = Number(facts[key]);
    return { key, label, value: Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0 };
  });
}

function shown(value, fallback) {
  const text = String(value ?? '').trim();
  return text || fallback;
}

const STATUS_LABELS = {
  draft: '草稿', rejected: '已退回', pending: '待处理', accepted: '已接单',
  in_progress: '处理中', submitted: '待审核', overdue: '已超期', approved: '已通过',
};

const SCHEDULE_TYPE_LABELS = {
  weekly: '周计划', monthly: '月计划', quarterly: '季计划', yearly: '年计划',
};

function statusLabel(value) {
  const raw = shown(value, '未知');
  return STATUS_LABELS[raw] || raw;
}

export function cleanupCandidateIdentityRows(item) {
  const source = item && typeof item === 'object' ? item : {};
  if (source.kind === 'workorder') {
    return [
      `工单编号：${shown(source.order_no, '未生成')}`,
      `标题：${shown(source.title, '未填写')}`,
      `站点：${shown(source.site_name, '未关联')}`,
      `状态：${statusLabel(source.status)}`,
    ];
  }
  const rows = [`执行人：${shown(source.owner_name, '未指定')}`];
  if (String(source.period_start || '').trim() || String(source.period_end || '').trim()) {
    rows.push(`周期：${shown(source.period_start, '未设置')} ~ ${shown(source.period_end, '未设置')}`);
  } else {
    rows.push(`计划类型：${SCHEDULE_TYPE_LABELS[source.schedule_type] || shown(source.schedule_type, '未记录')}`);
    rows.push(`来源日期：${shown(source.created_at, '未记录').slice(0, 10)}`);
  }
  const invalidSites = Array.isArray(source.invalid_sites) ? source.invalid_sites : [];
  if (invalidSites.length) {
    rows.push(`无效站点：${invalidSites.map(site => {
      const siteId = Number(site?.site_id);
      const rawName = shown(site?.site_name, Number.isFinite(siteId) ? `站点#${siteId}` : '未知站点');
      const name = rawName === '站点已不存在' && Number.isFinite(siteId) ? `站点#${siteId}` : rawName;
      return `${name}（${shown(site?.invalid_reason, '原因未记录')}）`;
    }).join('；')}`);
  }
  rows.push(`状态：${statusLabel(source.status)}`);
  return rows;
}

export function reconcileCleanupSelection(selection, candidates) {
  const available = new Set((Array.isArray(candidates) ? candidates : [])
    .map(item => `${item?.kind || ''}:${Number(item?.id)}`));
  return (Array.isArray(selection) ? selection : []).filter(item =>
    available.has(`${item?.kind || ''}:${Number(item?.id)}`));
}
