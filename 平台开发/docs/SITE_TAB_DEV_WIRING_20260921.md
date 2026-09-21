# 站点 Tab 开发接线清单

> 状态：`PRODUCT_REVIEW_CORRECTED / WAITING_LATEST_ICON_REVIEW`
> 适用范围：小程序首页运维摘要、`pages/responsible-sites/`、第五个“站点”Tab
> 上位合同：`WEB_SITE_IDENTITY_PLAN_SEED_MOBILE_HOME_OVERVIEW_DEVELOPMENT_HANDOFF_20260921.md`
> 停点：最新站点 Tab 图标通过设计 Review 后，开发经理按上位合同统一接线；不冻结、不部署

本文只记录设计节点到真实业务契约的绑定，不重复权限、事务、幂等和测试规则；冲突时以上位合同为准。

## 一、必须完成的结构接线

1. `app.json` 当前仍只有四个 Tab。开发需新增第五项：`首页｜巡检｜站点｜告警｜我的`，站点页固定为 `pages/responsible-sites/responsible-sites`，使用最终通过的两张 PNG。
2. 首页两条摘要进入站点 Tab 必须使用 `wx.switchTab`，不得对 Tab 页使用 `navigateTo`，也不得向 Tab URL 传查询参数。
3. 跨 Tab 目标只使用一次性 `app.globalData.stationHubTarget`：
   - `{ view: 'stations' }`
   - `{ view: 'reagents', filter: '' | 'expired' | 'expiring' | 'low_volume' }`
4. 站点页在 `onShow` 校验、应用并立即清除目标；没有新目标时保留用户上次的模式、范围和筛选。`switchTab` 失败时只清除本次目标并提示重试。
5. 新 Tab 接通后，删除“我的”页重复的“负责站点”主入口。
6. 站点卡片沿用真实详情路由：`/pages/site/site?site_id=<id>&source=<source>`；不得改成不存在的 `/pages/site-detail`。

## 二、站点页 ViewModel

### 页面状态

| 字段 | 类型/取值 |
|---|---|
| `activeTab` | `stations` 或 `reagents`，默认 `stations` |
| `scope` | `mine` 或 `all`；`all` 仅服务端授权管理员可用 |
| `scopeCounts` | `{ mine: number|null, all: number|null }` |
| `canViewAll` | 由接口 `available_scopes` 决定 |
| `monitoringEnabled` / `monitoringPublic` | 监测能力和字段展示开关；未启用不阻断站点档案 |
| `sites` / `loading` | 列表与加载状态 |
| `error` | `string`，无错误为 `''`；WXML 直接显示错误文案 |
| `keywordInput` / `keyword` | 输入值与已确认搜索值 |

站点列表继续读取 `GET /api/station-monitoring/sites?scope=mine|all&keyword=...`；监测未开放时按上位合同回退到有权站点目录。`monitoring_status` 直接使用服务端权威值，当前包括：

`not_connected / awaiting_first_frame / raw_received_config_pending / waiting_first_valid / interval_unconfigured / normal / attention`

WXML 展示 `monitoring_status_label` 和 `monitoring_reason`，不得另造 `warning / error / offline / unknown` 等业务结论。卡片点击使用 `site_id`（兼容现有 `id` 时须在投影层统一），不得从“收到数据”推断 RTU、仪器或设备健康。

## 三、试剂页 ViewModel

### 页面状态

| 字段 | 类型/取值 |
|---|---|
| `reagentEnabled` / `reagentLoading` | 能力与加载状态 |
| `reagentError` | `string`，无错误为 `''` |
| `reagentNoViewPermission` | `boolean`；与零站点、无关注项分开表达 |
| `activeFilter` | 空串、`expired`、`expiring` 或 `low_volume`；空串就是“全部” |
| `reagentItems` | 当前筛选后的去重关注项 |

`calibration_failed` 和 `pending_calibration` 不是可见筛选项，只在“全部”的多原因标签中到达。

接口仍以 `GET /api/reagent-overview` 为唯一跨站来源，后端目标结构以上位合同为准。JS 必须显式投影为 WXML 所需结构：

| WXML 字段 | 来源/规则 |
|---|---|
| `key` | `site_id + '_' + reagent_id` |
| `id` | `reagent_id` |
| `site_name` / `reagent_name` | 接口同名字段 |
| `volume` | `current_qty`，必须保留数值 `0` |
| `unit` | 试剂主数据单位，不得固定写成 `mL` |
| `expiryText` | 由服务端到期/预计可用事实格式化；无事实显示明确空态，不猜测 |
| `attention_reasons` | 稳定原因码映射为 `{ type, label }[]`；多原因不得丢失 |
| `canReplace` / `canCalibrate` | 服务端动作能力，不按前端角色名自行推导 |

后端稳定原因码为 `expired / expiring / low_volume / pending_qc / failed_qc`。若保留现有样式名，投影层仅可把 `pending_qc` 映射为 `pending_calibration`、`failed_qc` 映射为 `calibration_failed`；筛选仍只识别前三种库存原因。

## 四、两个维护 Sheet

静态结构已经通过设计 Review。JS 需绑定以下状态，并遵守上位合同的权限、事务和幂等要求：

- 登记更换：只读站点、试剂、当前余量与单位；填写新余量、更换日期、预计可用天数，可选批次号和备注。
- 完成标定：只读站点、试剂和更换时间；填写标样值、实测值、结果；不通过时必须选择重新标定或报修。
- 字段错误是 `string|null`；用户修改对应字段时即时清除。
- `serverError` 是 `string|null`；失败保留全部输入和目标对象，可沿用原幂等键重试。
- `submitting=true` 时禁止重复提交和关闭，所有输入禁用；过期响应和页面卸载不得回写。
- 成功后关闭 Sheet、刷新试剂并以服务端结果为准，不在客户端先行伪造成功状态。

## 五、首页摘要合同

WXML 已采用以下字段，JS 必须与之保持一致：

```text
stationSummary = { total, normal, attention, unavailable }
reagentSummary = { concernCount, expiredCount, expiringCount, lowVolumeCount }
```

- `unavailable = total - normal - attention`，表示暂无有效业务数据，不表示离线或设备故障。
- 三类试剂原因可重叠，分类数不要求相加等于 `concernCount`。
- 首页不再显示试剂明细前两条或“另有 N 项”。
- 首次加载失败与保留旧数据后的刷新失败分开；两块局部失败互不阻断。
- `not_enabled` 为中性状态，站点档案入口仍可用。

## 六、开发验收

- 五 Tab 配置、首页两种目标跳转、一次性目标消费与失败清理通过。
- 管理员/非管理员范围、搜索、四个试剂筛选、多原因、`0` 数值和空态通过。
- 两个 Sheet 的字段错误、提交中、服务端失败保留、成功刷新和重复提交保护通过。
- 站点详情、位置校准、首页今日行动、巡检、告警和“我的”相邻回归通过。
- 自动化和静态检查完成后停在代码 Review；真实微信 UI 标记 `NOT RUN`，由独立 UI 验收任务执行。

## 七、设计 Review 结论

`sheet-review.html` 与 `sheet-states-review.jpg` 覆盖默认、字段错误、提交中、服务端失败保留输入、成功，以及标定失败后续动作等关键状态，静态通过。

`tab-site.png` 与 `tab-site-on.png` 已在上一轮 Review 后再次更新，旧的“偏小偏细”结论失效。最新资产尺寸已进入现有 Tab 范围，但视觉重量与五 Tab 并排效果仍待复核；通过后释放开发接线。
