# 站点监测双端执行契约 v2

> 状态：`PRODUCT_REVIEW_RETURNED / REWORK_REQUIRED`
> 唯一写入者：开发经理。当前代码是开发基线，不是冻结候选；完成后停产品 Code Review，不提交标签、不部署。
> 工作树：`E:\杂七杂八\水质运维-开发线`，分支 `codex/next-development`。

## 1. 目标和边界

为 Web 管理端和微信小程序提供同一套服务端权威的站点监测观察能力。首版只回答“身份是否接入、原文是否到达、档案是否就绪、是否形成有效观测、问题位于哪一层”，不做水质达标判断、超限告警、自动工单或复杂分析。

既有站点管理、现场巡检、备件申请、位置校准和其他业务链路继续使用原模块，不被监测页面替代。

## 2. 数据事实归属（必须按此实现）

| 业务事实 | 唯一来源 | 页面可用语义 |
| --- | --- | --- |
| 身份是否绑定业务站点 | `trusted_endpoints.enabled=1 AND endpoint_state='bound' AND business_site_id IS NOT NULL` | 身份已绑定/未绑定 |
| 认证原文是否到达 | `ingest_raw_frames`，按绑定 endpoint 且 `authentication_status='authenticated'` | 最后通信/等待首帧 |
| 监测档案和因子 | `monitoring_endpoint_profiles`、`monitoring_factor_mappings`、已发布定义 | 档案待批准/因子覆盖 |
| 有效观测 | 当前 `observation_batches` + `observation_values`，只取 `is_current=1`、`is_published=1`、质量 `valid/suspect` | 最新有效值/最后有效观测 |
| 趋势能力 | 服务端已有对应聚合事实，不能扫描原始报文临时生成 | 趋势可用/暂不可用 |
| 仪器事实 | 有效观测存在或无有效观测 | 有数据/暂无有效观测/健康未知 |

不得用 `monitoring_endpoint_profiles` 单独判断 B1 是否接入；不得用旧 `sensor_data`、`device_shadows.status`、客户端时间差或客户端阈值推导监测状态。不得把有值推导为设备健康。

## 3. 服务端状态契约

站点投影使用独立字段 `monitoring_status`、`monitoring_status_label`、`monitoring_reason`；不得覆盖旧站点对象的 `status`、`online` 或其他既有业务字段。

| 状态 | 服务端判定 | 可展示 | 禁止展示 |
| --- | --- | --- | --- |
| `not_connected` | 无启用且已绑定的身份 | 未接入、站点资料 | 离线、数据为 0 |
| `awaiting_first_frame` | 身份已绑定，无认证原文 | 等待首条报文、绑定事实 | 离线、等待有效值 |
| `raw_received_config_pending` | 有认证原文，无当前有效 B2 档案/因子 | 原文已接收、档案待批准、最后通信 | 数值、趋势、正常 |
| `waiting_first_valid` | B2 已生效，无有效 `valid/suspect` 观测 | 等待首个有效观测、最后通信 | 正常、无异常 |
| `interval_unconfigured` | 有有效观测，但因子没有周期 | 有效值和观测时间 | 按固定时长判断新鲜度 |
| `normal` | 配置因子均在服务端周期/容忍时间内 | 有效值、可用趋势和主状态 | 水质达标 |
| `attention` | 缺报、过期、质量或已确认运行事实需关注 | 主原因、受影响因子/轴、时间 | 笼统“设备坏了” |
| `data_unavailable` | 读取接口失败 | 上次成功内容、失败时间、重试 | 刷新成空白或伪造无数据 |

`last_communication_at` 与 `last_valid_observation_at` 必须分别返回；补传旧观测按观测时间展示，不能冒充刚到数据。

## 4. API 响应要求

### `GET /api/station-monitoring/sites`

- 按当前登录用户的站点范围批量返回，禁止逐站 N+1。
- `summary` 必须包含 `total` 以及上述七个业务状态的计数（`data_unavailable` 为请求级错误，不计入站点状态）。
- 每个 `item` 至少包含：`id/site_id/name/code/district`、`monitoring_status`、`monitoring_status_label`、`monitoring_reason`、`last_communication_at`、`last_valid_observation_at`、`published_factor_count`。
- 旧站点管理接口字段原样保留，不能用监测投影覆盖其业务状态。

