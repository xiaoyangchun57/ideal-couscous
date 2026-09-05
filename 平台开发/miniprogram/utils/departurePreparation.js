// 6.5.2 departure resource ViewModel. This module only projects server facts.

const REWORK_GATE_MAP = {
  arrangement_required: {
    gate: 'blocked', code: 'rework_arrangement_required',
    message: '整改返场需重新安排车辆或登记无车例外', tone: 'warning',
  },
  pending_approval: {
    gate: 'blocked', code: 'rework_pending_approval',
    message: '资源申请正在审批中，请等待审批结果', tone: 'warning',
  },
  rejected: {
    gate: 'blocked', code: 'rework_rejected',
    message: '资源申请被退回，请重新安排', tone: 'danger',
  },
  vehicle_checkout_required: {
    gate: 'blocked', code: 'rework_vehicle_checkout_required',
    message: '整改补检车辆已批准，完成出车登记后方可到站', tone: 'warning',
  },
  vehicle_checked_out: {
    gate: 'allowed', code: null, message: null, tone: 'success',
  },
  no_vehicle_approved: {
    gate: 'allowed', code: null, message: null, tone: 'success',
  },
  unknown: {
    gate: 'unknown', code: 'rework_resource_unknown',
    message: '整改资源状态暂不可用，请刷新后重试', tone: 'neutral',
  },
};

function isReworkPkg(pkg) {
  return Boolean(pkg && pkg.is_rework);
}

function isCarryoverPkg(pkg) {
  return Boolean(pkg && pkg.is_carryover && !pkg.is_rework);
}

function buildMode(pkg) {
  if (isReworkPkg(pkg)) return 'rework';
  if (isCarryoverPkg(pkg)) return 'carryover';
  return 'normal';
}

function quantityOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function applicationState(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (['pending', 'approved', 'rejected', 'expired'].includes(normalized)) return normalized;
  return normalized ? 'unavailable' : 'none';
}

