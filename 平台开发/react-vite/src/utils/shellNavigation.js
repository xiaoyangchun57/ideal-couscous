const normalizedRoles = (roles) => new Set((Array.isArray(roles) ? roles : [roles]).filter(Boolean));

const hasAnyRole = (roles, allowed) => {
  const current = normalizedRoles(roles);
  return allowed.some((role) => current.has(role));
};

export function buildGlobalSearchPath(item) {
  const identifier = encodeURIComponent(item?.identifier ?? item?.id ?? '');
  switch (item?.type) {
    case 'site':
      return identifier ? `/sites?archive=${identifier}` : '/sites';
    case 'workorder':
      return identifier ? `/workorders?search=${identifier}` : '/workorders';
    case 'device':
      return identifier ? `/equipment?q=${identifier}` : '/equipment';
    default:
      return item?.path || '/';
  }
}

const hasIdentifier = (value) => value !== undefined && value !== null && String(value) !== '';

const REQUEST_SOURCE_TYPES = ['parts_request', 'spare_part_request'];
const REQUEST_PREFIXES = {
  parts_request: 'pr_',
  spare_part_request: 'spr_',
};
const PROCESSED_REQUEST_STATUSES = new Set([
  'approved', 'rejected', 'processed', 'completed', 'migrated', 'legacy_readonly',
]);

function buildAuditTargetPath(tab, queryKey, sourceId, sourceType, extra = {}) {
  const params = new URLSearchParams({ tab });
  if (hasIdentifier(sourceId)) {
    params.set(queryKey, String(sourceId));
  } else {
    params.set('target_missing', '1');
    params.set('source_type', sourceType);
  }
  Object.entries(extra).forEach(([key, value]) => {
    if (hasIdentifier(value)) params.set(key, String(value));
  });
  return `/audit?${params.toString()}`;
}

function notificationPayload(item) {
  if (!item?.payload_json) return {};
  try {
    const payload = typeof item.payload_json === 'string'
      ? JSON.parse(item.payload_json)
      : item.payload_json;
    return payload && typeof payload === 'object' ? payload : {};
  } catch {
    return {};
  }
}

export function getNotificationTarget(item, roles) {
  const sourceId = item?.source_id;
  switch (item?.source_type) {
    case 'workorder':
      return hasAnyRole(roles, ['admin', 'operator'])
        ? (hasIdentifier(sourceId) ? `/workorders?search=${encodeURIComponent(sourceId)}` : '/workorders')
        : null;
    case 'workorder_review':
      return hasAnyRole(roles, ['admin', 'reviewer'])
        ? buildAuditTargetPath('workorder', 'order', sourceId, item.source_type)
        : null;
    case 'inspection':
      return hasAnyRole(roles, ['admin', 'operator']) ? '/plan-schedules' : null;
    case 'inspection_review':
      if (hasAnyRole(roles, ['admin', 'reviewer'])) {
        return buildAuditTargetPath('inspection', 'inspection', sourceId, item.source_type);
      }
      return hasAnyRole(roles, ['operator']) ? '/plan-schedules' : null;
    case 'inspection_review_batch':
    case 'inspection_batch':
      return hasAnyRole(roles, ['admin', 'reviewer'])
        ? buildAuditTargetPath('inspection', 'inspection_batch', sourceId, item.source_type)
        : null;
    case 'inspection_rework':
      return hasAnyRole(roles, ['admin', 'operator'])
        ? (hasIdentifier(sourceId) ? `/plan-schedules?rework_plan=${encodeURIComponent(sourceId)}` : '/plan-schedules')
        : null;
    case 'photo_review':
      return hasAnyRole(roles, ['admin', 'reviewer'])
        ? buildAuditTargetPath('photo', 'photo', sourceId, item.source_type)
        : null;
    case 'attachment_review':
      return hasAnyRole(roles, ['admin', 'reviewer'])
        ? buildAuditTargetPath('photo', 'photo', sourceId, item.source_type)
        : null;
    case 'attachment_review_batch':
      if (!hasAnyRole(roles, ['admin', 'reviewer'])) return null;
      {
        const attachmentIds = notificationPayload(item).pending_attachment_ids;
        return Array.isArray(attachmentIds) && attachmentIds.length
          ? buildAuditTargetPath('photo', 'photos', attachmentIds.join(','), item.source_type)
          : buildAuditTargetPath('photo', 'site', sourceId, item.source_type);
      }
    case 'data_review':
      return hasAnyRole(roles, ['admin', 'reviewer'])
        ? buildAuditTargetPath('data', 'review', sourceId, item.source_type)
        : null;
    case 'parts_request':
      return hasAnyRole(roles, ['admin'])
        ? buildAuditTargetPath('parts', 'request', sourceId, item.source_type, { request_type: item.source_type })
        : null;
    case 'spare_part_request':
      return hasAnyRole(roles, ['admin'])
        ? buildAuditTargetPath('parts', 'request', sourceId, item.source_type, { request_type: item.source_type })
        : null;
    case 'vehicle_application':
      return hasAnyRole(roles, ['admin'])
        ? buildAuditTargetPath('vehicle', 'request', sourceId, item.source_type)
        : null;
    case 'plan_schedule':
      if (hasAnyRole(roles, ['admin'])) {
        return buildAuditTargetPath('plan', 'plan', sourceId, item.source_type,
          /变更|change/i.test(`${item.title || ''} ${item.content || ''}`) ? { change: 1 } : {});
      }
      return hasAnyRole(roles, ['operator'])
        ? (hasIdentifier(sourceId) ? `/plan-schedules?schedule=${encodeURIComponent(sourceId)}` : '/plan-schedules')
        : null;
    case 'plan_schedule_change':
    case 'plan_change':
      return hasAnyRole(roles, ['admin'])
        ? buildAuditTargetPath('plan', 'plan', sourceId, item.source_type, { change: 1 })
        : null;
    case 'user_work_transfer':
      return hasAnyRole(roles, ['admin', 'operator']) ? '/?view=operations' : null;
    case 'manual_report':
      return hasAnyRole(roles, ['admin', 'reviewer']) ? '/reports' : null;
    case 'alert':
      return hasAnyRole(roles, ['admin', 'reviewer']) ? '/alerts' : null;
    case 'reagent_qc':
      return hasAnyRole(roles, ['admin']) ? '/equipment' : null;
    default:
      return null;
  }
}

