function planScheduleDetailUrl(sourceId) {
  const id = String(sourceId == null ? '' : sourceId).trim();
  return /^[1-9]\d*$/.test(id)
    ? '/pages/plan-detail/plan-detail?id=' + id
    : '/pages/plan/plan';
}

function cleanId(value) {
  return String(value == null ? '' : value).trim();
}

function reviewUrl(reviewType, sourceId, attachmentIds) {
  let url = '/pages/review/view?target_type=' + encodeURIComponent(reviewType)
    + '&target_id=' + encodeURIComponent(sourceId);
  if (attachmentIds && attachmentIds.length) {
    url += '&target_attachment_ids=' + encodeURIComponent(attachmentIds.join(','));
  }
  return url;
}

function invalidTarget(message) {
  return { kind: 'invalid', message };
}

function notificationPayload(notification) {
  if (!notification || !notification.payload_json) return {};
  try {
    const payload = JSON.parse(notification.payload_json);
    return payload && typeof payload === 'object' ? payload : {};
  } catch (e) {
    return {};
  }
}

const REVIEW_TARGETS = {
  inspection_review: 'inspection_batch',
  inspection_review_batch: 'inspection_batch',
  inspection_batch: 'inspection_batch',
  attachment_review: 'photo_review',
  attachment_review_batch: 'photo_review',
  photo_review: 'photo_review',
  data_review: 'data_review',
  workorder_review: 'workorder_review',
  parts_request: 'parts_request',
  spare_part_request: 'spare_part_request',
  vehicle_application: 'vehicle_application'
};

function resolveNotificationTarget(notification) {
  const sourceType = cleanId(notification && notification.source_type);
  const sourceId = cleanId(notification && notification.source_id);

  if (!sourceType) return invalidTarget('通知缺少对象类型，无法打开对应业务。');

  if (sourceType === 'vehicle_use_expiry') {
    return { kind: 'page', page: '/pages/vehicle/vehicle' };
  }
  if (sourceType === 'alert' || sourceType === 'manual_report') {
    return { kind: 'page', page: '/pages/alert/alert' };
  }
  if (!sourceId) return invalidTarget('通知缺少对象 ID，无法打开具体业务。');

  if (sourceType === 'plan_schedule') {
    if (!/^[1-9]\d*$/.test(sourceId)) {
      return invalidTarget('计划通知中的对象 ID 无效，无法打开计划。');
    }
    return { kind: 'page', page: planScheduleDetailUrl(sourceId) };
  }
  if (sourceType === 'workorder') {
    return { kind: 'page', page: '/pages/workorder/workorder', workorderNo: sourceId };
  }
  if (sourceType === 'inspection' || sourceType === 'inspection_rework') {
    return { kind: 'tab', page: '/pages/inspection/inspection', planId: sourceId };
  }
  if (sourceType === 'reagent_qc') {
    return { kind: 'tab', page: '/pages/inspection/inspection', siteId: sourceId };
  }

  const reviewType = REVIEW_TARGETS[sourceType];
  if (reviewType) {
    const payload = notificationPayload(notification);
    const attachmentIds = sourceType === 'attachment_review_batch'
      ? (payload.pending_attachment_ids || []).map(cleanId).filter(Boolean)
      : [];
    return {
      kind: 'review',
      reviewType,
      sourceId,
      attachmentIds,
      page: reviewUrl(reviewType, sourceId, attachmentIds)
    };
  }

  return invalidTarget('暂不支持打开“' + sourceType + '”类型的通知。');
}

function sameId(left, right) {
  return cleanId(left) !== '' && cleanId(left) === cleanId(right);
}

function prefixedId(item, prefix, sourceId) {
  return sameId(item && item.id, prefix + sourceId);
}

function findReviewItem(groups, target) {
  if (!target || target.kind === 'invalid') return null;
  const items = (groups || []).reduce((all, group) => all.concat(group.items || []), []);
  return items.find(item => {
    if (!item) return false;
    const isPhotoBatch = target.reviewType === 'photo_review'
      && (item.source_type === 'photo_review' || item.source_type === 'inspection_batch');
    if (item.source_type !== target.reviewType && !isPhotoBatch) return false;
    if (target.reviewType === 'inspection_batch') {
      return sameId(item.id, target.sourceId)
        || (item.item_ids || []).some(id => sameId(id, target.sourceId))
        || (sameId(item.plan_id, target.sourceId) && sameId(item.site_id, target.siteId));
    }
    if (target.reviewType === 'photo_review') {
      if (target.attachmentIds && target.attachmentIds.length) {
        return (item.attachment_ids || []).some(id => target.attachmentIds.indexOf(cleanId(id)) >= 0);
      }
      return sameId(item.site_id, target.sourceId)
        || sameId(item.id, target.sourceId)
        || (item.reviewPhotos || []).some(photo => sameId(photo.id, target.sourceId));
    }
    if (target.reviewType === 'workorder_review') {
      return sameId(item.order_no, target.sourceId) || sameId(item.source_name, target.sourceId);
    }
    if (target.reviewType === 'parts_request') return prefixedId(item, 'pr_', target.sourceId);
    if (target.reviewType === 'spare_part_request') return prefixedId(item, 'spr_', target.sourceId);
    if (target.reviewType === 'vehicle_application') return prefixedId(item, 'va_', target.sourceId);
    if (target.reviewType === 'data_review') return prefixedId(item, 'dr_', target.sourceId);
    return sameId(item.id, target.sourceId);
  }) || null;
}

module.exports = {
  planScheduleDetailUrl,
  resolveNotificationTarget,
  findReviewItem
};
