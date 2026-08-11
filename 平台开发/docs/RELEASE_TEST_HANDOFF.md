# 2026-08-11 r4 final local closure

Status: local code-freeze candidate created pending product UI re-test. The r3 product review is returned; r1/r2/r3 remain historical and rejected. `release-20260811-cross-module-freeze-r4` is the only machine-allowed candidate in `deploy/release-candidates.json`. No push, deployment, online database, or container operation was performed.

Final automated evidence (Asia/Shanghai, 2026-08-11):

| Check | Command | Result | Exit |
| --- | --- | --- | ---: |
| Backend full unittest, excluding `test_api.py` | `python -B -m unittest $mods` | 240 tests, OK, 09:35:29-09:36:10 | 0 |
| Miniprogram Node | `node --test` over all `miniprogram/**/*.test.js` | 11/11 passed, 09:35:19 | 0 |
| Syntax aggregator | `python dev_scripts/check_syntax.py --node-root miniprogram --python-root backend` | 117 files, JS/Python all PASS, 09:35:19-09:35:27 | 0 |
| React API tests | `npm.cmd run test:api` | 35/35 passed, 09:35:18-09:35:19 | 0 |
| React lint | `npm.cmd run lint` | passed, 09:35:28-09:35:31 | 0 |
| React build | `npm.cmd run build` | Vite build passed, 09:35:25 | 0 |
| Backend Python compile | `python -m py_compile` over all backend Python files | passed, 09:35:28-09:35:29 | 0 |
| Whitespace | `git diff --check` | no whitespace errors; only LF/CRLF warnings, 09:35:26 | 0 |
| API with existing local service | `TEST_API_BASE_URL=http://127.0.0.1:5000 python backend/test_api.py` | login/dashboard/sites shape OK; 37 sites, 09:36:50 | 0 |
| API with unbound local port | `TEST_API_BASE_URL=http://127.0.0.1:5999 python backend/test_api.py` | WinError 10061 connection refused, 09:36:49-09:36:52 | 1 |

The first syntax-aggregator invocation omitted its required roots and returned exit 2 at 09:25:17. It was immediately corrected with the command recorded above and returned exit 0; the invocation error is not a business test failure. `test_api.py` now keeps the default `127.0.0.1:5000` and accepts `TEST_API_BASE_URL` only for local checks; `backend/test_api_base_url.py` covers both defaults and override normalization.

Candidate guard evidence before tagging: `release-20260810-cross-module-freeze`, `...-r2`, and `...-r3` each returned `REJECT`/exit 1; `release-20260811-cross-module-freeze-r4` returned `ACCEPT`/exit 0. The local freeze commit and annotated r4 tag are created only after this handoff is committed; the tag points to that freeze commit. Parent-directory `project.config.json` and `project.private.config.json` are intentionally excluded.

## Image deletion review

The independent review found and fixed the request contract to send only `{ reason }`, added `manual_report` and explicit pending inspection links to the server-owned evidence block, expanded the deletion-before snapshot, and kept failure reasons in the React modal for retry. The implementation is one-record soft deletion only: it never removes the physical file, has no batch-delete endpoint, requires a role set containing `admin`, requires `source_type=test` with no source id and no formal link, returns the fixed 409 message for formal evidence, returns 403/404/400 as applicable, and makes repeated deletion idempotent without another audit row. Focused coverage is included in `backend/test_attachment_delete.py` (5 tests) and the React attachment deletion tests (2 tests).

## UI evidence and residual boundaries

The following are explicitly **未验证 / not verified** for this r4 closure and must be re-tested by the product manager in real UI; API and unit evidence must not be presented as UI proof:

- Web: pending/processed/missing/forbidden notifications for both `parts_request` and `spare_part_request`, same-number exact routing, plan executor display, risk-image gate, and refresh/state rendering.
- Web image archive: admin deletion of `TEST_DELETE_` unbound test media; confirmation metadata and impact; cancel; empty/whitespace reason; non-admin hidden entry/403; formal evidence 409; failure reason retained for retry; post-delete list/statistics/pending-review/notification/unread/thumbnail consistency after refresh.
- WeChat developer-tool rapid clicks: exactly one risk confirmation, cancel then resubmit, one request, and failed request releasing the lock while retaining the reason.