function normalizeSearchParams(searchParams) {
  return searchParams instanceof URLSearchParams
    ? searchParams
    : new URLSearchParams(searchParams || '');
}

export function getAuditTargetFromSearchParams(searchParams) {
  const params = normalizeSearchParams(searchParams);
  const tab = params.get('tab') || '';
  if (!tab) return null;
  if (params.get('target_missing') === '1') {
    return {
      tab,
      kind: 'missing',
      value: null,
      sourceType: params.get('source_type') || '',
      requestType: params.get('request_type') || null,
    };
  }
  const targets = [
    ['plan', 'plan', 'plan'],
    ['plan', 'plan_change', 'plan'],
    ['inspection', 'inspection_batch', 'inspection_batch'],
    ['inspection', 'inspection', 'inspection'],
    ['photo', 'photo', 'photo'],
    ['photo', 'photos', 'photos'],
    ['photo', 'site', 'site'],
    ['parts', 'request', 'request'],
    ['vehicle', 'request', 'request'],
    ['workorder', 'order', 'order'],
    ['data', 'review', 'review'],
  ];
  for (const [targetTab, kind, queryKey] of targets) {
    if (tab !== targetTab) continue;
    const value = params.get(queryKey);
    if (hasIdentifier(value)) {
      return {
        tab,
        kind: kind === 'plan' && params.get('change') === '1' ? 'plan_change' : kind,
        value,
        queryKey,
        ...(kind === 'request' ? { requestType: params.get('request_type') || null } : {}),
      };
    }
  }
  return null;
}

function matchesValue(value, targetValue) {
  return hasIdentifier(value) && String(value) === String(targetValue);
}

function matchesPrefixedId(value, targetValue, prefix) {
  return matchesValue(value, targetValue) || matchesValue(value, `${prefix}${targetValue}`);
}

function normalizedRequestType(value) {
  return REQUEST_SOURCE_TYPES.includes(value) ? value : null;
}

function requestIdMatches(candidate, targetValue, requestType) {
  const prefix = REQUEST_PREFIXES[requestType];
  const candidateId = String(candidate?.id ?? '').trim();
  const candidateRequestId = String(candidate?.request_id ?? '').trim();
  const value = String(targetValue ?? '').trim();
  if (!prefix || !value) return false;

  if (value.startsWith('pr_') || value.startsWith('spr_')) {
    return value.startsWith(prefix) && candidateId === value;
  }

  return candidateId === `${prefix}${value}`
    || candidateId === value
    || candidateRequestId === value;
}

