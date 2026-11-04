export function vehicleSummary(vehicles = []) {
  const rows = Array.isArray(vehicles) ? vehicles : [];
  return {
    inUse: rows.filter((vehicle) => vehicle.status === 'in_use').length,
    restrictedOrMaintenance: rows.filter((vehicle) => ['restricted', 'maintenance'].includes(vehicle.status)).length,
    maintenanceReminder: rows.filter((vehicle) => {
      const currentMileage = Number(vehicle.current_mileage || 0);
      const nextMileage = Number(vehicle.next_maintenance_mileage || 0);
      return nextMileage > 0 && currentMileage >= nextMileage;
    }).length,
  };
}