The in-app browser skill was read. Browser runtime initialization failed with the exact error `failed to write kernel assets: 系统找不到指定的路径。 (os error 3)`. There was one initial connection attempt followed by two diagnostics; retries stopped before the 09:30:44 local-service check and no browser tab was created in this attempt. No external browser or Playwright was used.

The existing WeChat IDE was not closed and no second IDE was started. Official CLI recovery was attempted twice in the prior handoff: attempt 1 reported `IDE may already started at port 36992, trying to connect` and launched HTTP at 25730; attempt 2 with `--port 36992` failed with `EEXIST: file already exists, mkdir 'C:\Users\11708\AppData\Local\微信开发者工具'`. Automated recovery stopped after attempt 2; no further retry was made. The user-owned IDE processes around 08:59 and the pre-existing backend PID 14584 (`python.exe backend/app.py`, started 09:15) were left untouched.

Agent-started backend/Vite sessions were stopped. The agent Vite attempt used 5175 because 5174 was already in use, then was stopped; the pre-existing 5000 backend was not stopped because it predated this run. No temporary `TEST_DELETE_` rows or temporary SQLite database were created by the blocked browser run; focused-test temporary databases were removed by test teardown. Final status must separately report the existing 5000 service and the product UI re-test boundary.

# 上线测试交接（r4）

## 边界

本交接仅面向上线测试线。所有门槛通过前不得创建标签，也不得执行生产部署、修改线上数据库或切换线上容器。测试线必须使用独立数据和可回滚环境。门槛通过后创建的本地冻结标签只用于交给上线测试线，不代表生产部署授权。

历史 r1/r2/r3 标签 `release-20260810-cross-module-freeze`、`release-20260810-cross-module-freeze-r2` 和 `release-20260810-cross-module-freeze-r3` 均只保留历史追溯，禁止部署、复用或移动。`release-20260811-cross-module-freeze-r4` 是当前唯一候选；机器门禁以 `deploy/release-candidates.json` 为准，已在最终全量测试全绿后创建本地冻结提交和注释标签。

本地后端固定为 `http://127.0.0.1:5000`，本地数据库为 `backend/data/water.db`。网页端本轮实际访问 `http://127.0.0.1:5173/`；禁止为了浏览器验收反复切换端口。

## 提交测试线前的验证命令

```powershell
$mods = Get-ChildItem backend -File -Filter 'test_*.py' | Where-Object { $_.Name -ne 'test_api.py' } | Sort-Object BaseName | ForEach-Object { 'backend.' + $_.BaseName }
python -B -m unittest $mods
Get-ChildItem miniprogram/tests -File -Filter '*.test.js' | Sort-Object Name | ForEach-Object { node --test $_.FullName }
python -B dev_scripts/check_syntax.py --node-root miniprogram --python-root backend
Push-Location react-vite; npm.cmd run test:api; Pop-Location
Push-Location react-vite; npm.cmd run lint; Pop-Location
Push-Location react-vite; npm.cmd run build; Pop-Location
git diff --check
python deploy/verify_release_candidate.py --tag release-20260811-cross-module-freeze-r4
python backend/test_api.py
# 停止本地后端后再次执行，必须为非零退出码
python backend/test_api.py
```

记录实际输出、日期和提交版本；未执行或失败的项目不得写为通过。

上方命令是 r4 最终复跑清单。历史 r3 的通过数量不作为 r4 证据；文档更新后必须重新记录实际时间、测试数和退出码。`check_syntax.py` 会执行全部文件并累计失败，不能由最后一个成功文件覆盖前面的失败。

## 测试线验收重点

- 多角色和站点范围：普通角色不可跨站，管理员兼运维员按合并角色获得授权范围。
- 聚合审核：计划、影像、备件、工单和计划变更显示决策重点、详情和证据；批量跨范围审核必须失败且不产生部分写入。
- 通知：消息角标、current/history 切换、已读归档、同一影像批次去重，以及计划通知直达对应详情。
- 现场闭环：巡检签到/离站、影像审核驳回后的整改资源、车辆从批准至归还的占用与延期、备件实际领用扣减。
- 登录与密码：会话失效提示和安全返回地址、强制改密、管理员设置自定义密码。

