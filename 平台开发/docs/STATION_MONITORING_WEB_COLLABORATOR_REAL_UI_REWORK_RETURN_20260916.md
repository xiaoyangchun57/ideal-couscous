# 站点监测 Web 真实 UI Review 返修交付

> 日期：2026-09-16
>
> 协作者：`LI991020`
>
> 分支：`codex/station-monitoring-web-db20184`
>
> 提交前 HEAD：`a10f04507f4a3b4d29677afd6bd645b7dc131387`
>
> 状态：`WEB_REWORK_PUSHED / PRODUCT_READONLY_REVIEW`

## 1. 交付身份与边界

本轮依据 `STATION_MONITORING_WEB_COLLABORATOR_HANDOFF_20260914(5).md` 完成九项 Web 返修。返修只位于独立协作工作树：

`D:\李\1\软件\新建文件夹\1\1\ssz\.worktrees\station-monitoring-web-db20184`

交接声明的验收基线 `a3d16c7ce40891f0625e6599e4a2bc95b0f171d4` 不在本地对象库，也不在当前可见远端引用中。本轮未改写历史，实际从协作分支当前 HEAD `a10f04507f4a3b4d29677afd6bd645b7dc131387` 继续；只读核对时 `origin/codex/next-development` 为 `db20184fd11b0958d63fe6836ea7563756ddcfe8`。

交接文件最初要求完成后停在产品只读 Review，不得自行提交或推送；用户随后明确授权提交并推送本次九项 Web 返修。因此：

- 九项 Web 返修提交：`c209bdcce56ac1df8372418540dea3a1f8273437`。
- 代码提交已推送至 `origin/codex/station-monitoring-web-db20184`；本交付状态更新使用后续文档提交，SHA 以回传消息为准。
- 未合并、未创建 PR、未打标签、未冻结、未部署。
- 未修改后端、小程序、接收器、数据库、角色、业务数据、依赖版本或 lockfile。
- 未启动真实后端、接收器或数据库；浏览器测试拦截全部业务 API，非 GET 写请求会直接失败。

## 2. 九项返修结果

| 返修项 | 实现结果 | 直接证据 |
| --- | --- | --- |
| 告警历史不可达 | 保留今日/本周/本月，增加“全部历史”；汇总明确为全部历史，列表明确显示所选范围与数量；范围无记录时提示可切换全部历史 | `alertDateRange.test.js` PASS |
| 无样本被表现为失败 | 完整性按 `expected`、有效性按 `actual` 判断分母；及时性优先使用专用样本分母，现有后端未返回时以 `actual` 判断是否存在样本；零分母统一显示中性“无样本”，不绘制红色百分比、失败图标或填充条 | `dataQualityPresentation.test.js` PASS |
| 影像暴露内部枚举 | `manual_report` 显示“人工上报”；已知采集来源显示中文；缺失显示“未记录”，未知非空值显示“待确认”；表格和详情共用同一映射 | 纯函数测试及浏览器测试 PASS |
| 影像筛选空态误导 | 有筛选且结果为空时显示“没有符合当前筛选条件的记录”，并提供“重置筛选”入口 | 浏览器测试 PASS |
| 已取消计划仍显示进行中风险 | `cancelled` 纳入前置风险关闭状态；已取消详情不再显示未安排站点、缺执行任务、路线优先级或旧校验风险，只保留取消事实和原计划内容 | 单元测试及浏览器测试 PASS |
| 接入观察时间暴露 ISO/UTC | `updated_at` 复用站点监测本地时间格式；缺失仍显示“服务端未提供” | 单元测试及浏览器测试 PASS |
| 站点表格横向不可达 | 站点名称、编码和类型合并为“站点身份”；通信和有效观测合并为“关键时间”；压缩区县、状态和负责人列；操作列固定在右侧并设置明确表格宽度 | 1280×720、37 条隔离记录浏览器测试 PASS |
| 筛选反馈抬高工具栏 | 删除独立“当前结果”双层元信息，改为同一工具栏内紧凑“已筛选 N 条” | 浏览器断言工具栏高度不增加 PASS |
| 单站监测末项不可达 | 页面根节点建立确定 flex 高度，正文使用独立纵向滚动区域；较矮窗口可连续滚动到“近期告警、工单与巡检” | 1280×600 浏览器测试 PASS |

## 3. 修改文件

### 告警

- `平台开发/react-vite/src/pages/alerts/AlertsPage.jsx`
- `平台开发/react-vite/src/pages/alerts/alertDateRange.js`
- `平台开发/react-vite/src/pages/alerts/alertDateRange.test.js`

### 驾驶舱

- `平台开发/react-vite/src/pages/cockpit/CockpitPage.jsx`
- `平台开发/react-vite/src/pages/cockpit/dataQualityPresentation.js`
- `平台开发/react-vite/src/pages/cockpit/dataQualityPresentation.test.js`

### 影像档案

- `平台开发/react-vite/src/pages/archive/ArchivePage.jsx`
- `平台开发/react-vite/src/pages/archive/archivePresentation.js`
- `平台开发/react-vite/src/pages/archive/archivePresentation.test.js`
- `平台开发/react-vite/src/pages/archive/ArchivePage.browser.test.js`

### 巡检计划

