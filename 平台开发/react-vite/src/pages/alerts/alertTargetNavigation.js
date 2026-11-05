export function normalizeAlertTargetId(value) {
  const target = String(value ?? '').trim();
  return /^\d+$/.test(target) && Number(target) > 0 ? target : '';
}

export function pinTargetAlert(alerts, targetAlert) {
  if (!targetAlert) return alerts;
  const targetId = String(targetAlert.id ?? '');
  return [targetAlert, ...alerts.filter((alert) => String(alert.id ?? '') !== targetId)];
}
