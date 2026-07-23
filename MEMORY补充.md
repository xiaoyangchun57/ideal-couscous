## 水质运维系统业务认知（用户纠正，2026-07-21）
- 人员距站点远，车辆和备品备件需在巡检计划/工单中**提前确认**，不是附属功能而是计划环节
- 周计划是**高频核心功能**，由运维人员主动排程→提交管理员审核（判断合理性+物资调配），不是"可降级"的边缘功能
- 备品备件有双重归属：设备属站点，但备品备件是**公司库存**，库存管理和回收需独立智能化管理
- 巡检≈定期无目的计划，工单≈针对特定问题；为降低运维成本，巡检计划会**主动迎合工单**（顺路处理）
- 照片审核严格，管理者重视是否现场拍摄，**必须到站打卡后才能拍照**（反作弊设计，非体验缺陷）
- PC端运维人员使用频率低，**移动端才是高频**；PC端主要服务管理者
§
## 水质运维系统项目
- 项目路径: E:\杂七杂八\水质运维\平台开发（React19+Vite+Antd5 / Flask / SQLite / 微信小程序）
- PC端17页面服务管理者，移动端8页面服务一线运维（5-10人，每人固定3-5站）
- 核心链路：周计划排程→资源确认(车/备件)→管理员审核→移动端执行→影像审核→归档
§
## 水质运维补充业务规则（2026-07-21）
- 周计划审批通过后仍需支持变更（车辆故障/突发事件），变更后重新审核，增量同步任务
- 试剂更换后必须做质控验证（跑标样），质控通过才算更换完成，不通过需校准或报修
- 备件全生命周期分三期：P1出库+库存+使用记录，P2回收+盘点，P3维修/报废/采购
§
## 水质运维系统被动智能层现状（2026-07-22确认）
- 被动层已完整：5管道异常检测+L1/L2/L3审核+自动派单(user_sites)+SLA升级+告警↔审核↔工单闭环
- 真正缺口是"衔接层"：被动产生的工单/告警未喂入周计划的智能建议中
- SPC动态阈值有骨架(spc_3sigma模板)但rules数组为空，需真实数据积累后落地
- 数据模拟器当前关闭(静态演示模式)，真实部署需对接实际数据源
§
## 水质运维系统认知修正（2026-07-22，用户纠正）
- 工单/巡检的用车+备件申请必须在**接单后、出发前**完成（不是处置后）。人员驻地统一，但站点分布广泛，出发前必须落实交通和物资
- **周计划≠独立模块，周计划就是最高频的巡检任务**。每位运维每周需巡检所有站点（周频次固定）。还有月/季/年度巡检计划，巡检重点不同。巡检计划是统一层级体系：周(全覆盖)/月/季/年(各有重点)，周计划是其中最频繁的一档，不应与巡检分离设计
- 现场拍照数量多，是审核主要工作量。两个减负方向：①直观体现"需拍什么/拍多少张/哪些没拍"；②审核改为"展开所有照片→只点异常照片标记→其余自动通过"（异常驱动审核，而非逐张点通过）
§
## 交互设计约束（2026-07-22）
- 用户要求所有按钮/关联数据/状态在3次点击内可达，避免过度点击或埋太深
- 移动端高频动作(打卡/拍照/接单)须1-2次触达；PC端管理者操作≤3-4次可接受
- 设计交付物偏好：详细设计文档(.md) + 可视化总览图(.html) 成对输出
§
## 水质运维系统 P3 已落地（2026-07-22）
- P3-1多频次：plan-edit支持周/月/季/年检(频次picker+非周检手动加日期)，periodRange()算周期
- P3-2变更流程：状态机 draft/rejected→submitted→approved；approved→(request-change)→modifying→(submit)→change_submitted→approve(重建任务_ps_rebuild_tasks_on_change,保护已有result的plan)/reject(回滚previous_plan_data+previous_vehicle_days,恢复approved)。移动端plan-detail"发起变更"+plan-edit变更横幅，PC抽屉显示变更原因+change-aware审批文案
- P3-3试剂质控：reagent_inventory.qc_status(pending/passed/failed,默认passed避免存量误标)+reagent_qc_records表；更换接口自动置pending；POST /api/reagent-qc(标样值/实测值/偏差/通过与否,不通过必填fail_action calibrate|repair并通知管理者)；GET /api/reagent-qc/pending。PC在SitesPage试剂Tab加质控状态列+质控弹窗
- P3-3备件回收：POST /api/parts/recovery(type=in,ref_type=ref_type='recovery'区别于采购入库,一线可提交非admin专属)。PC EquipmentPage加"回收"按钮+弹窗+日志"回收"青色Tag
- 注意：reagent_inventory表不在app.py的CREATE TABLE里(来自DB/seed)，补列靠migrate_reagent_qc()幂等ALTER；SQLite未开foreign_keys pragma，级联不生效，删insp_plans须先手动删insp_plan_items
- 前端构建0 error；冒烟全绿(变更approve/reject回滚+QC+回收E2E均验证)
§
Windows cmd编码：Python处理中文docx需sys.stdout.reconfigure(encoding='utf-8')否则乱码；pandoc输出也乱码，改用Python zipfile+ET解析。Bash工具实际跑bash非cmd.exe，用正斜杠路径+ls；Grep不可用(ripgrep ENOENT)用python替代。Flask启动>7s(port 5000)，用HTTP 401验证已启动。杀进程用powershell.exe(taskkill的/F被MSYS转路径失败)。注意僵尸python同绑端口问题。
§
## 巡检计划体系与审批（2026-07-22）
- 巡检按频次分层：周(全覆盖，最高频)/月/季/年(各有检查重点)，周计划是其中最频繁一档，不独立模块
- 运维自排→管理员审批；审批需站点情况卡(健康度/近期问题)+风险预警(高危站优先)+路线合理性(折返检测+顺序建议，不做TSP，运维比算法熟路况)
- 审批识别两类不合理：①严重问题站被排后②路线折返浪费路费
§
## 水质运维系统已实现功能总览（P1-P3，2026-07-22）
- **调度层**：plan_schedules表(注意不叫inspection_schedules)；API含CRUD+submit+approve+reject+validate+suggestions；审批通过自动流转(生成任务+锁车+备件出库+通知)；模板复用inspection_templates.frequency；站点坐标列gps_lat/gps_lng
- **PC端**：计划调度列表+抽屉详情(风险预警+站点情况卡+SVG路线示意图含折返红虚线)；批量照片审核页(按站分组网格/异常驱动/驳回填原因)
- **移动端**：plan列表+plan-edit(逐日选站+智能建议+车辆picker+多频次支持)+plan-detail(状态头+变更发起)；首页"我的计划"入口
- **变更流程**：draft/rejected→submitted→approved→(request-change)→modifying→change_submitted→approve(重建任务)/reject(回滚)；approved后仍可变更
- **拍照管理**：required_photos/actual_photos跟踪；站点级进度条+检查项级"需拍X已拍Y"+未拍占位框；批量审核approve/reject+驳回通知
- **试剂质控**：reagent_inventory.qc_status(pending/passed/failed)+reagent_qc_records表；更换自动置pending；QC不通过需calibrate/repair+通知
- **备件回收**：parts/recovery接口(ref_type='recovery'区分采购入库)；PC回收按钮+日志青色Tag
- **SQLite注意**：未开foreign_keys pragma需手动级联删除；部分列(insp_plan_items的拍照列)是库外ALTER加的，重建库需migrate；reagent_inventory来自seed不在app.py CREATE TABLE