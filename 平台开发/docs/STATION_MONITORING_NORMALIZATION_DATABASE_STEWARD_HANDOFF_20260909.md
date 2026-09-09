# 站点监测数据第二阶段开发交接

> 日期：2026-09-09
> 执行者：数据接入与数据库管理员
> 开发树：`E:\杂七杂八\水质运维-开发线`
> 分支：`codex/next-development`
> 基线：`f5410051e34a883b93df5a0c0b60fb99e5d00e49`
> 阶段：已授权实现 / 完成后停产品只读 Review
> 唯一写入者：本批数据接入与数据库管理员；开发经理和设计师暂停重叠写入

## 1. 本批目标

把第一阶段保存的可信原始帧转成可追溯的规范化监测值，并提供一个站点可用的只读监测查询闭环：

```text
ingest_raw_frames
  -> observation_batches / observation_values / status_events
  -> 站点摘要 / 最新值 / 趋势 / 仪器状态 / 数据质量只读接口
```

本批不修改任何页面，不接驾驶舱和告警，不向旧 `sensor_data` 写入，不部署、不开放公网端口、不连接真实RTU。

## 2. 已确认的输入事实

- 第一阶段已实现独立TCP接收、`7E 7E`帧边界、CRC、认证、应答、原始帧追加留存、重复关系、解析尝试和隔离错误。
- 第一阶段表仅有：`trusted_endpoints`、`ingest_raw_frames`、`ingest_parse_attempts`、`ingest_errors`；目前没有正式观测批次、因子值、仪器映射和业务投影。
- 正式协议为“国家水站平台通讯协议”，首个解析范围仍是水质站 `32H` 上行及其应答；心跳、其他粒度和厂商扩展在无真实帧前保持捕获或隔离。
- `sensor_data_raw` 是数值热表，不是协议原始字节；`sensor_data_hourly/daily` 是旧服务端聚合表。
- `sites.last_heartbeat`、`device_shadows.status` 以及固定30分钟规则不能作为本批权威在线语义。
- 协议指标需映射到现有业务指标：`water_temperature -> water_temp`、`ammonia_nitrogen -> ammonia`、`permanganate_index -> codmn`；`oxidation_reduction_potential` 先保留为已识别、未发布业务因子。

## 3. 必须实现的数据契约

### 3.1 三层责任

1. **接收事实层**：第一阶段表继续追加；原始字节不可改写或删除。
2. **规范化观测层**：新增映射、批次、因子值和状态事件；允许按解析器版本重解析，但旧结果保留并可追溯。
3. **业务投影层**：新只读API只读规范化层；旧 `sensor_data` 兼容投影本批保持关闭。

### 3.2 新增对象的最低字段责任

实现可调整表名，但语义不得缺失：

| 对象 | 必须表达 |
| --- | --- |
| 端点/RTU/仪器 | 业务站点、现有资产关联、时区、启停、预期粒度和有效期 |
| 协议因子/业务因子 | 协议码、编码格式、原始单位、唯一业务指标、标准单位、精度、是否发布 |
| 有效期映射 | 端点、RTU、仪器、协议因子、业务因子、生效/失效时间 |
| 观测批次 | 原始帧、端点、功能码、流水号、发报/观测/接收时间、粒度、幂等键、批次状态 |
| 观测值 | 批次、业务因子、原值/原单位、标准值/标准单位、质量、仪器、解析器版本 |
| 状态事件 | 通信、RTU、电源、信号、仪器工况的类型、值、发生/接收时间和来源 |
| 投影状态 | 待处理、已完成、失败可重试、来源版本和替代关系 |

迁移必须版本化、只新增对象，不 ALTER 或删除现有业务表。旧库、空隔离库、重复执行、校验和冲突、失败恢复及迁移前后完整性均沿用第一阶段门禁。

### 3.3 时间、质量和幂等

- `received_at`、`reported_at`、`observed_at`、`normalized_at` 分别保存，不互相冒充。
- 迟到/补传按 `observed_at` 落入历史位置；旧观测不能显示为刚更新。
- 因子质量至少区分 `valid/suspect/invalid/fault/unmapped`。
- 缺失不是0；合法0必须保存；业务阈值越限不等于协议无效。
- 一批部分因子失败时保留其他有效因子，批次区分 `accepted/partial/rejected`。
- 每次重复接收仍保留原始记录；规范化批次和值必须幂等。
- 重解析产生新版本和替代关系，不 UPDATE 覆盖历史规范化结果。
- 设备主动上报与服务端聚合必须以 `aggregation_source` 分开；本批无需实现长期归档任务，但schema需保留粒度和来源语义。