function buildVehicle(pkg) {
  if (!pkg) return null;

  const mode = buildMode(pkg);
  const vehicle = pkg.vehicle || null;
  const vehicleUse = pkg.vehicle_use || null;
  const applicationId = pkg.vehicle_application_id || null;
  const reworkResource = pkg.rework_resource || {};
  const reworkState = reworkResource.state || 'unknown';
  const needsExtension = Boolean(pkg.vehicle_needs_extension);
  const exceptionReason = String(
    reworkResource.vehicle_exception_reason || pkg.vehicle_exception_reason || ''
  ).trim() || null;

  let requirement = 'no_vehicle';
  let vehicleId = vehicle ? vehicle.id : null;
  let plateNo = vehicle ? vehicle.plate_no : null;
  let appState = applicationState(
    reworkResource.application_status || pkg.vehicle_application_status || (applicationId ? 'approved' : null)
  );
  let useState = vehicleUse
    ? (vehicleUse.returned_at || vehicleUse.status === 'returned' ? 'returned' : 'checked_out')
    : 'not_checked_out';
  let statusLabel = '';
  let statusTone = 'neutral';
  let primaryAction = 'none';
  const documentState = vehicle && vehicle.document_state
    ? (vehicle.document_state.expired || []).length ? 'expired'
      : (vehicle.document_state.due_soon || []).length ? 'due_soon' : 'valid'
    : 'unknown';

  if (mode === 'rework') {
    if (reworkState === 'arrangement_required') {
      requirement = 'rework_no_vehicle';
      statusLabel = '待安排资源';
      statusTone = 'warning';
      primaryAction = 'request_resource';
    } else if (reworkState === 'pending_approval') {
      requirement = vehicle ? 'rework_vehicle' : 'rework_no_vehicle';
      statusLabel = '资源待审批';
      statusTone = 'warning';
    } else if (reworkState === 'rejected') {
      requirement = vehicle ? 'rework_vehicle' : 'rework_no_vehicle';
      statusLabel = '申请被退回';
      statusTone = 'danger';
      primaryAction = 'request_resource';
    } else if (reworkState === 'vehicle_checkout_required') {
      requirement = 'rework_vehicle';
      statusLabel = useState === 'returned' ? '整改车辆已还车' : '车辆待出车';
      statusTone = 'warning';
      primaryAction = useState === 'returned' ? 'none' : 'checkout';
      appState = 'approved';
    } else if (reworkState === 'vehicle_checked_out') {
      requirement = 'rework_vehicle';
      statusLabel = '车辆已出车';
      statusTone = 'success';
      appState = 'approved';
      useState = 'checked_out';
    } else if (reworkState === 'no_vehicle_approved' && exceptionReason) {
      requirement = 'rework_no_vehicle';
      vehicleId = null;
      plateNo = null;
      appState = 'approved';
      useState = 'not_checked_out';
      statusLabel = '无车例外已批准';
      statusTone = 'success';
    } else {
      requirement = vehicle ? 'rework_vehicle' : 'rework_no_vehicle';
      statusLabel = '资源状态暂不可用';
      statusTone = 'neutral';
      primaryAction = 'retry';
    }
  } else if (!vehicle && !vehicleUse) {
    statusLabel = exceptionReason ? '计划无需用车' : '未安排车辆';
  } else {
    requirement = 'planned_vehicle';
    if (vehicleUse && useState === 'checked_out') {
      statusLabel = '车辆已出车';
      statusTone = 'success';
    } else if (vehicleUse && useState === 'returned') {
      statusLabel = '车辆已还车';
    } else {
      statusLabel = '车辆待出车';
      statusTone = 'warning';
      primaryAction = 'checkout';
    }
    if (mode === 'carryover' && needsExtension) {
      statusLabel = '用车安排已超期';
      statusTone = 'warning';
      primaryAction = 'extend';
    }
  }

  if (primaryAction === 'checkout' && documentState === 'expired') {
    statusLabel = '车辆证照已到期';
    statusTone = 'danger';
    primaryAction = 'none';
  } else if (primaryAction === 'checkout' && documentState === 'due_soon') {
    statusLabel = '证照即将到期，车辆待出车';
    statusTone = 'warning';
  }

  return {
    requirement,
    vehicleId,
    plateNo,
    applicationId,
    applicationState: appState,
    useId: vehicleUse ? vehicleUse.id : null,
    useState,
    documentState,
    tripStartDate: (vehicleUse && (vehicleUse.start_date || vehicleUse.checked_out_at))
      || pkg.vehicle_trip_start_date || null,
    tripEndDate: (vehicleUse && (vehicleUse.end_date || vehicleUse.returned_at))
      || pkg.vehicle_trip_end_date || null,
    needsExtension,
    exceptionReason,
    statusLabel,
    statusTone,
    primaryAction,
  };
}

function buildAcknowledgement(confirmed, confirmedAt, note) {
  return {
    status: confirmed ? 'confirmed' : 'unconfirmed',
    confirmedAt: confirmed ? (confirmedAt || null) : null,
    note: confirmed ? (note || null) : null,
    submitting: false,
  };
}

function buildParts(pkg) {
  const parts = pkg && Array.isArray(pkg.resource_parts) ? pkg.resource_parts : [];
  return parts.map(part => ({
    partId: part.id || part.part_id,
    name: part.name || part.part_name || '未命名备件',
    unit: part.unit || null,
    plannedQuantity: quantityOrNull(part.planned_quantity),
    issuedQuantity: quantityOrNull(part.issued_quantity),
    remainingQuantity: quantityOrNull(part.remaining_quantity),
  }));
}

function sumKnown(parts, field) {
  if (parts.some(part => part[field] === null)) return null;
  return parts.reduce((total, part) => total + part[field], 0);
}

function buildPartsSummary(parts) {
  const remainingKnown = parts.every(part => part.remainingQuantity !== null);
  const planned = sumKnown(parts, 'plannedQuantity');
  const issued = sumKnown(parts, 'issuedQuantity');
  const remaining = sumKnown(parts, 'remainingQuantity');
  const quantitiesAvailable = parts.every(part =>
    part.plannedQuantity !== null && part.issuedQuantity !== null && part.remainingQuantity !== null
  );
  return {
    planned,
    issued,
    remaining,
    totalKinds: parts.length,
    remainingKinds: remainingKnown
      ? parts.filter(part => part.remainingQuantity > 0).length
      : null,
    quantitiesAvailable,
    canIssue: quantitiesAvailable && remaining > 0,
  };
}

