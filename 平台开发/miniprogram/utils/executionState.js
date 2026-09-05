function photoRequirement(required, remotePhotos, localPhotos) {
  const expected = Math.max(0, Number(required) || 0);
  const captured = Math.max(0, Number(remotePhotos) || 0) + Math.max(0, Number(localPhotos) || 0);
  const missing = Math.max(0, expected - captured);
  return { required: expected, captured, missing, ready: missing === 0 };
}

function inspectionPhotoProgress(categories) {
  let required = 0;
  let current = 0;
  (Array.isArray(categories) ? categories : []).forEach((category) => {
    (category.items || []).forEach((item) => {
      const itemRequired = Math.max(0, Number(item.required_photos) || 0);
      if (item.evidence_status === 'supplement_required') {
        if (item.replacement_photo_status === 'pending_review') {
          return;
        }
        const replacementRequired = item.replacement_required_photos == null
          ? Math.max(1, itemRequired - inspectionItemPhotoState(item).formalPhotos.length)
          : Math.max(0, Number(item.replacement_required_photos) || 0);
        required += replacementRequired;
        return;
      }
      required += itemRequired;
      if (item.evidence_status === 'effective' && Number(item.review_status || 0) === 2) {
        current += itemRequired;
        return;
      }
      if (item.effective_evidence_count != null) {
        current += Math.max(0, Number(item.effective_evidence_count) || 0);
        return;
      }
      let legacyPhotos = [];
      try { legacyPhotos = item.photo_urls ? JSON.parse(item.photo_urls) : []; } catch (error) { legacyPhotos = []; }
      current += Array.isArray(legacyPhotos) ? legacyPhotos.length : 0;
    });
  });
  return { req: required, taken: current, missing: Math.max(0, required - current) };
}

function inspectionFieldItemCompleted(item) {
  const source = item || {};
  return String(source.result || '').trim() !== ''
    && Number(source.review_status || 0) !== 3
    && source.evidence_status !== 'supplement_required';
}

function projectInspectionFieldProgress(categories) {
  let total = 0;
  let completed = 0;
  const projected = (Array.isArray(categories) ? categories : []).map(category => {
    let categoryCompleted = 0;
    const items = category.items || [];
    items.forEach(item => {
      total += 1;
      if (inspectionFieldItemCompleted(item)) {
        completed += 1;
        categoryCompleted += 1;
      }
    });
    return Object.assign({}, category, {
      items,
      total: items.length,
      completed: categoryCompleted,
    });
  });
  return {
    categories: projected,
    total,
    completed,
    percent: total ? Math.round(completed * 100 / total) : 0,
  };
}

function inspectionFieldProgress(categories) {
  const projected = projectInspectionFieldProgress(categories);
  return {
    total: projected.total,
    completed: projected.completed,
    percent: projected.percent,
  };
}

function parsePhotoUrls(value) {
  if (Array.isArray(value)) return value.filter(item => typeof item === 'string' && item);
  try {
    const parsed = value ? JSON.parse(value) : [];
    return Array.isArray(parsed) ? parsed.filter(item => typeof item === 'string' && item) : [];
  } catch (error) {
    return [];
  }
}

