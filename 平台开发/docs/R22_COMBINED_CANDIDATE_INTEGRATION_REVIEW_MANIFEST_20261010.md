# r22 综合候选冻结门禁返修复审清单

- 更新日期：`2026-10-11`
- 执行合同：`R22_COMBINED_CANDIDATE_INTEGRATION_DEVELOPMENT_HANDOFF_20261010.md`
- 集成工作区：`E:\杂七杂八\水质运维-r21候选`
- 分支：`codex/r22-combined-candidate-integration-20261010`
- 基线提交：`368491a595c31f8945a8f7fa25e631be95c02729`
- 状态：`FREEZE_GATE_PASS / FREEZE_METADATA_READY / NOT_COMMITTED / NOT_FROZEN / NOT_DEPLOYED`

## 1. 本轮返修结论

1. 仅修改允许范围内的 `backend/app.py` 和七个后端测试文件；数据接收、可信照片取景合同、小程序、React 和生产资源均未修改。
2. 三处失败注入已在真实事务/文件边界收口，均返回明确 HTTP `500`、`retryable=true`，并保留客户端原输入供重试：
   - 途中补给幂等写入失败：`VEHICLE_REFUELING_SAVE_FAILED`；补给记录和幂等记录均回滚。
   - 车辆故障限制更新失败：`VEHICLE_FAULT_SAVE_FAILED`；故障记录、车辆状态和幂等记录均回滚。
   - Web 工单照片风控失败：`WORKORDER_PHOTO_SAVE_FAILED`；附件、旧 `images` 缓存和物理文件均零残留；若文件清理自身失败则返回 `WORKORDER_PHOTO_CLEANUP_FAILED`，不伪报成功。
3. 测试夹具已对齐当前生产字段：两处 `vehicle_documents.id`、人工上报 `users.phone`、巡检影像 `operation_attachments.extra_json`。未修改业务查询迁就旧表，也未使用 mock 行绕开字段。
4. 人员停用测试先验证唯一活动负责人未转交时 `409 / USER_SITE_TRANSFER_REQUIRED` 且账号、站点归属和现有会话不变；建立真实替代负责人后，停用成功并撤销原会话。服务端负责人转交门禁未放宽。
5. 未自行重跑冻结级完整门禁；本清单只回传最小返修及定向验证，等待产品复审。

## 2. 本轮精确修改文件与因果归属

| 文件 | 因果修复 |
|---|---|
| `backend/app.py` | 仅为途中补给、车辆故障、Web 工单照片三条写入路径增加局部异常收口、回滚、文件清理和可重试 500；成功合同不变。 |
| `backend/test_vehicle_lifecycle.py` | 补给和故障失败注入断言明确错误码、可重试、事务回滚、车辆状态不变和幂等零残留。 |
| `backend/test_workorder_evidence_flow.py` | Web 工单照片风控失败断言明确错误码和可重试；原有附件、缓存、文件零残留断言保留。 |
| `backend/test_b0b_truth_and_closure.py` | `vehicle_documents` 补生产主键 `id`。 |
| `backend/test_plan_schedule_favorites.py` | `vehicle_documents` 补生产主键 `id`。 |
| `backend/test_manual_report_evidence.py` | `users` 补生产字段 `phone`，种子数据改用显式列写入。 |
| `backend/test_freeze_security.py` | `operation_attachments` 补生产字段 `extra_json`，真实 SQLite 行覆盖单站查询和批量审核。 |
| `backend/test_auth_security.py` | 停用流程先断言唯一负责人门禁及零副作用，再建立替代负责人并验证停用与会话撤销。 |

## 3. 定向验证

- 原稳定失败和三条失败注入聚焦复跑：`16/16` 通过。
- 交接指定七个失败模块：`100/100` 通过：
  - `backend.test_vehicle_lifecycle`
  - `backend.test_workorder_evidence_flow`
  - `backend.test_auth_security`
  - `backend.test_b0b_truth_and_closure`
  - `backend.test_freeze_security`
  - `backend.test_manual_report_evidence`
  - `backend.test_plan_schedule_favorites`
