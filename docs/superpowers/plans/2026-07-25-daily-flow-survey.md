# 日常动线现状梳理（阶段二·物资管理「编织进执行流」实施底稿）

> 只读调研，未修改任何文件。锚点均来自真实代码（行号 / 端点 / 表名 / 路径）。
> 配套战略文件：`2026-07-25-proactive-ops-roadmap.md`
> 核心纠偏：**物资管理不是缺模块，而是已有能力散布各处、与日常作业脱节**；阶段二目标是把这些动作**嵌入移动端巡检/工单执行流**，让一线边做边管，不新建独立「物资中心」。

---

## 第 1 块：日常动线还原（一次「巡检 + 工单处置」）

移动端真实可走的入口只有 10 个 `/api/mobile/*` 端点（app.py:11679–12458）。完整动线：

| 步骤 | 移动端页 | 后端端点 | 落表 | 物资动作是否在这步 |
|---|---|---|---|---|
| 1 出发前：确认车辆/备件 | 首页 `pages/index`（仅只读展示 `readiness`） | `GET /api/mobile/my-today`（app.py:11701）聚合 `work_package` 含 `vehicles`/`spare_parts` | 计划 JSON 字段 `plan_schedules.vehicle_days/spare_parts` | ❌ 仅展示，无确认/申请按钮 |
| 2 到站：GPS 签到 | `pages/inspection`(inspection.js:139) / `pages/site`(site.js:42) | `POST /api/mobile/check-in`（app.py:12315） | `inspection_checkins`；工单分支写 `work_orders.check_in_*` | — |
| 3 现场作业：逐项检查+拍照 | `pages/inspection`（inspection.js，今日包 `GET /api/mobile/today-execution` app.py:12069） | `POST /api/mobile/submit-item`（app.py:12206）写 `insp_plan_items`；`POST /api/mobile/upload-site-photo`（app.py:12369）写 `operation_attachments` | `insp_plan_items` / `operation_attachments` | ❌ **无**试剂更换+质控、无设备异常主动上报 |
| 4 异常处置：设备异常/旧件回收 | 设备异常=巡检项 abnormal 自动转单；回收=无入口 | 自动 `create_alert_internal`+插 `work_orders`(source='inspection')（app.py:12273-12291）；用车/备件申请藏在工单详情 `workorder.js:223-264` | `work_orders` / `vehicle_applications` / `spare_part_requests` | ⚠️ 仅"系统自动"，人无法主动上报；回收全在网页端 |
| 5 离站：提交/关闭工单 | `pages/workorder`（workorder.js） | 签到→`in_progress`→上传影像→`submit-review`(带 client:'mobile' 触发影像门禁)→`approve` 闭环 | `work_orders` + `operation_attachments` | — |

**动线小结**：移动端已闭环的是「巡检打卡→逐项检查→拍照→异常自动转工单→工单签到→处置→影像→审核闭环」。但**所有物资动作（车辆/备件/试剂/回收）几乎都不在动线主路上**，要么只读展示、要么深埋工单详情、要么只在网页端。

---

## 第 2 块：五大物资触点的当前落点

### ① 车辆（申请/行车/加油/充电/保养/维修/月度车况检查）
- **业务范围（用户 2026-07-25 补充）**：运维人员对车辆承担的是**全生命周期日常事务**，不只是"申请+使用"：
  - 加油（油车）：登记时间、起始里程、金额、单价、油量(L)、经手人、备注；
  - 充电（**新能源车**）：仅登记时间、起始里程、金额、经手人、备注——**不记单价、不记充电度数（kWh）**（与传统加油表字段有差异）；
  - 保养：**按里程触发**（车辆表 `vehicles.next_maintenance_mileage` 已有），**不与任务/工单绑定，任何时候任何人都可登记**；
  - 维修：与保养共用同一张表（靠 `maint_type` 区分）；
  - 行车记录：每次出行的起止时间、起止里程、事由、用车人签字；
  - **月度车况检查（新增能力）**：用户提供的纸质表共 17 项（保险是否到期、车辆是否年检、车内有无驾驶证、大灯/远灯/近灯、前后左右转向灯、尾灯/雾灯/刹车灯/倒车灯、车内空调、喇叭、左右后视镜、前/后/左/右漆面、四轮磨损、车内卫生等），每月一次、任何人都可做。
