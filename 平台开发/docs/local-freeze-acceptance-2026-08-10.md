# 2026-08-11 r4 appendix: code-freeze candidate pending product UI re-test

The r3 product review is returned. r1/r2/r3 remain historical and rejected; `release-20260811-cross-module-freeze-r4` is the sole candidate allowed by the local guard. This appendix records the r4 local closure and does not authorize push, deployment, online database/container access, or delivery-line operations.

Final local evidence (Asia/Shanghai, 2026-08-11): backend unittest 240/240 exit 0 (`09:35:29-09:36:10`); miniprogram Node 11/11 exit 0 (`09:35:19`); `python dev_scripts/check_syntax.py --node-root miniprogram --python-root backend` 117 files exit 0 (`09:35:19-09:35:27`); React test:api 35/35 exit 0 (`09:35:18-09:35:19`); React lint exit 0 (`09:35:28-09:35:31`); React build exit 0 (`09:35:25`); backend py_compile exit 0 (`09:35:28-09:35:29`); diff check exit 0 (`09:35:26`). The initial syntax invocation without root arguments returned exit 2 at `09:25:17`, then the corrected command above returned exit 0.

`backend/test_api.py` retains default `http://127.0.0.1:5000` and now accepts `TEST_API_BASE_URL` for local-only testing. Against the existing local 5000 service it returned exit 0 at `09:36:50` and confirmed 37 sites; against unbound `127.0.0.1:5999` it returned WinError 10061 and exit 1 at `09:36:49-09:36:52`. The 5000 process (PID 14584, `python.exe backend/app.py`, started 09:15) predated this run and was not terminated.

The following r4 UI acceptance items are all `未验证` and require product-manager real UI re-test: both typed parts notification states and same-number routing; plan executor display; Web risk-image gate; admin deletion of unbound `TEST_DELETE_` media; confirmation metadata/impact, cancel, empty reason, non-admin 403, formal-evidence 409, failure retry retention; post-delete list/statistics/pending-review/notification/unread/thumbnail refresh consistency; and WeChat rapid-click single-confirm/request-lock/cancel/failure-release behavior.

Browser runtime exact residual: `failed to write kernel assets: 系统找不到指定的路径。 (os error 3)`. One initial browser connection attempt plus two diagnostics were made; retries stopped before `09:30:44`, no external Playwright was used, and no new tab was created. WeChat official recovery was attempted twice and then stopped: first attempt reported an IDE already at 36992 and launched HTTP 25730; second attempt with 36992 failed `EEXIST: file already exists, mkdir 'C:\Users\11708\AppData\Local\微信开发者工具'`. No second IDE was started or retried. Only agent-started services were stopped; existing IDE and 5000 service remain.

The image deletion audit/soft-delete changes and the `TEST_API_BASE_URL` compatibility test are local code changes. Focused temporary SQLite databases were removed by teardown; the blocked browser run created no UI sample rows. Parent-directory `project.config.json` and `project.private.config.json` remain excluded from the commit.

# 本地冻结 r3 验收与交接（2026-08-10）

## 边界

- 本记录只覆盖 `平台开发` 本地工作区。
- 本地后端固定为 `http://127.0.0.1:5000`，数据库为 `backend/data/water.db`。
- 验收过程未部署服务器、未修改线上数据库、未切换线上容器。r1/r2/r3 标签 `release-20260810-cross-module-freeze`、`release-20260810-cross-module-freeze-r2` 和 `release-20260810-cross-module-freeze-r3` 均已退回，禁止部署、复用或移动；r4 `release-20260811-cross-module-freeze-r4` 是唯一候选，已在全量验证全绿后创建本地冻结提交和注释标签。
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

Vite 本轮实际端口为 `5173`，并代理 `/api`、`/uploads` 到本地后端。微信开发者工具应打开 `miniprogram` 目录；不得为了浏览器验收反复切换端口。

## 跨端契约

- 后端待审响应返回 `parts_request` 兼容字段和 Web 契约要求的 `spare_part_request`；详情字段与 `PartsRequestAuditTab` 对齐。
- 审批详情/动作显式传递 `request_type`，统计覆盖两类备件；契约回归见 `backend/test_audit_parts_contract.py`。
- 计划审核 `executor_name` 为执行人、`requester_name` 为申报人，Web 与小程序语义一致。

## r3 最终验收证据

