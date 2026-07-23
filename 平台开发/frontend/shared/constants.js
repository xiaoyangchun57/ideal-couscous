/** ══════════════════════════════════
 *  水质监测智慧运营平台 - 共享常量层
 *  ⚠️ 旧版（dashboard/mobile 用）
 *  新版开发请用 react-vite/src/services/constants.js
 * ══════════════════════════════════ */
/* Dashboard用 const；Mobile用 var（函数作用域） */

// 站点类型映射
var TM={water_quality:'水质自动站',manual_station:'水质手动站',drinking_source:'饮用水源站',cross_boundary:'跨界断面站',groundwater:'地下水站'};

// 工单状态映射
var SM={pending:'待受理',accepted:'已受理',generated:'已生成',dispatched:'已派发',in_progress:'处置中',reviewing:'审核中',acceptance:'验收中',closed:'已完成'};

// 工单级别映射
var LM={normal:'一般',urgent:'紧急',critical:'重大', red:'重大', orange:'紧急', yellow:'一般', blue:'一般'};

// 工单来源映射
var SL={auto:'自动',patrol:'巡查',manual:'人工',superior:'上级',hotline:'热线'};

// 指标名称映射（水质常规参数）
var MT={codmn:'高锰酸盐指数',ammonia:'氨氮',total_phosphorus:'总磷',total_nitrogen:'总氮',water_temp:'水温',dissolved_oxygen:'溶解氧',ph:'pH',turbidity:'浊度',conductivity:'电导率',device_status:'设备状态',data_gap:'数据缺失',data_spike:'数据突变',data_freeze:'数据冻结'};

// 指标名称映射（带单位，移动端使用）
var MCN={codmn:'高锰酸盐指数(mg/L)',ammonia:'氨氮(mg/L)',total_phosphorus:'总磷(mg/L)',total_nitrogen:'总氮(mg/L)',water_temp:'水温(°C)',dissolved_oxygen:'溶解氧(mg/L)',ph:'pH',turbidity:'浊度(NTU)',conductivity:'电导率(μS/cm)',temperature:'温度(°C)',battery:'电池电量(V)',signal_strength:'信号强度(dBm)'};

// 设备类型映射
var DT={multi_param_analyzer:'多参数水质分析仪',ph_meter:'pH计',do_sensor:'溶解氧传感器',turbidity_meter:'浊度仪',ammonia_analyzer:'氨氮分析仪',codmn_analyzer:'高锰酸盐分析仪',tp_analyzer:'总磷分析仪',tn_analyzer:'总氮分析仪',conductivity_meter:'电导率仪',thermometer:'温度计',submersible_pump:'潜水泵',sample_float:'采样浮筒',dtu:'数据采集传输终端',fire_extinguisher:'灭火器',lighting:'照明设备'};

// 巡检类型映射
var ITM={daily:'日常',weekly:'定期',monthly:'月度',special:'专项'};

// 设备类型中文映射（用于设备台账筛选）
var DEVICE_TYPE_CN={'multi_param_analyzer':'多参数分析仪','ph_meter':'pH计','do_sensor':'溶解氧传感器','turbidity_meter':'浊度仪','ammonia_analyzer':'氨氮分析仪','codmn_analyzer':'高锰酸盐分析仪','tp_analyzer':'总磷分析仪','tn_analyzer':'总氮分析仪','conductivity_meter':'电导率仪','thermometer':'温度计','submersible_pump':'潜水泵','sample_float':'采样浮筒','dtu':'数据采集终端','fire_extinguisher':'灭火器','lighting':'照明设备'};

// 指标中文映射（用于告警等）
var METRIC_CN={'codmn':'高锰酸盐指数','ammonia':'氨氮','total_phosphorus':'总磷','total_nitrogen':'总氮','dissolved_oxygen':'溶解氧','ph':'pH','turbidity':'浊度','conductivity':'电导率','water_temp':'水温','device_status':'设备状态','data_gap':'数据缺失'};

// 站点类型在地图上的颜色
var CLR={water_quality:'#1890FF',manual_station:'#00CCFF',drinking_source:'#07C160',cross_boundary:'#FFA600',groundwater:'#722ed1'};

// 告警级别颜色映射（dashboard用四级：蓝黄橙红）
var ALERT_LEVEL_COLOR={blue:'#1890ff',yellow:'#faad14',orange:'#fa8c16',red:'#f5222d'};
var ALERT_LEVEL_LABEL={blue:'蓝色关注',yellow:'黄色警示',orange:'橙色预警',red:'红色警报'};

// 工单流转状态颜色映射（dashboard用）
var OB={'pending':'gray','accepted':'blue','generated':'cyan','dispatched':'org','in_progress':'grn','reviewing':'purple','acceptance':'yellow','closed':'darkgrn'};

// 工单流转进度百分比（移动端用）
var WO_PCT={pending:0,accepted:15,generated:0,dispatched:0,in_progress:40,reviewing:70,acceptance:90,closed:100};

// 工单流转状态条CSS类名（移动端用）
var SB_CLASS={pending:'sb-pending',accepted:'sb-accepted',generated:'sb-generated',dispatched:'sb-dispatched',in_progress:'sb-inprogress',reviewing:'sb-reviewing',acceptance:'sb-acceptance',closed:'sb-closed'};

// 事件类型中文映射（时间线用）
var TL_CN={alert:'告警',order:'工单',inspection:'巡检',maintenance:'运维',sample_check:'采样核查',acknowledged:'确认',urged:'督办',converted:'转工单',created:'创建',completed:'完成',checked:'校验',auto_checked:'自动校验',alert_generated:'触发告警',acceptance:'验收中',closed:'已关闭'};

// 告警状态颜色映射（dashboard用）
var ALERT_STATUS_COLOR={pending:'#ffa040',acknowledged:'#5ea8c8',resolved:'#2ea080'};
var ALERT_STATUS_LABEL={pending:'待处理',acknowledged:'处理中',resolved:'已办结'};

// ===== 地表水III类标准阈值 (GB 3838-2002) =====
var WQ_STD={
  ph_min:6.0, ph_max:9.0,
  dissolved_oxygen_min:5.0,
  codmn_max:6.0,
  ammonia_max:1.0,
  total_phosphorus_max:0.2,
  total_nitrogen_max:1.0
};

// 指标名称映射（移动端用辅助函数）
var _mcn=function(key){return MCN[key]||key};
