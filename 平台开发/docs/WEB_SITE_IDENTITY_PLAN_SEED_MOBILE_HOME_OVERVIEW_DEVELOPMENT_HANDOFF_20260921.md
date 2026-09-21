# 跨端集中返修与小程序“站点”主 Tab 开发交接

> 状态：`STATION_TAB_LATEST_ICON_REVIEW_PENDING / WEB_REWORK_QUEUED`
> 当前写入者：专业设计师仅写设计交接允许的 UI 文件；开发经理暂停重叠写入
> 停点：设计通过产品静态 Review 后，开发经理统一接线与返修；不提交、不冻结、不部署、不操作生产资源

## 当前目标

1. Web 收口已确认的站点列表和人员退出问题。
2. 小程序新增第五个底部 Tab：`首页｜巡检｜站点｜告警｜我的`，把站点目录、监测数据、试剂关注与维护整合为一个站点运维入口。
3. 首页只保留站点/试剂紧凑摘要和直达，不新增独立告警区域；紧急告警继续由“今日行动”和“告警”Tab 承担。

## 已确认 Web 返修

- 站点全景列表恢复“最后数据”，并保留“关键时间”；设备台账删除“最后数据/运行状态”已经通过，不再改动。
- 人员退出系统不得因存在历史业务而被阻断。账号退出后登录、会话、微信绑定、角色、站点关系和候选项全部解除；历史业务、原姓名和删除审计继续可读。不强制物理删除底层用户行。
- Web 中文搜索继续实时筛选；输入法组合期间不得由 URL 回写覆盖输入，选词结束只提交最终值。

## 小程序结构与路由契约

- 复用 `pages/responsible-sites/responsible-sites` 作为“站点”Tab 根页，不新增重复站点根页；页面标题改为“站点”。
- `app.json` 注册第五个 Tab，站点 Tab 使用设计师交付的普通/选中 PNG 图标。
- Tab 根页内部一级模式为 `stations / reagents`；站点模式默认 `mine`，管理员可切 `all`。
- 首页站点摘要、首页试剂摘要及仍需保留的相邻入口必须用 `wx.switchTab`，不能再对 Tab 页调用 `navigateTo`。Tab URL 不携带查询参数。
- 在 `app.globalData` 新增一次性目标 `stationHubTarget`，只接受：
  - `{ view: 'stations' }`
  - `{ view: 'reagents', filter: '' | 'expired' | 'expiring' | 'low_volume' }`
- 目标页在 `onShow` 校验、应用并立即清除该目标。无新目标时保留用户上次的模式、范围和筛选；返回站点详情时不得被重置。
- 发起方需要防重复点击；`switchTab` 失败时清除仍属于本次的目标并给出可重试提示，旧目标不得污染下次进入。
- 新 Tab 建立后移除“我的”页重复的“负责站点”主入口；不得留下两个同权入口。

## 读取接口契约

### 站点

- 继续使用 `GET /api/station-monitoring/sites?scope=mine|all&keyword=...`。`all` 仅管理员可用，服务端仍是范围和状态权威。
- 监测能力未开放时，Tab 仍可用 `GET /api/mobile/responsible-sites` 展示有权站点档案入口，并以中性状态表达“监测能力未启用”；不得整页报红或丢失站点目录。
- 列表展示接口已有的站点身份、监测状态、关键时间、因子数量和责任人事实；实际因子最新值进入站点详情读取，不在全站列表逐站追加请求。不得从有数据推断 RTU 在线、仪器正常或设备健康。
- 站点详情继续使用现有站点档案和 `/api/station-monitoring/sites/<id>/overview`；服务端站点范围校验不能下放到前端。

### 试剂

- 以现有 `GET /api/reagent-overview` 为唯一跨站摘要来源，不另造重复聚合接口。
- 响应保持现有 `total / concern_count / items` 兼容字段，并新增稳定结构：
  - `status_counts`: `expired / expiring / low_volume / pending_qc / failed_qc`
  - 每项至少包含 `site_id`、`site_name`、`reagent_id`、`reagent_name`、兼容 `status`、`attention_reasons`、`qc_status`、`current_qty`、`unit`、到期或预计可用信息、可执行动作能力。