- 直接相邻人工上报、巡检审核与影像资格：`76/76` 通过：
  - `backend.test_manual_report_closure`
  - `backend.test_inspection_audit_unit`
  - `backend.test_inspection_review_rework`
  - `backend.test_attachment_evidence_qualification`
- 直接相邻工单投影、跨角色权限和计划变更审批：`21/21` 通过：
  - `backend.test_workorder_flow_projection`
  - `backend.test_multirole_cross_module_access`
  - `backend.test_plan_schedule_change_approval`
- Python 内存编译：本轮八个修改文件全部通过；`git diff --check` 通过，仅有既有 Windows LF/CRLF 转换提示，无空白错误。
- 完整冻结级后端、小程序、React、Compose 和备份恢复门禁：`NOT RUN`，按交接等待产品复审后由下一门禁动作执行。

## 4. 候选清单变化

- 上一轮：`128` 个已跟踪内容差异 + `85` 个未跟踪文件 = `213`。
- 本轮新增六个真实已跟踪测试文件差异：`test_auth_security.py`、`test_b0b_truth_and_closure.py`、`test_freeze_security.py`、`test_manual_report_evidence.py`、`test_plan_schedule_favorites.py`、`test_workorder_evidence_flow.py`。
- 冻结授权后仅新增发布元数据及其守卫差异：`deploy/release-candidates.json` 将 r22 设为唯一候选并把 r21 转为历史标签，`backend/test_release_candidate_gate.py` 同步验证新旧标签边界；不涉及业务代码。
- 当前：`136` 个已跟踪内容差异 + `85` 个未跟踪文件 = `221`；暂存区为空，status-only 异常项为 `0`。
- 与只读来源工作区逐字节一致：`201` 个；集成工作区有意差异：`19` 个；本清单自身：`1` 个。
- 本轮未引入私有配置、数据库、上传物、日志、缓存、本地运行地址或个人路径。

## 5. 不变边界

- 可信竖拍合同和上一轮照片准入返修保持闭环。
- HJ212 `2011/2061` 自动调度、`2061 Avg` 正式结果和 r21 生产接收/迁移合同未修改。
- 真实 Android / 微信真机 UI：`NOT RUN`。
- 未暂存、未提交、未推送、未打标签、未打包、未冻结、未部署。

## 6. 精确候选文件清单（221）

