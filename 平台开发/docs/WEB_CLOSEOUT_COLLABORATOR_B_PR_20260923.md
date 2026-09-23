# PR · 协作者 B 第一轮 Web 收口

> 分支：`collab/b-web-closeout` ｜ 目标 base：`main`
> 任务起点 `origin/main` SHA：`e168e1253c43ce02300e0efb01567dfe67bcac75`（建 PR 时 `main` 已前进到 `5b33d5f`，与本次改动文件无重叠）
> 合同：`平台开发/docs/workstreams/B_CURRENT.md` §7 ｜ 模板：`.github/pull_request_template.md`
> 停点：完成本 PR 并等待 Review；不部署、不操作生产数据

---

## 结果责任

- 结果负责人：协作者 B（`collab/b-web-closeout`）
- 主责领域：B
- 变更类型：领域内（仅前端 hook 与测试；未跨域、未改公共契约）

## 产品判断

- 用户与场景：运维/审核人员在 Web 站点页用中文输入法搜索站名。选词（候选词未上屏）期间，浏览器前进/后退，或页面内其他模块回写 URL 查询参数。
- 当前损失：正在选的拼音草稿被外部 URL 回写覆盖，输入框被清空或回退成旧词。用户表现为「打拼音打到一半字没了」，必须重新输入，中文站名搜索高频中断。
- 完成标准：① 选词进行中，外部 URL 回写不得改变草稿；② `compositionend`（上屏）后，以用户选中的文本写入 URL 的 `q`；③ 非选词路径行为不变——普通键盘输入仍实时写 URL、外部写 URL 仍生效、外部清空 `q` 输入框仍跟随清空。

## 范围与契约

- 修改范围：
  - `平台开发/react-vite/src/hooks/useUrlSyncedSearch.js`（+3 行，`syncExternal()` 增加 composition 守卫）
  - `平台开发/react-vite/src/hooks/useUrlSyncedSearch.test.js`（+22 行，新增 1 条单测）
  - `平台开发/react-vite/src/hooks/useUrlSyncedSearch.browser.test.js`（新增 161 行，真实 Edge 回归）
  - 随 PR 新增：`平台开发/docs/WEB_CLOSEOUT_COLLABORATOR_B_PR_20260923.md`、`平台开发/docs/evidence/b-web-closeout/`（8 个证据文件）
  - **未修改 `backend/app.py`**；未触碰 `pages/sites/`、`pages/users/` 的运行时代码
- 受影响领域及 Reviewer：消费方是 B 自己在 `pages/sites/` 的搜索接线；`useUrlSyncedSearch.js` 属公共 hook，若 A/C 或主线也用它做 URL 同步，请一并抽查。请求 Reviewer：主线（公共 hook 与跨域一致性）。
- 接口、权限、状态、错误和幂等变化：无接口、权限、状态迁移或错误码变化；无幂等键变化。唯一行为变化是「选词期间忽略外部 URL 回写」，纯前端时序修正。
- 向后兼容或 Mock/样例：URL 查询参数名（`q`）与编码均不变，只改变写入时机。三条既有行为由回归场景 1、2 与单测第 3 条守住。浏览器回归使用隔离 API fixture（`page.route` 拦截 `**/api/**`，非 GET 一律返回 405 并断言零业务写入），不依赖固定数据库或真实后端。
- 临时写入权及合并顺序：写入权为 `B_CURRENT.md` §7 授予的三项（`pages/sites/`、`pages/users/`、`hooks/useUrlSyncedSearch.js` 及对应测试），本 PR 只用到 hook 与其测试，未扩张范围。本 PR 可独立合并：`main` 自起点后未改动这三个文件，无冲突面。合入 `main` 不代表发布。

## 验证

- 自动测试：
  - `node --test src/hooks/useUrlSyncedSearch.test.js` → **4/4 passed**（含本轮新增 1 条）
  - `npm run test:api` → **101/101 passed**（基线 100/100，本轮 +1）
  - 真实浏览器回归（真实 Edge + 隔离 fixture）→ **3/3 passed**（2 个场景，`node:test` 计 3 项含父测试）
