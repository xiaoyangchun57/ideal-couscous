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

module.exports = { hasInspectionFieldRecord, isPendingInspectionSubmit };