- ` M` `.gitignore`
- ` M` `平台开发/backend/app.py`
- `??` `平台开发/backend/assets/fonts/NotoSansCJKsc-Regular.otf`
- `??` `平台开发/backend/assets/fonts/OFL.txt`
- ` M` `平台开发/backend/hj212_parser.py`
- ` M` `平台开发/backend/import_operational_workbook.py`
- ` M` `平台开发/backend/sl651_server.py`
- ` M` `平台开发/backend/station_ingest_discovery.py`
- ` M` `平台开发/backend/station_ingest_provision.py`
- ` M` `平台开发/backend/station_monitoring.py`
- ` M` `平台开发/backend/test_asset_fulfillment_closure.py`
- ` M` `平台开发/backend/test_attachment_delete.py`
- ` M` `平台开发/backend/test_attachment_evidence_qualification.py`
- ` M` `平台开发/backend/test_auth_security.py`
- ` M` `平台开发/backend/test_b0b_truth_and_closure.py`
- ` M` `平台开发/backend/test_freeze_security.py`
- ` M` `平台开发/backend/test_hj212_ingestion.py`
- ` M` `平台开发/backend/test_inspection_audit_unit.py`
- ` M` `平台开发/backend/test_inspection_review_rework.py`
- ` M` `平台开发/backend/test_manual_report_evidence.py`
- ` M` `平台开发/backend/test_mobile_checkin_integrity.py`
- ` M` `平台开发/backend/test_mobile_my_today_scope.py`
- ` M` `平台开发/backend/test_mobile_photo_provenance.py`
- `??` `平台开发/backend/test_operational_reagent_import.py`
- ` M` `平台开发/backend/test_plan_resource_archive_flow.py`
- ` M` `平台开发/backend/test_plan_schedule_change_approval.py`
- ` M` `平台开发/backend/test_plan_schedule_favorites.py`
- `??` `平台开发/backend/test_plan_schedule_legacy_retirement.py`
- `??` `平台开发/backend/test_reagent_overview_authority.py`
- ` M` `平台开发/backend/test_release_candidate_gate.py`
- ` M` `平台开发/backend/test_sl651_server_isolation.py`
- `??` `平台开发/backend/test_station_b2_identity_resolution.py`
- ` M` `平台开发/backend/test_station_identity_discovery.py`
- `??` `平台开发/backend/test_station_ingest_b2_batch.py`
- ` M` `平台开发/backend/test_station_monitoring_normalization.py`
- ` M` `平台开发/backend/test_user_overdue_notification_closure.py`
- ` M` `平台开发/backend/test_vehicle_lifecycle.py`
- ` M` `平台开发/backend/test_wechat_approval_subscription.py`
- ` M` `平台开发/backend/test_workorder_evidence_flow.py`
- ` M` `平台开发/backend/test_workorder_flow_projection.py`
- ` M` `平台开发/deploy/backup-water-monitor.sh`
- ` M` `平台开发/deploy/SERVER_PRECHECK.md`
- ` M` `平台开发/deploy/release-candidates.json`
- ` M` `平台开发/deploy/test_backup_water_monitor.py`
- `??` `平台开发/dev_scripts/prepare-miniprogram-local-api.ps1`
- ` M` `平台开发/docker-compose.yml`
- `??` `平台开发/docs/R22_COMBINED_CANDIDATE_INTEGRATION_REVIEW_MANIFEST_20261010.md`
- ` M` `平台开发/miniprogram/app.js`
- ` M` `平台开发/miniprogram/app.json`
- ` M` `平台开发/miniprogram/app.wxss`
- ` M` `平台开发/miniprogram/components/custom-navbar/custom-navbar.wxss`
- `??` `平台开发/miniprogram/images/overview/icon-reagent.svg`
- `??` `平台开发/miniprogram/images/overview/icon-site.svg`
- `??` `平台开发/miniprogram/images/reagent/empty-error.svg`
- `??` `平台开发/miniprogram/images/reagent/empty-lock.svg`
- `??` `平台开发/miniprogram/images/reagent/empty-ok.svg`
- `??` `平台开发/miniprogram/images/reagent/empty-search.svg`
- `??` `平台开发/miniprogram/images/station-monitoring/empty-lock.svg`
- `??` `平台开发/miniprogram/images/station-monitoring/icon-gate-closed.svg`
- ` M` `平台开发/miniprogram/images/tab-alert-on.png`
- ` M` `平台开发/miniprogram/images/tab-alert.png`
- ` M` `平台开发/miniprogram/images/tab-home-on.png`
- ` M` `平台开发/miniprogram/images/tab-home.png`
- ` M` `平台开发/miniprogram/images/tab-inspection-on.png`
- ` M` `平台开发/miniprogram/images/tab-inspection.png`
- ` M` `平台开发/miniprogram/images/tab-mine-on.png`
- ` M` `平台开发/miniprogram/images/tab-mine.png`
- `??` `平台开发/miniprogram/images/tab-site-on.png`
- `??` `平台开发/miniprogram/images/tab-site.png`
- ` M` `平台开发/miniprogram/pages/alert/alert.js`
- ` M` `平台开发/miniprogram/pages/alert/alert.wxss`
- ` M` `平台开发/miniprogram/pages/index/index.js`
- ` M` `平台开发/miniprogram/pages/index/index.wxml`
- ` M` `平台开发/miniprogram/pages/index/index.wxss`
- ` M` `平台开发/miniprogram/pages/inspection/inspection.js`
- ` M` `平台开发/miniprogram/pages/inspection/inspection.wxml`
- ` M` `平台开发/miniprogram/pages/inspection/inspection.wxss`
- ` M` `平台开发/miniprogram/pages/login/login.wxml`
- ` M` `平台开发/miniprogram/pages/mine/mine.js`
- ` M` `平台开发/miniprogram/pages/mine/mine.wxml`
- ` M` `平台开发/miniprogram/pages/mine/mine.wxss`
- ` M` `平台开发/miniprogram/pages/plan-detail/plan-detail.js`
- ` M` `平台开发/miniprogram/pages/plan-detail/plan-detail.wxml`
- ` M` `平台开发/miniprogram/pages/plan-detail/plan-detail.wxss`
- ` M` `平台开发/miniprogram/pages/plan-edit/plan-edit.js`
- ` M` `平台开发/miniprogram/pages/plan-edit/plan-edit.wxml`
- ` M` `平台开发/miniprogram/pages/plan/plan.js`
- ` M` `平台开发/miniprogram/pages/plan/plan.wxss`
- ` M` `平台开发/miniprogram/pages/responsible-sites/responsible-sites.js`
- ` M` `平台开发/miniprogram/pages/responsible-sites/responsible-sites.json`
- ` M` `平台开发/miniprogram/pages/responsible-sites/responsible-sites.wxml`
- ` M` `平台开发/miniprogram/pages/responsible-sites/responsible-sites.wxss`
- ` M` `平台开发/miniprogram/pages/review/view.js`
- ` M` `平台开发/miniprogram/pages/review/view.wxml`
- ` M` `平台开发/miniprogram/pages/review/view.wxss`
- ` M` `平台开发/miniprogram/pages/site/site.js`
- ` M` `平台开发/miniprogram/pages/site/site.wxml`
- ` M` `平台开发/miniprogram/pages/site/site.wxss`
- `??` `平台开发/miniprogram/pages/trusted-camera/trusted-camera.js`
- `??` `平台开发/miniprogram/pages/trusted-camera/trusted-camera.json`
- `??` `平台开发/miniprogram/pages/trusted-camera/trusted-camera.wxml`
- `??` `平台开发/miniprogram/pages/trusted-camera/trusted-camera.wxss`
- ` M` `平台开发/miniprogram/pages/vehicle/vehicle.js`
- ` M` `平台开发/miniprogram/pages/vehicle/vehicle.wxml`
- ` M` `平台开发/miniprogram/pages/workorder/workorder.wxss`
- ` M` `平台开发/miniprogram/README.md`
- ` M` `平台开发/miniprogram/services/api.js`
- ` M` `平台开发/miniprogram/tests/alertWorkorderConnection.test.js`
- ` M` `平台开发/miniprogram/tests/config.test.js`
- ` M` `平台开发/miniprogram/tests/executionState.test.js`
- ` M` `平台开发/miniprogram/tests/homeTaskState.test.js`
- ` M` `平台开发/miniprogram/tests/inspectionDeparturePreparation.test.js`
- ` M` `平台开发/miniprogram/tests/inspectionExecutionEntry.test.js`
- `??` `平台开发/miniprogram/tests/inspectionProtectedMedia.test.js`
- ` M` `平台开发/miniprogram/tests/inspectionSiteExecution.test.js`
- ` M` `平台开发/miniprogram/tests/loginSessionRecovery.test.js`
- ` M` `平台开发/miniprogram/tests/mineBusinessScope.test.js`
- ` M` `平台开发/miniprogram/tests/notificationTarget.test.js`
- ` M` `平台开发/miniprogram/tests/photoCaptureState.test.js`
- ` M` `平台开发/miniprogram/tests/planDetailTasks.test.js`
- ` M` `平台开发/miniprogram/tests/planInspectionItems.test.js`
- ` M` `平台开发/miniprogram/tests/requestLocalApi.test.js`
- ` M` `平台开发/miniprogram/tests/responsibleSitesMonitoring.test.js`
- `??` `平台开发/miniprogram/tests/reviewProtectedMedia.test.js`
- `??` `平台开发/miniprogram/tests/round1DevelopmentWiring.test.js`
- `??` `平台开发/miniprogram/tests/section102ScopeAndTopOrder.test.js`
- ` M` `平台开发/miniprogram/tests/siteReadonlySource.test.js`
- `??` `平台开发/miniprogram/tests/trustedCamera.test.js`
- ` M` `平台开发/miniprogram/tests/url.test.js`
- ` M` `平台开发/miniprogram/tests/vehicleEntryConnection.test.js`
- ` M` `平台开发/miniprogram/utils/config.js`
- ` M` `平台开发/miniprogram/utils/executionState.js`
- ` M` `平台开发/miniprogram/utils/homeTaskState.js`
- ` M` `平台开发/miniprogram/utils/notificationTarget.js`
- ` M` `平台开发/miniprogram/utils/photos.js`
- `??` `平台开发/miniprogram/utils/round1Wiring.js`
- ` M` `平台开发/miniprogram/utils/url.js`
- ` M` `平台开发/react-vite/package.json`
- `??` `平台开发/react-vite/public/icons/actions/eye.gif`
- `??` `平台开发/react-vite/public/icons/actions/eye.png`
- `??` `平台开发/react-vite/public/icons/actions/magnifier.gif`
- `??` `平台开发/react-vite/public/icons/actions/magnifier.png`
- `??` `平台开发/react-vite/public/icons/actions/pencil.gif`
- `??` `平台开发/react-vite/public/icons/actions/pencil.png`
- `??` `平台开发/react-vite/public/icons/actions/trash-bin.gif`
- `??` `平台开发/react-vite/public/icons/actions/trash-bin.png`
- `??` `平台开发/react-vite/public/icons/empty/arrow-out-square.gif`
- `??` `平台开发/react-vite/public/icons/empty/bar-chart.gif`
- `??` `平台开发/react-vite/public/icons/empty/confetti.gif`
- `??` `平台开发/react-vite/public/icons/empty/inbox.gif`
- `??` `平台开发/react-vite/public/icons/empty/magnifier.gif`
- `??` `平台开发/react-vite/public/icons/empty/warning-triangle.gif`
- `??` `平台开发/react-vite/public/icons/nav/api.gif`
- `??` `平台开发/react-vite/public/icons/nav/api.png`
- `??` `平台开发/react-vite/public/icons/nav/article.gif`
- `??` `平台开发/react-vite/public/icons/nav/article.png`
- `??` `平台开发/react-vite/public/icons/nav/avatar.gif`
- `??` `平台开发/react-vite/public/icons/nav/avatar.png`
- `??` `平台开发/react-vite/public/icons/nav/bar-chart.gif`
- `??` `平台开发/react-vite/public/icons/nav/bar-chart.png`
- `??` `平台开发/react-vite/public/icons/nav/book-open.gif`
- `??` `平台开发/react-vite/public/icons/nav/book-open.png`
- `??` `平台开发/react-vite/public/icons/nav/calendar-dots.gif`
- `??` `平台开发/react-vite/public/icons/nav/calendar-dots.png`
- `??` `平台开发/react-vite/public/icons/nav/car.gif`
- `??` `平台开发/react-vite/public/icons/nav/car.png`
- `??` `平台开发/react-vite/public/icons/nav/check-circle-empty.gif`
- `??` `平台开发/react-vite/public/icons/nav/check-circle-empty.png`
- `??` `平台开发/react-vite/public/icons/nav/clipboard-text.gif`
- `??` `平台开发/react-vite/public/icons/nav/clipboard-text.png`
- `??` `平台开发/react-vite/public/icons/nav/globe.gif`
- `??` `平台开发/react-vite/public/icons/nav/globe.png`
- `??` `平台开发/react-vite/public/icons/nav/image-mountain.gif`
- `??` `平台开发/react-vite/public/icons/nav/image-mountain.png`
- `??` `平台开发/react-vite/public/icons/nav/layers.gif`
- `??` `平台开发/react-vite/public/icons/nav/layers.png`
- `??` `平台开发/react-vite/public/icons/nav/list-rules.gif`
- `??` `平台开发/react-vite/public/icons/nav/list-rules.png`
- `??` `平台开发/react-vite/public/icons/nav/location-pin.gif`
- `??` `平台开发/react-vite/public/icons/nav/location-pin.png`
- `??` `平台开发/react-vite/public/icons/nav/person-cycling.gif`
- `??` `平台开发/react-vite/public/icons/nav/person-cycling.png`
- `??` `平台开发/react-vite/public/icons/nav/trending-up.gif`
- `??` `平台开发/react-vite/public/icons/nav/trending-up.png`
- `??` `平台开发/react-vite/public/icons/nav/warning-triangle.gif`
- `??` `平台开发/react-vite/public/icons/nav/warning-triangle.png`
- `??` `平台开发/react-vite/public/icons/nav/wrench.gif`
- `??` `平台开发/react-vite/public/icons/nav/wrench.png`
- ` M` `平台开发/react-vite/src/components/GlobalSearch.jsx`
- `??` `平台开发/react-vite/src/components/HoverGifIcon.jsx`
- ` M` `平台开发/react-vite/src/components/WorkspacePage.jsx`
- ` M` `平台开发/react-vite/src/config/navigation.jsx`
- `??` `平台开发/react-vite/src/hooks/urlSearchWiring.test.js`
- `??` `平台开发/react-vite/src/hooks/useUrlSyncedSearch.js`
- `??` `平台开发/react-vite/src/hooks/useUrlSyncedSearch.test.js`
- ` M` `平台开发/react-vite/src/layouts/AppShell.css`
- ` M` `平台开发/react-vite/src/pages/alerts/AlertsPage.jsx`
- ` M` `平台开发/react-vite/src/pages/alerts/components/ThresholdRulesTab.jsx`
- ` M` `平台开发/react-vite/src/pages/archive/ArchivePage.browser.test.js`
- ` M` `平台开发/react-vite/src/pages/archive/ArchivePage.jsx`
- ` M` `平台开发/react-vite/src/pages/archive/archivePresentation.js`
- ` M` `平台开发/react-vite/src/pages/archive/archivePresentation.test.js`
- ` M` `平台开发/react-vite/src/pages/audit/AuditPage.jsx`
- ` M` `平台开发/react-vite/src/pages/audit/AuditPage.modalIdentity.test.js`
- ` M` `平台开发/react-vite/src/pages/cockpit/CockpitPage.jsx`
- `??` `平台开发/react-vite/src/pages/equipment/equipmentOperationLogs.test.js`
- ` M` `平台开发/react-vite/src/pages/equipment/EquipmentPage.jsx`
- ` M` `平台开发/react-vite/src/pages/inspection-settings/InspectionSettingsPage.jsx`
- ` M` `平台开发/react-vite/src/pages/plan-schedules/PlanSchedulesPage.jsx`
- ` M` `平台开发/react-vite/src/pages/reagents/ReagentMasterPage.jsx`
- ` M` `平台开发/react-vite/src/pages/reports/ReportsPage.jsx`
- ` M` `平台开发/react-vite/src/pages/sites/SiteMonitoringPage.jsx`
- ` M` `平台开发/react-vite/src/pages/sites/SitesPage.jsx`
- ` M` `平台开发/react-vite/src/pages/sites/stationMonitoring.js`
- ` M` `平台开发/react-vite/src/pages/sites/stationMonitoring.test.js`
- ` M` `平台开发/react-vite/src/pages/sites/stationMonitoringWiring.test.js`
- ` M` `平台开发/react-vite/src/pages/users/UsersPage.jsx`
- `??` `平台开发/react-vite/src/pages/vehicles/vehicleMaintenancePermissions.test.js`
- ` M` `平台开发/react-vite/src/pages/vehicles/VehiclesPage.jsx`
- ` M` `平台开发/react-vite/src/pages/workorders/WorkOrdersPage.jsx`
- ` M` `平台开发/README.md`