function buildArrivalGate(pkg) {
  const serverGate = pkg && pkg.arrival_gate;
  if (serverGate && typeof serverGate.allowed === 'boolean') {
    const reworkMapped = buildMode(pkg) === 'rework'
      ? (REWORK_GATE_MAP[(pkg.rework_resource || {}).state] || REWORK_GATE_MAP.unknown)
      : null;
    return serverGate.allowed
      ? { gate: 'allowed', code: null, message: null, tone: 'success' }
      : {
        gate: 'blocked',
        code: serverGate.code || 'arrival_resource_blocked',
        message: serverGate.message || '当前还不能进入到站阶段，请按提示完成出发准备',
        tone: reworkMapped ? reworkMapped.tone : 'warning',
      };
  }
  if (buildMode(pkg) !== 'rework') {
    return {
      gate: 'unknown', code: 'arrival_resource_unknown',
      message: '车辆履约状态暂不可用，请刷新后重试', tone: 'neutral',
    };
  }
  const resource = pkg && pkg.rework_resource;
  if (!resource || typeof resource.arrival_allowed !== 'boolean') {
    return REWORK_GATE_MAP.unknown;
  }
  const mapped = REWORK_GATE_MAP[resource.state] || REWORK_GATE_MAP.unknown;
  if (resource.arrival_allowed && mapped.gate === 'allowed') return mapped;
  if (!resource.arrival_allowed && mapped.gate !== 'allowed') {
    return Object.assign({}, mapped, {
      code: resource.gate_code || mapped.code,
      message: resource.gate_message || mapped.message,
    });
  }
  return REWORK_GATE_MAP.unknown;
}

function buildTodoItems(vehicle, partsSummary, partsAcknowledgement, gate) {
  const items = [];

  // 车辆项
  if (vehicle) {
    if (vehicle.requirement === 'planned_vehicle' || vehicle.requirement === 'rework_vehicle') {
      if (vehicle.useState === 'checked_out') {
        items.push({
          type: 'vehicle',
          icon: '🚗',
          title: '车辆已出车',
          sub: vehicle.plateNo || '已登记出车',
          status: 'done',
          statusText: '已完成',
          action: 'verify',
        });
      } else if (vehicle.primaryAction === 'extend') {
        items.push({
          type: 'vehicle',
          icon: '🚗',
          title: '用车安排超期',
          sub: vehicle.plateNo || '需延期后继续',
          status: 'pending',
          statusText: '待延期',
          action: 'extend',
        });
      } else if (vehicle.primaryAction === 'request_resource') {
        items.push({
          type: 'vehicle',
          icon: '🚗',
          title: '车辆待安排',
          sub: '整改返场需申请车辆或登记无车例外',
          status: 'pending',
          statusText: '待申请',
          action: 'request_resource',
        });
      } else if (vehicle.primaryAction !== 'checkout') {
        items.push({
          type: 'vehicle',
          icon: '🚗',
          title: '车辆状态',
          sub: vehicle.statusLabel || '状态待确认',
          status: 'info',
          statusText: vehicle.statusLabel || '查看',
          action: 'none',
        });
      }
    } else if (vehicle.requirement === 'no_vehicle') {
      items.push({
        type: 'vehicle',
        icon: '🚶',
        title: '无需用车',
        sub: vehicle.exceptionReason || '计划无需用车',
        status: 'done',
        statusText: '已确认',
        action: 'none',
      });
    } else if (vehicle.requirement === 'rework_no_vehicle') {
      if (vehicle.applicationState === 'approved' && vehicle.exceptionReason) {
        items.push({
          type: 'vehicle',
          icon: '🚶',
          title: '无车例外已批准',
          sub: vehicle.exceptionReason,
          status: 'done',
          statusText: '已批准',
          action: 'none',
        });
      } else {
        items.push({
          type: 'vehicle',
          icon: '🚶',
          title: '无车安排',
          sub: vehicle.statusLabel || '待确认',
          status: 'pending',
          statusText: '待确认',
          action: 'request_resource',
        });
      }
    }
  }

  // 备件项
  if (partsSummary && partsSummary.totalKinds > 0) {
    if (partsSummary.quantitiesAvailable && partsSummary.remaining === 0) {
      items.push({
        type: 'parts',
        icon: '📦',
        title: '备件已全部领用',
        sub: `${partsSummary.totalKinds} 个品种`,
        status: 'done',
        statusText: '已完成',
        action: 'none',
      });
    } else if (partsSummary.quantitiesAvailable && partsSummary.canIssue) {
      items.push({
        type: 'parts',
        icon: '📦',
        title: '备件待领用',
        sub: `剩余 ${partsSummary.remainingKinds} 个品种待领`,
        status: 'pending',
        statusText: '待领用',
        action: 'issue',
      });
    } else {
      items.push({
        type: 'parts',
        icon: '📦',
        title: '备件信息待加载',
        sub: '数量信息暂不可用',
        status: 'info',
        statusText: '查看',
        action: 'none',
      });
    }
  }

  // 核对项
  if (partsAcknowledgement && partsSummary && partsSummary.totalKinds > 0) {
    if (partsAcknowledgement.status === 'confirmed') {
      items.push({
        type: 'ack',
        icon: '✓',
        title: '备件计划已核对',
        sub: partsAcknowledgement.confirmedAt ? `核对于 ${partsAcknowledgement.confirmedAt}` : '已确认',
        status: 'done',
        statusText: '已核对',
        action: 'none',
      });
    } else if (partsAcknowledgement.status === 'unavailable') {
      items.push({
        type: 'ack',
        icon: '○',
        title: '备件计划待核对',
        sub: '数量信息暂不可用，刷新后重试',
        status: 'info',
        statusText: '待核对',
        action: 'ack',
      });
    } else {
      items.push({
        type: 'ack',
        icon: '○',
        title: '备件计划待核对',
        sub: '确认备件计划无误后方可出发',
        status: 'pending',
        statusText: '待核对',
        action: 'ack',
      });
    }
  }

  return items;
}

