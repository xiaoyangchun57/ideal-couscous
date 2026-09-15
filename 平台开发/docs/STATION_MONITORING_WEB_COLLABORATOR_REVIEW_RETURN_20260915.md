# 站点监测 Web 协作者提交与 Review 回传

> 日期：2026-09-15  
> 协作者：`LI991020`  
> 依据：用户提供的 `STATION_MONITORING_WEB_COLLABORATOR_HANDOFF_20260914(1).md`  
> 状态：`WEB_IMPLEMENTED / READY_FOR_PRODUCT_CODE_REVIEW`，不是冻结或发布候选。  
> 本文件只描述下列独立协作分支；不描述旧工作树中未提交的后端修缮。

## 1. 提交身份与范围

- 独立分支：`codex/station-monitoring-web-db20184`。
- 分支基线：`db20184fd11b0958d63fe6836ea7563756ddcfe8`，历史包含交接要求的 `b48d7008a33d47f794f8b2a59bc26091451c12d5`。
- Web 首次实现：`dac5da0b5033c1c96d87f20d35fde4574e53db79`。
- 依赖漏洞交接：`25b14372caadcfa11c2e9a3a68da571df033fb22`，只记录审计，未升级依赖。
- 本轮 Web 补修与浏览器测试：`8cd7086d68d237161c09c5a6846d8d661a53eb8d`。
- 本回传文档另行提交；文档提交号以协作分支日志及回传消息为准，避免自引用 SHA。
- 用户明确授权本次在现有 D 盘独立协作工作树继续、提交和推送。

未修改后端、小程序、接收模块、迁移、Compose、生产配置、依赖或锁文件；未合并、创建 PR、打标签、部署、操作生产数据。旧 `fix/station-monitoring-web-collaborator` 工作树的未提交改动和用户输入文件全部保留，未纳入交付。

## 2. 修改文件

累计 Web 修改相对 `db20184`，均在交接白名单内：

- `平台开发/react-vite/src/App.jsx`：接入中心使用精确 admin 路由权限键。
- `平台开发/react-vite/src/config/navigation.jsx`：目录角色及接入观察元信息、搜索入口权限。
- `平台开发/react-vite/src/components/GlobalSearch.jsx`：普通站点搜索结果进入监测，不沿用旧档案路径。
- `平台开发/react-vite/src/pages/cockpit/CockpitPage.jsx`：监测和档案动作分流。
- `平台开发/react-vite/src/pages/alerts/AlertsPage.jsx`：普通站点入口进入监测，试剂入口保留档案。
- `平台开发/react-vite/src/pages/sites/SitesPage.jsx`：独立监测状态、主原因、两类时间、状态摘要；取消过期请求；首次监测失败与刷新失败分别提示；保留原站点管理。
- `平台开发/react-vite/src/pages/sites/SiteMonitoringPage.jsx`：可信首屏、有效值、趋势能力、四分轴、仪器与近期事项；可刷新、可重试；同站失败保留上次成功结果；拒绝与路由站点不一致的响应；不展示协议因子，不用创建时间冒充事项发生时间。
- `平台开发/react-vite/src/pages/sites/StationAccessPage.jsx`：admin 组件保护、脱敏分轴摘要、刷新重试；缺少批准因子、周期、有效值数量时显示“暂无”，不以其他数量替代。
- `平台开发/react-vite/src/pages/sites/stationMonitoring.js`：八状态与分轴呈现；失败刷新才复用旧投影，成功响应漏站则清除该站旧监测事实；趋势需服务端能力与返回聚合事实同时存在。
- `平台开发/react-vite/src/pages/sites/stationMonitoring.test.js`：状态、旧业务字段、数据范围收缩、缺失投影、趋势事实直接测试。
- `平台开发/react-vite/src/pages/sites/stationMonitoringWiring.test.js`：角色、路由、入口分流、旧流程、请求取消与过期响应源码守护。
- `平台开发/react-vite/src/pages/sites/stationMonitoring.browser.test.js`：当前真实 React UI 的隔离浏览器行为测试。
- `平台开发/react-vite/src/services/api.js`：监测严格请求支持调用方取消，区分取消、超时与请求失败。
- `平台开发/react-vite/src/services/api.test.js`：取消和 403/404 原样传播、不回退其他接口。
- `平台开发/react-vite/src/utils/shellNavigation.js`：普通站点搜索定位监测详情。
- `平台开发/react-vite/src/utils/shellNavigation.test.js`：入口路径回归。

文档：本文件及 `平台开发/docs/STATION_MONITORING_WEB_DEPENDENCY_AUDIT_HANDOFF_20260915.md`。本轮补修修改 7 个 Web/测试文件，加本回传文档；其余实现来自首次实现提交。

## 3. 路由和权限

