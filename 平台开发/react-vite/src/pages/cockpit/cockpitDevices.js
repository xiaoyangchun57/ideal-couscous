export function buildMonitoringDeviceIndex(devices) {
  const byId = new Map();
  const byCode = new Map();
  (Array.isArray(devices) ? devices : []).forEach((device) => {
    if (!device?.last_data_time) return;
    if (device.management_scope && device.management_scope !== 'managed') return;
    if (device.monitoring_enabled != null && Number(device.monitoring_enabled) !== 1) return;
    if (device.site_id != null) {
      const key = Number(device.site_id);
      byId.set(key, [...(byId.get(key) || []), device]);
    }
    if (device.site_code) {
      byCode.set(device.site_code, [...(byCode.get(device.site_code) || []), device]);
    }
  });
  return { byId, byCode };
}

export function devicesForSite(index, site) {
  const combined = [
    ...(index?.byId?.get(Number(site?.id)) || []),
    ...(index?.byCode?.get(site?.code) || []),
  ];
  const unique = new Map();
  combined.forEach((device) => unique.set(device.id ?? device.device_code, device));
  return [...unique.values()];
}