function buildDeparturePreparation(pkg, target) {
  if (!pkg) {
    return {
      executionTarget: target || null,
      mode: 'normal',
      state: 'blocking_error',
      title: '出发准备',
      workDate: null,
      currentSite: null,
      routePositionText: null,
      routeProgressPercent: 0,
      routeProgressFillStyle: 'width:0%',
      routeSummary: null,
      todoItems: [],
      arrivalGate: 'unknown',
      gateCode: null,
      gateMessage: '执行包不存在',
      gateTone: 'danger',
      vehicleAcknowledgement: buildAcknowledgement(false),
      partsAcknowledgement: buildAcknowledgement(false),
      vehicle: null,
      parts: [],
      partsSummary: {
        planned: 0, issued: 0, remaining: 0, totalKinds: 0,
        remainingKinds: 0, quantitiesAvailable: true, canIssue: false,
        progressFillStyle: 'width:0%',
      },
      canContinueToArrival: false,
      preparationCompleted: false,
      refreshError: null,
    };
  }

  const mode = buildMode(pkg);
  const confirmation = pkg.departure_confirmation || {};
  const parts = buildParts(pkg);
  const partsSummary = buildPartsSummary(parts);
  const partsAcknowledgement = buildAcknowledgement(
    Boolean(confirmation.parts_confirmed), confirmation.confirmed_at, confirmation.note
  );
  if (parts.length > 0 && !partsSummary.quantitiesAvailable) {
    partsAcknowledgement.status = 'unavailable';
    partsAcknowledgement.confirmedAt = null;
    partsAcknowledgement.note = null;
  }
  const gate = buildArrivalGate(pkg);
  const vehicle = buildVehicle(pkg);
  const progressPercent = partsSummary.quantitiesAvailable && partsSummary.planned > 0
    ? Math.max(0, Math.min(100, Math.round(partsSummary.issued / partsSummary.planned * 100)))
    : null;
  const sites = Array.isArray(pkg.sites) ? pkg.sites : [];
  const siteCount = sites.length;

  // 当前站点：优先取 target 中的站点，否则取第一个未关闭站点
  let currentSite = null;
  let currentIndex = 0;
  if (target && target.siteId && sites.length > 0) {
    const idx = sites.findIndex(s => Number(s.id || s.site_id) === Number(target.siteId));
    if (idx !== -1) {
      currentSite = sites[idx];
      currentIndex = idx;
    }
  }
  if (!currentSite && sites.length > 0) {
    const firstOpen = sites.findIndex(s => s.status !== 'closed' && s.status !== 'completed');
    if (firstOpen !== -1) {
      currentSite = sites[firstOpen];
      currentIndex = firstOpen;
    } else {
      currentSite = sites[0];
      currentIndex = 0;
    }
  }

  const routePositionText = siteCount > 0
    ? `第 ${currentIndex + 1} 站 / 共 ${siteCount} 站`
    : null;
  const routeProgressPercent = siteCount > 0
    ? Math.round((currentIndex / siteCount) * 100)
    : 0;

  // 待办列表
  const todoItems = buildTodoItems(vehicle, partsSummary, partsAcknowledgement, gate);

  const canContinue = gate.gate === 'allowed';
  const targetIsExact = Boolean(
    target && target.executionPlanId && target.siteId
    && Number(target.executionPlanId) === Number(pkg.plan_id)
    && currentSite && Number(target.siteId) === Number(currentSite.id || currentSite.site_id)
  );
  const vehicleAcknowledgement = buildAcknowledgement(
    Boolean(confirmation.vehicle_confirmed), confirmation.confirmed_at, confirmation.note
  );
  let vehiclePreparationCompleted = false;
  if (vehicle) {
    if (vehicle.requirement === 'planned_vehicle' || vehicle.requirement === 'rework_vehicle') {
      vehiclePreparationCompleted = vehicle.useState === 'checked_out'
        && (mode === 'carryover' || vehicleAcknowledgement.status === 'confirmed');
    } else if (vehicle.requirement === 'no_vehicle') {
      vehiclePreparationCompleted = Boolean(vehicle.exceptionReason);
    } else if (vehicle.requirement === 'rework_no_vehicle') {
      vehiclePreparationCompleted = vehicle.applicationState === 'approved'
        && Boolean(vehicle.exceptionReason);
    }
  }
  const partsPreparationCompleted = parts.length === 0 || (
    partsSummary.quantitiesAvailable
    && partsSummary.remaining === 0
    && partsAcknowledgement.status === 'confirmed'
  );
  const preparationCompleted = Boolean(
    targetIsExact && canContinue && vehiclePreparationCompleted && partsPreparationCompleted
  );

  return {
    executionTarget: target || null,
    mode,
    modeLabel: mode === 'rework' ? '整改返场' : mode === 'carryover' ? '结转任务' : '现场巡检',
    state: 'ready',
    title: '出发准备',
    workDate: pkg.work_date || null,
    currentSite: currentSite ? {
      siteId: currentSite.id || currentSite.site_id,
      siteName: currentSite.name || currentSite.site_name || '未命名站点',
      siteCode: currentSite.code || currentSite.site_code || null,
    } : null,
    routePositionText,
    routeProgressPercent,
    routeProgressFillStyle: `width:${routeProgressPercent}%`,
    routeSummary: siteCount > 0 ? `${siteCount} 个站点` : null,
    todoItems,
    arrivalGate: gate.gate,
    gateCode: gate.code,
    gateMessage: gate.message,
    gateTone: gate.tone,
    vehicleAcknowledgement,
    partsAcknowledgement,
    vehicle,
    parts,
    partsSummary: Object.assign({}, partsSummary, {
      progressPercent,
      progressFillStyle: progressPercent === null ? '' : `width:${progressPercent}%`,
    }),
    canContinueToArrival: canContinue,
    preparationCompleted,
    refreshError: null,
  };
}

module.exports = {
  buildDeparturePreparation,
  buildVehicle,
  buildParts,
  buildPartsSummary,
  buildArrivalGate,
  buildAcknowledgement,
  buildMode,
  quantityOrNull,
};
