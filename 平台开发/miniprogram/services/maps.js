const { resolveUploadUrl } = require('../utils/url.js');

// 枚举中文映射（界面禁英文，枚举走集中映射）
const WORKORDER_STATUS = {
  pending: '待受理', accepted: '已受理', dispatched: '已派发',
  in_progress: '处置中', reviewing: '审核中', resolved: '已解决', closed: '已完成'
};
const WORKORDER_LEVEL = { normal: '普通', urgent: '紧急', critical: '严重' };
const WORKORDER_SOURCE = {
  auto: '自动派发', auto_created: '自动派发', patrol: '巡检生成',
  inspection: '巡检生成', manual: '手动创建', report: '上报工单', hotline: '热线',
  escalation: '告警升级', alert_convert: '告警转工单', alert_auto: '告警自动派发',
  superior: '上级派发', auto_inspection: '巡检自动'
};
// 工单状态→配色后缀（蓝=待处理/已受理、橙=处置中、黄=审核中、绿=已完成、灰=未知）
const WORKORDER_STATUS_CLS = {
  pending: 'blue', accepted: 'blue', dispatched: 'blue',
  in_progress: 'orange', reviewing: 'yellow', resolved: 'green', closed: 'green'
};
// 工单等级→配色后缀（红=严重、橙=紧急、灰=普通/未知）
const WORKORDER_LEVEL_CLS = { critical: 'red', urgent: 'orange', normal: 'gray' };
// 统一注入工单中文与配色；未知来源兜底“其他来源”，杜绝英文透出
function workorderCn(w) {
  if (!w) return w;
  const s = WORKORDER_SOURCE[w.source];
  let imagesArr = [];
  try { imagesArr = w.images ? JSON.parse(w.images) : []; } catch (e) { imagesArr = []; }
  if (!Array.isArray(imagesArr)) imagesArr = [];
  return Object.assign({}, w, {
    level_cn: map(WORKORDER_LEVEL, w.level, '未知'),
    status_cn: map(WORKORDER_STATUS, w.status, '未知'),
    status_cls: WORKORDER_STATUS_CLS[w.status] || 'gray',
    level_cls: WORKORDER_LEVEL_CLS[w.level] || 'gray',
    source_cn: s != null ? s : '其他来源',
    checked_in: !!w.check_in_time,
    images_arr: imagesArr.map(resolveUploadUrl),
    has_images: imagesArr.length > 0
  });
}
// 巡检分类中文映射（严禁 wxml 直接写英文 category）
const INSPECTION_CATEGORY = {
  equipment_ops: '设备运维', log_books: '登记本', qaqc_calibration: '质控校准',
  reagent: '试剂管理', site_check: '站点检查'
};
const ALERT_LEVEL = { red: '红色', orange: '橙色', yellow: '黄色', blue: '蓝色' };
// 告警等级→配色后缀（红橙黄蓝，对应 dot-/tag- 后缀；缺省灰）
const ALERT_LEVEL_CLS = { red: 'red', orange: 'orange', yellow: 'yellow', blue: 'blue' };
function alertLevelCls(key) { return ALERT_LEVEL_CLS[key] || 'gray'; }
const ALERT_STATUS = { pending: '待处理', acknowledged: '已确认', resolved: '已处理' };
const SITE_TYPE = {
  water_quality: '水质自动站', manual_station: '水质手动站',
  drinking_source: '饮用水源站', cross_boundary: '跨界断面站', groundwater: '地下水站'
};
const FREQ = { daily: '每日', weekly: '每周', monthly: '每月', hourly: '每小时' };
const ROLE = { admin: '管理员', operator: '运维人员', viewer: '查看员' };
const RESULT = { normal: '正常', abnormal: '异常', pending: '待检' };

// 监测指标中文（/api/alerts 仅返回原始 metric）
const METRIC = {
  ph: 'pH', ammonia: '氨氮', nh3: '氨氮', tp: '总磷', tn: '总氮',
  cod: '化学需氧量', codmn: '高锰酸盐指数', do: '溶解氧',
  turbidity: '浊度', conductivity: '电导率', wtemp: '水温', flow: '流量',
  level: '水位', rainfall: '雨量', cyanobacteria: '蓝藻'
};
function metricCn(key) {
  if (!key) return '';
  return METRIC[('' + key).toLowerCase()] || '未知指标';
}

// 计划调度状态
const PLAN_SCHEDULE_STATUS = {
  draft: '草稿', submitted: '待审批', approved: '已通过',
  rejected: '已退回', modifying: '变更中', change_submitted: '变更待审', archived: '已归档'
};
const PLAN_SCHEDULE_STATUS_CLS = {
  draft: 'gray', submitted: 'blue', approved: 'green',
  rejected: 'red', modifying: 'orange', change_submitted: 'orange', archived: 'gray'
};
// 排程类型
const SCHEDULE_TYPE = { weekly: '周检', monthly: '月检', quarterly: '季检', yearly: '年检' };

function map(obj, key, def) {
  if (key == null) return def || '';
  return obj[key] != null ? obj[key] : (def != null ? def : key);
}

module.exports = {
  WORKORDER_STATUS, WORKORDER_STATUS_CLS, WORKORDER_LEVEL, WORKORDER_LEVEL_CLS,
  WORKORDER_SOURCE, workorderCn, INSPECTION_CATEGORY,
  ALERT_LEVEL, ALERT_LEVEL_CLS, alertLevelCls, ALERT_STATUS, SITE_TYPE, FREQ, ROLE, RESULT, map,
  METRIC, metricCn,
  PLAN_SCHEDULE_STATUS, PLAN_SCHEDULE_STATUS_CLS, SCHEDULE_TYPE
};