- `平台开发/react-vite/src/pages/plan-schedules/PlanSchedulesPage.jsx`
- `平台开发/react-vite/src/pages/plan-schedules/planExecutionPackages.js`
- `平台开发/react-vite/src/pages/plan-schedules/planExecutionPackages.test.js`
- `平台开发/react-vite/src/pages/plan-schedules/PlanSchedulesPage.browser.test.js`

### 站点页面

- `平台开发/react-vite/src/pages/sites/SitesPage.jsx`
- `平台开发/react-vite/src/pages/sites/SiteMonitoringPage.jsx`
- `平台开发/react-vite/src/pages/sites/SiteMonitoringPage.css`
- `平台开发/react-vite/src/pages/sites/StationAccessPage.jsx`
- `平台开发/react-vite/src/pages/sites/stationMonitoring.test.js`
- `平台开发/react-vite/src/pages/sites/stationMonitoring.browser.test.js`

### 交付文档

- `平台开发/docs/STATION_MONITORING_WEB_COLLABORATOR_REAL_UI_REWORK_RETURN_20260916.md`

## 4. 路由和权限变化

- 未新增、删除或重命名路由。
- 未修改页面角色配置、导航权限或服务端权限逻辑。
- `/alerts`、`/archive`、`/plan-schedules`、`/sites`、`/sites/:siteId`、`/sites/data-access` 的既有责任不变。
- 前端可见性不替代服务端权限校验；本轮没有以客户端状态扩大站点范围或管理员能力。

## 5. 服务端字段依赖

本轮未增加或修改公共契约，Web 依赖现有只读字段：

- 告警：`GET /api/alerts` 的 `created_at`、状态、等级及详情字段；`GET /api/alerts/statistics` 仍作为全部历史汇总。
- 数据质量：`expected`、`actual`、`completeness_rate`、`validity_rate`、`timeliness_rate`；若以后返回 `sampled_metric_count`，及时性优先使用该专用分母判断样本存在性。Web 不自行计算或覆盖服务端率值。
- 影像：`source_type`、`capture_source`。未知枚举只做“待确认”展示，不反向推导业务类型。
- 计划：`status`、`execution_status` 和既有 `cancellation`；取消状态只影响当前风险提示，不改写历史行程和取消审计事实。
- 接入观察：`updated_at`；Web 仅进行本地时区格式化，不改变时间事实。
- 站点目录：既有站点身份、`monitoring_status`、主原因、`last_communication_at`、`last_valid_observation_at`、负责人及档案入口。

## 6. 测试结果

所有通过结果均在本轮最终代码上执行：

| 验证 | 结果 |
| --- | --- |
| 九项新增及直接相邻测试 | PASS，35/35 |
| `npm run test:api` | PASS，78/78 |
| 站点监测浏览器测试 | PASS，18/18 |
| 影像档案浏览器测试 | PASS，8/8 |
| 计划取消与删除记录浏览器测试 | PASS，3/3 |
| `npm run lint` | PASS |
| `npm run build -- --outDir dist` | PASS |
| `git diff --check` | PASS，仅工作树 LF/CRLF 转换提示 |
| 浏览器业务写请求 | 0；测试夹具拒绝所有非 GET 请求 |
| 页面运行异常 | 0 `pageerror` |

浏览器覆盖常用桌面宽度 1280×720、较矮桌面 1280×600、既有 1440×1000 和 390×844 回归。隔离 Vite 服务和测试浏览器已停止，构建目录未纳入 Git 状态。

## 7. `NOT RUN` 与残余风险

以下事项不能由本轮隔离 Web 测试替代，保持 `NOT RUN`：

1. 真实后端中的历史告警两条记录打开详情及统计一致性。
2. 真实监测接入数据进入页面后的当前值、趋势、四分轴和切站防旧数据。
3. 审核员、现场人员在真实后端上的站点数据范围，以及非管理员接入观察拒绝。
4. 真实接口失败后的原位重试、普通角色无删除记录入口、达到分页阈值后的真实影像分页。
5. 生产环境、线上容器、固定数据库和任何业务写流程；均禁止操作。

其他残余风险：

- 验收基线 `a3d16c7ce40891f0625e6599e4a2bc95b0f171d4` 当前不可获取，产品 Review 需确认其是否为未推送提交、错误 SHA 或已被改写的引用。
- 当前后端数据质量响应没有返回 `sampled_metric_count`。Web 已兼容当前契约，但若服务端需要严格区分“有实际值”和“进入及时性统计的指标”，应补回该只读分母字段并增加真实联调。
- Vite 浏览器运行期间出现既有 Ant Design 警告：`Instance created by useForm is not connected to any Form element`。未出现页面异常；警告来源不在本次返修范围内，需主项目另行定位。

## 8. 下一步与交付判断

主项目下一步应：

1. 在当前未提交 diff 上执行产品只读 Code Review，核对九项业务语义和 UI 行为。
2. 明确缺失验收基线 `a3d16c7` 的来源，并决定以哪个不可变提交作为后续验收基线。
3. 使用远端协作分支及回传提交 SHA 执行产品只读 Review；不得直接合并、冻结或部署。
4. 使用隔离真实后端和非生产数据完成上述 `NOT RUN` 联调，再决定是否进入冻结候选。

完成前检查：现有相关 Web 回归已通过；九项边界均有直接测试；本轮只读展示不产生业务副作用；状态和空样本语义已统一；未引入新的权限或不可逆风险。现有证据足以进入 `PRODUCT_READONLY_REVIEW`，不足以宣称可冻结、可部署或真实后端已经验收。
