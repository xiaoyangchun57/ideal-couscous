# PR #4 · 协作者 B 首轮 Web 收口校准（①②③）

> 分支：`collab/b-web-closeout` → `main`；负责人：协作者 B；主线 Reviewer：`xiaoyangchun57`（已请求 Review）。
> 合同：`平台开发/docs/workstreams/B_CURRENT.md` §7；本文件是整个 PR 的**现行交接**，不再按返修轮次拆开阅读。版本以 PR 当前分支为准，历史过程由 Git 保存。
> 停点：等待主线 Review；**未合并、未冻结、未部署，不操作生产数据**。

## 一、产品结果与完成边界

| 当前事项 | 本 PR 完成结果 | 验收口径 |
|---|---|---|
| ① 站点全景保留“最后数据”和“关键时间” | 列表新增“最后数据”列，保持“关键时间”；从一次 `GET /api/station-monitoring/sites` 返回的可信批量投影展示每个因子的最新有效值。刷新失败保留上次成功事实，服务端明确缺失时清除旧事实。 | 真实 Edge + 隔离正式观测 SQLite + **实际 Flask 监测接口**贯通 PASS；另有隔离 API 浏览器回归。未使用生产业务数据。 |
| ② 人员退出后退出登录/分配/候选，历史仍可读 | `status === 'active' && !deleted_at` 作为统一可分配判定，站点负责人、转交接收人和旧周计划巡检人复用；原有服务端注销拒绝登录并保留历史。 | 既有真实 Edge + 本地开发后端的同一账号注销前/后证据 PASS；周计划页当前**未接线**，只验证代码与单测，不宣称其 UI 已通过。 |
| ③ 中文输入法选词时不被 URL 回写覆盖 | `useUrlSyncedSearch` 在 composition 进行中保留草稿，上屏后写入所选中文，普通输入/清空保持原行为。 | 真实 Edge 隔离 API 浏览器回归 3/3；微软拼音取证另见证据说明。 |

① 的“最后数据”按用户确认，特指**单站详情的最新有效值**，不是最近收到的原始报文或旧表 `sensor_data` 的值。用户追加授权在本 PR 修改 `backend/app.py`；② 的 `weekly-plans/` 和公共工具明确保留。三项同属原 PR，不新建 PR。

## 二、接口、数据与兼容性

- **① 新增只读响应字段**：`GET /api/station-monitoring/sites?scope=mine|all` 的 `items[].latest_values` 是数组；每项仅包含 `business_metric`、`factor_name_cn`、`standard_value`、`standard_unit`、`observed_at`。未接入、未形成有效观测或不满足单站详情展示门禁时返回 `[]`；数值 `0` 不当作空值。`last_received_at`、`last_valid_observation_at` 与原状态字段继续保留。
- **来源与隔离**：沿用当前绑定、启用的可信端点、当前因子配置及 `monitoring_latest_values` 正式选定且质量有效的观测；列表和单站详情共用展示门禁，批量投影复用已有每站查询结果，不从前端逐站请求详情。原有 `mine/all` 权限与站点范围不变，不泄漏端点内部字段。`GET /api/sites` 仍只承载静态档案。
- **② 仅客户端候选收窄**：服务端人员注销、登录和历史业务规则未改；站点负责人仍要求 operator 角色，旧周计划巡检人不附加新角色限制。`package.json` 将统一判定与站点候选测试纳入 `test:api`。**服务端直接调用旧周计划写接口的缺口仍在**，见第五节。
- **③ 仅搜索草稿同步行为改变**：composition 中忽略外部 URL 覆写，上屏后以最终选词持久化 `q`，普通键入、外部清空及取消待提交写入保持原有语义。
- 除①的只读响应新增字段外，无新增状态迁移、写接口、迁移、部署开关或仓库依赖；当前环境监测能力默认关闭，隔离测试只在临时进程开启。

## 三、PR 文件全集与归属（共 36 个）

以下均为**相对仓库根目录**的路径；本交接文件本身也计入。以当前 PR 的文件清单核对，三项代码、测试及原有证据均在 `collab/b-web-closeout`。

| 归属 | 文件（未写出的路径前缀见分组） |
|---|---|
| ① 后端：`平台开发/backend/` | `app.py`；`test_station_monitoring_normalization.py`；`test_station_monitoring_web_integration.py` |
| ① Web：`平台开发/react-vite/src/pages/sites/` | `SitesPage.jsx`；`stationMonitoring.js`；`stationMonitoring.test.js`；`stationMonitoring.browser.test.js`；`stationMonitoring.integrated.browser.test.js` |
| ② Web：`平台开发/react-vite/src/` | `utils/assignableUsers.js`；`utils/assignableUsers.test.js`；`pages/sites/siteManagerCandidates.js`；`pages/sites/siteManagerCandidates.test.js`；`pages/users/UsersPage.jsx`；`pages/weekly-plans/WeeklyPlansPage.jsx` |
| ② 测试门禁：`平台开发/react-vite/` | `package.json` |
| ③ Web：`平台开发/react-vite/src/hooks/` | `useUrlSyncedSearch.js`；`useUrlSyncedSearch.test.js`；`useUrlSyncedSearch.browser.test.js` |
| 本 PR 交接：`平台开发/docs/` | `WEB_CLOSEOUT_COLLABORATOR_B_PR_20260923.md` |

全部 17 个证据文件均位于 `平台开发/docs/evidence/b-web-closeout/`：

