function hasInspectionFieldRecord({ remark, calibrator, calibrationValues, photoCount }) {
  const hasCalibration = String(calibrator || '').trim() && String(calibrationValues || '').trim();
  return Boolean(String(remark || '').trim() || Number(photoCount) > 0 || hasCalibration);
}

function isPendingInspectionSubmit(pendingOperations, itemId, planId) {
  return (pendingOperations || []).some((operation) => operation.type === 'submit'
    && operation.syncStatus === 'pending'
    && String(operation.data.item_id) === String(itemId)
    && String(operation.data.plan_id) === String(planId));
}

function resolveLocalSubmitFlush(summary, operationId, stillPending) {
  const rejected = ((summary && summary.rejected) || [])
    .find(item => item.id === operationId);
  if (rejected) return { status: 'rejected', error: rejected.error || '服务器拒绝了该操作' };
  if (stillPending) return { status: 'pending' };
  const result = ((summary && summary.results) || [])
    .find(item => item.id === operationId);
  return { status: 'synced', response: (result && result.response) || { success: true } };
}

module.exports = { hasInspectionFieldRecord, isPendingInspectionSubmit, resolveLocalSubmitFlush };
