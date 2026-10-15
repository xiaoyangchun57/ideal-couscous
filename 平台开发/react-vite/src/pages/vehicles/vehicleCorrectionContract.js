const FIELD_LABELS = {
  current_mileage: '当前里程',
  next_maintenance_mileage: '下次保养里程',
  status: '车辆状态',
};

const STATUS_LABELS = {
  idle: '可用',
  in_use: '出车中',
  maintenance: '维修中',
  restricted: '限制使用',
  retired: '已报废',
};

function formatValue(field, value) {
  if (value === null || value === undefined || value === '') return '未设置';
  if (field === 'status') return STATUS_LABELS[value] || String(value);
  if (field === 'current_mileage' || field === 'next_maintenance_mileage') {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${numeric.toLocaleString()} km` : String(value);
  }
  return String(value);
}

export function formatVehicleCorrectionLog(log) {
  let details = {};
  try {
    details = typeof log?.details === 'string' ? JSON.parse(log.details) : (log?.details || {});
  } catch {
    details = {};
  }
  const changes = Object.entries(details.changes || {}).map(([field, change]) => ({
    field,
    label: FIELD_LABELS[field] || field,
    before: formatValue(field, change?.before),
    after: formatValue(field, change?.after),
  }));
  return {
    id: log?.id,
    operator: log?.operator || '系统',
    createdAt: log?.created_at || '',
    reason: details.reason || '未记录原因',
    changes,
  };
}

export async function runVehicleEditSubmission({ guard, vehicleId, validate, buildPayload, save, reload }) {
  if (guard.current) return { status: 'ignored' };
  guard.current = true;
  try {
    const values = await validate();
    const payload = buildPayload(values);
    await save(vehicleId, payload);
    await reload();
    return { status: 'saved' };
  } catch (error) {
    return { status: 'failed', error };
  } finally {
    guard.current = false;
  }
}
