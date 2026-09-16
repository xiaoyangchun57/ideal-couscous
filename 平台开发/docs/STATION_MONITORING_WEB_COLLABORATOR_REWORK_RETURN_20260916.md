# 站点监测 Web 协作者返修交付

> 日期：2026-09-16
>
> 协作者：`LI991020`
>
> 分支：`codex/station-monitoring-web-db20184`
>
> 状态：`WEB_REWORK_COMPLETE / PRODUCT_CODE_REVIEW`

## 1. 提交

- 原返修非合并检查点：`657c6cbe6e13baf01bdc7a34cb97e033705fd045`
- 计划取消与删除记录实现：`8a45d44214b1ee60acabef7e8f25d16c7fb4e215`
- 计划详情条件字段布局修正：`2d9e63042dfcd52bee7ce6f6c43868b8d3897b0b`
- 本交付文档单独提交；文档提交号以协作分支日志和回传消息为准，避免在文档中自引用。

上述代码提交均已推送至原协作分支。未合并、未打标签、未部署、未操作生产数据。

## 2. 修改文件

累计返修文件如下，均为交接允许的 Web 文件及直接测试：

- `平台开发/react-vite/package.json`
- `平台开发/react-vite/src/services/api.js`
- `平台开发/react-vite/src/services/api.test.js`
- `平台开发/react-vite/src/layouts/MainLayout.jsx`
- `平台开发/react-vite/src/layouts/mainLayoutNavigation.js`
- `平台开发/react-vite/src/layouts/mainLayoutNavigation.test.js`
- `平台开发/react-vite/src/pages/sites/SitesPage.jsx`
- `平台开发/react-vite/src/pages/sites/SiteMonitoringPage.jsx`
- `平台开发/react-vite/src/pages/sites/stationMonitoring.js`
- `平台开发/react-vite/src/pages/sites/stationMonitoring.test.js`
- `平台开发/react-vite/src/pages/sites/stationMonitoring.browser.test.js`
- `平台开发/react-vite/src/pages/archive/ArchivePage.jsx`
- `平台开发/react-vite/src/pages/archive/ArchivePage.css`
- `平台开发/react-vite/src/pages/archive/archivePagination.js`
- `平台开发/react-vite/src/pages/archive/archivePagination.test.js`
- `平台开发/react-vite/src/pages/archive/ArchivePage.browser.test.js`
- `平台开发/react-vite/src/pages/plan-schedules/PlanSchedulesPage.jsx`
- `平台开发/react-vite/src/pages/plan-schedules/planAuditPresentation.js`
- `平台开发/react-vite/src/pages/plan-schedules/planAuditPresentation.test.js`
- `平台开发/react-vite/src/pages/plan-schedules/PlanSchedulesPage.browser.test.js`

未修改后端、小程序、接收器、数据库、Compose、依赖版本或 lockfile。

## 3. 行为、路由和权限

- 管理员站点目录明确请求 `scope=all`；非管理员明确请求 `scope=mine`。页面以服务端实际返回的 `scope`、`available_scopes` 和站点投影为权威。
- 监测四分轴优先使用服务端 `status_label`，视觉语义使用服务端 `status`；不由“有值”推导设备健康。
- 最新值、趋势和仪器关联名称优先使用 `factor_name_cn`，内部 `business_metric` 仅作最后兜底。
- `/sites/data-access` 使用精确页面标题和面包屑，同时保持 `/sites` 父导航选中；未知路径不继承错误导航。
- 影像档案表格和网格共用筛选、当前/历史范围、服务端分页及总数；网格在工作区独立滚动。
- 已取消计划详情只消费服务端 `cancellation`，显示原因、操作人和时间；缺失字段显示“未记录”，不使用计划备注替代。
- “删除记录”位于现有巡检计划页面，仅完整角色集合含 `admin` 时可发现和请求；记录只读，不提供恢复或再次删除。

前端可见性不替代服务端权限校验。

## 4. 服务端字段依赖

- 目录范围：`scope`、`available_scopes`、站点监测投影和摘要。
- 四分轴：`status`、`status_label`、`reason`、`last_received_at`。
- 业务因子：`factor_name_cn`，其次为明确业务名称，最后才使用 `business_metric`。
- 取消详情：`GET /api/plan-schedules/<id>` 返回 `cancellation: { reason, operator_id, operator_name, occurred_at }`。
- 删除记录：管理员接口 `GET /api/plan-schedules/purge-audits?page=&page_size=` 返回 `{ items, total, page, page_size }`。
- 删除记录项：`plan_id`、`plan_name`、`status_before_delete`、`owner_name`、`period_start`、`period_end`、`site_ids`、`reason`、`operator_name`、`purged_at`。

Web 不读取通用时间线拼装计划审计，不在本地生成删除记录。

## 5. 测试结果

| 验证 | 结果 |
| --- | --- |
| `npm run test:api` | PASS，77/77，包含监测与计划追溯直接测试 |
| 计划模块全部直接测试 | PASS，33；浏览器测试在无环境变量的通配命令中按设计 SKIP 1 |
| 计划追溯浏览器测试 | PASS，3/3，覆盖管理员入口、接口失败重试、空态、完整记录、非管理员不可发现、取消字段缺失 |
| 站点监测浏览器测试 | PASS，16/16 |
| 影像档案浏览器测试 | PASS，6/6 |
| `npm run lint` | PASS |
| `npm run build -- --outDir dist` | PASS |
| `git diff --check` | PASS |
| 隔离契约夹具下当前 React UI | PASS；无业务写入，截图仅保存在本机临时目录 |
| 真实后端联调 | `NOT RUN` |
| 真实业务数据、生产环境、部署 | `NOT RUN` / 禁止 |
| 产品 Code Review | `PENDING` |

## 6. 剩余阻断和下一步

主项目需实现并验证以下后端只读契约：

1. 计划详情稳定返回 `cancellation`，并保证原因、操作人与发生时间来自取消审计事实。
2. `purge-audits` 仅管理员可访问，分页、总数及各审计摘要字段符合固定契约。
3. 站点范围、监测分轴、中文因子名等服务端事实与 Web 返修契约完成真实联调。

当前浏览器测试使用隔离契约夹具，只证明 Web 的请求、呈现、空态、失败重试和前端发现性符合约定，不证明真实服务端越权拒绝。后端契约可用后，应由主项目执行隔离真实后端联调和产品 Code Review；本协作者交付停在 `WEB_REWORK_COMPLETE / PRODUCT_CODE_REVIEW`。