### 3.4 状态边界

通信端点、数据到报、RTU工况、仪器工况、因子质量和资产状态分别保存。站点摘要只做只读投影，至少返回关注级别、主要原因、原因码、最后通信、最后有效观测及各分轴状态。

新鲜度按端点/因子的预期周期和容忍时间判断。周期未配置时返回 `unknown/unconfigured`，不得使用固定30分钟制造正常、缺报或离线状态。

## 4. 本批只读接口

在独立监测命名空间中提供最小接口；路径命名可按现有路由规范微调，但不得覆盖旧接口：

| 能力 | 最低响应 |
| --- | --- |
| 单站监测摘要 | 分轴状态、关注级别/原因、最后通信、最后有效数据、更新时间 |
| 单站最新值 | 因子、值、单位、质量、观测/接收时间、粒度、仪器 |
| 单站趋势 | 因子、时间点、值、质量、粒度、来源、覆盖率、缺口、版本 |
| 单站仪器状态 | 资产关联、运行状态、因子、最后有效数据和原因 |
| 数据质量事项 | 类型、对象摘要、首次/最近、次数、处理状态、可否重解析 |

站点接口复用现有服务端站点范围校验；普通操作员只能读取本人有权站点。数据质量与映射诊断本批仅管理员可读。不存在、越权、周期未配置、未接入、无观测、查询失败必须有不同响应，不统一伪装成空数组。

趋势首期规则：24小时可读有效实时/分钟值；7天及以上优先读聚合或明确降采样；缺报不补0；不同单位不混轴。迟到数据影响已聚合窗口时可重算并产生新版本，查询只返回当前有效版本。

## 5. 允许修改范围

允许在以下范围内最小修改或新增：

- `平台开发/backend/migrations/`：新增第二阶段迁移；
- `平台开发/backend/migrate_station_ingestion.py`，或新增单一版本化迁移入口；
- `平台开发/backend/sl651_mapping.json`；
- `平台开发/backend/sl651_parser.py`；
- `平台开发/backend/sl651_server.py`；
- `平台开发/backend/app.py`：仅新增监测只读接口及其最小接线；
- `平台开发/backend/` 下新增规范化、投影和查询模块；
- 上述范围的直接单元/集成测试；
- `平台开发/docker-compose.yml`、`平台开发/.env.example`：仅当隔离规范化工作进程确有需要；
- 本交接文件的完成回传节。

若必须修改 `Dockerfile`、备份脚本、现有业务表、现有业务接口或上述范围外文件，先停止并说明原因，不自行扩围。

开发树当前存在与本批无关的 `AGENTS.md`、小程序配置文件及大量未跟踪资料；不得清理、覆盖、暂存或纳入回传。

## 6. 禁止事项

- 不修改Web、小程序、驾驶舱、设备页、告警、工单或巡检业务。
- 不启用旧 `sensor_data` 兼容写入，不运行模拟数据生成器。
- 不把解析错误直接转业务告警或工单。
- 不引入消息队列、时序数据库或新基础设施依赖。
- 不处理远程改参、校时、控制或完整原始字节的普通页面展示。
- 不连接固定开发库、生产库、生产容器、真实RTU或公网监听。
- 不写入真实站号、MN、密码、IP、端口、服务器密钥或生产连接串。
- 不提交、不打标签、不推送、不部署；完成后先停产品 Review。

## 7. 验收要求

### 7.1 迁移与数据

- 旧库升级、空隔离库初始化、重复执行和校验和冲突；
- 迁移失败恢复后旧对象及数据不变；
- 映射有效期覆盖仪器更换前后归属；
- 重复帧、重启、重解析不重复生成当前有效值；
- 原始帧在规范化失败后仍完整可查。

### 7.2 解析与质量

- PDF黄金 `32H` 帧覆盖全部当前字义的水质因子、时间和状态要素；
- 缺失、合法0、非法BCD、未知因子、未知映射、故障位和部分解析分别断言；
- 三个业务别名映射正确，未发布因子不进入普通查询；
- 迟到、乱序、补传、流水号回绕和不同粒度不串线。

### 7.3 查询与权限