- 表：
  - `vehicles`：plate_no/model/seats/status/current_mileage/**last_maintenance_at**/**next_maintenance_mileage**（保养里程触发字段已就位）；
  - `vehicle_applications`(4 行)：申请主表，site_id+work_order_no 字段已就位；
  - `vehicle_use_records`(0 行)：application_id/start_mileage/end_mileage/returned_at；
  - `vehicle_refueling_records`(0 行)：vehicle_id/refuel_at/**liters**/**amount**/**mileage_at**/remark（油车专用）；
  - `vehicle_maintenance_records`(0 行)：vehicle_id/**maint_type**/maint_at/mileage_at/next_maint_mileage/items/cost/remark；
  - ❌ **缺** `vehicle_charging_records`（新能源充电，**字段无 liters/amount-only**）；
  - ❌ **缺** `vehicle_inspection_records`（月度 17 项车况检查，**纯新增能力**）。
- 端点：
  - `/api/vehicles` CRUD（app.py:13242/13254/13273）；
  - `/api/vehicle/applications` + approve（app.py:13297/13317/13360）；
  - `/api/vehicle/use-records` + return（app.py:13382/13414）；
  - `/api/vehicle/refueling`（app.py:13432）；
  - `/api/vehicle/maintenance`（app.py:13465）；
  - ❌ **无充电端点、无月度检查端点**。
- 移动端：**无独立 `pages/vehicle` 页**；申请埋在 `workorder.js:223`；加油/充电/保养/维修/月度检查**全无移动端入口**（`api.js` 无对应封装）。
- 网页端：`VehiclesPage.jsx` 只管车辆主数据 + 保养/加油，无申请审批、无维修、无充电、无月度检查。
- 顺手度：**只有申请（且须先打开工单）**；加油/充电/保养/维修/月度车况**全部无移动端**，一线日常几乎无法"边做边管"车辆。

### ② 备品备件（库存/申领/预申报/回收）
- 表：库存实际只用 `spare_parts_inventory`；申请两套 `spare_part_requests`（旧）+ `parts_requests`（新，巡检预申报）；明细 `parts_request_items`；流水 `inventory_logs`
- 端点（旧）：`GET/POST /api/parts/requests`（app.py:11361/11382 写 `spare_part_requests`）、`/mine`(11415)、`/<rid>/approve|reject`(11427/11462 扣库存)
- 端点（新）：`POST /api/inspection-v2/plans/<pid>/parts-request`（app.py:8213 写 `parts_requests`）、`/approve|reject`(8257/8303)
- 端点（库存/回收）：`GET/POST /api/parts/inventory`（app.py:11135/11160）、`POST /api/parts/recovery`（app.py:11296）
- 移动端：**申领**在 `workorder.js:240`（写 `spare_part_requests`），下拉来自 `api.partsInventory()`；**预申报/回收/库存管理均无**
- 网页端：库存+回收 `EquipmentPage.jsx:679,825`；预申报 `MaintenancePage.jsx:336`；审批两 Tab `AuditPage.jsx:290/533`
- 顺手度：**仅"发起备件申请"，且必须先打开工单**；库存/预申报/回收均无移动端

### ③ 试剂（更换/质控/库存）
- 表：`reagents`/`reagent_inventory`/`reagent_records`/`reagent_qc_records`/`reagent_usage`/`reagent_alerts`
- 端点：`GET/POST /api/reagents`(12911/12919)、`/api/reagent-inventory`(13006/13025)、`POST /api/reagent-inventory/replacement`(**更换** 13096)、`POST /api/reagent-qc`(**质控** 13136)、`/api/reagent-qc/pending`(13191)、`/api/reagent-overview`(15241)
- 移动端：**无任何页/调用**（仅 `maps.js:43` 一处中文标签 `'reagent':'试剂管理'`）
- 网页端：更换+质控 `SitesPage.jsx:302,344`；主数据 `ReagentMasterPage.jsx`；预警 `AlertsPage.jsx:634`
- 顺手度：**完全不能**（巡检执行页未嵌入）

### ④ 设备（台账/异常上报/回收）
- 表：`devices`/`device_shadows`（台账）、`device_recycle`（回收）、`anomaly_traceability`/`timeline_events`
- 端点-系统自动：`GET /api/alerts`(4362 阈值生成)；巡检 abnormal 自动转单（app.py:12273）
- 端点-人主动上报：**无专用端点**；仅网页端 `POST /api/workorders`(4784，source 可 report/manual)；小程序 `api.js` 无 create-workorder、无 report 类
- 端点-回收：`GET/POST /api/device-recycle`（app.py:11059/11080，admin-only），写 `device_recycle` 并置 `device_shadows.status='offline'`
- 移动端：**无**台账/上报/回收入口
- 顺手度：**不能**；设备异常只有"巡检 abnormal→自动转工单"被动链路