| 验收项 | 命令或工具 | 结果 |
| --- | --- | --- |
| 后端全量 unittest（排除独立 `test_api.py`） | `$mods = ...; python -B -m unittest $mods` | 23:25:23-23:26:02，227 tests，exit 0 |
| 小程序全部 Node 测试 | `Get-ChildItem miniprogram/tests -Filter '*.test.js' ... node --test` | 23:26:12，10 passed，exit 0 |
| 小程序全部 JavaScript 语法 | `Get-ChildItem miniprogram -Recurse -Filter '*.js' ... node --check` | 23:26:24-23:26:26，46 files，exit 0 |
| React 逻辑 | `Push-Location react-vite; npm.cmd run test:api` | 23:26:34-23:26:35，31 passed，exit 0 |
| React lint/build | `npm.cmd run lint`、`npm.cmd run build` | lint 23:26:42-45、build 23:26:53-57，均 exit 0 |
| backend 全量 Python 语法 | `Get-ChildItem backend -Recurse -Filter '*.py' ... python -m py_compile` | 23:27:06-23:27:12，66 files，exit 0 |
| 本地服务集成 | 5000 后端运行中/停止后 `python backend/test_api.py` | 运行中 23:27:32 exit 0、37 sites；停止后 23:27:47-50 exit 1，WinError 10061 |
| 微信开发者工具 | 现有 `36992/9420` | 见下方真实工具证据 |
| Web 内置浏览器 | 本地浏览器 | 仅计划列表/弹窗执行人已确认，其余指定项逐项未覆盖 |
| 补丁格式 | `git diff --check` | 23:27:21，exit 0；无空白错误，仅 LF→CRLF 警告 |

历史 r2 曾记录 230 项 pytest 通过，但该数量不作为 r3 结果；本表只记录文档更新后的实际 r3 复跑。后端 unittest 期间有迁移/通知辅助逻辑的既有跳过提示，无测试失败，最终为 `227 tests ... OK`。

最终小程序 Node/语法命令：

```powershell
Get-ChildItem miniprogram/tests -File -Filter '*.test.js' | Sort-Object Name | ForEach-Object { node --test $_.FullName }
Get-ChildItem miniprogram -Recurse -File -Filter '*.js' | Sort-Object FullName | ForEach-Object { node --check $_.FullName }
```

`backend/test_api.py` 直接访问本地 5000 端口，属于服务集成测试，不纳入 unittest 全量模块；必须分别记录服务运行中 exit 0 和服务停止后非零。

## 实际工具验收

### Web 内置浏览器

- 已确认计划列表和弹窗显示 `排程人/执行人：万松`。
- `spare_part_request` 通知精确定位、Web 已处理/不存在状态：未覆盖；通知中心未成功渲染面板。
- Web 风险影像确认门禁：未覆盖。
- Web 消息空状态、失败状态和有旧数据失败提示：未覆盖。
- Web 截图：未覆盖；截图调用工具阻塞并已终止。
- 未继续切换浏览器端口，最后执行 `iab.tabs.finalize({keep: []})` 清理 tab。

### 微信开发者工具

复用现有 IDE HTTP `36992` 和 automator `9420`，未启动第二个 IDE。本轮已完成：

- 计划页读取当前计划 `user_name: 肖永平`；
- 审核 API 返回 `total: 1`、`source_type: plan_schedule`、`executor_name: 万松`、`requester_name: 万松`；
- 不存在计划 `ps_999999` 的审核失败设置 `submittingId`，失败后恢复为空，错误弹窗可确认，无 JS exception；
- 合成风险照片触发确认弹窗，取消返回 `true`，取消后无提交且 `submittingId` 为空；
- 消息空态为 `loaded: true`、`loading: false`、`list: []`、`viewState: "empty"`；无数据失败为 `viewState: "error"`；有旧数据失败保留列表和 `errorMessage`，`viewState: "data"`。

上述结果只证明本地候选状态，不代表生产部署。上线测试线门槛和开发规范分别见 `RELEASE_TEST_HANDOFF.md`、`DEVELOPMENT_STANDARDS.md`。

## 进程清理与残余边界

- 已终止挂起的 automator/截图脚本；不启动第二个微信开发者工具。
- 最终测试后停止本轮后端和 Vite 进程，保留用户已有微信开发者工具实例。
- Web 通知面板、Web 风险影像门禁、Web 消息状态和 Web 截图是本轮未覆盖边界，不能标记为通过。