- 摘要、最新值、趋势、仪器和数据质量接口分别覆盖有数据、未接入、无数据、周期未配置、越权和不存在；
- 最新值只取当前有效版本，不用接收时间替代观测时间；
- 趋势缺口不补0，设备上报与服务端聚合来源可区分；
- 普通用户不能读其他站点和管理员数据质量事项。

只运行相关后端 unittest、Python 编译、迁移/接口隔离集成和定向 `git diff --check`。如修改 Compose，再运行相应隔离容器健康和重启恢复检查；不机械重跑全量门禁。

## 8. 停止与回传

完成后停在产品只读 Review，回传：

1. 实际数据对象和处理链；
2. 新只读接口及权限；
3. 关键异常语义如何落地；
4. 修改文件；
5. 测试结果；
6. 仍为 `NOT RUN` 的真实厂商帧、真实RTU、公网、生产权限/备份恢复、真实UI和部署边界；
7. 是否存在需要产品拍板的新事项，没有则写“无”。

不得把隔离测试写成真实站点验收，也不得继续进入页面、告警、提交或部署。

## 9. 完成回传 / 产品只读 Review

### 1. 实际数据对象和处理链

- 新增版本化迁移 `20260909_002_station_monitoring_normalization`：端点有效期档案、协议因子定义、因子有效期映射、观测批次、观测值、状态事件和数据质量事项均为新增对象；旧 `sensor_data` 未写入、未修改。
- 处理链为 `ingest_raw_frames -> 独立后台规范化队列 -> observation_batches / observation_values / monitoring_status_events / monitoring_quality_issues`。原始帧先持久化再应答；规范化失败保留原始帧并标为 `pending_reparse`，重启时重新排队。
- 批次和值以 `normalization_version`、`is_current` 和 `replaces_batch_id` 保留重解析历史；同一端点可保存多段有效期档案，因子映射按上报时间选择仪器，覆盖仪器更换前后归属。
- `water_temperature -> water_temp`、`ammonia_nitrogen -> ammonia`、`permanganate_index -> codmn` 已发布；ORP 已识别但不发布到普通查询。

### 2. 新只读接口及权限

- `GET /api/station-monitoring/sites/<id>/summary`
- `GET /api/station-monitoring/sites/<id>/latest`
- `GET /api/station-monitoring/sites/<id>/trend?metric=<metric>`
- `GET /api/station-monitoring/sites/<id>/instruments`
- `GET /api/station-monitoring/quality-issues`

站点接口复用服务端站点范围校验；无权为 `403`，不存在为 `404`，未接入或档案已失效为 `409`，无观测和周期未配置分别显式返回。数据质量事项仅管理员可读；未发布因子查询返回 `404`。

### 3. 关键异常语义

- 合法零值保留；缺失不补零；未知因子产生 `unmapped` 值与数据质量事项，其余已解析因子仍以 `partial` 批次留存。
- 解析或映射失败不生成告警或工单，原始事实保持可重试。
- 规范化改为接收确认后的独立后台队列，避免 SQLite 锁延长确认路径。关闭时先耗尽持久化和规范化队列，再取消空闲任务，防止 SQLite 线程仍持有隔离数据库文件。

### 4. 修改文件

- `backend/migrations/20260909_002_station_monitoring_normalization.sql`
- `backend/migrate_station_ingestion.py`
- `backend/sl651_parser.py`
- `backend/sl651_server.py`
- `backend/station_monitoring.py`
- `backend/app.py`
- `backend/test_station_monitoring_normalization.py`
- 本交接文件。

### 5. 测试结果

- `python -m unittest backend.test_station_monitoring_normalization backend.test_station_ingestion_migration backend.test_sl651_parser backend.test_sl651_ingestion_storage backend.test_sl651_server_isolation`：`33 tests` PASS。
- 隔离 L1：`2,980` 帧 TCP 接收与 `160` 次真实 Flask 认证站点写入并行，全部 `201`；确认 `p95=0.718s`、`p99=2.000s`，无 Web `500`。
- `python -m py_compile`（本批新增和修改 Python 文件）：PASS。
- `git diff --check`：PASS。

### 6. NOT RUN 边界

真实厂商帧、真实 RTU、公网监听、生产数据库权限及备份恢复、真实 UI、部署：均为 `NOT RUN`。本批未提交、未打标签、未推送、未部署，未连接固定开发库、生产库、生产容器或真实 RTU。

### 7. 产品拍板事项

无。当前停在产品只读 Review；下一动作仅等待产品对本批数据契约与只读接口的明确 Review 结论。

