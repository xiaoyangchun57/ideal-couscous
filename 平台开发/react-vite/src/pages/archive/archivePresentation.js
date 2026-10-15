const SOURCE_LABELS = {
  inspection: '巡检',
  workorder: '工单',
  site_photo: '现场影像',
  calibration: '校准',
  reagent: '试剂作业',
  vehicle: '车辆记录',
  maintenance: '设备养护',
  test: '试验资料',
  manual_report: '人工上报',
  other: '其他资料',
};

const CAPTURE_LABELS = {
  camera: '系统现场取证',
  watermark_album: '历史水印材料（非当前取证方式）',
  web_upload: '网页补充',
};

function enumLabel(value, labels) {
  if (!value) return '未记录';
  return labels[value] || '待确认';
}

export function archiveSourceLabel(value) {
  return enumLabel(value, SOURCE_LABELS);
}

export function archiveCaptureLabel(value) {
  return enumLabel(value, CAPTURE_LABELS);
}

export function hasArchiveFilters(filters = {}) {
  return Boolean(filters.keyword || filters.site_id || filters.business_type
    || (Array.isArray(filters.date_range) && filters.date_range.length === 2));
}

function materialRole(item = {}) {
  try {
    return JSON.parse(item.extra_json || '{}').material_role || '';
  } catch {
    return '';
  }
}

export function archiveMaterialNature(item = {}) {
  if (item.is_archive_material === 1 || item.material_kind === 'archive_material') {
    return '普通归档材料（不作为现场证据）';
  }
  const role = materialRole(item);
  if (role === 'supplement') return '补充材料（不作为现场证据）';
  if (role === 'pending_inspection' || role === 'pending_manual_report') {
    return '待提交现场照片（尚未形成有效证据）';
  }
  if (item.association_status && item.association_status !== 'linked') {
    return '未关联材料（不作为有效现场证据）';
  }
  if (item.evidence_qualification !== 'qualified' || item.is_effective_evidence !== 1) {
    return '待核对材料（不作为有效现场证据）';
  }
  return '正式现场证据';
}