| 入口 | 角色/责任 |
| --- | --- |
| `/sites` | admin、reviewer、operator；现有站点台账和管理入口 |
| `/sites/:siteId` | 复用目录角色权限；只读监测详情，接口最终收口站点范围 |
| `/sites/data-access` | 精确 admin 权限；非管理员不可发现且直达不调用接入摘要 |
| `/sites?archive=<id>` | 原档案、试剂库存入口，沿用现有权限 |

驾驶舱、普通告警、普通站点搜索进入监测；明确档案和试剂动作保留档案。前端没有新增监测写操作，也没有自行扩大站点范围。多角色按完整角色集合判断。

## 4. 交接完成情况

| 必须项 | Web 实现/证据 |
| --- | --- |
| 目录与八类状态 | 独立 `monitoring_*` 呈现，保留业务 `status`；直接测试及真实页面夹具测试通过 |
| 详情可信首屏、有效值与后置事实 | 已实现；周期未配置仍显示服务端有效值及观测时间，不推导新鲜度或设备健康 |
| admin 接入中心 | 路由、导航发现、组件保护；身份/原文/B2/观测/质量/存储数量分开呈现 |
| 上游入口及旧管理 | 分流直接测试通过；真实 UI 验证创建、文件导入、档案、档案编辑和试剂库存入口可用 |
| 首次失败与刷新 | 重试、保留上次成功结果、请求取消和切站隔离均有浏览器行为证据 |
| 数据边界 | 无聚合事实不显示趋势可用；缺失事实准确空态；不显示协议字段、不以事项创建时间冒充业务时间 |

服务端事实缺失按交接第 6 节记录依赖，不通过前端造值或修改后端补齐。

## 5. 服务端字段依赖

- 目录、详情站点：`id`、`name`、`monitoring_status`、`monitoring_status_label`、`monitoring_reason`、`last_communication_at`、`last_valid_observation_at`、`published_factor_count`。不使用旧 `status/status_label/reason` 判定监测结果。
- 原因代码：兼容服务端 `reason_code` 和可选 `monitoring_reason_code`；不作为监测状态权威来源，也不要求后端为了页面额外增加别名。
- 详情：`monitoring.latest_values` 的业务因子、标准值、单位、`observed_at`；顶层或监测区的 `axes`、`capabilities`、`instruments`、`recent_items`。
- 趋势：服务端 `capabilities.trend === true` 且 `monitoring.trend` 返回非空聚合事实；能力标记本身不替代趋势载荷。
- 近期事项：`title/type`、可信 `occurred_at`；缺少发生时间显示无记录，不采用 `created_at`。
- 接入：`runtime.enabled_endpoints`；身份绑定/认证原文站点数；`profile/configuration` 的配置站点、`approved_factors`、`configured_intervals`；`observation/observations` 的观测站点、`observation_batches`、`valid_values`；`quality.open_items`、存储原文/批次数和 `updated_at`。

不读取 MN、PW、完整原文，不解析协议，不从“有值”推断 RTU 或仪器健康。

## 6. 本轮测试与 UI 证据

所有以下通过结果均在本轮最终代码上复跑，不沿用旧工作树的 60/51/23 项结果。

| 验证 | 结果 |
| --- | --- |
| `npm run test:api` | PASS，50/50 |
| 两个监测直接测试文件 | PASS，14/14 |
| 浏览器行为测试 | PASS，11 个子场景，测试器 12/12（含父测试），0 skipped |
| `npm run lint` | PASS；最终构建亦重新执行 lint |
| `npm run build -- --outDir dist` | PASS；仅输出协作工作树 dist，不生成发布包或提交产物 |
| `git diff --check` | PASS，仅 LF/CRLF 转换提示 |
| 当前桌面 Web UI | PASS（隔离模拟接口），1440×1000，截图检查 |
| 当前移动 Web UI | PASS（隔离模拟接口），390×844，详情/接入非空及无文档级横向溢出，截图检查 |
| 真实后端联调及实际服务端越权拒绝 | NOT RUN；403/404 用例仅证明 Web 正确处理拒绝响应，不自证后端权限安全 |
| 原管理流程真实保存/导入/试剂写入 | NOT RUN；本轮 UI 仅验证入口和表单不回归，禁止隔离浏览器发业务写请求 |
| 产品 Code Review / 最终验收 | PENDING |
| 正式设计 Review | NOT RUN；不实施旧设计优化建议 |
| 部署 | NOT RUN / 禁止 |

浏览器覆盖八状态、首次失败重试、刷新失败保留、错误站点响应拒绝、403/404 不回退、在途刷新切站、趋势能力无载荷、协议字段不展示、多角色 admin/reviewer/operator、非 admin 接入直达拒绝、摘要分轴、目录缺失投影、旧入口、站点搜索分流和移动宽度。模拟接口拦截全部 `/api` 请求，业务写请求会导致测试失败；未启动后端、接收器或数据库。

初轮测试曾因按钮图标可访问名称和弹窗定位超时失败，修正标签/测试定位后最终复跑全部通过；Vite 沙箱写临时配置曾报 EPERM，经授权重跑成功。这些失败没有被当作通过。