## 10. 产品只读 Review（2026-09-09）

### Review结论

**不通过，沿用本交接原范围返修。** 数据对象和接口方向成立，旧 `sensor_data` 未写入，权限、重复接收留证及规范化版本历史已有基础；但真实协议正文、时间语义、状态投影和积压恢复尚未达到本批完成标准。不得进入页面、告警、提交或部署。

现有五个定向模块复跑 `33/33 PASS`。该结果只能证明当前测试夹具自洽：新增黄金帧把因子直接放在发报时间之后，省略了PDF明确的 `F1 F1` 测站标识段和 `F0 F0` 观测时间段。只读探针使用“标识段 + 观测时间 + 水温因子”的正文调用当前解析器，结果仅得到 `F1F1/unmapped`，未得到水温值，直接证明真实结构尚未覆盖。

### 必须返修

1. **恢复真实 `32H` 正文和时间语义。** 按PDF解析 `F1 F1` 测站标识、测站类型、`F0 F0` 五字节观测时间，再解析因子；不得靠搜索跳过前缀。正文站号与帧头/端点归属不一致时进入明确隔离，不能继续投影。`reported_at` 来自六字节发报时间，`observed_at` 来自五字节观测时间，`received_at` 保持平台接收事实。应用端点时区形成一致、可比较的时间表示，并按 `observed_at` 选择仪器/因子有效期映射。黄金帧必须使用完整PDF正文，直接断言三个时间不同且迟到帧落入历史位置。

2. **完成分轴状态的真实投影。** 当前摘要只按最后接收判断通信，数据轴只要历史上曾有值就标记 `available`，RTU和仪器轴固定为未知；返回的 `last_communication_at` 还是设备发报时间。返修后：通信使用平台接收时间；数据到报按最后有效 `observed_at` 与预期周期判断；RTU只消费已确认状态事件，厂商位义未确认时保持未知；仪器只返回当前有效映射，并以“仪器 + 因子”关联各自最后有效值，不能把新仪器值挂到已失效仪器。补充因子级预期周期和容忍时间；未配置时不得判正常、缺报或离线。

3. **保证幂等与积压无需重启即可最终处理。** 当前 `idempotency_key` 没有数据库唯一约束；启动时和运行中都使用 `put_nowait`，队列满后只保留数据库 `pending_*`，进程不再扫描；已有 `failed_retryable` 批次的同版本重试又会直接返回 `already_normalized`，使原始记录长期停在待重解析。为规范化版本建立数据库级幂等约束，并采用有界、分页的数据库待办拉取或等价机制：队列腾出后自动发现持久化待办，失败采用有上限和退避的重试，等待新解析器/映射的事项进入稳定可诊断状态，不得热循环，也不得一次把全量ID读入内存。测试使用小队列和大于队列容量的待办，证明不重启也能全部到达成功或明确等待状态，并覆盖异常、重复调度和恢复路径。

4. **收紧只读查询并补齐当前语义。** 当前趋势可无时间范围读取全量高频数据，质量事项也无分页；趋势覆盖率只有点数和缺口数，仪器查询包含历史失效映射。首期可将趋势明确限制为最近24小时原始/分钟值，不要求本轮实现长期聚合，但必须提供默认窗口、最大窗口、点数上限、参数校验、覆盖率/有效数/预期数和明确的“不支持更长周期”响应。趋势只返回允许展示的 `valid/suspect` 当前值，不能让未来的 `invalid/fault` 混入普通曲线。质量事项必须分页和限制最大页长。所有站点查询只返回当前有效版本和当前有效映射。

5. **如实收口质量语义和回传。** 当前生产者实际只形成 `valid` 和 `unmapped`，没有 `suspect/invalid/fault` 的判定来源；未知协议码因无定义不会形成 `observation_values`，只形成质量事项。真实厂商质量位尚未确认时不得编造状态：保留schema能力，未知码进入隔离事项；已知因子发生非法BCD时标为因子级无效并形成 `partial`，在边界仍可确定时保留此前及后续有效因子，不得把整批已解析值全部丢弃。回传明确哪些质量已落地、哪些仍依赖真实帧。数据质量事项的 `occurrence_count` 必须表示业务发生次数，不能因同一原始帧重试而虚增。

### 返修验证

