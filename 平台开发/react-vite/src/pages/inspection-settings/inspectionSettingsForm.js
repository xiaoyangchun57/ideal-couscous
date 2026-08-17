export function inspectionItemFormValues(row, itemCount = 0) {
  const source = row || {};
  const parsedOrder = Number(source.sort_order);
  return {
    item_name: source.item_name || '',
    category: source.category || '',
    frequency_level: source.frequency_level || 'mid',
    photo_required: Boolean(source.photo_required),
    need_review: Boolean(source.need_review),
    max_photos: Number.isFinite(Number(source.max_photos)) ? Number(source.max_photos) : 0,
    inspection_standard: source.inspection_standard || '',
    sort_order: Number.isFinite(parsedOrder) ? parsedOrder : itemCount + 1,
  };
}

export function inspectionConfigFormValues(row) {
  const source = row || {};
  let devices = [];
  try {
    devices = Array.isArray(source.device_types)
      ? source.device_types
      : JSON.parse(source.device_types || '[]');
  } catch {
    devices = [];
  }
  return {
    site_type: source.site_type || 'water_quality',
    template_id: source.template_id,
    device_types: Array.isArray(devices) ? devices : [],
    remark: source.remark || '',
    is_active: row ? Boolean(source.is_active) : true,
  };
}

export function inspectionItemAccessState({ loading = false, error = '', saving = false } = {}) {
  return {
    canMutate: !loading && !error && !saving,
    showEmpty: !loading && !error,
  };
}

export function inspectionItemRefreshNotice(refreshSucceeded) {
  return refreshSucceeded ? '' : '检查项已保存，但列表刷新失败，请点击重试';
}