### ⑤ 盘点/维修/报废/外部采购（P2/P3）
- 盘点：库存即"账"，网页端 `EquipmentPage.jsx`；移动端无
- 维修：`maintenance_plans`/`maintenance_templates` + `GET/POST /api/maintenance/*`（app.py:6618 起）；网页端 `MaintenancePage.jsx`；移动端无
- 报废/回收：`device_recycle`（见④）；移动端无
- 外部采购：**DB 与端点均未发现采购模块**，备件补充仅靠"申请→审批→扣库存"内部闭环

### ⑥ 月度车况检查（新增能力）
- 触发：每月一次，**任何人都可做**（不受任务/工单绑定）。
- 表：**缺失** `vehicle_inspection_records`（无 schema 痕迹）。
- 端点：**缺失**（app.py 无 `inspection` 字样的 vehicle 路由）。
- 形态：纸质表 17 项 checklist（保险是否到期、车辆是否年检、车内有无驾驶证、大灯/远灯/近灯、前后左右转向灯、尾灯/雾灯/刹车灯/倒车灯、车内空调、喇叭、左右后视镜、前/后/左/右漆面、四轮磨损、车内卫生）+ 检查人签字 + 检查日期；可加"当前里程"读自 `vehicles.current_mileage`，便于后续比对保养里程。
- 顺手度：**完全无**（移动端、网页端均无入口）。

---

## 第 3 块：缺口与脱节点（=阶段二要填的坑）

1. **出发前"确认车辆/备件"无落点**：首页只读展示 `readiness`（index.wxml:33-53），真实申请被埋进工单详情（workorder.js:223-264）→ 出发前想先确认车/件在移动端做不到，要等到现场开工单。
2. **巡检执行页未嵌入试剂更换+质控、未嵌入设备异常主动上报**：`inspection.js` 只有打卡/提交项/拍照；检查项字段 `calibrator/calibration_values` 是"校准"非"试剂更换+标样质控"；设备异常只有被动自动转单，**一线无法主动上报一条设备异常**（无移动端点、无手动建工单端点）。
3. **双套备件申请表并存、前端暴露两套入口**：`spare_part_requests`（旧）与 `parts_requests`（新）同时存活且都被消费（AuditPage 为此分两个 Tab：290/517 vs 533/806），逻辑重复都扣 `spare_parts_inventory`。**`parts_inventory` 为孤儿表**（DB 有、app.py 零引用），须清理。
4. **物资动作散落、彼此不串联（"两张皮"）**：备件申领（工单内）与巡检执行（inspection）不相交；巡检换下的旧件、用掉的备件不会自动回写库存/回收；工单与物资仅弱关联（`spare_part_requests.work_order_no`），工单页只展示不驱动库存；试剂/设备回收与巡检/工单完全无关联字段。
5. **移动端缺"物资顺手管"总入口**：小程序 tab 为 index/inspection/workorder/alert/plan/mine，**无 vehicle/parts/reagent/device 页**；所有物资发起动作深埋工单或仅在网页端。
6. **车辆日常事务近乎全无移动端**：①加油端点有但移动端 0 入口；②**充电记录（新能源）后端完全缺失**（无表无端点），且**字段差异**（不记单价、不记度数）需单独建 `vehicle_charging_records`；③保养/维修按里程触发、不与任务绑定——任何时候任何人都要能登记，但移动端 0 入口；④**月度车况检查 17 项是新增能力**（无表无端点无 UI），纸质版登记要数字化。任何"车辆管理只差一个申请页"的想法都是误判。

---

## 第 4 块：阶段二实施底稿建议（优先编织进移动端执行流）

> 仅给落点建议，不改代码。按"一线能否边做边管"排序。

**建议 1 — 巡检执行页嵌入「试剂更换+标样质控」子流**
- 嵌入口：`inspection.js` 检查项详情 `onOpenItem`(161) 增"换试剂/做质控"
- 复用：`POST /api/reagent-inventory/replacement`(app.py:13096) + `POST /api/reagent-qc`(app.py:13136)；表 `reagent_inventory`/`reagent_records`/`reagent_qc_records`
- 缺：`api.js` 新增 `reagentReplacement`/`reagentQc`；`today-execution` 执行包顺带下发该站 `reagent_inventory` 列表（目前只下发 vehicle/spare_parts，app.py:12103）