- 代码自检：`npm run lint` **PASS**（无告警输出）；`npm run build` **PASS**（`✓ built in 8.18s`，产物输出到 `frontend/v2/`，该路径已在 `.gitignore`）；`git status --short` 仅含本 PR 文件；未新增依赖（浏览器回归所需 `playwright-core` 由环境变量外供，未写入 `package.json`）。
- 设计静态 Review：N/A（纯时序修正，无视觉与信息架构变化；按 `workstreams/README.md` §8，不强制经过设计师）
- 真实 UI：**PASS（T3）／ NOT RUN（站点列、退出账号候选列表）**，分项见附录 B。

## 残余边界

- 未完成、阻断或后续候选：
  - **T1 站点全景保留「最后数据」与「关键时间」= `NOT RUN`**。精确阻断见附录 D：服务端能力开关为 `disabled`，且本地库无监测事实。属环境与数据前置条件，**不是代码缺陷**；本轮不修改 `backend/app.py`，是否启用及数据来源请主线裁决。
  - **T2 真实 Web 候选列表截图 = `NOT RUN`**。仅完成服务端 API 级真实验证（附录 C）；候选列表筛选逻辑本轮未改动，但不用 API 结果冒充 UI 通过。
  - 后续候选：按 `B_CURRENT.md` §8「首轮完成后的领域接管」独立执行，本 PR 不夹带。
- 是否涉及生产、迁移、冻结或部署：**否**。

## 作者自检

- [x] 已核对上下游、权限、状态和直接相邻回归
- [x] 同一文件不存在并发写入者
- [x] 失败可见、可重试，关键写入无部分副作用
- [x] 未用自动测试冒充真实 UI
- [x] 返修继续更新本 PR，没有创建平行 PR 或重复交接

---

## 附录 A · 提交与起点

| 项 | 值 |
|---|---|
| 起点 `origin/main` | `e168e1253c43ce02300e0efb01567dfe67bcac75` |
| 建 PR 时远端 `main` | `5b33d5f71a19d44a67d1ce19cc24d4b5d5fd38fe`（与本次改动文件无重叠） |
| 远端分支 | `collab/b-web-closeout`（起点 `e168e12`，本 PR 为单次合成提交） |
| 本地工作树 | `C:\dev\ideal-couscous`（`git status --short` 干净） |

核心改动（净 +3 行）：

```js
syncExternal(value) {
  // 输入法选词（composition）进行中：外部 URL 回写不得覆盖正在选词的草稿，
  // 最终值由 compositionEnd 时的用户输入决定（中文搜索不被打断）。
  if (composing) return;
  compositionCommitValue = null;
  cancelPending();
  onDraft(value);
}
```

## 附录 B · 真实 UI 分项

### T3 = PASS

真机（真实微软拼音，含地址栏与候选窗）：

| 证据 | 地址栏 | 输入框 | 候选窗 |
|---|---|---|---|
| `evidence/…/B1-1-选词中-输入框显示拼音.png` | `127.0.0.1:5174/sites`（**无 `q=`**） | `zhong wen`（逐键递进，无累积） | `1 中文 2 种文 3 中湍 …` |
| `evidence/…/B1-2-选词后-输入框中文且URL已写入.png` | `127.0.0.1:5174/sites?q=中文` | `中文` | 已关闭（已上屏） |

判定点全部通过：草稿未被清空或回退；候选窗全程未闪断；选词**中**未写入 `q=拼音`，上屏后才写入；过滤结果正确。

自动化（真实 Edge，逐步截图 `evidence/…/url-search-{1..5}-*.png`）：非选词时外部 `q=beta` 生效 → 选词中外部写 `q=gamma` 后输入框**仍为** `zhong` → 上屏后地址栏 `q=%E4%B8%AD%E6%96%87`。

