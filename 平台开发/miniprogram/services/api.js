// 全部后端契约封装（已精确核对 app.py）
const { request } = require('../utils/request.js');

const api = {
  // 登录（工号密码复用网页端）
  login: (username, password) =>
    request('/api/auth/login', 'POST', { username, password }, { retry: 1, queue: false }),

  // 绑定微信 openid（wx.login 的 code → 服务端换取并落库，用于订阅消息）
  bindOpenId: (code) =>
    request('/api/mobile/bind-openid', 'POST', { code }, { retry: 1, queue: false }),

  // 今日聚合
  myToday: () => request('/api/mobile/my-today', 'GET'),

  // 今日已批准巡检执行包（巡检 Tab 的唯一入口）
  todayExecution: () => request('/api/mobile/today-execution', 'GET'),
  executionSiteTasks: (planId, siteId) =>
    request('/api/mobile/execution-plans/' + planId + '/sites/' + siteId, 'GET'),

  // 站点任务（含已完成）
  siteTasks: (siteId) => request('/api/mobile/site-tasks/' + siteId, 'GET'),

  // 提交检查项；photo_urls 必须为 JSON 字符串
  submitItem: (payload) => request('/api/mobile/submit-item', 'POST', payload, { queue: false }),

  // 到站打卡
  checkIn: (payload, managedByOutbox) =>
    request('/api/mobile/check-in', 'POST', payload, { queue: !managedByOutbox }),

  // 位置校准
  calibrate: (siteId, lat, lng) =>
    request('/api/sites/' + siteId + '/calibrate', 'PUT', { lat, lng }),

  // 上传站点影像（base64）；弱网失败自动进入失败队列待重传
  uploadSitePhoto: (siteId, image, idempotencyKey) =>
    request('/api/mobile/upload-site-photo', 'POST', {
      site_id: siteId, image, _idempotency_key: idempotencyKey || ''
    }, { queue: false }),

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
  uploadWorkorderImage: (orderNo, image) =>
    request('/api/mobile/workorder/' + orderNo + '/image', 'POST', { image }),

  // 极简用车申请（仅需事由，可关联工单/站点）
  applyVehicle: (payload) => request('/api/vehicle/applications', 'POST', payload),

  // 备件申请（关联工单/站点）
  applyParts: (payload) => request('/api/parts/requests', 'POST', payload),

  // 车辆列表（用车申请关联下拉，可选）
  vehicles: () => request('/api/vehicles', 'GET'),

  // 备件库存列表（备件申请关联下拉，可选）
  partsInventory: () => request('/api/parts/inventory', 'GET'),

  // 工单列表（无 _cn，需前端映射）
  workorders: (status) =>
    request('/api/workorders' + (status ? '?status=' + status : ''), 'GET'),

  // 工单状态流转；closed 仅管理员；extra 透传（移动端强制携带 client:'mobile' 触发流程门禁）
  updateWorkorderStatus: (orderNo, status, extra) =>
    request('/api/workorders/' + orderNo + '/status', 'PUT', Object.assign({ status }, extra || {})),

  // 提交核验（in_progress -> reviewing）；移动端携带 client 触发影像门禁
  submitWorkorderReview: (orderNo) =>
    request('/api/workorders/' + orderNo + '/submit-review', 'POST', { client: 'mobile' }),

  // 核验通过（reviewing -> closed，专用端点，后端已接审批结果推送）
  approveWorkorder: (orderNo) =>
    request('/api/workorders/' + orderNo + '/approve', 'POST', {}),

  // 核验退回（reviewing -> in_progress，仅管理员，后端已接审批结果推送）
  rejectWorkorder: (orderNo) =>
    request('/api/workorders/' + orderNo + '/reject', 'POST', {}),

  // 告警列表（无 _cn，需前端映射）
  alerts: (status) =>
    request('/api/alerts' + (status ? '?status=' + status : ''), 'GET'),

  // 确认告警
  acknowledgeAlert: (id) =>
    request('/api/alerts/' + id + '/acknowledge', 'POST', {}),

  // 通知
  notifications: (page) =>
    request('/api/notifications?page=' + (page || 1) + '&limit=50', 'GET'),
  unreadCount: () => request('/api/notifications/unread-count', 'GET'),
  readNotification: (id) => request('/api/notifications/' + id + '/read', 'PUT', {}),
  readAllNotifications: () => request('/api/notifications/read-all', 'PUT', {}),

  // 移动端审核（管理者/审批者；复用现有审核端点，token 需 admin/manager）
  auditPending: () => request('/api/audit/pending', 'GET'),

  // 巡检检查项审核（source_type=inspaction）
  reviewInspectionItem: (id, status, comment) =>
    request('/api/inspection-v2/items/' + id + '/review', 'PUT', { status, review_comment: comment || '' }),

  // 照片审核（workorder_photo / photo_review）
  reviewPhoto: (ids, action, reason) =>
    request('/api/operation-attachments/review', 'POST', { attachment_ids: ids, action, reject_reason: reason || '' }),

  // 备件预申报审核（source_type=parts_request，来自 parts_requests 表）
  approvePartsRequest: (id) => request('/api/inspection-v2/parts-request/' + id + '/approve', 'PUT'),
  rejectPartsRequest: (id) => request('/api/inspection-v2/parts-request/' + id + '/reject', 'PUT', {}),

  // 备件申请审核（source_type=spare_part_request，来自 spare_part_requests 表）
  approveSparePart: (id) => request('/api/parts/requests/' + id + '/approve', 'PUT'),
  rejectSparePart: (id) => request('/api/parts/requests/' + id + '/reject', 'PUT'),

  // 用车申请审核（source_type=vehicle_application，仅通过）
  approveVehicle: (id) => request('/api/vehicle/applications/' + id + '/approve', 'POST', {}),

  // ===== 计划调度（排程） =====
  // 我的排程列表（运维只看自己的）
  planSchedules: () => request('/api/plan-schedules', 'GET'),

  // 排程详情（含 site_map、generated_plans）
  planScheduleDetail: (id) => request('/api/plan-schedules/' + id, 'GET'),

  // 创建排程（submit=true 直接提交审批）
  createPlanSchedule: (payload) => request('/api/plan-schedules', 'POST', payload),

  // 编辑排程（仅 draft/rejected 可编辑）
  updatePlanSchedule: (id, payload) => request('/api/plan-schedules/' + id, 'PUT', payload),

  // 提交排程审批
  submitPlanSchedule: (id) => request('/api/plan-schedules/' + id + '/submit', 'POST', {}),

  // 发起变更（已通过的计划，approved → modifying）
  requestPlanScheduleChange: (id, changeReason) =>
    request('/api/plan-schedules/' + id + '/request-change', 'POST', { change_reason: changeReason }),

  // 排程校验（车辆冲突等）
  validatePlanSchedule: (payload) => request('/api/plan-schedules/validate', 'POST', payload),

  // 智能建议（站点优先级+工单顺路）
  planSuggestions: (userId) =>
    request('/api/plan-schedules/suggestions?user_id=' + userId, 'GET')
};

module.exports = api;
