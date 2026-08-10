# 本地冻结候选验收与交接（2026-08-10）

## 边界

- 本记录只覆盖 `平台开发` 本地工作区。
- 本地后端固定为 `http://127.0.0.1:5000`，数据库为 `backend/data/water.db`。
- 验收过程未部署服务器、未修改线上数据库、未切换线上容器、未创建标签；全部门槛通过后只允许创建本地冻结标签交上线测试线，不代表生产部署授权。
- 微信开发者工具在桌面环境使用本地 API；真机和正式版本不携带 `127.0.0.1`。

## 本地运行

```powershell
cd backend
python app.py
```

管理台可访问 `http://127.0.0.1:5000`。独立调试 React 时运行：

```powershell
cd react-vite
npm.cmd install
npm.cmd run dev
```

Vite 端口为 `5174`，并代理 `/api`、`/uploads` 到本地后端。微信开发者工具应打开 `miniprogram` 目录。

## 最终验收证据

| 验收项 | 命令或工具 | 结果 |
| --- | --- | --- |
| 后端回归 | `python -m pytest backend -q --ignore=backend/test_api.py` | `224 passed`，退出码 0 |
| 小程序纯逻辑 | 下方 7 个 Node 测试文件 | `7/7` 通过 |
| React 逻辑 | `cd react-vite; npm.cmd run test:api` | `23/23` 通过 |
| React lint/build | `cd react-vite; npm.cmd run build` | lint 与 Vite build 均通过 |
| 本地服务集成 | 最新本地 5000 后端下运行 `python backend/test_api.py` | 通过，读取 37 个站点 |
| 微信开发者工具 | 9420 自动化通道 | 已恢复并完成下述实测 |
| 浏览器界面 | 本地浏览器 | 计划审批表头、用车页签、消息 current/history `0/22` 通过 |
| Python 语法 | `python -m py_compile backend/app.py` | 通过，退出码 0 |
| 补丁格式 | `git diff --check` | 通过，退出码 0；仅有 Windows LF→CRLF 提示，无空白错误 |

后端 pytest 运行期间出现来自全局环境的 `pytest-qt`、PySide6 与 NumPy 兼容性警告；测试仍为 224 项通过且命令退出码为 0。该警告应作为环境治理项保留，不应误记为业务测试失败。

小程序纯逻辑验收命令：

```powershell
node --test miniprogram/tests/executionState.test.js miniprogram/tests/inspectionSubmissionState.test.js miniprogram/tests/inspectionReviewDecision.test.js miniprogram/tests/reworkFlow.test.js miniprogram/tests/notificationTarget.test.js miniprogram/tests/pagedList.test.js miniprogram/tests/vehicleScope.test.js
```

`backend/test_api.py` 直接访问本地 5000 端口，属于服务集成测试，不纳入 pytest 全量收集；本轮已在最新本地后端上单独通过。

## 最终本地冻结补充证据

- 后端最终仅监听 `127.0.0.1:5000`，遗留 `0.0.0.0` 进程已清理。
- 浏览器实测：数据审核页签可见；计划审批保持选中，表头为“计划内容/路线与站点/用车/备件/提交时间/操作”；整改 `rework_plan=128` 解析为 `schedule=37` 且无控制台错误；备件 `?tab=parts` 正确选中；计划详情 Descriptions 告警已消失。
- 微信开发者工具实测：admin 肖永平 `unread/reviewTodo=0/1`、周雄雄 `=2/0`、万松 `=0/1`；当前/历史消息分别为 `0/22`、`2/1`、`0/20`；周雄雄车辆卡片只含 `applicant_id=26`，其他账号未串入。
- 安全回归覆盖旧巡检照片与工单直达端点角色/站点授权、批量原子校验和默认回环绑定。
- 本轮仍未提交、未打标签、未推送、未部署，未操作线上数据库或容器。全部门槛通过后才创建本地冻结提交和标签交上线测试线。

## 微信开发者工具实测

自动化 9420 通道已恢复。本地后端仍固定为 5000，已完成：

- 管理员、周雄雄、万松三个账号的角色和车辆可见范围核对；
- “待我审核” `reviewTodo` 数量核对；
- 消息 current/history 切换与列表读取；
- 计划审批通知直达 `/pages/plan-detail/plan-detail?id=38`。

上述结果只证明本地冻结候选状态，不代表生产部署。上线测试线门槛和开发规范分别见 `RELEASE_TEST_HANDOFF.md`、`DEVELOPMENT_STANDARDS.md`。
