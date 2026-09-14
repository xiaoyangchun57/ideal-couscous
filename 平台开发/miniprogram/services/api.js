// 全部后端契约封装（已精确核对 app.py）
const { request } = require('../utils/request.js');

function vehicleListQuery(options) {
  if (!options) return '';
  const pairs = [];
  ['scope', 'page', 'limit', 'offset', 'vehicle_id', 'applicant_id', 'application_id', 'status'].forEach(key => {
    const value = options[key];
    if (value !== undefined && value !== null && value !== '') pairs.push(key + '=' + encodeURIComponent(value));
  });
  return pairs.length ? '?' + pairs.join('&') : '';
}

const api = {
  // 登录（工号密码复用网页端）
  login: (username, password) =>
    request('/api/auth/login', 'POST', { username, password }, { retry: 1, queue: false }),
  logout: () => request('/api/auth/logout', 'POST', {}, { retry: 0, queue: false }),
  changePassword: (currentPassword, newPassword) =>
    request('/api/auth/change-password', 'POST', {
      current_password: currentPassword,
      new_password: newPassword,
    }, { retry: 1, queue: false }),

  // 绑定微信 openid（wx.login 的 code → 服务端换取并落库，用于订阅消息）
  bindOpenId: (code) =>
    request('/api/mobile/bind-openid', 'POST', { code }, { retry: 1, queue: false }),
  subscriptionTemplates: () =>
    request('/api/mobile/subscription-templates', 'GET', {}, { retry: 1, queue: false }),

  // 今日聚合
  myToday: () => request('/api/mobile/my-today', 'GET'),
  // 当前登录人服务端授权的站点；手工异常上报不能使用登录缓存代替此范围。
  sites: () => request('/api/sites', 'GET', {}, { queue: false }),
  stationMonitoringSites: () => request('/api/station-monitoring/sites', 'GET', {}, { queue: false }),
  stationMonitoringOverview: (siteId) => request('/api/station-monitoring/sites/' + encodeURIComponent(siteId) + '/overview', 'GET', {}, { queue: false }),
  anomalyCodes: () => request('/api/anomaly-codes', 'GET'),
  // 失败必须由上报页保留草稿并显式重试，不能在未知网络结果下悄悄排队二次写入。
  submitManualReport: (payload) => request('/api/manual-reports', 'POST', payload, { queue: false }),
  manualReports: (status) => request('/api/manual-reports' + (status ? '?status=' + status : ''), 'GET'),

  // 今日已批准巡检执行包（巡检 Tab 的唯一入口）
  todayExecution: () => request('/api/mobile/today-execution', 'GET'),
  executionSiteTasks: (planId, siteId, options) =>
    request('/api/mobile/execution-plans/' + planId + '/sites/' + siteId
      + (options && options.reworkOnly ? '?scope=rework' : ''), 'GET'),
  checkOutExecutionSite: (planId, siteId, payload) =>
    request('/api/mobile/execution-plans/' + planId + '/sites/' + siteId + '/check-out', 'POST', payload || {}),
  executionSiteReagents: (planId, siteId) =>
    request('/api/mobile/execution-plans/' + planId + '/sites/' + siteId + '/reagents', 'GET'),
  replaceExecutionReagent: (planId, siteId, payload) =>
    request('/api/mobile/execution-plans/' + planId + '/sites/' + siteId + '/reagent-replacements', 'POST', payload),
  // 试剂更换后的标定；保留 reagent-qc 路径以兼容现有后端数据。
  submitExecutionReagentQc: (planId, siteId, payload) =>
    request('/api/mobile/execution-plans/' + planId + '/sites/' + siteId + '/reagent-qc', 'POST', payload),
  // 出发前资源软确认：仅留痕，不锁车、不扣库，也不阻断现场巡检。
  confirmDepartureResources: (planId, payload) =>
    request('/api/mobile/execution-plans/' + planId + '/departure-confirmation', 'POST', payload || {}),
  // 今日执行包现场领用：提交成功后立即扣减库存，并返回最新计划/领用数量。
  issueExecutionParts: (planId, items) =>
    request('/api/mobile/execution-plans/' + planId + '/parts/issue', 'POST', { items: items || [] }),
  vehicleInspectionTemplate: () => request('/api/vehicle/inspection-template', 'GET'),
  submitVehicleInspection: (payload) => request('/api/vehicle/inspections', 'POST', payload),
  checkOutVehicle: (payload) => request('/api/vehicle/use-records', 'POST', payload),
  returnVehicle: (recordId, payload) => request('/api/vehicle/use-records/' + recordId + '/return', 'POST', payload),
  refuelVehicleUse: (recordId, payload) => request('/api/mobile/vehicle-use-records/' + recordId + '/refueling', 'POST', payload),
  reportVehicleFault: (recordId, payload) => request('/api/mobile/vehicle-use-records/' + recordId + '/faults', 'POST', payload),
  vehicleUseRecords: (options) => request('/api/vehicle/use-records' + vehicleListQuery(options), 'GET'),
  vehicleApplications: (options) => request('/api/vehicle/applications' + vehicleListQuery(options), 'GET'),
  requestReworkResource: (planId, payload) =>
    request('/api/inspection-v2/rework-plans/' + planId + '/resource-request', 'POST', payload),
  extendVehicleApplication: (applicationId, endDate) =>
    request('/api/vehicle/applications/' + applicationId + '/extend', 'POST', { end_date: endDate }),

  // 站点任务（含已完成）
  siteTasks: (siteId) => request('/api/mobile/site-tasks/' + siteId, 'GET'),

  // 提交检查项；photo_urls 必须为 JSON 字符串
  submitItem: (payload) => request('/api/mobile/submit-item', 'POST', payload, { queue: false }),

  // 到站打卡
  checkIn: (payload, managedByOutbox) =>
    request('/api/mobile/check-in', 'POST', payload, { queue: !managedByOutbox }),

  // 位置校准
  calibrate: (siteId, lat, lng, extra) =>
    request('/api/sites/' + siteId + '/calibrate', 'PUT', Object.assign({ lat, lng }, extra || {})),

  // 上传站点影像（base64）；弱网失败自动进入失败队列待重传
  uploadSitePhoto: (siteId, image, idempotencyKey, metadata) =>
    request('/api/mobile/upload-site-photo', 'POST', {
      ...(metadata || {}), site_id: siteId, image,
      _idempotency_key: idempotencyKey || (metadata && metadata._idempotency_key) || ''
    }, { queue: false, timeout: 30000, retry: 1 }),
  createPhotoCaptureSession: (payload) =>
    request('/api/mobile/photo-capture-session', 'POST', payload, { queue: false, retry: 0 }),
  // 删除尚未提交到巡检、工单或异常上报的现场照片
  deletePendingSitePhoto: (url) => request(
    '/api/mobile/site-photos/delete', 'POST', { url }, { queue: false }
  ),
  purgeRejectedInspectionPhoto: (attachmentId) => request(
    '/api/attachments/' + attachmentId + '/purge-rejected', 'POST', {}, { queue: false }
  ),
  getRejectedInspectionPurgeBatch: (attachmentId) => request(
    '/api/attachments/' + attachmentId + '/purge-rejected-batch', 'GET', {}, { queue: false }
  ),
  purgeRejectedInspectionPhotoBatch: (attachmentId) => request(
    '/api/attachments/' + attachmentId + '/purge-rejected-batch', 'POST', {}, { queue: false }
  ),

  trackEvent: (eventName, context) => request('/api/telemetry/events', 'POST', {
    event_id: 'evt_' + Date.now() + '_' + Math.floor(Math.random() * 1e6),
    event_name: eventName,
    occurred_at: new Date().toISOString(),
    context: context || {}
  }, { retry: 0, queue: false }).catch(() => null),

  // 删除检查项照片
  deletePhoto: (itemId, photoIndex) =>
    request('/api/mobile/delete-photo', 'POST', { item_id: itemId, photo_index: photoIndex }),

  // 工单处置影像上传（追加到工单 images，移动端流程卡控用）
  uploadWorkorderImage: (orderNo, image, idempotencyKey, metadata) =>
    request('/api/mobile/workorder/' + orderNo + '/image', 'POST', {
      ...(metadata || {}), image, _idempotency_key: idempotencyKey || ''
    }, { queue: false, timeout: 30000, retry: 1 }),

  deleteWorkorderImage: (orderNo, url) =>
    request('/api/mobile/workorder/' + orderNo + '/image/delete', 'POST', { url }),

  // 用车申请；无车执行仅允许关联具体工单并登记例外原因。
  applyVehicle: (payload) => request('/api/vehicle/applications', 'POST', payload, { queue: false }),

  // 备件申请（关联工单/站点）
  applyParts: (payload) => request('/api/parts/requests', 'POST', payload, { queue: false }),
  myPartsRequests: () => request('/api/parts/requests/mine', 'GET'),
  issuePartsRequest: (id, items) => request('/api/parts/requests/' + id + '/issue', 'POST', { items }),
  orderPartsRequest: (id, payload) => request('/api/parts/requests/' + id + '/order', 'POST', payload || {}),
  fulfillPartsRequest: (id, payload) => request('/api/parts/requests/' + id + '/fulfill', 'POST', payload || {}),
  uploadPartsEvidence: (image) => request('/api/parts/requests/evidence', 'POST', { image }, { queue: false }),

  // 车辆列表（用车申请关联下拉，可选）
  vehicles: () => request('/api/vehicles', 'GET'),

  // 备件库存列表（备件申请关联下拉，可选）
  partsInventory: () => request('/api/parts/inventory', 'GET'),

  // 工单列表（无 _cn，需前端映射）
  workorders: (status) =>
    request('/api/workorders' + (status ? '?status=' + status : ''), 'GET'),
  workorderDetail: (orderNo) => request('/api/workorders/' + encodeURIComponent(orderNo), 'GET', {}, { queue: false }),
  workorderRelated: (orderNo) => request('/api/workorders/' + encodeURIComponent(orderNo) + '/related', 'GET', {}, { queue: false }),

  // 工单状态流转；closed 仅管理员；extra 透传（移动端强制携带 client:'mobile' 触发流程门禁）
  updateWorkorderStatus: (orderNo, status, extra) =>
    request('/api/workorders/' + orderNo + '/status', 'PUT', Object.assign({ status }, extra || {})),

  // 提交核验（in_progress -> reviewing）；移动端携带 client 触发影像门禁
  submitWorkorderReview: (orderNo, resolutionNote) =>
    request('/api/workorders/' + orderNo + '/submit-review', 'POST', {
      client: 'mobile', resolution_note: resolutionNote
    }),

  // 核验通过（reviewing -> closed，专用端点，后端已接审批结果推送）
  approveWorkorder: (orderNo, payload) =>
    request('/api/workorders/' + orderNo + '/approve', 'POST', payload || {}),

  // 核验退回（reviewing -> in_progress，仅管理员，后端已接审批结果推送）
  rejectWorkorder: (orderNo, reason) =>
    request('/api/workorders/' + orderNo + '/reject', 'POST', { reason: reason || '' }),

  // 告警列表（无 _cn，需前端映射）
  alerts: (status) =>
    request('/api/alerts' + (status ? '?status=' + status : ''), 'GET'),
  alertDetail: (id) => request('/api/alerts/' + encodeURIComponent(id), 'GET', {}, { queue: false }),

  // 确认告警
  acknowledgeAlert: (id) =>
    request('/api/alerts/' + id + '/acknowledge', 'POST', {}),

  // 通知
  notifications: (page, status, notificationId) =>
    request('/api/notifications?page=' + (page || 1) + '&limit=50&status=' + (status || 'all')
      + (notificationId ? '&notification_id=' + encodeURIComponent(notificationId) : ''), 'GET'),
  unreadCount: () => request('/api/notifications/unread-count', 'GET'),
  readNotification: (id) => request('/api/notifications/' + id + '/read', 'PUT', {}),
  readAllNotifications: () => request('/api/notifications/read-all', 'PUT', {}),

  // 移动端审核（管理者/审批者；复用现有审核端点，token 需 admin/manager）
  auditPending: () => request('/api/audit/pending', 'GET'),
  auditTargetStatus: (targetType, targetId, cycleKey) => request('/api/audit/target-status?target_type='
    + encodeURIComponent(targetType) + '&target_id=' + encodeURIComponent(targetId)
    + (cycleKey ? '&cycle_key=' + encodeURIComponent(cycleKey) : ''), 'GET'),
  dataReviewDetail: (id) => request('/api/data-reviews/' + id, 'GET'),
  reviewDataReview: (id, action, reason) =>
    request('/api/data-reviews/' + id + '/manual-review', 'POST', {
      action: action || 'approve', reason: reason || ''
    }),

  // 巡检检查项审核（source_type=inspaction）
  reviewInspectionItem: (id, status, comment) =>
    request('/api/inspection-v2/items/' + id + '/review', 'PUT', {
      action: status === 'rejected' ? 'reject' : 'approve',
      comment: comment || '',
      status,
      review_comment: comment || ''
    }),
  reviewInspectionBatch: (itemIds, action, reason) => {
    const ids = Array.isArray(itemIds) ? itemIds : [];
    return request('/api/inspection-v2/items/batch-review', 'POST', action === 'approve'
      ? { approve_ids: ids, reject_items: [] }
      : { approve_ids: [], reject_items: ids.map(id => ({ id, reason: reason || '' })) });
  },

  // 照片审核（workorder_photo / photo_review）
  reviewPhoto: (ids, action, reason) =>
    request('/api/operation-attachments/review', 'POST', { attachment_ids: ids, action, reject_reason: reason || '' }),
  reviewPhotoSelection: (approveIds, rejectIds, reason) =>
    request('/api/operation-attachments/review', 'POST', {
      approve_ids: approveIds || [], reject_ids: rejectIds || [], reject_reason: reason || ''
    }),
  reviewInspectionPhotoSelection: (approveIds, rejectIds, approveItemIds, reason) =>
    request('/api/operation-attachments/review', 'POST', {
      approve_ids: approveIds || [],
      reject_ids: rejectIds || [],
      approve_item_ids: approveItemIds || [],
      reject_reason: reason || ''
    }),

  // 备件预申报审核（source_type=parts_request，来自 parts_requests 表）
  approvePartsRequest: (id) => request('/api/inspection-v2/parts-request/' + id + '/approve', 'PUT'),
  rejectPartsRequest: (id, reason) => request('/api/inspection-v2/parts-request/' + id + '/reject', 'PUT', {
    comment: reason || ''
  }),

  // 备件申请审核（source_type=spare_part_request，来自 spare_part_requests 表）
  approveSparePart: (id) => request('/api/parts/requests/' + id + '/approve', 'PUT', {
    request_type: 'spare_part_request'
  }),
  rejectSparePart: (id, reason) => request('/api/parts/requests/' + id + '/reject', 'PUT', {
    comment: reason || '', request_type: 'spare_part_request'
  }),

  // 用车申请审核（source_type=vehicle_application，仅通过）
  approveVehicle: (id, action, reason) => request('/api/vehicle/applications/' + id + '/approve', 'POST', {
    action: action || 'approve', reject_reason: reason || ''
  }),

  // ===== 计划调度（排程） =====
  // 默认个人范围；管理员/审核员可在计划页显式切换团队范围。
  planSchedules: (team) => request('/api/plan-schedules' + (team ? '' : '?mine=1'), 'GET'),

  // 排程详情（含 site_map、generated_plans）
  planScheduleDetail: (id) => request('/api/plan-schedules/' + id, 'GET'),

  // 创建排程（submit=true 直接提交审批）
  createPlanSchedule: (payload) => request('/api/plan-schedules', 'POST', payload),

  // 编辑排程（仅 draft/rejected 可编辑）
  updatePlanSchedule: (id, payload, options) =>
    request('/api/plan-schedules/' + id, 'PUT', payload, options),
  deletePlanSchedule: (id) => request('/api/plan-schedules/' + id, 'DELETE'),

  // 常用排程：收藏的是相对日期模板，从收藏生成的始终是可编辑草稿。
  planScheduleFavorites: () => request('/api/plan-schedule-favorites', 'GET'),
  addPlanScheduleFavorite: (scheduleId, name) =>
    request('/api/plan-schedule-favorites', 'POST', { schedule_id: scheduleId, name: name || '' }),
  deletePlanScheduleFavorite: (id) => request('/api/plan-schedule-favorites/' + id, 'DELETE'),
  createDraftFromPlanScheduleFavorite: (id, periodStart) =>
    request('/api/plan-schedule-favorites/' + id + '/draft', 'POST', { period_start: periodStart }),

  // 提交排程审批
  submitPlanSchedule: (id, version) =>
    request('/api/plan-schedules/' + id + '/submit', 'POST', { version }, { queue: false }),
  approvePlanSchedule: (id) => request('/api/plan-schedules/' + id + '/approve', 'POST', {}),
  rejectPlanSchedule: (id, reason) => request('/api/plan-schedules/' + id + '/reject', 'POST', { reason: reason || '' }),

  // 发起变更（已通过的计划，approved → modifying）
  requestPlanScheduleChange: (id, changeReason) =>
    request('/api/plan-schedules/' + id + '/request-change', 'POST', { change_reason: changeReason }),

  cancelPlanSchedule: (id, reason, version) =>
    request('/api/plan-schedules/' + id + '/cancel', 'POST', {
      reason: String(reason || '').trim(), version
    }, { queue: false }),

  // 排程校验（车辆冲突等）
  validatePlanSchedule: (payload) => request('/api/plan-schedules/validate', 'POST', payload),

  inspectionConfigMatches: (siteId, scheduleType) => request(
    '/api/inspection-v2/configs/match?site_id=' + encodeURIComponent(siteId)
      + '&schedule_type=' + encodeURIComponent(scheduleType || 'weekly'), 'GET'),

  // 智能建议（站点优先级+工单顺路）
  planSuggestions: (userId, scheduleType) =>
    request('/api/plan-schedules/suggestions?user_id=' + userId + '&schedule_type=' + (scheduleType || 'weekly'), 'GET')
};

module.exports = api;
