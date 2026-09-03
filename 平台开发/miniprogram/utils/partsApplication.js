function inventoryOptions(parts) {
  return (Array.isArray(parts) ? parts : []).filter(part => {
    const id = Number(part && part.id);
    return Number.isInteger(id) && id > 0;
  }).map(part => ({
    id: Number(part.id),
    part_name: part.part_name || '',
    label: (part.part_name || '备件')
      + (part.part_code ? '（' + part.part_code + '）' : '')
      + ' 余' + (part.quantity || 0),
  }));
}

function inventoryErrorMessage(error) {
  return (error && (error.error || error.message)) || '请检查网络后重试';
}

function inventoryStateError(status) {
  if (status === 'loading' || status === 'idle') return '库存加载中，请稍候';
  if (status === 'empty') return '暂无可领用库存';
  if (status === 'error') return '库存加载失败，请重试';
  return '';
}

function buildPartsPayload(form, options, inventoryStatus) {
  const fulfillmentType = form && form.fulfillment_type;
  if (fulfillmentType === 'stock' && inventoryStatus && inventoryStatus !== 'ready') {
    return { error: inventoryStateError(inventoryStatus) };
  }
  const quantity = Number(form && form.quantity);
  const reason = String(form && form.reason || '').trim();
  if (!Number.isInteger(quantity) || quantity <= 0) return { error: '请填写有效数量' };
  if (!reason) return { error: '请填写申请事由' };
  if (fulfillmentType === 'stock') {
    const option = (options || [])[Number(form.index)];
    if (!option || !Number.isInteger(Number(option.id)) || Number(option.id) <= 0) {
      return { error: '请选择库存备件' };
    }
    return {
      payload: {
        part_name: option.part_name || '', specification: '', quantity, reason,
        spare_part_id: Number(option.id), fulfillment_type: fulfillmentType, estimated_amount: null,
      },
    };
  }
  if (fulfillmentType !== 'local_purchase' && fulfillmentType !== 'vendor_order') {
    return { error: '履约方式无效' };
  }
  const partName = String(form.part_name || '').trim();
  if (!partName) return { error: '请填写备件名称' };
  const rawAmount = form.estimated_amount;
  let estimatedAmount = null;
  if (rawAmount !== '' && rawAmount !== null && rawAmount !== undefined) {
    estimatedAmount = Number(rawAmount);
    if (!Number.isFinite(estimatedAmount) || estimatedAmount < 0) return { error: '预计金额必须为非负数' };
  }
  return {
    payload: {
      part_name: partName, specification: String(form.specification || '').trim(), quantity, reason,
      spare_part_id: null, fulfillment_type: fulfillmentType, estimated_amount: estimatedAmount,
    },
  };
}

module.exports = { inventoryOptions, inventoryErrorMessage, inventoryStateError, buildPartsPayload };
