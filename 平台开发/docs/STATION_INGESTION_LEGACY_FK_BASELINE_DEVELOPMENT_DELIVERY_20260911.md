# 站点接入迁移历史外键基线返修交付回传

> 日期：2026-09-11
> 状态：`READY_FOR_PRODUCT_REVIEW`
> 修复分支：`fix/station-ingest-fk-baseline`
> 修复代码提交：`6d33ead17d0ee754fa7237a64777974dee487c35`
> 基线提交：`c67ed5f9dfd4c6d215a044df8fa170666c18a472`
> 说明：本文件为修复代码提交之后追加的交付文档，其文档提交 SHA 以本文件所在提交为准。

## 产品动作

对修复代码提交 `6d33ead17d0ee754fa7237a64777974dee487c35` 做只读 Review。Review 通过前不得合并、打标签、部署、启用公网入口或操作生产数据。

## 原因

原实现把站点接入迁移、schema verify 和接收器低频完整检查都绑定到全库 `PRAGMA foreign_key_check` 必须为空。业务库存在与站点接入无关的历史外键孤儿时，即使迁移没有新增或改变异常，接收服务仍会被阻止启动。

本次返修将两类责任分开：

- 迁移在同一个 `BEGIN IMMEDIATE` 写事务中记录迁移前全库外键异常集合，并要求迁移后集合完全相同；任何新增、删除或改变都会导致迁移失败并触发现有备份恢复。
- schema verify 和接收器低频完整检查继续执行全库 `integrity_check`，但外键门禁只检查站点接入与归一化自有表。

没有关闭 `foreign_keys`，没有把外键检查改成无条件通过，没有针对特定业务表、行标识或异常数量写特例，也没有修改迁移 SQL 或迁移校验和。

## 修改文件

- `平台开发/backend/migrate_station_ingestion.py`
  - 定义 13 张站点接入/归一化自有表的检查范围。
  - 提供站点范围外键异常查询，并用于 schema verify。
  - 在迁移写事务内比较迁移前后全库外键异常集合。
  - 自动恢复后同时复核数据库完整性和原有外键异常集合。
- `平台开发/backend/sl651_server.py`
  - 接收器低频完整检查保留全库 `integrity_check`。
  - 外键门禁改为只检查站点自有表。
- `平台开发/backend/test_station_ingestion_migration.py`
  - 覆盖无关历史孤儿可迁移、记录与异常集合保持不变、重复执行通过。
  - 覆盖站点自有表外键异常被 schema verify 拒绝。
  - 覆盖迁移新增全库外键异常时失败并恢复迁移前数据库。
- `平台开发/backend/test_sl651_server_isolation.py`
  - 覆盖接收器完整检查允许无关历史孤儿且不修改或隐藏该异常。
  - 覆盖接收器完整检查拒绝站点自有表外键异常。

## 定向测试结果

执行：

```powershell
python -m unittest backend.test_station_ingestion_migration backend.test_sl651_server_isolation backend.test_station_ingest_provision
```

结果：`PASS`，共 38 项测试，耗时 127.294 秒。

并发接收回归证据：

- 持久化样本：2,980
- 队列峰值：84
- p95：1.304 秒
- p99：3.048 秒
- 实际 Web 写入：160 次，全部返回 HTTP 201

执行：

```powershell
python -m py_compile backend\migrate_station_ingestion.py backend\sl651_server.py backend\test_station_ingestion_migration.py backend\test_sl651_server_isolation.py
```

结果：`PASS`。

执行：

```powershell
git diff --check -- backend/migrate_station_ingestion.py backend/sl651_server.py backend/test_station_ingestion_migration.py backend/test_sl651_server_isolation.py
```

结果：`PASS`。

测试仅使用临时目录和隔离 SQLite 数据库。未读取或修改生产库。测试环境临时补充的 Python 包和编译缓存已清理，未进入提交。

## 现有行为回归

- 干净业务库迁移：通过。
- 重复执行：通过，不重复备份或重复迁移。
- 迁移校验和冲突拒绝：通过。
- 中途失败自动恢复：通过。
- 无关历史外键孤儿迁移：通过，迁移前后异常集合和孤儿记录完全不变。
- 迁移新增外键异常：被拒绝，目标数据库恢复到迁移前状态。
- 站点自有表外键异常：schema verify 与接收器完整检查均拒绝。
- 仅存在无关历史外键异常：schema verify 与接收器完整检查均通过。
- provisioning 与接收器隔离回归：通过。

未发现本次允许范围内的现有行为回归。

## 残余风险

- 未在生产数据库上执行迁移或验证；历史孤儿场景由隔离 SQLite 数据库模拟。
- 未执行部署、容器启动、公网入口启用、真实 RTU 接入或生产 `plan/apply/verify`。
- 未运行超出本交接范围的全量仓库测试；当前证据为交接指定测试和直接相邻回归。
- Windows 工作区执行 `git diff --check` 时出现 LF/CRLF 转换提示，但检查结果为通过，提交内容未包含纯行尾批量改写。

## 交付结论

修复分支已推送，远端修复代码提交已核对为 `6d33ead17d0ee754fa7237a64777974dee487c35`。当前停点为产品只读 Review：`READY_FOR_PRODUCT_REVIEW`。