- 保留现有33项相邻回归，并补完整PDF正文、三时间/时区、迟到映射、数据新鲜度、RTU未知边界、仪器更换接口、小队列持续排空、趋势窗口/覆盖率、质量分页和同一帧重试不虚增计数的直接测试。
- 只运行本批及直接相邻后端测试、Python编译和定向 `diff-check`；不机械扩跑全量门禁。
- 真实厂商帧、真实RTU、公网、生产权限/备份恢复、真实UI和部署继续为 `NOT RUN`。

完成后再次停产品只读 Review，回传只说明五项缺口如何闭环、测试结果及仍为 `NOT RUN` 的边界。

## 11. 第 10 节返修完成回传 / 产品只读 Review

### 五项缺口闭环

1. **真实 `32H` 正文和时间**：解析器严格按 `F1 F1 + 五字节站号 + 测站类型 + F0 F0 + 五字节观测时间 + 因子` 顺序解析，不再搜索跳过前缀。正文站号与帧头不一致会隔离为 `payload_station_mismatch`，不生成观测批次。`reported_at`、`observed_at` 按端点时区转换为 UTC 可比较时间，`received_at` 保持平台事实；仪器和因子映射按 `observed_at` 选取。
2. **分轴状态**：通信轴使用当前规范化批次的 `received_at`；数据轴逐因子按最后有效 `observed_at`、因子周期和容忍时间判定。未配置周期返回 `unknown/unconfigured`。RTU 位仍作为 `unconfirmed_protocol` 事实留存，摘要不将其投影为已确认状态。仪器接口仅读取当前有效映射，并按“仪器 + 因子”关联最后有效值。
3. **幂等和积压**：观测批次增加端点、幂等键和规范化版本的数据库唯一约束。接收进程采用有界分页扫描持久化待办，队列腾出后无需重启即可继续拉取；瞬态失败采用最多三次指数退避，耗尽后成为稳定可诊断质量事项，映射/解析等待同样不热循环。
4. **受限只读查询**：趋势默认最近 24 小时，限制最大 24 小时和 1,000 点，校验时区、参数和窗口；返回有效点、预期点、缺口与覆盖信息，超长周期明确返回不支持。趋势仅返回当前映射且 `valid/suspect` 的当前值。质量事项增加分页和最大页长；摘要、最新值、趋势和仪器均过滤已替代批次或失效映射。
5. **质量语义**：已落地 `valid`、`invalid` 和 `unmapped`。已知因子的非法 BCD 形成因子级 `invalid` 与 `partial` 批次，前后有效因子继续入库；未知协议码产生隔离质量事项。`suspect/fault` 的真实厂商质量位尚无确认来源，保留 schema 能力但不编造判定。重试只刷新同一原始业务事项的最近时间，不增加 `occurrence_count`。

### 验证

- `python -m unittest backend.test_station_monitoring_normalization backend.test_station_ingestion_migration backend.test_sl651_parser backend.test_sl651_ingestion_storage`：`30 tests` PASS。
- `python -m unittest backend.test_sl651_server_isolation`：`12 tests` PASS；含小队列 7 条持久化待办不重启持续排空，以及 L1 2,980 帧 TCP 与 160 次真实 Flask 认证站点写入并行，全部 `201`，`p95=0.841s`、`p99=2.485s`。
- `python -m py_compile`（本批修改及直接测试）：PASS；`git diff --check`：PASS。

### 修改范围

`backend/migrations/20260909_002_station_monitoring_normalization.sql`、`backend/migrate_station_ingestion.py`、`backend/sl651_parser.py`、`backend/station_monitoring.py`、`backend/sl651_server.py`、`backend/app.py` 及其直接测试；未修改页面、小程序、告警、工单、旧 `sensor_data` 或产品台账。

### NOT RUN 与停止点

真实厂商帧、真实 RTU、公网监听、生产数据库权限/备份恢复、真实 UI 和部署仍为 `NOT RUN`。本批未提交、未打标签、未推送、未部署，未连接固定开发库、生产库、生产容器或真实 RTU。当前再次停在产品只读 Review。

## 12. 第二次产品只读 Review（2026-09-09）

### Review 结论

**不通过，继续沿用本交接返修。** 真实正文解析、数据库唯一约束、分页积压扫描、受限查询框架和质量三态已经落地；独立复跑 `30/30 + 12/12 PASS`，第一阶段已应用库升级第二阶段也通过。但时间档案、分轴摘要和当前映射查询仍会产生错误业务结论，不得提交、部署或进入页面接线。

### 必须返修

