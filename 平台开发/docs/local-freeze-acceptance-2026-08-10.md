# 本地冻结 r2 验收与交接（2026-08-10）

## 边界

- 本记录只覆盖 `平台开发` 本地工作区。
- 本地后端固定为 `http://127.0.0.1:5000`，数据库为 `backend/data/water.db`。
- 验收过程未部署服务器、未修改线上数据库、未切换线上容器。旧标签 `release-20260810-cross-module-freeze` 已被产品评审打回并禁止部署；r2 仅创建本地标签 `release-20260810-cross-module-freeze-r2` 交上线测试线，不代表生产部署授权。
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
| 后端回归 | `python -m pytest backend -q --ignore=backend/test_api.py` | `230 passed`，退出码 0 |
| 小程序纯逻辑 | 下方 8 个 Node 测试文件 | `8/8` 通过 |
| React 逻辑 | `cd react-vite; npm.cmd run test:api` | `27/27` 通过 |
| React lint/build | `cd react-vite; npm.cmd run build` | lint 与 Vite build 均通过 |
| 本地服务集成 | 最新本地 5000 后端下运行 `python backend/test_api.py` | 通过，读取 37 个站点 |
| 微信开发者工具 | 9420 自动化通道 | 编译、计划通知直达和风险影像确认通过 |
| 浏览器界面 | 本地浏览器 | 计划通知定位、过期计划拒绝、数据审核缺失提示、消息 current/history `0/22` 通过 |
| Python 语法 | `python -m py_compile backend/app.py` | 通过，退出码 0 |
| 补丁格式 | `git diff --check` | 通过，退出码 0；仅有 Windows LF→CRLF 提示，无空白错误 |

后端 pytest 运行期间出现来自全局环境的 `pytest-qt`、PySide6 与 NumPy 兼容性警告；测试仍为 230 项通过且命令退出码为 0。该警告应作为环境治理项保留，不应误记为业务测试失败。

小程序纯逻辑验收命令：

```powershell
node --test miniprogram/tests/executionState.test.js miniprogram/tests/inspectionSubmissionState.test.js miniprogram/tests/inspectionReviewDecision.test.js miniprogram/tests/reworkFlow.test.js miniprogram/tests/notificationTarget.test.js miniprogram/tests/pagedList.test.js miniprogram/tests/vehicleScope.test.js miniprogram/tests/partsReview.test.js
```

`backend/test_api.py` 直接访问本地 5000 端口，属于服务集成测试，不纳入 pytest 全量收集；本轮已在最新本地后端上单独通过。

## 最终本地冻结补充证据

- 后端最终仅监听 `127.0.0.1:5000`，遗留 `0.0.0.0` 进程已清理。
- 浏览器实测：计划 #38 通知定位正确；过期计划显示明确业务拒绝且保留待办；不存在的数据审核对象显示无权限/不存在提示；消息 current/history 为 `0/22`。
- 微信开发者工具实测：现有实例编译并加载本地代码成功；admin 当前/历史消息为 `0/22`；计划 #38 的真实通知准确直达详情；风险影像提交前显示逐张核对确认，取消后不提交。
- 安全回归覆盖旧巡检照片与工单直达端点角色/站点授权、批量原子校验和默认回环绑定。
- r2 已完成本地冻结提交并创建本地标签；未推送、未部署，未操作线上数据库或容器。

## 微信开发者工具实测

自动化 9420 通道已恢复。本地后端仍固定为 5000，本轮已完成：

- 开发者工具原进程内重新编译并加载用户代码；
- admin 消息 current/history `0/22` 切换与列表读取；
- 计划审批通知直达 `/pages/plan-detail/plan-detail?id=38`。
- 风险影像逐张核对确认弹窗与取消不提交。

上述结果只证明本地冻结候选状态，不代表生产部署。上线测试线门槛和开发规范分别见 `RELEASE_TEST_HANDOFF.md`、`DEVELOPMENT_STANDARDS.md`。
