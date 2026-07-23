/**
 * ══════════════════════════════════════════════
 *  水质监测智慧运营平台 — 常量定义
 *  ⚠️ 此为正式源码，`frontend/shared/constants.js` 为旧版同步副本
 *  新增常量请仅在此处添加，确保单源一致
 * ══════════════════════════════════════════════
 */

// Station type mappings - only water quality monitoring stations
export const stationTypeMap = {
  water_quality: '水质监测站',
};

// Work order status mappings
export const orderStatusMap = {
  pending: '待受理',
  accepted: '已受理',
  generated: '已生成',
  dispatched: '已派发',
  in_progress: '处置中',
  reviewing: '待审核',
  closed: '已完成',
};

// Work order level mappings
export const orderLevelMap = {
  normal: '一般',
  medium: '一般',
  urgent: '紧急',
  critical: '重大',
  red: '重大',
  orange: '紧急',
  yellow: '一般',
  blue: '一般',
  high: '紧急',
  low: '一般',
};

// Work order source mappings
export const orderSourceMap = {
  auto: '自动',
  patrol: '巡查',
  manual: '人工',
  manual_report: '人工上报',
  superior: '上级',
  hotline: '热线',
  inspection: '巡检',
  alert_convert: '告警转工单',
  alert_auto: '告警自动生成',
  alert: '告警',
  escalation: '告警升级',
};

// Metric name mappings (水质9参数 + 数据质量)
export const metricMap = {
  codmn: '高锰酸盐指数',
  ammonia: '氨氮',
  total_phosphorus: '总磷',
  total_nitrogen: '总氮',
  water_temp: '水温',
  dissolved_oxygen: '溶解氧',
  ph: 'pH',
  turbidity: '浊度',
  conductivity: '电导率',
  device_status: '设备状态',
  data_gap: '数据缺失',
  data_spike: '数据突变',
  data_freeze: '数据冻结',
  temperature: '温度',
};

// Metric with units (for charts / detail views)
export const metricUnitMap = {
  codmn: { name: '高锰酸盐指数', unit: 'mg/L' },
  ammonia: { name: '氨氮', unit: 'mg/L' },
  total_phosphorus: { name: '总磷', unit: 'mg/L' },
  total_nitrogen: { name: '总氮', unit: 'mg/L' },
  water_temp: { name: '水温', unit: '°C' },
  dissolved_oxygen: { name: '溶解氧', unit: 'mg/L' },
  ph: { name: 'pH', unit: '' },
  turbidity: { name: '浊度', unit: 'NTU' },
  conductivity: { name: '电导率', unit: 'μS/cm' },
};

// Inspection item result mappings
export const inspectionItemResultMap = {
  normal: '已正常',
  abnormal: '异常上报',
  anomaly_reported: '异常已上报',
  pending: '待执行',
};

// Device type mappings
export const deviceTypeMap = {
  multi_param_analyzer: '多参数水质分析仪',
  ph_meter: 'pH计',
  do_sensor: '溶解氧传感器',
  turbidity_meter: '浊度仪',
  ammonia_analyzer: '氨氮分析仪',
  codmn_analyzer: '高锰酸盐分析仪',
  tp_analyzer: '总磷分析仪',
  tn_analyzer: '总氮分析仪',
  conductivity_meter: '电导率仪',
  thermometer: '温度计',
  submersible_pump: '潜水泵',
  sample_float: '采样浮筒',
  dtu: '数据采集传输终端',
  fire_extinguisher: '灭火器',
  lighting: '照明设备',
  rainfall_gauge: '翻斗雨量计',
  water_level_meter: '水位计',
  hydro_collector: '数据采集器',
  pressure_water_level: '压力式水位计',
  current_meter: '流速仪',
  radar_water_level: '雷达水位计',
};

// Inspection type mappings
export const inspectionTypeMap = {
  daily: '日常',
  weekly: '定期',
  monthly: '月度',
  special: '专项',
};

// Alert level colors
export const alertLevelColor = {
  blue: '#38bdf8',
  yellow: '#facc15',
  orange: '#fb923c',
  red: '#ef4444',
};

export const alertLevelLabel = {
  blue: '蓝色关注',
  yellow: '黄色警示',
  orange: '橙色预警',
  red: '红色警报',
};

// Work order status badge color mapping
export const orderStatusBadge = {
  pending: 'default',
  accepted: 'processing',
  generated: 'cyan',
  dispatched: 'warning',
  in_progress: 'success',
  reviewing: 'processing',
  closed: 'green',
};