function requestTargetMatches(candidate, target) {
  if (!REQUEST_SOURCE_TYPES.includes(candidate?.source_type)) return false;
  if (target.requestType && candidate.source_type !== target.requestType) return false;
  const requestTypes = target.requestType
    ? [target.requestType]
    : REQUEST_SOURCE_TYPES;
  return requestTypes.some(requestType => requestIdMatches(candidate, target.value, requestType));
}

function requestCandidateStatus(candidate) {
  if (candidate?.permission_denied || candidate?.accessible === false || candidate?.can_review === false) {
    return 'forbidden';
  }
  if (candidate?.is_processed || candidate?.processed || PROCESSED_REQUEST_STATUSES.has(candidate?.status)) {
    return 'processed';
  }
  return 'found';
}

function resolveRequestTarget(items, target) {
  if (target.requestType && !normalizedRequestType(target.requestType)) {
    return { status: 'invalid', item: null };
  }

  const matches = (items || []).filter(candidate => requestTargetMatches(candidate, target));
  if (matches.length > 1) return { status: 'ambiguous', item: null };
  if (matches.length === 1) {
    const status = requestCandidateStatus(matches[0]);
    return status === 'found'
      ? { status, item: matches[0] }
      : { status, item: null };
  }
  return { status: 'missing', item: null };
}

export function resolveAuditTarget(items, target) {
  if (!target) return { status: 'none', item: null };
  if (target.kind === 'missing' || !hasIdentifier(target.value)) {
    return { status: 'invalid', item: null };
  }
  const targetValue = String(target.value);
  const item = (items || []).find((candidate) => {
    switch (target.kind) {
      case 'plan':
      case 'plan_change':
        return candidate.source_type === 'plan_schedule'
          && (matchesValue(candidate.schedule_id, targetValue)
            || matchesPrefixedId(candidate.id, targetValue, 'ps_'))
          && (target.kind !== 'plan_change' || Boolean(candidate.is_change));
      case 'inspection_batch':
        return candidate.source_type === 'inspection_batch'
          && matchesValue(candidate.id, targetValue);
      case 'inspection':
        return ['inspection', 'inspection_batch'].includes(candidate.source_type)
          && (matchesPrefixedId(candidate.id, targetValue, 'insp_')
            || (candidate.item_ids || []).some((id) => matchesValue(id, targetValue)));
      case 'photo':
        return ['photo_review', 'inspection_batch'].includes(candidate.source_type)
          && ((candidate.attachment_ids || []).some((id) => matchesValue(id, targetValue))
            || (candidate.attachment_details || []).some((photo) => matchesValue(photo.id, targetValue)));
      case 'photos': {
        const targetIds = new Set(targetValue.split(',').filter(Boolean));
        return ['photo_review', 'inspection_batch'].includes(candidate.source_type)
          && ((candidate.attachment_ids || []).some((id) => targetIds.has(String(id)))
            || (candidate.attachment_details || []).some((photo) => targetIds.has(String(photo.id))));
      }
      case 'site':
        return candidate.source_type === 'photo_review' && matchesValue(candidate.site_id, targetValue);
      case 'request':
        if (target.requestType || (targetValue.startsWith('pr_') || targetValue.startsWith('spr_'))) return false;
        return ['parts_request', 'vehicle_application'].includes(candidate.source_type)
          && ((candidate.source_type === 'parts_request' && matchesPrefixedId(candidate.id, targetValue, 'pr_'))
            || (candidate.source_type === 'vehicle_application' && matchesPrefixedId(candidate.id, targetValue, 'va_'))
            || matchesValue(candidate.request_id, targetValue)
            || matchesValue(candidate.application_id, targetValue));
      case 'order':
        return candidate.source_type === 'workorder_review'
          && (matchesValue(candidate.order_no, targetValue) || matchesValue(candidate.source_name, targetValue));
      case 'review':
        return matchesValue(candidate.id, targetValue);
      default:
        return false;
    }
  });
  if (target.kind === 'request' && target.tab === 'parts') {
    return resolveRequestTarget(items, target);
  }
  return item ? { status: 'found', item } : { status: 'missing', item: null };
}
