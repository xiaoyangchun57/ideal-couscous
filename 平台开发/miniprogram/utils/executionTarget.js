const TYPE_LABELS = { weekly: '周检', monthly: '月检', quarterly: '季检', yearly: '年检' };

function positiveId(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function sameId(left, right) {
  const a = positiveId(left);
  const b = positiveId(right);
  return a !== null && a === b;
}

function normalizeExecutionTarget(target) {
  const source = target || {};
  const normalized = {
    executionPlanId: positiveId(source.executionPlanId || source.planId),
    scheduleId: positiveId(source.scheduleId),
    workDate: /^\d{4}-\d{2}-\d{2}$/.test(String(source.workDate || '')) ? source.workDate : null,
    siteId: positiveId(source.siteId),
    itemId: positiveId(source.itemId),
    source: String(source.source || 'generic'),
  };
  if (source.reworkOnly === true) normalized.reworkOnly = true;
  return normalized;
}

function hasExactTarget(target) {
  const value = normalizeExecutionTarget(target);
  return !!(value.executionPlanId || value.scheduleId || value.siteId || value.itemId);
}

function sitePhase(site) {
  if (site.checked_out) return { phase: 'closed', text: '已离站', tone: 'success' };
  const total = Number(site.total);
  const completed = Number(site.completed);
  if (site.checked_in && total > 0 && completed >= total) {
    return { phase: 'ready_to_leave', text: '待离站', tone: 'warning' };
  }
  if (site.checked_in) return { phase: 'in_progress', text: '检查中', tone: 'info' };
  return { phase: 'await_arrival', text: '待到站', tone: 'neutral' };
}

function orderedSites(pkg) {
  const sites = Array.isArray(pkg && pkg.sites) ? pkg.sites : [];
  const order = Array.isArray(pkg && pkg.site_order) ? pkg.site_order.map(positiveId) : [];
  const ids = sites.map(site => positiveId(site.site_id));
  const routeAvailable = order.length === ids.length
    && order.every((id, index) => id !== null && ids.indexOf(id) >= 0 && order.indexOf(id) === index);
  if (!routeAvailable) return { sites, routeAvailable: false };
  const indexed = new Map(sites.map(site => [positiveId(site.site_id), site]));
  return { sites: order.map(id => indexed.get(id)), routeAvailable: true };
}

function buildPackageOption(pkg) {
  const source = pkg || {};
  const ordered = orderedSites(source);
  const sites = ordered.sites.map(site => {
    const phase = sitePhase(site || {});
    const total = Number(site && site.total);
    const completed = Number(site && site.completed);
    const abnormal = Number(site && site.abnormal);
    return {
      siteId: positiveId(site && site.site_id),
      siteName: (site && (site.name || site.site_name)) || '站点未记录',
      address: (site && (site.address || site.code)) || '',
      phase: phase.phase,
      phaseText: phase.text,
      phaseTone: phase.tone,
      total: Number.isFinite(total) ? total : null,
      completed: Number.isFinite(completed) ? completed : null,
    abnormal: Number.isFinite(abnormal) ? abnormal : null,
    reworkItems: Math.max(0, Number(site && site.rework_items) || 0),
      checkedIn: !!(site && site.checked_in),
      checkedOut: !!(site && site.checked_out),
    };
  }).filter(site => site.siteId !== null);
  const total = sites.reduce((sum, site) => sum + (site.total === null ? 0 : site.total), 0);
  const completed = sites.reduce((sum, site) => sum + (site.completed === null ? 0 : site.completed), 0);
  const abnormal = sites.reduce((sum, site) => sum + (site.abnormal === null ? 0 : site.abnormal), 0);
  const totalKnown = sites.length > 0 && sites.every(site => site.total !== null);
  const completedKnown = sites.length > 0 && sites.every(site => site.completed !== null);
  const abnormalKnown = sites.length > 0 && sites.every(site => site.abnormal !== null);
  const scheduleDateText = source.work_date ? ('作业日期 ' + source.work_date) : '作业日期未记录';
  return {
    executionPlanId: positiveId(source.plan_id),
    scheduleId: positiveId(source.schedule_id),
    workDate: String(source.work_date || ''),
    planType: source.schedule_type || 'inspection',
    planTypeLabel: TYPE_LABELS[source.schedule_type] || '巡检',
    packageName: source.package_label || source.plan_name
      || (positiveId(source.schedule_id) ? ('计划#' + positiveId(source.schedule_id)) : '计划名称未记录'),
    scheduleDateText: ordered.routeAvailable ? scheduleDateText : (scheduleDateText + ' · 路线信息暂不可用'),
    siteCountText: sites.length + ' 个站点',
    routeAvailable: ordered.routeAvailable,
    routeMessage: ordered.routeAvailable ? '' : '路线信息暂不可用',
    total: totalKnown ? total : null,
    completed: completedKnown ? completed : null,
    abnormal: abnormalKnown ? abnormal : null,
    isCarryover: !!source.is_carryover,
    isRework: !!source.is_rework,
    sites,
  };
}

function unavailable(packages, message) {
  return {
    state: 'target_unavailable', selectedPackageId: null, selectedSiteId: null,
    targetMessage: message, packages, hasAmbiguity: false, canEnter: false,
  };
}

function packageForTarget(pkg, target) {
  const t = normalizeExecutionTarget(target);
  if (!t.siteId) return pkg;
  return Object.assign({}, pkg, {
    sites: (pkg.sites || []).filter(site => sameId(site.siteId, t.siteId)),
  });
}

function matchesExecutionTarget(pkg, site, target) {
  const t = normalizeExecutionTarget(target);
  if (t.executionPlanId && !sameId(pkg && pkg.executionPlanId, t.executionPlanId)) return false;
  if (t.scheduleId && !sameId(pkg && pkg.scheduleId, t.scheduleId)) return false;
  if (t.workDate && (!pkg || pkg.workDate !== t.workDate)) return false;
  if (t.siteId && !sameId(site && site.siteId, t.siteId)) return false;
  return true;
}

function packageIsFullyClosed(pkg) {
  const sites = Array.isArray(pkg && pkg.sites) ? pkg.sites : [];
  return sites.length > 0 && sites.every(site => site.phase === 'closed');
}

function resolveExecutionTarget(packages, target) {
  const list = Array.isArray(packages) ? packages : [];
  const t = normalizeExecutionTarget(target);
  if (!list.length) {
    return { state: 'empty', selectedPackageId: null, selectedSiteId: null,
      targetMessage: '当前没有可执行的巡检任务', packages: list, hasAmbiguity: false, canEnter: false };
  }
  if (t.reworkOnly && t.siteId) {
    const reworkPackages = list
      .filter(pkg => !t.executionPlanId || sameId(pkg.executionPlanId, t.executionPlanId))
      .map(pkg => packageForTarget(pkg, t))
      .filter(pkg => (pkg.sites || []).some(site => Number(site.reworkItems || 0) > 0));
    if (reworkPackages.length !== 1) {
      return unavailable([], reworkPackages.length
        ? '整改任务暂不可确认，请刷新首页后重试'
        : '整改任务状态已更新，请刷新首页后重试');
    }
    const pkg = reworkPackages[0];
    return { state: 'ready_to_enter', selectedPackageId: pkg.executionPlanId,
      selectedSiteId: t.siteId, targetMessage: null, packages: reworkPackages,
      hasAmbiguity: false, canEnter: true };
  }
  let candidates = list;
  if (t.executionPlanId) {
    const exact = list.find(item => sameId(item.executionPlanId, t.executionPlanId));
    if (!exact) return unavailable([], '目标执行包已不可用，请重新选择');
    if ((t.scheduleId && !sameId(exact.scheduleId, t.scheduleId))
      || (t.workDate && exact.workDate !== t.workDate)
      || (t.siteId && !exact.sites.some(site => sameId(site.siteId, t.siteId)))) {
      return unavailable([], '目标信息与当前执行包不一致，请重新选择');
    }
    candidates = [exact];
  } else if (t.scheduleId && t.workDate) {
    candidates = list.filter(item => sameId(item.scheduleId, t.scheduleId) && item.workDate === t.workDate);
    if (!candidates.length) return unavailable([], '未找到匹配的执行包');
  } else if (t.scheduleId) {
    candidates = list.filter(item => sameId(item.scheduleId, t.scheduleId));
    if (!candidates.length) return unavailable([], '该计划当前没有可执行包');
  } else if (t.siteId) {
    candidates = list.filter(item => item.sites.some(site => sameId(site.siteId, t.siteId)));
    if (!candidates.length) return unavailable([], '目标站点不在可执行范围内');
  }

  const targetsClosedObject = !!(t.executionPlanId || t.siteId || t.itemId);
  if (!targetsClosedObject) {
    const executable = candidates.filter(pkg => !packageIsFullyClosed(pkg));
    if (!executable.length) {
      return { state: 'closed_only', selectedPackageId: null, selectedSiteId: null,
        targetMessage: '今日站点均已闭环', packages: candidates.map(pkg => packageForTarget(pkg, t)),
        hasAmbiguity: false, canEnter: false };
    }
    candidates = executable;
  }

  const candidatePackages = candidates.map(pkg => packageForTarget(pkg, t));
  if (candidatePackages.every(pkg => (pkg.sites || []).length > 0
    && pkg.sites.every(site => site.phase === 'closed'))) {
    if (candidatePackages.length === 1 && t.siteId) {
      const pkg = candidatePackages[0];
      return { state: 'selecting', selectedPackageId: pkg.executionPlanId, selectedSiteId: t.siteId,
        targetMessage: '目标站点已离站，可查看本站结果', packages: candidatePackages,
        hasAmbiguity: false, canEnter: true, entryMode: 'read_only' };
    }
    return { state: 'closed_only', selectedPackageId: null, selectedSiteId: null,
      targetMessage: '今日站点均已闭环', packages: candidatePackages, hasAmbiguity: false, canEnter: false };
  }

  if (t.siteId && candidates.length > 1) {
    return { state: 'selecting', selectedPackageId: null, selectedSiteId: null,
      targetMessage: '该站点存在多个执行包，请选择本次执行包', packages: candidatePackages, hasAmbiguity: true, canEnter: false };
  }
  if (candidates.length > 1) {
    return { state: 'selecting', selectedPackageId: null, selectedSiteId: null,
      targetMessage: null, packages: candidatePackages, hasAmbiguity: true, canEnter: false };
  }
  const pkg = candidates[0];
  const targetedSite = t.siteId ? pkg.sites.find(site => sameId(site.siteId, t.siteId)) : null;
  if (targetedSite && targetedSite.phase === 'closed') {
    return { state: 'selecting', selectedPackageId: pkg.executionPlanId, selectedSiteId: targetedSite.siteId,
      targetMessage: '目标站点已离站，可查看本站结果', packages: candidatePackages, hasAmbiguity: false, canEnter: true, entryMode: 'read_only' };
  }
  const active = pkg.sites.filter(site => site.phase !== 'closed');
  if (targetedSite) {
    return { state: 'ready_to_enter', selectedPackageId: pkg.executionPlanId, selectedSiteId: targetedSite.siteId,
      targetMessage: null, packages: candidatePackages, hasAmbiguity: false, canEnter: true };
  }
  if (active.length === 1) {
    return { state: 'ready_to_enter', selectedPackageId: pkg.executionPlanId, selectedSiteId: active[0].siteId,
      targetMessage: null, packages: candidatePackages, hasAmbiguity: false, canEnter: true };
  }
  if (!active.length) {
    return { state: 'closed_only', selectedPackageId: pkg.executionPlanId, selectedSiteId: null,
      targetMessage: '今日站点均已闭环', packages: candidatePackages, hasAmbiguity: false, canEnter: false };
  }
  return { state: 'selecting', selectedPackageId: pkg.executionPlanId, selectedSiteId: null,
    targetMessage: null, packages: candidatePackages, hasAmbiguity: true, canEnter: false };
}

function uniqueExecutableTarget(packages, source) {
  const resolved = resolveExecutionTarget(packages, {});
  if (resolved.state !== 'ready_to_enter' || !resolved.selectedPackageId || !resolved.selectedSiteId) {
    return null;
  }
  const pkg = resolved.packages.find(item => sameId(item.executionPlanId, resolved.selectedPackageId));
  const site = pkg && (pkg.sites || []).find(item => sameId(item.siteId, resolved.selectedSiteId));
  if (!pkg || !site || site.phase === 'closed') return null;
  return normalizeExecutionTarget({
    executionPlanId: pkg.executionPlanId,
    scheduleId: pkg.scheduleId,
    workDate: pkg.workDate,
    siteId: site.siteId,
    source: source || 'generic',
  });
}

function closedResultPackages(packages, target) {
  const list = Array.isArray(packages) ? packages : [];
  const scoped = hasExactTarget(target)
    ? resolveExecutionTarget(list, target).packages
    : list;
  return scoped.map(pkg => {
    const sites = (pkg.sites || []).filter(site => site.phase === 'closed');
    if (!sites.length) return null;
    const totalKnown = sites.every(site => site.total !== null);
    const completedKnown = sites.every(site => site.completed !== null);
    const abnormalKnown = sites.every(site => site.abnormal !== null);
    return Object.assign({}, pkg, {
      sites,
      siteCountText: sites.length + ' 个站点',
      total: totalKnown ? sites.reduce((sum, site) => sum + site.total, 0) : null,
      completed: completedKnown ? sites.reduce((sum, site) => sum + site.completed, 0) : null,
      abnormal: abnormalKnown ? sites.reduce((sum, site) => sum + site.abnormal, 0) : null,
    });
  }).filter(Boolean);
}

function buildScheduleExecutionTarget(detail, scheduleId) {
  const source = detail || {};
  const rework = source.execution_status === 'rework'
    ? source.rework_execution_target : null;
  if (rework) {
    return normalizeExecutionTarget({
      scheduleId: rework.schedule_id || scheduleId,
      executionPlanId: rework.execution_plan_id,
      workDate: rework.work_date,
      siteId: rework.site_id,
      itemId: rework.item_id,
      reworkOnly: true,
      source: 'plan_detail_rework',
    });
  }
  const execution = source.execution_status !== 'rework'
    ? source.execution_target : null;
  if (execution) {
    return normalizeExecutionTarget({
      scheduleId: execution.schedule_id || scheduleId,
      executionPlanId: execution.execution_plan_id,
      workDate: execution.work_date,
      siteId: execution.site_id,
      source: 'plan_detail_execution',
    });
  }
  const tasks = Array.isArray(source.generated_site_tasks) ? source.generated_site_tasks : [];
  const plans = Array.from(new Set(tasks.map(task => positiveId(task.plan_id)).filter(Boolean)));
  const dates = Array.from(new Set(tasks.map(task => String(task.execution_date || task.date || '')).filter(Boolean)));
  const sites = Array.from(new Set(tasks.map(task => positiveId(task.site_id)).filter(Boolean)));
  return normalizeExecutionTarget({
    scheduleId,
    executionPlanId: plans.length === 1 ? plans[0] : null,
    workDate: dates.length === 1 ? dates[0] : null,
    siteId: sites.length === 1 ? sites[0] : null,
    source: 'plan_detail',
  });
}

function itemBelongsToCategories(categories, itemId) {
  if (!positiveId(itemId)) return true;
  return (Array.isArray(categories) ? categories : []).some(category =>
    (category.items || []).some(item => sameId(item.item_id, itemId)));
}

function shouldApplyAsyncResult(alive, responseId, currentId, contextMatches) {
  return !!alive && responseId === currentId && contextMatches !== false;
}

module.exports = {
  normalizeExecutionTarget,
  hasExactTarget,
  buildPackageOption,
  resolveExecutionTarget,
  closedResultPackages,
  matchesExecutionTarget,
  packageIsFullyClosed,
  uniqueExecutableTarget,
  buildScheduleExecutionTarget,
  itemBelongsToCategories,
  shouldApplyAsyncResult,
};
