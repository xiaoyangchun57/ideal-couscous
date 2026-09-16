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
  camera: '小程序现场拍摄',
  watermark_album: '水印相册',
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