- ③ 真实微软拼音与作业指导：`B1-1-选词中-输入框显示拼音.png`、`B1-2-选词后-输入框中文且URL已写入.png`、`T3_输入法选词验证作业指导书.html`。
- ② 注销前后真实 UI：`B2-1-注销前-该账号可正常登录.png`、`B2-2-注销前-站点负责人候选含该账号.png`、`B2-3-注销前-转交接收人候选含该账号.png`、`B2-4-注销前-人员列表全貌.png`、`B2-5-注销后-该账号登录被拒.png`、`B2-6-注销后-站点负责人候选已排除该账号.png`、`B2-7-注销后-转交接收人候选已排除该账号.png`、`B2-8-注销后-人员列表仍显示该账号.png`、`B2-9-注销后-历史用车记录仍显示该账号.png`。
- ③ 隔离 Edge 回归：`url-search-1-external-applied.png`、`url-search-2-composing.png`、`url-search-3-crossing-write.png`、`url-search-4-committed.png`、`url-search-5-external-cleared.png`。

**计数**：后端 3 + Web 15 + 交接 1 + 证据 17 = **36**。①正式观测贯通验证由后端/浏览器可复跑测试及测试输出证明，**未在 PR 中存放①的浏览器截图**；不能把②③截图当作①证据。

## 四、已执行验证与证据限制

| 验证 | 当前结论 |
|---|---|
| Python：`test_station_monitoring_normalization` + `test_station_monitoring_public_gate` + `test_station_monitoring_web_integration` | **55/55 PASS**（前两组 54，隔离 Flask→Edge 贯通 1）；Python 编译 PASS。覆盖有效值与详情一致、绑定/权限/站点隔离、缺测、能力关闭。 |
| React：`npm run test:api` | **109/109 PASS**；②/③原有测试与①的 0 值、失败保留、成功缺失清除断言均执行。 |
| ① 真实 Edge + 隔离 API fixture | **19/19 PASS**；覆盖状态、数值和时间列、刷新与导航边界。该组不能冒充实际后端贯通。 |
| ① 真实 Edge + 临时 SQLite 正式归一化 + 实际 Flask API | **1/1 PASS**；浏览器调用 `GET /api/station-monitoring/sites?scope=all` 实际返回 200，所属站展示“水温：8.8 degC”及正式观测时间，未接入站展示缺测且无跨站值。2026-09-23 在本地重新执行通过。 |
| ③ 真实 Edge 隔离 API 浏览器回归 | **3/3 PASS**；合成 composition 事件测试 URL 回写与上屏，另有两张微软拼音实际渲染截图。 |
| `npm run build`（内含 `npm run lint`）及 `git diff --check` | **PASS**（针对本 PR 实现版本；本次交接文件纯文档覆写另行核对）。 |

①的贯通测试中，`/api/auth/me` 与静态站点档案由隔离浏览器夹具提供；**监测列表数值、状态、时间来自实际 Flask + 临时 SQLite**。浏览器为本机 Edge；数据库和服务均为一次性临时环境，不触碰固定库或生产。测试入口是 `平台开发/backend/test_station_monitoring_web_integration.py`，Web 断言在 `平台开发/react-vite/src/pages/sites/stationMonitoring.integrated.browser.test.js`；须显式传入 `STATION_WEB_TEST_URL` 和外部 `STATION_WEB_PLAYWRIGHT_MODULE`，缺任一项会被 unittest 跳过，**不能将 skip 记为 PASS**。

② 九张前/后对照来自此前本地开发后端一次真实注销：正确密码此前可登录，注销后 `POST /api/auth/login` 为 `401`；`GET /api/users?status=active` 从 4 条变 3 条，全量人员仍是 5 条；历史用车申请/已还车行程仍可读。原取证对本地开发库 112 张表做行数对照，除 3 条站点授权移除、新增生命周期快照和登录审计计数外，41 张业务表未删除记录。取证曾按正规业务流程完成该账号待处理用车后再软删；**本 PR 后续测试未重复注销，也未操作生产数据**。

③ 微软拼音截图由程序经 OS 层注入按键产生真实 IME 候选窗，不是人工逐键；自动化回归使用合成 composition 事件，二者分别证明输入法实际呈现与回写时序，不互相冒充。

## 五、Review 聚焦点与残余风险

1. 请主线核对①的只读批量新增字段与站点归属/有效性：未接入及无正式结果返回 `[]`；单站详情同门禁；0 值、失败保留和服务端明确缺失时清除旧事实；默认关闭的监测能力**不因本 PR 擅自开启**。生产业务数据与部署验收在后续发布门禁，不声称已通过。
2. 请主线核对②统一公共工具 `utils/assignableUsers.js` 的归属与命名。旧 `WeeklyPlansPage.jsx` 目前未被 import/挂路由，本 PR 保留其预防性过滤，但不称为当前线上可触发的 UI 缺陷。
3. **留给主线裁决的真实后端缺口**：`POST /api/weekly-plans` 使用管理员或本人权限判据，却不校验目标 `user_id` 是否仍 `active`、是否已注销；管理员直接调用仍可能指派给已退出账号。预期是服务端在创建时拒绝并返回明确业务错误（400/409 具体规则由主线确定）。本 PR 对 `backend/app.py` 的修改仅限①只读投影，**没有宣称②的直接 API 绕过已修复**。
4. ③不扩散到无关表单；已有历史截图和回归保留。审核三项后可合入主线，但**合并不是发布**；本 PR 没有迁移、生产开关调整或部署授权。

## 六、交接状态

- 当前 PR #4 已向 `xiaoyangchun57` 请求 Review；此文件覆写后在**同一分支与同一 PR**更新，不创建平行 PR。
- 下一动作：主线 Review 三项实现和上述剩余服务端缺口；按 `B_CURRENT.md` §8 的巡检领域接管在本 PR 合并后另行启动，不夹带本次 Web 收口。