function photoStorageKey(value) {
  const text = String(value || '').split(/[?#]/)[0];
  const uploadIndex = text.indexOf('/uploads/');
  return uploadIndex >= 0 ? text.slice(uploadIndex) : text;
}

function inspectionItemPhotoState(item) {
  const source = item || {};
  const historical = new Set();
  (Array.isArray(source.evidence_attachments) ? source.evidence_attachments : []).forEach(attachment => {
    if (!['rejected', 'voided', 'superseded'].includes(attachment && attachment.review_status)) return;
    const key = photoStorageKey(attachment.stored_path);
    if (key) historical.add(key);
  });
  const formalPhotos = [];
  const known = new Set();
  const submittedPhotos = source.evidence_status === 'supplement_required'
    && Array.isArray(source.current_photo_urls)
    ? source.current_photo_urls : parsePhotoUrls(source.photo_urls);
  submittedPhotos.forEach(path => {
    const key = photoStorageKey(path);
    if (!historical.has(key) && !known.has(key)) { known.add(key); formalPhotos.push(path); }
  });
  const pendingPhotos = [];
  parsePhotoUrls(source.pending_photo_urls).forEach(path => {
    const key = photoStorageKey(path);
    if (!historical.has(key) && !known.has(key)) { known.add(key); pendingPhotos.push(path); }
  });
  return { formalPhotos, pendingPhotos, photos: formalPhotos.concat(pendingPhotos) };
}

function inspectionItemPhotoRequirement(item, localPhotoCount) {
  const source = item || {};
  if (source.replacement_photo_status === 'pending_review') {
    return {
      required: 0, captured: 0, missing: 0, ready: false, blocked: true,
      blockReason: source.replacement_block_reason || '原照片待审核，审核完成后才能补拍',
    };
  }
  const state = inspectionItemPhotoState(source);
  if (source.evidence_status === 'supplement_required') {
    const expected = source.replacement_required_photos == null
      ? Math.max(1, Math.max(0, Number(source.required_photos) || 0) - state.formalPhotos.length)
      : Math.max(0, Number(source.replacement_required_photos) || 0);
    return photoRequirement(expected, state.pendingPhotos.length, localPhotoCount);
  }
  return photoRequirement(source.required_photos, state.photos.length, localPhotoCount);
}

function applyRejectedInspectionPhotoPurge(item, attachmentId, result) {
  const source = item || {};
  const response = result || {};
  const attachments = Array.isArray(source.evidence_attachments)
    ? source.evidence_attachments : [];
  const target = attachments.find(attachment => Number(attachment.id) === Number(attachmentId));
  const targetPath = photoStorageKey(target && target.stored_path);
  return Object.assign({}, source, {
    photo_urls: Array.isArray(response.photo_urls) ? response.photo_urls : parsePhotoUrls(source.photo_urls),
    current_photo_urls: Array.isArray(response.photo_urls)
      ? response.photo_urls : parsePhotoUrls(source.current_photo_urls),
    actual_photos: Math.max(0, Number(response.actual_photos) || 0),
    pending_photo_urls: parsePhotoUrls(source.pending_photo_urls)
      .filter(path => !targetPath || photoStorageKey(path) !== targetPath),
    evidence_attachments: attachments
      .filter(attachment => Number(attachment.id) !== Number(attachmentId)),
  });
}

function rejectedInspectionEvidence(attachments, userId, resolveDisplayUrl) {
  const resolve = typeof resolveDisplayUrl === 'function' ? resolveDisplayUrl : value => value;
  return (Array.isArray(attachments) ? attachments : [])
    .filter(attachment => attachment && attachment.review_status === 'rejected'
      && Number(attachment.uploader_id) === Number(userId))
    .map(attachment => Object.assign({}, attachment, {
      display_url: resolve(attachment.stored_path),
    }));
}

function addPendingInspectionPhotos(categories, itemId, photoUrls) {
  return (Array.isArray(categories) ? categories : []).map(category => ({
    ...category,
    items: (category.items || []).map(item => {
      if (Number(item.item_id) !== Number(itemId)) return item;
      const state = inspectionItemPhotoState(item);
      const formal = new Set(state.formalPhotos.map(photoStorageKey));
      const pending = state.pendingPhotos.map(photoStorageKey);
      parsePhotoUrls(photoUrls).forEach(url => {
        const path = photoStorageKey(url);
        if (path && !formal.has(path) && pending.indexOf(path) === -1) pending.push(path);
      });
      return Object.assign({}, item, { pending_photo_urls: pending });
    }),
  }));
}

function applyInspectionSubmission(categories, itemId, result, remotePhotos, syncPending,
                                   reviewStatus, evidenceStatus) {
  const formalPhotos = syncPending ? null : Array.from(new Set(parsePhotoUrls(remotePhotos)));
  return (Array.isArray(categories) ? categories : []).map(category => ({
    ...category,
    items: (category.items || []).map(item => {
      if (Number(item.item_id) !== Number(itemId)) return item;
      const next = Object.assign({}, item, {
        result,
        review_status: reviewStatus === undefined ? item.review_status : reviewStatus,
        evidence_status: evidenceStatus === undefined ? item.evidence_status : evidenceStatus,
        sync_pending: !!syncPending,
      });
      if (!syncPending) {
        next.photo_urls = JSON.stringify(formalPhotos);
        next.actual_photos = formalPhotos.length;
        next.effective_evidence_count = formalPhotos.length;
        next.pending_photo_urls = [];
      }
      return next;
    }),
  }));
}

function removePendingInspectionPhoto(categories, itemId, storagePath) {
  return (Array.isArray(categories) ? categories : []).map(category => ({
    ...category,
    items: (category.items || []).map(item => Number(item.item_id) === Number(itemId)
      ? Object.assign({}, item, {
          pending_photo_urls: parsePhotoUrls(item.pending_photo_urls)
            .filter(path => photoStorageKey(path) !== photoStorageKey(storagePath)),
        })
      : item),
  }));
}

function userRoles(user) {
  const source = user || {};
  const roles = Array.isArray(source.roles) ? source.roles.filter(Boolean) : [];
  if (roles.length) return roles;
  return source.role ? [source.role] : [];
}

function canCancelPlanSchedule(schedule, user) {
  const plan = schedule || {};
  const actor = user || {};
  if (!['approved', 'modifying'].includes(plan.status)) return false;
  return Number(plan.user_id) === Number(actor.id) || userRoles(actor).includes('admin');
}

function normalizePlanCancelReason(rawReason) {
  const value = String(rawReason || '').trim();
  if (!value) return { value, error: '请填写取消原因' };
  if (value.length > 500) return { value, error: '取消原因不能超过500字' };
  return { value, error: '' };
}

function startPlanCancellation(owner, action) {
  if (owner.__planCancellationPromise) {
    return { started: false, promise: owner.__planCancellationPromise };
  }
  let promise;
  promise = Promise.resolve().then(action).finally(() => {
    if (owner.__planCancellationPromise === promise) owner.__planCancellationPromise = null;
  });
  owner.__planCancellationPromise = promise;
  return { started: true, promise };
}

module.exports = {
  photoRequirement,
  inspectionPhotoProgress,
  inspectionFieldItemCompleted,
  inspectionFieldProgress,
  projectInspectionFieldProgress,
  inspectionItemPhotoState,
  inspectionItemPhotoRequirement,
  applyRejectedInspectionPhotoPurge,
  rejectedInspectionEvidence,
  addPendingInspectionPhotos,
  applyInspectionSubmission,
  removePendingInspectionPhoto,
  canCancelPlanSchedule,
  normalizePlanCancelReason,
  startPlanCancellation,
};