### `GET /api/station-monitoring/sites/{site_id}/overview`

返回 `site`、`monitoring`、`axes`、`instruments`、`recent_items`、`capabilities`、`section_status`。无 B2 或无有效事实时返回明确状态和空态，不返回假数值、假曲线或 0。

### `GET /api/station-monitoring/access-summary`

仅 `admin` 可访问。摘要至少区分启用端点、已绑定身份、收到认证原文、已配置档案、已形成有效观测、开放质量事项和存储事实；不得把档案覆盖数冒充身份覆盖数。不得返回 MN、PW、完整原文或控制动作。

## 5. Web 页面和上游入口

| 页面/入口 | 责任 | 权限和去向 |
| --- | --- | --- |
| `/sites` | 站点目录 + 原有站点管理 | 保留创建、导入、负责人筛选、档案、试剂库存 |
| `/sites/:siteId` | 单站监测详情 | 站点名、可信状态、主原因、两种时间、有效值、趋势能力、通信/数据/RTU/仪器分轴、近期事项 |
| `/sites/data-access` | 接入治理摘要 | 仅 admin；必须有可发现入口 |
| 驾驶舱/告警/全局搜索的普通站点点击 | 进入监测详情 | 跳 `/sites/:siteId` |
| 明确的档案动作 | 维护站点资料 | 保留 `/sites?archive=<id>` 兼容路径 |

目录和详情首次加载失败必须原位重试；刷新失败保留旧结果并标记失败。监测详情不得回退到档案、现场任务或本地缓存数据。

## 6. 小程序页面和上下游

- `我的 -> 负责站点` 只消费 `stationMonitoringSites`，按服务端本人范围返回；失败不使用登录缓存伪造监测列表。
- 负责站点进入 `/pages/site/site?source=responsible_sites_monitoring`，只消费 `stationMonitoringOverview`；失败不得回退 `siteTasks`。
- 站点详情保留导航和有权位置校准；签到、现场作业、备件申请继续从原业务入口进入，不在只读监测入口恢复。
- 首次加载失败可重试；刷新失败保留上次成功内容和数据时间；过期响应、重复点击和卸载需收口。

## 7. 允许文件和禁止范围

允许修改：

- 后端：`平台开发/backend/app.py`、`backend/station_monitoring.py` 及直接测试；
- Web：`平台开发/react-vite/src/App.jsx`、`src/config/navigation.jsx`、`src/services/api.js`、`src/pages/sites/` 及直接测试；
- 小程序：`平台开发/miniprogram/services/api.js`、`pages/responsible-sites/`、`pages/site/` 及直接测试。

禁止修改：接收器协议、身份导入、原文存储、数据库迁移、Compose、生产配置、私有凭据、r18 标签和无关业务模块。不同写入者不得同时修改同一文件。

## 8. 验收矩阵和回传

必须使用隔离数据库/临时目录，运行相关 Python 编译、后端 unittest、React 测试/lint/build、小程序 Node 测试和 JS 语法检查。至少覆盖：

1. 七种站点状态各一条夹具，尤其是“已绑定+已收原文+B2 未配置”；
2. 周期未配置仍返回有效值；聚合事实不存在时趋势不可用；
3. admin/reviewer/operator 权限、站点越权 403、不存在 404、接入中心非 admin 403；
4. Web 目录、详情、档案、接入中心和驾驶舱/告警/搜索的精确跳转；
5. 小程序首次失败、刷新保留、无旧业务回退和既有签到/备件入口回归；
6. 批量接口无逐站 N+1，旧站点创建/导入/档案/试剂库存不回归。

完成回传必须包含：唯一提交号、实际修改文件、API 字段样例、状态夹具结果、直接/相邻测试结果、保留的旧能力、仍待 B2 的边界和 `NOT RUN` 项。代码 Review、设计 Review、真实 UI 验收和部署分别表述；本批完成后停产品 Review，不得自行冻结或发布。
