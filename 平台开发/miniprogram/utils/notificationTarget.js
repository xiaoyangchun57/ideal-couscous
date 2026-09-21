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

function positivePayloadId(payload, key) {
  const value = Number(payload && payload[key]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

const REVIEW_TARGETS = {
  inspection_review: 'inspection_batch',
  inspection_review_batch: 'inspection_batch',
  inspection_batch: 'inspection_batch',
  attachment_review: 'inspection_batch',
  attachment_review_batch: 'inspection_batch',
  photo_review: 'inspection_batch',
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
    if (!/^[1-9]\d*$/.test(sourceId)) {
      return invalidTarget('用车超期通知缺少有效申请编号，无法打开延续操作。');
    }
    return {
      kind: 'page', page: '/pages/vehicle/vehicle',
      vehicleTarget: { applicationId: Number(sourceId), expectedAction: 'extend', source: sourceType }
    };
  }
  if (sourceType === 'vehicle_application_result') {
    if (!/^[1-9]\d*$/.test(sourceId)) {
      return invalidTarget('用车结果通知缺少有效申请编号，无法打开。');
    }
    return {
      kind: 'page',
      page: '/pages/vehicle/vehicle?application_id=' + encodeURIComponent(sourceId)
        + '&source=approval_result',
      vehicleTarget: { applicationId: Number(sourceId), expectedAction: 'view_result', source: 'approval_result' }
    };
  }
  if (sourceType === 'parts_request_result') {
    const notificationId = Number(notification && notification.id);
    if (!Number.isInteger(notificationId) || notificationId <= 0) {
      return invalidTarget('备件结果通知缺少有效消息编号，无法打开。');
    }
    return {
      kind: 'page',
      page: '/pages/message/message?notification_id=' + encodeURIComponent(notificationId)
    };
  }
  if (sourceType === 'alert' || sourceType === 'manual_report') {
    if (!sourceId) return invalidTarget('通知缺少告警 ID，无法打开具体告警。');
    return { kind: 'tab', page: '/pages/alert/alert', alertId: sourceId };
  }
  if (sourceType === 'inspection_due_suggestion') {
    return { kind: 'tab', page: '/pages/plan/plan' };
  }
  if (!sourceId) return invalidTarget('通知缺少对象 ID，无法打开具体业务。');

  if (sourceType === 'plan_schedule' || sourceType === 'vehicle_extension_conflict') {
    if (!/^[1-9]\d*$/.test(sourceId)) {
      return invalidTarget('计划通知中的对象 ID 无效，无法打开计划。');
    }
    const payload = notificationPayload(notification);
    if (sourceType === 'plan_schedule'
        && payload.notification_target === 'review' && payload.review_type === 'plan_schedule') {
      return {
        kind: 'review', reviewType: 'plan_schedule', sourceId,
        attachmentIds: [], page: reviewUrl('plan_schedule', sourceId)
      };
    }
    return { kind: 'page', page: planScheduleDetailUrl(sourceId) };
  }
  if (sourceType === 'workorder') {
    return { kind: 'page', page: '/pages/workorder/workorder', workorderNo: sourceId };
  }
  if (sourceType === 'inspection' || sourceType === 'inspection_rework') {
    return {
      kind: 'page', page: '/pages/inspection/inspection',
      executionTarget: { executionPlanId: Number(sourceId), source: sourceType }
    };
  }
  if (sourceType === 'attachment_void' || sourceType === 'replacement_review') {
    const payload = notificationPayload(notification);
    const planId = positivePayloadId(payload, 'plan_id');
    const itemId = positivePayloadId(payload, 'item_id');
    const siteId = positivePayloadId(payload, 'site_id');
    if (!planId || !itemId || !siteId) return invalidTarget('补传通知缺少可信计划、检查项或站点标识，无法精确定位。');
    return {
      kind: 'page', page: '/pages/inspection/inspection',
      executionTarget: { executionPlanId: planId, itemId, siteId, source: sourceType }
    };
  }
  if (sourceType === 'reagent_qc') {
    return {
      kind: 'page', page: '/pages/inspection/inspection',
      executionTarget: { siteId: Number(sourceId), source: sourceType }
    };
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
    if (item.source_type !== target.reviewType) return false;
    if (target.reviewType === 'inspection_batch') {
      if (target.attachmentIds && target.attachmentIds.length) {
        return (item.attachment_ids || []).some(id => target.attachmentIds.indexOf(cleanId(id)) >= 0);
      }
      return sameId(item.id, target.sourceId)
        || (item.item_ids || []).some(id => sameId(id, target.sourceId))
        || sameId(item.site_id, target.sourceId)
        || (sameId(item.plan_id, target.sourceId) && sameId(item.site_id, target.siteId));
    }
    if (target.reviewType === 'workorder_review') {
      return sameId(item.order_no, target.sourceId) || sameId(item.source_name, target.sourceId);
    }
    if (target.reviewType === 'plan_schedule') {
      return sameId(item.schedule_id, target.sourceId) || prefixedId(item, 'ps_', target.sourceId);
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