**建议 2 — 巡检执行页增「设备异常主动上报」入口**
- 嵌入口：站点/检查项页加"上报设备异常"按钮（独立于点异常自动转单）
- 复用：补轻量端点或复用 `POST /api/workorders` 带 `source='report'`；落 `work_orders`+可选 `anomaly_traceability`；台账读 `device_shadows`
- 缺：移动端**无 create-workorder 端点**（`api.js` 无），需补；并与"异常自动转单"区分来源

**建议 3 — 首页出发前卡做可操作前置**
- 嵌入口：`index.wxml` 的 `workPackage` 区块(33-53) 从只读改"确认车辆 / 一键申领缺件"
- 复用：已有 `POST /api/vehicle/applications`、`POST /api/parts/requests`、执行包 `vehicle`/`spare_parts`(app.py:12103)
- 缺：需"确认"状态落点（`readiness.vehicle_confirmed` 现仅由排程推导 app.py:11943，无人工确认写回）；建议加执行包 `confirmed` 字段

**建议 4 — 统一备件申请为一套，消除两张皮**
- 目标：移动端申领与网页端预申报**共用同一张表/同一审批流**
- 落点：保留 `spare_part_requests`（移动端已接），网页端 MaintenancePage 的 `parts_requests` 预申报改写同一表（或反向合并），并下线孤儿表 `parts_inventory`
- 缺：审批端点二合一（`/api/parts/requests/<rid>/approve` app.py:11427 已是 admin 扣库存，可直接接管两类来源）

**建议 5 — 工单/巡检与物资做「自动回写」串联**
- 嵌入口：工单 `doApprove`(workorder.js:181) 与巡检异常分支(inspection.js:297) 自动关联旧件回收+备件耗用
- 复用：`POST /api/device-recycle`(app.py:11080)、`POST /api/parts/recovery`(app.py:11296)、库存扣减(11446/8286)
- 缺：移动端补 `device-recycle`/`parts-recovery` 的 `api.js` 封装与"旧件回收"UI；工单/巡检上下文带出"本次处置消耗/回收了什么"

**建议 6 — 车辆管理整套事务入移动端（申请+行车+加油/充电+保养+维修+月度车况）**
- 嵌入口：新建 `miniprogram/pages/vehicle/`，首页出发前卡 / 我的 也可直达。五个 tab 对应五类动作（加油/充电/保养/维修/月度检查），与现有 `VehiclesPage.jsx` 对齐但不是替代。
- 后端补建：
  - `vehicle_charging_records` 表（vehicle_id/charged_at/amount/mileage_at/remark，**不记单价、不记度数**）+ `POST /api/vehicle/charging` 端点；`vehicles` 表加 `fuel_type`(gasoline/electric/hybrid) 字段以便区分走哪个端点。
  - `vehicle_inspection_records` 表（vehicle_id/inspected_at/inspector_id/items_json/result/remark；items_json 存 17 项 checklist 的勾选与备注）+ `POST/GET /api/vehicle/inspections` 端点；items_json 可用用户提供的 17 项作 seed。
- 复用：`/api/vehicle/use-records`(app.py:13382)、`/api/vehicle/refueling`(13432)、`/api/vehicle/maintenance`(13465)；`vehicles.next_maintenance_mileage` 字段已就位（保养里程触发）。
- 缺：移动端 0 入口、`api.js` 0 封装；充电/检查两张表后端完全缺失；`vehicles.fuel_type` 字段缺。
- 顺手规则：保养触发=车辆里程 ≥ `next_maintenance_mileage` 时提醒（不强制阻断任务）；月度检查=日历月（任意一天、任何人都可登）。

---

### 关键事实速查（后续开发直接引用）
- 移动端端点全集：`backend/app.py:11679–12458`（`/api/mobile/*` 共 10 个）
- 双备件表：`spare_part_requests`(app.py:854 建表/11382 写) vs `parts_requests`(app.py:8225 建表/8213 写)，均存活且前端双入口；`parts_inventory` 为孤儿表
- 试剂全链路仅网页端：`/api/reagent-inventory/replacement`(13096)、`/api/reagent-qc`(13136)；小程序零引用
- 设备回收仅网页端、admin-only：`/api/device-recycle`(11080)；移动端无台账/上报/回收入口
- 用车申请移动端可发起但仅在工单内（workorder.js:223）；审批在网页审核中心 `AuditPage.jsx:550`
- 车辆端点现状：`/api/vehicles`(13242)+use-records(13382)+refueling(13432)+maintenance(13465)；**充电/月度检查 0 表 0 端点**；`vehicles` 表已有 `next_maintenance_mileage`（保养里程触发），缺 `fuel_type`（油/电/混区分）