本机截图目录：`C:\Users\LENOVO\AppData\Local\Temp\station-web-collaborator-20260915`，含 `detail-desktop.png`、`access-desktop.png`、`directory-desktop.png`、`detail-mobile.png`、`access-mobile.png`。截图、浏览器缓存和夹具运行产物不提交；主项目可按下列命令重新生成。

复跑监测直接测试：

```powershell
node --test src/pages/sites/stationMonitoring.test.js src/pages/sites/stationMonitoringWiring.test.js
```

浏览器复跑（在 `平台开发/react-vite` 执行；使用外部已安装的 Playwright，无新增仓库依赖）：

```powershell
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5187 --strictPort
# 另一个终端；模块路径替换为验收机已安装 Playwright 的绝对路径。
$env:STATION_WEB_TEST_URL = 'http://127.0.0.1:5187'
$env:STATION_WEB_PLAYWRIGHT_MODULE = '<absolute-path-to-playwright>'
$env:STATION_WEB_BROWSER_CHANNEL = 'msedge'
$env:STATION_WEB_EVIDENCE_DIR = '<temporary-evidence-directory>'
node --test src/pages/sites/stationMonitoring.browser.test.js
```

缺少测试 URL 或 Playwright 模块时浏览器测试显式 SKIP，不算 UI 通过。监测直接测试尚未进入 `package.json` 的 `test:api`；该文件不在交接白名单内，必须另跑上述命令。

## 7. 代码 Review 整改与遗留

已进入协作提交：请求竞态与卸载保护、旧业务状态标签、独立监测字段消费、失败刷新保留。此次补齐成功页刷新、响应站点校验、成功漏站不保留旧投影、趋势事实门槛、数据轴状态标签、脱敏字段与业务时间边界、真实浏览器行为测试。

未整改的范围外项：

- **P2 主布局导航定位**：`src/layouts/MainLayout.jsx:61` 仍只取 URL 第一段，接入中心面包屑/选中态仍归属“站点全景”。该文件不在交接白名单中，未修改；请主项目授权后另行修复。
- **P2 标准测试入口**：`package.json` 不含新增监测测试，未越界改脚本。本回传给出完整直接命令，主项目应加入协作验收流水线。

这是协作者自查与整改回传，不代替主项目独立 Code Review，不宣称旧 Review 所有问题全部闭环。

## 8. 剩余后端阻断与依赖

基于 `db20184` 服务端代码只读核查，本分支未修后端、未运行后端测试：

1. 接入摘要未返回批准因子、配置周期、当前有效观测批次和有效值数量。Web 对这些项显示“暂无”，不把配置站点数、存储批次或未批准因子数量当作替代。
2. 总览缺少 RTU 轴；近期事项固定空数组；声明趋势能力时也没有总览趋势聚合载荷。Web 保留各区准确空态；具体服务端契约和事实补齐由开发经理负责。
3. **需后端验证的当前身份一致性风险**：`backend/app.py` 的 `_station_monitoring_projection` 按选定端点过滤因子和值，`_station_monitoring_overview` 则重新调用站点级配置和值查询，未应用同一端点过滤。多端点/重绑定场景可能出现主状态与明细事实来源不一致；未运行隔离服务端夹具复现，不能作为已确认或已修复结论。建议开发经理优先补场景测试并统一当前端点投影。
4. 八类呈现中 `data_unavailable` 是请求失败表达；现服务端业务状态摘要统计七种状态，不要求后端伪造第八种站点事实。

依赖审计仍有 8 个既有受影响包节点（1 moderate、7 high），详情见依赖审计交接文件；未执行依赖升级或 `npm audit fix`。本轮未重跑网络审计，不能宣称漏洞已经消失。

## 9. 下一断点与交付判断

下一断点：主项目人员针对本独立分支开展产品 Code Review，确认范围外主布局/标准门禁的处理归属；开发经理补后端事实及端点一致性测试，再安排隔离真实后端联调和产品验收。由产品主任务把本文件索引及待办同步到权威产品台账；本开发树台账只是产品树指针，协作者不维护第二份台账。

- 现有功能回归：自动化和旧 UI 入口检查通过，实际保存/导入未验收。
- 边界：切站、请求取消、失败保留、成功漏站、趋势缺事实已覆盖；真实服务端身份/范围联调待验收。
- 失败：明确可见、可重试；新增监测只读，不产生业务写请求，不在失败时用档案或缓存冒充监测。
- 类型与状态：监测独立于业务状态；分轴仅消费服务端事实；业务时间不使用创建时间替代。
- 权限与不可逆风险：无新增控制或数据写入；前端拒绝用例不替代服务端权限验证。
- 证据充分性：足以回传 Web 协作实现进入 Review，不足以冻结、部署或宣称全部后端阻断修复。

结论：按 `(1)` 的 Web 白名单实现、直接测试与回传已完成，停在产品 Code Review；真实后端联调、正式设计 Review、最终产品验收和范围外修复不在本交付中冒充完成。
