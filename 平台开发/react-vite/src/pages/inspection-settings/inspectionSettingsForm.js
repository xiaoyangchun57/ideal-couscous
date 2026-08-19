export function inspectionItemFormValues(row, itemCount = 0) {
  const source = row || {};
  const parsedOrder = Number(source.sort_order);
  return {
    item_name: source.item_name || '',
    category: source.category || '',
    photo_required: Boolean(source.photo_required),
    need_review: Boolean(source.need_review),
    max_photos: Number.isFinite(Number(source.max_photos)) ? Number(source.max_photos) : 0,
    inspection_standard: source.inspection_standard || '',
    sort_order: Number.isFinite(parsedOrder) ? parsedOrder : itemCount + 1,
  };
}

const DEFAULT_TEMPLATE_CATEGORY = '水质';

export function inspectionTemplateFormValues(row) {
  const source = row || {};
  return {
    template_name: source.template_name || '',
    frequency: source.frequency || undefined,
    description: source.description || '',
  };
}

export function inspectionTemplatePayload(values, row) {
  const source = row || {};
  const { template_name, frequency, description } = values || {};
  return {
    template_name,
    frequency,
    description,
    category: String(source.category || '').trim() || DEFAULT_TEMPLATE_CATEGORY,
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
