# 站点接入迁移历史外键基线返修交接

> 日期：2026-09-11
> 唯一执行者：具备代码仓库权限的协作同事
> 基线：`release-20260910-cross-module-freeze-r14` / `b63791b13986c39606afbc45090e2e0257e400e1`
> 停点：完成代码与定向测试后提交并推送独立修复分支，停在产品只读 Review；不得直接合并、打标签、部署或生产写入

## 目标

用最小代码返修消除“站点迁移要求整个历史业务库零外键异常”的错误耦合，同时继续保证迁移不新增外键异常、站点接入自有表无外键异常、失败自动恢复。返修通过后才能形成新候选并重跑生产入口启用，目标是尽快达到 `READY_FOR_VENDOR`。

## 已确认事实

- r14 已在线且 `POSTDEPLOY_OK`；接收容器未启动，`31000/31001` 未监听，Nginx stream 与 UFW 未改。
- 生产库 `PRAGMA integrity_check=ok`，但 `PRAGMA foreign_key_check` 有且仅有一条既存异常：`parts_requests` 行标识 `17` 引用缺失的 `insp_plans` 父记录，属于与站点接入无关的历史孤儿记录。
- 本轮证据不足以决定该记录应删除、作废、补父记录或重新关联，因此禁止修改该业务数据。
- 失败迁移已自动恢复，13 张站点接入/归一化新表均不存在；r14 Web 与备份链正常。
- 协作入口为远端 `codex/station-ingest-fk-baseline`；执行者必须从该分支创建独立修复分支，不得从旧默认分支自行重建。

## 实现边界

1. 迁移在同一个受控写事务内记录迁移前全库外键异常集合；迁移后不得新增或改变该集合。迁移前为空时，迁移后仍必须为空。
2. `verify_station_ingestion_schema` 和接收器低频完整检查只对站点接入/归一化自有表执行外键门禁，同时继续执行全库 `integrity_check`。不得让无关历史孤儿阻止接收服务启动。
3. 站点自有表出现任何外键异常时必须失败；迁移新增任意全库外键异常时必须失败并按现有机制恢复备份。
4. 不得写死 `parts_requests`、行标识 `17` 或某个异常数量作为特例；不得关闭 `foreign_keys`、放宽校验为无条件通过，或改动迁移 SQL 校验和。

## 允许文件

- `平台开发/backend/migrate_station_ingestion.py`
- `平台开发/backend/sl651_server.py`
- `平台开发/backend/test_station_ingestion_migration.py`
- `平台开发/backend/test_sl651_server_isolation.py`

如确需超出该清单，先停止并说明原因。不得改 `app.py`、业务表、迁移 SQL、前端、Compose、发布清单或产品资料。

## 验收

- 干净业务库迁移、重复执行、校验和冲突和失败恢复保持通过。
- 带无关历史孤儿的隔离库可迁移，孤儿记录及迁移前后全库外键异常集合完全不变。
- 注入“迁移新增外键异常”时迁移失败，目标数据库恢复到迁移前状态。
- 已迁移库的站点自有表出现外键异常时，schema verify 与接收器完整检查均拒绝。
- 已迁移库只有无关历史孤儿时，站点 schema verify 与接收器完整检查通过，但测试仍直接证明该历史异常没有被修改或隐藏为不存在。

至少执行：

```powershell
python -m unittest backend.test_station_ingestion_migration backend.test_sl651_server_isolation backend.test_station_ingest_provision
python -m py_compile backend\migrate_station_ingestion.py backend\sl651_server.py backend\test_station_ingestion_migration.py backend\test_sl651_server_isolation.py
git diff --check -- backend/migrate_station_ingestion.py backend/sl651_server.py backend/test_station_ingestion_migration.py backend/test_sl651_server_isolation.py
```

测试只用隔离数据库和临时目录，不读取或修改生产库。

## 回传

只返回：修复分支名与提交 SHA、产品动作、原因、修改文件、定向测试结果、现有行为是否回归、残余风险，以及 `READY_FOR_PRODUCT_REVIEW` 或明确阻断。不得直接合并；不得回传 MN、协议凭据、pepper、服务器密钥、生产连接串或业务字段内容。

## 首帧路径

本轮 Review 通过后再单独授权：提交并冻结新候选、部署该候选、按现有入口启用交接重跑迁移与 `plan/apply/verify`、启动接收容器和公网入口，达到 `READY_FOR_VENDOR` 后才通知对方配置丰城首站。小时连续性、断线/人工补传、跨平台数值核对、其他站点批量开通和长期监控均可在首条真实数据打通后补齐。