- `attention_reasons` 使用稳定代码 `expired / expiring / low_volume / pending_qc / failed_qc`。同一项目可同时包含多个原因，不能因主状态优先级丢失“已过期且低余量”等事实。
- `items` 是上述原因的去重并集，同一 `site_id + reagent_id` 只返回一项。`concern_count` 是去重项目数；`status_counts` 按原因分别计数，三类库存数之和可以大于 `concern_count`。
- 首页使用去重后的 `concern_count` 和三类库存原因分布；站点 Tab 的“全部”显示完整并集，三个筛选按 `attention_reasons` 筛选，待标定/失败仍可在“全部”中到达。
- 管理员读取全站，其他角色只读取服务端授权站点；零站点返回空集合，不得退化为全站。

## 试剂权限与写入契约

站点 Tab 首版只暴露“登记更换”和“完成标定”。不得复用依赖巡检计划的以下接口：

- `/api/mobile/execution-plans/<plan_id>/sites/<site_id>/reagent-replacements`
- `/api/mobile/execution-plans/<plan_id>/sites/<site_id>/reagent-qc`

继续使用并加固通用站点接口：

- `POST /api/reagent-inventory/replacement`
- `POST /api/reagent-qc`

开发必须同时校对同族站点接口 `POST /api/reagent-inventory`、`DELETE /api/reagent-inventory/<site_id>/<reagent_id>`、`POST /api/reagent-inventory/usage`、`GET /api/reagent-qc/pending`，确保没有可绕过的新旧入口：

- 所有接口要求有效登录；写入仅 `admin` 或同时具有 `operator` 角色的用户可用，按用户全部角色判断。仅有 `reviewer` 不获得维护权。
- 非管理员只能访问当前活动站点关系内的站点；站点不存在返回 404，越权返回 403，不能返回空成功。
- `pending` 读取必须按同一站点范围过滤；删除、用量、更换和标定均须做服务端站点校验。
- 客户端每次真实提交生成稳定 `_idempotency_key`，失败重试沿用原键，用户修改输入后生成新键。服务端按“操作者 + 端点 + 键”防重，并校验同键请求体一致。
- 在同一事务内重读用户权限、站点关系、库存和当前 QC 状态，再写库存、历史、QC、告警/通知和幂等响应；任一失败全部回滚，不得留下半条记录或重复通知。
- 更换不得信任客户端传入的旧余量；服务端以事务内库存为准，写更换历史、更新新余量/批次/预计时长并置为待标定，同时关闭或重算已失效的库存关注事实。
- 更换的新余量必须为大于 0 的有限数，预计可用天数必须为正整数，更换时间必须是可解析业务时间；单位由试剂主数据提供，客户端不得修改。
- 标定只允许当前存在库存的项目；标样值、实测值和通过/不通过结果必填且数值有效，不通过时处置动作必填。重复提交返回第一次的稳定结果，不重复写 QC 或通知。
- 客户端提交失败保留 Sheet 输入和目标对象，显示可执行原因并允许重试；过期响应、页面卸载、重复点击和提交中关闭均须处理。

## 设计接线边界

- UI 唯一合同为 `MOBILE_HOME_OPERATIONS_OVERVIEW_DESIGN_HANDOFF_20260921.md`。设计通过前不得自行补视觉方案。
- 首页站点与试剂摘要整行可点击；不显示前两条具体试剂，不增加告警卡，不制造 RTU/仪器状态。
- “站点 / 试剂”主模式、管理员范围、试剂筛选和两个维护 Sheet 的绑定必须保持设计层级，开发不得用临时按钮或独立第三页替代。
- 位置校准继续在站点详情中对所有现有角色开放，但服务端仍校验有权站点。

## 验证要求

- 后端：试剂读取/写入覆盖管理员、运维、仅审核、零站点、越权、不存在、重复请求、同键异参和事务失败回滚；断言无越权写入、无重复历史/通知。
- 小程序：覆盖五 Tab 配置、首页两种目标跳转、一次性目标消费、导航失败清理、返回详情状态保持、管理员/非管理员范围、筛选清除、两 Sheet 失败保留及重复提交保护。
- 相邻回归：首页今日行动告警直达、告警 Tab、巡检 Tab、“我的”页、站点详情和位置校准。
- 只跑改动范围和直接相邻测试；React 返修跑相关测试/lint/build，后端跑相关 unittest/编译，小程序跑相关 Node/语法检查。
- 静态和自动测试不能替代真实微信小程序 UI。接线代码 Review PASS 后，由独立 UI 验收覆盖五 Tab、长列表、两个 Sheet、角色差异和失败重试。

## 开发回传

仅回传：修改文件、接口兼容说明、权限/事务/幂等证据、定向测试结果、残余 `NOT RUN` 和停点。不得提交、冻结、推送、部署或操作生产数据。
