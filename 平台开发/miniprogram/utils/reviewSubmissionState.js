function normalizeId(id) {
  if (id === undefined || id === null || id === '') return '';
  return String(id);
}

function getSubmissionGuard(submittingId, itemId, options) {
  const activeId = normalizeId(submittingId);
  const targetId = normalizeId(itemId);
  const allowSameItem = !!(options && options.allowSameItem);
  if (!activeId) {
    return { allowed: !!targetId, sameItem: false, otherItem: false, message: '' };
  }

  const sameItem = !!targetId && activeId === targetId;
  if (sameItem && allowSameItem) {
    return { allowed: true, sameItem: true, otherItem: false, message: '' };
  }
  return {
    allowed: false,
    sameItem,
    otherItem: !sameItem,
    message: sameItem
      ? '当前审核事项正在提交，请稍候'
      : '已有其他审核事项正在提交，请等待完成后再操作'
  };
}

function getSubmissionErrorMessage(error) {
  const raw = error && (error.error || error.message || error.errMsg);
  const message = String(raw || '操作失败').replace(/^request:fail\s*/, '').trim();
  return message || '操作失败';
}

function getRetryErrorMessage(error) {
  return getSubmissionErrorMessage(error) + '，请重试';
}

module.exports = {
  getSubmissionGuard,
  getSubmissionErrorMessage,
  getRetryErrorMessage
};