1. **消除时区与有效期的循环误判。** 当前先把设备本地 `reported_at` 当 UTC 选择端点档案，再读取该档案时区。独立探针证明：设备时间实际落在档案有效期内，仍会被判为 `unmapped_endpoint`。应以可验证且无歧义的端点时区解析发报/观测时间，再按 UTC 有效期选择档案；配置时间统一为带时区的可比较口径，并覆盖有效期起止边界及歧义档案。通信状态事件的 `occurred_at` 必须使用平台 `received_at`，不能保存为 `observed_at`。
2. **摘要必须覆盖全部当前配置，而不是只看已有值。** 当前只从 `latest_values` 生成因子状态；仅一个因子新鲜、其余当前映射从未到报时，接口仍返回数据轴 `fresh` 和站点 `normal`。应以当前有效发布映射为全集，逐因子关联最后有效值，明确 `fresh/stale/no_observation/unconfigured`；没有确认的仪器工况时保持 `unknown/unconfirmed`，不得用“存在监测值”推断仪器运行正常。
3. **修正仪器归属与映射回退查询。** 规范化允许 `mapping.instrument_asset_code` 为空并回退端点档案仪器，但查询只比较映射字段，导致值已入库而 `/latest` 返回 `no_observations`。最新值、趋势和仪器接口须使用与写入一致的有效仪器归属，并限制观测时间确实落在对应映射有效期内；覆盖档案级兜底、仪器更换后换回原资产及映射 `business_metric` 使用定义回退的场景。
4. **覆盖率和缺口必须如实。** 当前 10 分钟窗口、60 秒周期、应有 11 点而只有 1 点时，仍返回 `gap_count=0`，且无覆盖率字段。补窗口首尾缺口、覆盖率（或等价明确字段）、有效/可展示点与预期点的一致语义；保留 24 小时、1,000 点、质量过滤和分页边界。

### 返修验证与停止点

- 保留已通过的解析、迁移、幂等、积压和质量回归，只补上述四类直接测试；定向后端测试、Python 编译和 `git diff --check` 即可，不重跑无关全量门禁。
- 真实厂商帧、真实 RTU、公网、生产、真实 UI 和部署继续为 `NOT RUN`。完成后仍停产品只读 Review，回传只写四项闭环、测试和 `NOT RUN` 边界。

## 13. 第 12 节返修完成回传 / 产品只读 Review

### 四项闭环

1. **时区和档案有效期**：端点时区先由全部启用档案的唯一配置解析；缺失或多时区均进入明确等待事项，不再把设备本地时间当 UTC 反推档案。设备发报/观测时间先按该时区转换 UTC，再以 UTC 有效期选择档案和映射。档案及映射有效期强制存为 `+00:00` UTC 口径。通信状态事件的 `occurred_at` 与 `received_at` 均为平台接收事实。
2. **完整因子摘要**：摘要从当前有效且已发布的因子映射构建全集，不再从已有值反推配置。每个因子分别返回 `fresh/stale/no_observation/unconfigured`；任一已配置因子未到报时，数据轴不再错误显示 `fresh`，站点不再显示 `normal`。仪器轴在没有已确认工况来源时固定为 `unknown/unconfirmed`。
3. **一致仪器归属查询**：规范化与最新值、趋势、仪器查询统一使用 `COALESCE(映射仪器, 档案仪器)`；查询同时验证观测时间落在当时有效映射/档案内，且当前映射对应同一仪器。覆盖了映射仪器为空、业务指标定义回退，以及 `A -> B -> A` 仪器更换归属。
4. **趋势缺口与覆盖率**：趋势现在计算窗口起点、内部和终点缺口，返回 `expected_points`、`valid_points`、`displayed_points`、`missing_points`、`coverage_rate` 和具缺失点数的 `gaps`。10 分钟、60 秒周期、仅一点的直接契约结果为应有 11、有效 1、缺失 10、缺口 1、覆盖率 `1/11`。

### 验证

- `python -m unittest backend.test_station_monitoring_normalization backend.test_station_ingestion_migration backend.test_sl651_parser backend.test_sl651_ingestion_storage`：`34 tests` PASS。
- `python -m unittest backend.test_sl651_server_isolation`：`12 tests` PASS；L1 隔离验证 2,980 帧 TCP 与 160 次真实 Flask 认证站点写入均成功，`p95=0.893s`、`p99=2.774s`，无 Web `500`。
- `python -m py_compile`（本批修改与直接测试）及 `git diff --check`：PASS。