## 实际工具验收

### Web 内置浏览器

已读取并使用 `browser:control-in-app-browser` 技能。实际确认计划列表和弹窗显示 `排程人/执行人：万松`。

以下项目没有真实 browser UI 证据，不能用单测、API 响应或微信开发者工具结果替代，均列为未覆盖：

- `spare_part_request` 通知精确定位，以及 Web 上“已处理/不存在”明确状态：未覆盖；通知中心未成功渲染面板。
- Web 风险影像门禁：未覆盖。
- Web 消息空状态、失败状态和有旧数据失败提示：未覆盖。
- Web 截图：未覆盖；截图调用发生工具阻塞并已终止。

浏览器端口未继续切换，最后执行 `iab.tabs.finalize({keep: []})` 清理 tab，未因该工具阻塞制造额外服务进程。

### 微信开发者工具

复用现有开发者工具实例和 `36992/9420` 通道，项目为 `miniprogram`，未启动第二个 IDE。实际证据如下：

- 计划页读取当前计划 `user_name: 肖永平`；审核接口返回 `total: 1`、`source_type: plan_schedule`、`executor_name: 万松`、`requester_name: 万松`。
- 使用不存在计划 `ps_999999` 做审核失败短链路：提交时设置 `submittingId`，失败后恢复为空，错误弹窗可确认，无 JS exception，覆盖 loading/disabled、防重入和失败恢复。
- 合成风险照片触发逐张确认弹窗；取消动作返回 `true`，取消后 `submittingId` 为空且没有继续提交。
- 消息真实空态为 `loaded: true`、`loading: false`、`list: []`、`viewState: "empty"`；无数据失败为 `viewState: "error"`；有旧数据失败保留列表和 `errorMessage`，`viewState: "data"`。

截图工具多次超时，已在命令边界终止对应脚本；该项作为残余工具边界记录，不影响上述状态读取证据。

r4 候选已创建本地提交和 `release-20260811-cross-module-freeze-r4` annotated tag，未推送、未部署、未操作线上数据库或容器；候选校验命令只做精确放行，不创建或移动标签。

## r3 历史复跑记录（不作为 r4 证据）

以下表格是 r3 的历史复跑证据，仅用于追溯；r4 必须按上方命令重新执行并记录实际时间、测试数和退出码，不得沿用这些数量：

| 项目 | 实际时间 | 测试数/结果 | 退出码 |
| --- | --- | --- | --- |
| 后端全量 unittest（排除独立 `test_api.py`） | 23:25:23-23:26:02 +08:00 | 227 tests，OK | 0 |
| 小程序全部 Node 测试 | 23:26:12 +08:00 | 10 tests，10 passed | 0 |
| 小程序全部 JavaScript 语法检查 | 23:26:24-23:26:26 +08:00 | 46 files，0 failures | 0 |
| React `test:api` | 23:26:34-23:26:35 +08:00 | 31 tests，31 passed | 0 |
| React lint | 23:26:42-23:26:45 +08:00 | eslint `src` passed | 0 |
| React build | 23:26:53-23:26:57 +08:00 | Vite build passed | 0 |
| backend 全量 `py_compile` | 23:27:06-23:27:12 +08:00 | 66 files，0 failures | 0 |
| `git diff --check` | 23:27:21 +08:00 | 无空白错误；仅 LF→CRLF 警告 | 0 |
| `backend/test_api.py`（服务运行中） | 23:27:32 +08:00 | `/api/sites` 37 sites，结构 OK | 0 |
| `backend/test_api.py`（服务停止后） | 23:27:47-23:27:50 +08:00 | WinError 10061 连接拒绝 | 1 |

最终测试结束后停止本轮启动的后端和 Vite 进程，只保留用户已有微信开发者工具；确认无遗留自动化脚本或额外 IDE。

## 部署线待确认项

不在本轮修改生产配置。`deploy/SERVER_PRECHECK.md` 与 Nginx 配置内容均使用 `ops.hhyc-tec.cn`，但配置文件名为 `deploy/nginx/ops.xiaoyangchun.space.conf`。部署线必须在发布前确认目标域名、Baota 站点、ESA/NAT 规则和证书归属，再执行既有备份流程。