**修复前对照**：加守卫前同一条回归失败于 `expected 'zhong'` / `actual 'gamma'`；加守卫后转 PASS。失败与通过共用同一断言，不是另写一条必然通过的测试。

### 站点列（T1）= NOT RUN，退出账号候选列表（T2）= NOT RUN

原因见「残余边界」。二者不使用单元测试、API 结果或历史截图冒充。

## 附录 C · T2 服务端实测（本地 5000）

| 检查 | 请求 | 实际响应 |
|---|---|---|
| 注销前可登录 | `POST /api/auth/login` | `200`，返回 token |
| 注销 | `DELETE /api/users/4` | `soft_deleted: true` |
| 不进 active 候选 | `GET /api/users?status=active` | `200`，**4 条**，不含该账号；全量 `GET /api/users` → **5 条**，该账号 `status=inactive`、`deleted_at=2026-09-22 10:45:41` |
| 注销后无登录权限 | `POST /api/auth/login`（该账号） | **`401`** |

结论：服务端已是权威，注销后不可登录、不进入 active 集合，历史记录仍可读（未被物理删除）。因此**前端无需新增过滤**，也不存在「用前端隐藏冒充退出成功」。

## 附录 D · 后端缺口（精确端点 / 请求 / 实际响应 / 预期）

**缺口 A — 能力开关关闭，监测事实不可达（非代码缺陷）**

| 项 | 内容 |
|---|---|
| 端点 | `GET /api/station-monitoring/sites` |
| 请求 | `Authorization: Bearer <admin token>`；`GET /api/auth/me` 返回 `capabilities.station_monitoring_public = true`，但门禁按模式判定 |
| 实际响应 | `403` `{"code":"STATION_MONITORING_PUBLIC_DISABLED","error":"站点监测能力尚未启用"}`；`?scope=all` 与 `/api/station-monitoring/summary` 同返回 |
| 预期 | 站点列表携带每站 `last_received_at` / `last_valid_observation_at` 等监测事实，使「保留最后数据 / 关键时间」有可观测对象 |
| 触发条件 | `STATION_MONITORING_ACCESS_MODE` 未设置时默认 `disabled`（`backend/app.py:182-198`；门禁在 `:5229-5243`） |

**缺口 B — 本地库无监测事实（数据面）**

| 项 | 内容 |
|---|---|
| 端点 | `GET /api/sites` |
| 实际响应 | `200`，`count = 235`，均无监测事实 |
| 预期 | 至少 1 站具备真实监测事实（真实接入链路，或受控注入并在 PR 注明来源），否则「降级保留最后数据」无可观测对象 |

该缺口即 T1 被 `NOT RUN` 的直接原因。同时 T1 受既有产品决策约束——`src/pages/sites/stationMonitoring.test.js:78` 规定「成功响应缺失站点时不得复用上次监测事实」，**该约束本轮原样保留通过，未被改动**。

## 附录 E · 已知限制（如实标注）

1. `B1-1` / `B1-2` 两张真机截图的按键由程序经 OS 层注入（`keybd_event`），**不是人工逐键敲的**；但触发的是本机真实微软拼音，事件链为真实 IME 序列（`keydown → compositionstart → beforeinput(insertCompositionText) → input(isComposing=true) → compositionend`），地址栏与候选窗均为真实渲染。如需纯手工版本，按 `evidence/…/T3_输入法选词验证作业指导书.html` 第 4 节重拍即可替换。
2. 浏览器回归使用合成 composition 事件（`new CompositionEvent` / `new InputEvent`）而非真实输入法；与真机截图是两套手段，不互相替代。
3. 守卫 `if (composing) return;` 依赖 composition 事件成对闭合。若某浏览器只发 `beforeinput(isComposing)` 而漏发 `compositionend`，守卫可能延迟解除。当前无实测实例，列为观察项，不预设修复。