### NOT RUN 与停止点

真实厂商帧、真实 RTU、公网、生产数据库权限/备份恢复、真实 UI 和部署继续为 `NOT RUN`。未提交、未打标签、未推送、未部署，未连接固定开发库、生产库、生产容器或真实 RTU。当前再次停在产品只读 Review。

## 14. 第三次产品只读 Review（2026-09-09）

### Review 结论

**不通过，仅返修以下两项。** 第12节的时区转换、通信时间、全配置摘要和仪器工况未知边界已经闭环；独立复跑 `34/34 + 12/12 PASS`。但当前映射查询和覆盖统计仍未满足第12节验收，不得提交、部署或进入页面接线。

### 必须返修

1. **当前映射必须对应当前有效段，且业务指标不可串线。** 当前查询只证明观测时间曾落在某段同协议码、同仪器映射中，没有证明它落在当前配置这一有效段，也没有核对当时的业务指标。直接探针复现：`A -> B -> A` 后新 A 尚无数据，`/latest` 仍返回第一次 A 的旧值；协议因子从旧业务指标改映射为新指标后，请求新指标趋势会返回 `business_metric` 仍为旧指标的点；两条重叠当前映射会同时进入配置全集。返修后每个时点只能得到唯一权威配置，重叠配置须明确隔离或诊断；最新值和趋势必须同时匹配当前有效段、当时的协议码、业务指标及最终仪器归属。补上述三项直接测试。
2. **覆盖字段必须内部一致并区分质量。** 非周期网格对齐的10分钟窗口已复现 `expected_points=11`、`displayed_points=1`、`missing_points=9`，同时产生 `missing_points=0` 的假缺口；`suspect` 点也被计入 `valid_points`。返修后 `valid_points` 只计 `valid`，`displayed_points` 计允许展示的 `valid/suspect`；`missing_points`、`gap_count`、`coverage_rate` 与预期时间槽一致，零缺失段不生成缺口，重复上报不得使覆盖率超过1。补非整周期窗口、`suspect` 和同时间槽重复点测试。

### 验证与停止点

- 只补并运行上述查询直接测试及现有监测定向回归；接收高并发无需再次机械复跑，除非修改触及接收或队列代码。运行 Python 编译和定向 `git diff --check`。
- 其余已通过范围保持，所有真实环境边界继续为 `NOT RUN`。完成后仍停产品只读 Review，回传仅写两项闭环和验证结果。

## 15. 第14节返修回传（2026-09-09）

### 两项闭环

1. **当前映射的有效段与归属。** `monitoring_factor_mappings` 现以插入和更新触发器拒绝同一端点、协议码的启用有效期重叠；规范化时遇到既存歧义会留存 `ambiguous_factor_mapping` 质量事项。最新值和趋势同时约束当前映射段、当前档案段、协议码、业务指标和最终仪器，并再次校验观测时的业务指标与仪器归属。隔离直接契约覆盖 `A -> B -> A` 后新 A 未到报不返回旧 A 值、业务指标变更后趋势不串旧指标、重叠映射写入被拒绝。
2. **时间槽覆盖字段。** 趋势按查询窗口起点和配置周期生成唯一时间槽：`valid_points` 仅统计含 `valid` 的槽，`displayed_points` 统计含 `valid` 或 `suspect` 的槽，重复报文不重复计槽；缺失槽连续段才生成缺口，覆盖率恒以已展示槽/预期槽计算。隔离直接契约覆盖非整周期窗口、`suspect` 与同槽重复报文。

### 验证

- `python -m py_compile backend\\app.py backend\\station_monitoring.py backend\\migrate_station_ingestion.py backend\\test_station_monitoring_normalization.py`：PASS。
- `python -m unittest backend.test_station_monitoring_normalization backend.test_station_ingestion_migration backend.test_sl651_parser backend.test_sl651_ingestion_storage`：`39 tests` PASS。
- `git diff --check`：PASS（仅工作树既存 CRLF 提示）。

### NOT RUN 与停止点

未重跑未改动的接收/队列高并发隔离测试；真实厂商帧、真实 RTU、公网、固定或生产数据库、真实 UI、提交、标签、部署均为 `NOT RUN`。未提交、未打标签、未推送、未部署，当前停在产品只读 Review。

## 16. 第四次产品只读 Review（2026-09-09）

### Review 结论