// Alert status mappings
export const alertStatusMap = {
  pending: '待处理',
  acknowledged: '处理中',
  resolved: '已办结',
};

// Timeline event type mappings
export const timelineEventMap = {
  alert: '告警',
  order: '工单',
  inspection: '巡检',
  maintenance: '运维',
  sample_check: '采样核查',
  acknowledged: '确认',
  urged: '督办',
  converted: '转工单',
  created: '创建',
  completed: '完成',
  checked: '校验',
  auto_checked: '自动校验',
  alert_generated: '触发告警',
  acceptance: '验收中',
  closed: '已关闭',
};

// ===== 地表水III类标准阈值 (GB 3838-2002) =====
export const WQ_STANDARD = {
  class: 'III',
  ph: { min: 6.0, max: 9.0, unit: '' },
  dissolved_oxygen: { min: 5.0, unit: 'mg/L', direction: 'gte' },
  codmn: { max: 6.0, unit: 'mg/L', direction: 'lte' },
  ammonia: { max: 1.0, unit: 'mg/L', direction: 'lte' },
  total_phosphorus: { max: 0.2, unit: 'mg/L', direction: 'lte' },
  total_nitrogen: { max: 1.0, unit: 'mg/L', direction: 'lte' },
};

// Alert threshold config: warn = 接近超标, critical = 超标
export const WQ_ALERT_THRESHOLDS = {
  ph: { warn_low: 6.5, warn_high: 8.5, crit_low: 6.0, crit_high: 9.0 },
  dissolved_oxygen: { warn: 6.0, crit: 5.0 },
  codmn: { warn: 5.0, crit: 6.0 },
  ammonia: { warn: 0.8, crit: 1.0 },
  total_phosphorus: { warn: 0.15, crit: 0.2 },
  total_nitrogen: { warn: 0.8, crit: 1.0 },
  turbidity: { warn: 10, crit: 20 },
  conductivity: { warn: null, crit: null },
};

// ===== 闭环联动：统一处置结论枚举 =====
export const CONCLUSION_MAP = {
  false_alarm: '误报',
  normal_deviation: '正常偏差',
  equipment_maintenance: '设备维护',
  environmental_factor: '环境因素',
  fixed: '已修复',
  other: '其他',
};

export const CONCLUSION_OPTIONS = Object.keys(CONCLUSION_MAP).map(k => ({ value: k, label: CONCLUSION_MAP[k] }));

// ===== 附件 / 影像资料 统一分类字典 =====
// source_type：照片/资料归属的业务模块（后端 operation_attachments.source_type 同义）
export const attachmentSourceTypeMap = {
  workorder: '工单照片',
  inspection: '巡检照片',
  calibration: '校准照片',
  reagent: '试剂配置照片',
  vehicle: '车辆照片',
  maintenance: '养护记录照片',
  patrol: '巡查照片',
  test: '试验照片',
  site_photo: '站点现场照片',
};

// category：模块内细分场景（用于二级筛选与标签）
export const attachmentCategoryMap = {
  现场照片: '现场照片',
  仪器照片: '仪器照片',
  环境照片: '环境照片',
  签字确认: '签字确认',
  其他: '其他',
  巡检照片: '巡检照片',
  设备照片: '设备照片',
  试剂配置: '试剂配置',
  车辆里程: '车辆里程',
  车辆加油: '车辆加油',
  养护记录: '养护记录',
};

// 来源类型下拉（全量，含已声明未启用的 patrol/test 供筛选）
export const ATTACHMENT_SOURCE_OPTIONS = Object.keys(attachmentSourceTypeMap).map(k => ({
  value: k,
  label: attachmentSourceTypeMap[k],
}));

// 分类下拉（全量）
export const ATTACHMENT_CATEGORY_OPTIONS = Object.keys(attachmentCategoryMap).map(k => ({
  value: k,
  label: attachmentCategoryMap[k],
}));

// 分类标签颜色（antd Tag color）
export const attachmentCategoryColor = {
  现场照片: 'green',
  仪器照片: 'blue',
  环境照片: 'cyan',
  签字确认: 'purple',
  其他: 'default',
  巡检照片: 'geekblue',
  设备照片: 'volcano',
  试剂配置: 'magenta',
  车辆里程: 'orange',
  车辆加油: 'lime',
  养护记录: 'red',
};

// 审核状态字典（operation_attachments.review_status）
export const attachmentReviewStatusMap = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已驳回',
};
export const ATTACHMENT_REVIEW_STATUS_OPTIONS = Object.keys(attachmentReviewStatusMap).map(k => ({
  value: k,
  label: attachmentReviewStatusMap[k],
}));