**第14节两项返修通过；整体候选暂不通过，仅补一个启动安全门禁。** 独立复跑 `39/39 PASS`，并直接确认 `A -> B -> A`、业务指标改映射、重叠映射、`suspect` 与重复时间槽均符合契约。数据语义范围不再重开。

### 最终集成收口

当前 `IngestionStorage.healthcheck()` 仍只验证第一阶段表和 WAL。独立探针证明，仅应用第一阶段迁移的数据库会通过健康检查；删除 `reject_overlapping_monitoring_factor_mapping_insert` 后，`verify_station_monitoring_schema()` 也会通过。若部署漏跑或损坏第二阶段迁移，接收服务可能启动并确认收报，但规范化扫描随后失效。

返修要求：接收服务在打开监听端口和启动工作任务前，必须验证第二阶段关键表、数据库幂等约束及两条重叠映射触发器；缺任一对象应启动失败并给出非敏感明确原因。迁移 `--check` 使用同一完整校验。补“仅第一阶段库拒绝启动/健康检查、删除任一关键约束拒绝校验、完整库通过”的直接测试。若修改接收服务健康检查，复跑接收隔离模块；其余已通过数据逻辑不改、不重测全量。

完成后再次停产品只读 Review。真实厂商帧、真实 RTU、公网、生产、真实 UI、提交和部署继续为 `NOT RUN`。

## 17. 第16节返修回传（2026-09-09）

### 启动安全门禁闭环

`IngestionStorage.healthcheck()` 现复用第二阶段启动契约校验，并在监听器、持久化 worker 和规范化任务创建前执行。校验要求第一、二阶段关键表，第二阶段迁移版本与校验和，批次和重试的数据库幂等唯一约束，当前批次部分唯一索引，以及两条拒绝重叠映射的触发器及其拒绝语义。任一对象缺失或不兼容时，启动以非敏感 `station monitoring schema unavailable` 原因失败。迁移 `--check` 复用同一契约，并额外执行完整性和外键检查。

### 直接契约与验证

- 仅第一阶段迁移库：健康检查和 `start()` 均拒绝，且 `start()` 前未创建监听器、持久化 worker、规范化 worker 或扫描任务。
- 完整库：迁移 `--check` 和轻量健康检查通过；删除任一重叠映射触发器或 `uq_observation_current_raw` 后完整校验拒绝。
- `python -m py_compile backend\\migrate_station_ingestion.py backend\\sl651_server.py backend\\test_station_ingestion_migration.py backend\\test_sl651_server_isolation.py`：PASS。
- `python -m unittest backend.test_station_ingestion_migration`：`10 tests` PASS。
- `python -m unittest backend.test_sl651_server_isolation`：`13 tests` PASS；隔离 L1 为 2,980 帧和 160 次真实 Flask 测试写入，`p95=0.794s`、`p99=2.650s`，无 Web `500`。
- `git diff --check`：PASS（仅工作树既存 CRLF 提示）。

### NOT RUN 与停止点

第14节已通过的数据语义范围未重开、未重测全量。真实厂商帧、真实 RTU、公网、固定或生产数据库、真实 UI、提交、标签和部署均为 `NOT RUN`。未提交、未打标签、未推送、未部署，当前停在产品只读 Review。

## 18. 第五次产品只读 Review（2026-09-09）

### Review 结论

**第16节启动安全门禁代码 Review PASS；第二阶段定向集成收口通过。** 接收服务在任务和监听器创建前执行完整轻量契约校验；迁移 `--check` 复用相同第二阶段契约并保留完整性、外键检查。未发现需继续返修的问题，已通过的数据语义范围未重开。

### 独立证据

- `backend.test_station_ingestion_migration`：`10/10 PASS`。
- `backend.test_sl651_server_isolation`：`13/13 PASS`；2,980 帧、160 次真实 Flask 隔离写入，无 Web `500`。
- 临时数据库直接探针：仅第一阶段库的健康检查与 `start()` 均在创建任务和监听器前拒绝；完整第二阶段库通过；删除任一重叠映射触发器或 `uq_observation_current_raw` 后校验均拒绝。
- 相关 Python 编译和定向 `git diff --check`：PASS；仅有既存 CRLF 提示。

### 边界与下一动作

真实厂商帧、真实 RTU、公网、固定或生产数据库、真实 UI、提交、标签和部署仍为 `NOT RUN`。当前不是冻结或发布候选；停止业务写入，等待用户明确批准提交第二阶段检查点。
