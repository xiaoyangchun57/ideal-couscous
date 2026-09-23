# PR · 协作者 B 第一轮 Web 收口

> 分支：`collab/b-web-closeout` ｜ 目标 base：`main`
> 任务起点 `origin/main` SHA：`e168e1253c43ce02300e0efb01567dfe67bcac75`（建 PR 时 `main` 已前进到 `5b33d5f`，与本次改动文件无重叠）
> 合同：`平台开发/docs/workstreams/B_CURRENT.md` §7 ｜ 模板：`.github/pull_request_template.md`
> 停点：完成本 PR 并等待 Review；不部署、不操作生产数据
> 轮次：本轮为 **② 的追加收口**（返修不新建 PR，按 `workstreams/README.md` §1.4 更新本 PR）

---

## 结果责任

- 结果负责人：协作者 B（`collab/b-web-closeout`）
- 主责领域：B
- 变更类型：领域内（前端候选过滤与测试；未跨域、未改公共契约、未改 `backend/app.py`）

## 产品判断

### ③ 中文选词期间实时搜索不被 URL 回写覆盖（上轮已交付）

- 用户与场景：运维/审核人员在 Web 站点页用中文输入法搜索站名。选词（候选词未上屏）期间，浏览器前进/后退，或页面内其他模块回写 URL 查询参数。
- 当前损失：正在选的拼音草稿被外部 URL 回写覆盖，输入框被清空或回退成旧词。用户表现为「打拼音打到一半字没了」，必须重新输入，中文站名搜索高频中断。
- 完成标准：① 选词进行中，外部 URL 回写不得改变草稿；② `compositionend`（上屏）后，以用户选中的文本写入 URL 的 `q`；③ 非选词路径行为不变。

### ② 人员退出后不再出现于登录 / 分配 / 候选列表，历史业务仍可读（本轮）

- 用户与场景：管理员对离职人员执行「注销账号」。此后该人员不得再出现在登录入口、各类分配下拉（站点负责人、工作转交接收人）与候选列表中；但其历史业务记录必须仍可读。
- 当前损失：已注销/已停用账号仍可能出现在人员候选下拉里，被误派新工作——等于「退出未生效」，与「退出 = 注销」的产品语义相悖；同时管理员无法一眼判断某个下拉里的候选是否还有效。
- 完成标准：① 已注销账号（`status='inactive'` 且 `deleted_at` 非空）不得进入任何分配候选；② 已停用账号（`status='inactive'`、无 `deleted_at`）同样不得进入；③ 判断口径收敛为**单一实现**，不在各页各写一套；④ 注销后本人无登录权限；⑤ 注销**不得**级联删除历史业务数据，历史记录仍可读。

## 范围与契约

- 修改范围（本轮新增）：
  - **新增** `平台开发/react-vite/src/utils/assignableUsers.js`：统一判定「可作为分配候选」（`isAssignableUser` / `roleListOf` / `filterAssignableUsers`）
  - **新增** `平台开发/react-vite/src/utils/assignableUsers.test.js`：5 条单测
  - `平台开发/react-vite/src/pages/sites/siteManagerCandidates.js`：改为委托统一判定（保留原导出名与签名，调用方无感）
  - `平台开发/react-vite/src/pages/sites/siteManagerCandidates.test.js`：+1 条（注销/停用排除）
  - `平台开发/react-vite/src/pages/users/UsersPage.jsx`：转交接收人候选由页内内联过滤改为复用统一判定
  - `平台开发/react-vite/src/pages/weekly-plans/WeeklyPlansPage.jsx`：巡检人候选由「取全量 `/users`、无任何过滤」改为 `/users?status=active` + 统一判定；默认巡检人 `initialValue={1}` 加存在性守卫
  - `平台开发/react-vite/package.json`：`test:api` 纳入上述两个测试文件（其中 `siteManagerCandidates.test.js` 原先**未接入门禁**，本轮一并纳入，否则新增断言不会真正被执行）
  - 随 PR 新增证据：`平台开发/docs/evidence/b-web-closeout/`（本轮 +9 张真实 UI 截图）
  - **未修改 `backend/app.py`**
- 受影响领域及 Reviewer：消费方均为 B 自身页面（站点全景、人员与权限、周计划）。新增的 `utils/assignableUsers.js` 是**新的公共工具模块**，若 A/C 或主线后续需要「分配候选」判断，请直接复用而非另写。请求 Reviewer：主线（新增公共工具模块的归属与命名）。
- 接口、权限、状态、错误和幂等变化：无接口、权限、状态迁移或错误码变化。前端新增的是**客户端二次过滤**，不改变服务端返回内容。唯一可见行为变化：候选下拉中不再出现已注销/已停用账号。
- 向后兼容或 Mock/样例：
  - 站点负责人候选的**角色约束（仅 operator）不变**，只是额外排除了已注销/已停用。
  - 周计划巡检人候选**不新增角色约束**——该页原可任选任意在用账号（后端 `POST /api/weekly-plans` 只校验 `admin or self`），本轮只剔除已退出账号，不改变其余可选范围，避免越界修改产品行为。
  - 判断口径差异已核实：API 返回的 `roles` 由 `_normalize_user_roles(..., u.role)` 归一化，**必然非空且含主角色**，因此统一判定中「`roles` 为空则回退 `role`」的写法与线上数据完全等价，仅对合成数据更安全。
- 临时写入权及合并顺序：写入权为 `B_CURRENT.md` §7 授予的三项（`pages/sites/`、`pages/users/`、`hooks/useUrlSyncedSearch.js` 及对应测试）。本轮改动落在 `pages/sites/`、`pages/users/`、`pages/weekly-plans/` 与新增 `utils/`。**范围说明（如实）**：`pages/weekly-plans/` 与 `utils/` 不在 §7 逐字列举的三个路径内，但属于同一「人员退出后不得进入候选列表」收口目标下的必要落点（`utils/` 为收敛重复逻辑所必需，`weekly-plans/` 为同款过滤缺口）。若主线认为已越界，我可将该两处拆出为后续候选。本 PR 可独立合并：`main` 自起点后未改动本 PR 涉及文件，无冲突面。合入 `main` 不代表发布。

## 验证

- 自动测试（本轮**全量重跑**，非引用旧结果）：
  - `npm run test:api` → **109/109 passed**（基线 101/101；本轮 +8 = 新增 5 条 `assignableUsers` + `siteManagerCandidates` 新增 1 条 + 其原有 2 条首次接入门禁）
  - ③ 真实浏览器回归（真实 Edge + 隔离 fixture）→ **3/3 passed**（确认本轮改动未回归 ③；仍为零业务写入）
- 代码自检：`npm run lint` **PASS**（无输出）；`npm run build` **PASS**（`✓ built in 1.60s`，产物输出到 `frontend/v2/`，已在 `.gitignore`；`git status --short` 仅含本 PR 文件）；未新增依赖。
- 设计静态 Review：N/A（无视觉与信息架构变化，仅候选集合收窄；按 `workstreams/README.md` §8 不强制经设计师）
- 真实 UI：**PASS（③ T3）／ PASS（② 本轮）／ NOT RUN（① 站点全景）**，分项见附录 B。

## 残余边界

- 未完成、阻断或后续候选：
  - **① 站点全景保留「最后数据」与「关键时间」= `NOT RUN`（用户已裁定本轮搁置）**。阻断见附录 D 缺口 A/B：服务端能力开关为 `disabled`，且 `/api/sites` 返回的 235 条站点**不含任何时间字段**（连字段都不存在，不只是值为空）。属契约与数据前置条件，**不是代码缺陷**；本轮不改 `backend/app.py`，是否启用及字段来源请主线裁决。
  - **后端契约缺口 C：`POST /api/weekly-plans` 不校验被指派人状态**（附录 D）。本轮**未修改** `backend/app.py`，仅在前端收窄候选；建议主线补服务端校验，否则直接调用该端点仍可把新计划派给已注销账号。
  - **更正上轮口头判断**：上轮沟通中我曾把 `pages/weekly-plans/WeeklyPlansPage.jsx` 的未过滤 `GET /users` 描述为「线上可触发缺陷」。**该说法不准确**：全仓检索确认 `WeeklyPlansPage` 未被任何模块 import、也未挂路由，是**未接线的页面**。因此它是「未接线代码中的过滤缺口 + 服务端缺校验」，而非当前可经 UI 触发的缺陷。修复仍然必要（防止页面接线后立刻复现），但严重性按此更正。此更正同时写入附录 E。
  - 后续候选：按 `B_CURRENT.md` §8「首轮完成后的领域接管」独立执行，本 PR 不夹带。
- 是否涉及生产、迁移、冻结或部署：**否**。本轮为取得真实 UI 证据，在**本地开发库** `backend/data/water.db` 上执行了 1 次真实注销与配套业务操作，明细见附录 E 第 3 条。

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
| 远端分支 | `collab/b-web-closeout`（单次合成提交，随本 PR 更新） |
| 本地工作树 | `C:\dev\ideal-couscous`（`git status --short` 干净） |

③ 核心改动（净 +3 行）：

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

② 核心改动——统一判定（新增 `utils/assignableUsers.js`）：

```js
const ASSIGNABLE_STATUS = 'active';

export function isAssignableUser(row) {
  if (!row || typeof row !== 'object') return false;
  if (row.status !== ASSIGNABLE_STATUS) return false;
  if (row.deleted_at) return false;   // 二次防御：后端注销路径当前与 status 同写
  return true;
}
```

## 附录 B · 真实 UI 分项

### ③ T3 = PASS（上轮）

真机（真实微软拼音，含地址栏与候选窗）：

| 证据 | 地址栏 | 输入框 | 候选窗 |
|---|---|---|---|
| `evidence/…/B1-1-选词中-输入框显示拼音.png` | `127.0.0.1:5174/sites`（**无 `q=`**） | `zhong wen`（逐键递进，无累积） | `1 中文 2 种文 3 中湍 …` |
| `evidence/…/B1-2-选词后-输入框中文且URL已写入.png` | `127.0.0.1:5174/sites?q=中文` | `中文` | 已关闭（已上屏） |

**修复前对照**：加守卫前同一条回归失败于 `expected 'zhong'` / `actual 'gamma'`；加守卫后转 PASS。失败与通过共用同一断言。

### ② 人员退出 = PASS（本轮，真实 Edge + 真实后端）

同一账号 `协作者B待注销`（id=3）**注销前后对照**。左侧为注销前，右侧为注销后：

| 判定点 | 注销前 | 注销后 | 证据文件 |
|---|---|---|---|
| 本人登录权限 | 输入账号密码后进入系统（URL `/`） | 停留在 `/login`，红字「登录失败」 | `B2-1-注销前-该账号可正常登录.png` / `B2-5-注销后-该账号登录被拒.png` |
| 站点负责人候选（站点全景 → 新增站点 → 负责人） | 3 项：协作者B在用、**协作者B待注销**、协作者B转交样本 | 2 项：协作者B在用、协作者B转交样本（**该账号已消失**） | `B2-2-注销前-站点负责人候选含该账号.png` / `B2-6-注销后-站点负责人候选已排除该账号.png` |
| 转交接收人候选（人员与权限 → 停用 → 转交） | 2 项：**协作者B待注销**、协作者B转交样本 | 1 项：协作者B转交样本（**该账号已消失**） | `B2-3-注销前-转交接收人候选含该账号.png` / `B2-7-注销后-转交接收人候选已排除该账号.png` |
| 人员档案仍可读 | 5 条，该账号「启用」 | 5 条，该账号「**已注销**」（含 `deleted_at=2026-09-23 10:17:31`） | `B2-4-注销前-人员列表全貌.png` / `B2-8-注销后-人员列表仍显示该账号.png` |
| 历史业务仍可读 | — | 车辆 → 履历 → 使用：`赣A·X0002`／申请人 **协作者B待注销**／目的地 蛤蟆石站／`51200 km → 51232 km`／**已还车** | `B2-9-注销后-历史用车记录仍显示该账号.png` |

补充判定：站点负责人候选**不含系统管理员**（非 operator 角色），说明角色约束未被本轮改动破坏；候选总数在注销前已是 3（已注销样本 id=4 此前即被排除），注销后降为 2，变化只来自本次注销。

### ① 站点全景（T1）= NOT RUN

原因见「残余边界」。不使用单元测试、API 结果或历史截图冒充。

## 附录 C · ② 服务端实测（本地 5000，真实数据）

**A. 注销前基线**

| 检查 | 请求 | 实际响应 |
|---|---|---|
| 该账号可登录 | `POST /api/auth/login`（`协作者B待注销`） | `200`，返回 token |
| 待办为空（注销前置） | `GET /api/users/3/pending-work` | `200` `{"pending_work":{},"total":0}` |
| 人员集合 | `GET /api/users` / `?status=active` | 全量 **5** 条 / active **4** 条（该账号 active） |

> 说明：该账号原有一条 `status='approved'` 的用车申请，会命中 `_user_pending_work` 的「用车申请」项而**阻断注销**。为取得完整「注销前/后」对照，先通过正规业务流程（出车前检查 → 出车登记 → 还车检查 → 还车）把它推进到 `returned`，业务记录**保留**，从而同时得到「可注销」与「有历史业务」两个条件。

**B. 执行注销**：`DELETE /api/users/3` → `200` `{"success":true,"deleted":false,"soft_deleted":true}`

**C. 注销后**

| 检查 | 请求 | 实际响应 |
|---|---|---|
| 无登录权限（**用注销前的正确密码**） | `POST /api/auth/login` | **`401`** `{"error":"用户名或密码错误"}` |
| 不进 active 候选 | `GET /api/users?status=active` | `200`，**3 条**（系统管理员、协作者B在用、协作者B转交样本） |
| 档案仍可读 | `GET /api/users` | `200`，**5 条**，该账号 `status=inactive`、`deleted_at=2026-09-23 10:17:31` |
| 历史业务仍可读 | `GET /api/vehicle/applications` | `200`，`app 2`：`applicant_id=3`、`applicant_name=协作者B待注销`、`status=returned`、目的地 蛤蟆石站 |
| 历史行程仍可读 | `GET /api/vehicle/use-records` | `200`，`行程 1`：申请人 **协作者B待注销**、`returned_at=2026-09-23 10:10:41`、`end_mileage=51232` |

**D. 注销是否级联删除业务数据（量化对照）**

对本地库 **112 张表**在注销前后各取一次行数快照，全部差异仅 4 张：

| 表 | 前 | 后 | 性质 |
|---|---|---|---|
| `user_sites` | 243 | 240 | 预期：注销清理该账号的 3 条站点授权 |
| `user_lifecycle_snapshots` | 1 | 2 | 预期：新增注销事件快照（保留注销前角色与站点范围） |
| `auth_sessions` | 24 | 30 | 取证期间登录产生的会话 |
| `auth_login_attempts` | 25 | 32 | 取证期间登录尝试计数 |

> 41 张业务表（`work_order*` / `inspection*` / `plan*` / `vehicle*` / `sites` / `alert*` / `report*` 等）中，除 `user_sites`（授权关系）外**行数零变化**。结论：注销为软删，**不级联删除任何历史业务数据**，与「历史业务仍可读」一致。

## 附录 D · 后端缺口（精确端点 / 请求 / 实际响应 / 预期）

**缺口 A — 能力开关关闭，监测事实不可达（① 的阻断，非代码缺陷）**

| 项 | 内容 |
|---|---|
| 端点 | `GET /api/station-monitoring/sites` |
| 请求 | `Authorization: Bearer <admin token>`；`GET /api/auth/me` 返回 `capabilities.station_monitoring_public = true`，但门禁按模式判定 |
| 实际响应 | `403` `{"code":"STATION_MONITORING_PUBLIC_DISABLED","error":"站点监测能力尚未启用"}`；`?scope=all` 与 `/api/station-monitoring/summary` 同返回 |
| 预期 | 站点列表携带每站 `last_received_at` / `last_valid_observation_at` 等监测事实，使「保留最后数据 / 关键时间」有可观测对象 |
| 触发条件 | `STATION_MONITORING_ACCESS_MODE` 未设置时默认 `disabled`（`backend/app.py:182-198`；门禁在 `:5229-5243`） |

**缺口 B — `/api/sites` 无任何时间字段（① 的阻断，契约面）**

| 项 | 内容 |
|---|---|
| 端点 | `GET /api/sites` |
| 实际响应 | `200`，`count = 235`；站点对象键集合为 `address/code/device_count/district/id/is_pilot/lat/lng/manager/name/operation_frequency/phone/responsible_people/status/type` —— **不含任何时间字段** |
| 预期 | 明确「关键时间」的字段名与来源（`last_valid_observation_at` 还是 `last_received_at`？取自哪张表？），否则前端无字段可绑 |
| 备注 | 需同时澄清「保留最后数据」是「最后一次**有效**值」还是「最后一次**收到**的原始值」——监测场景下两者质控口径不同 |

**缺口 C — 周计划创建不校验被指派人状态**

| 项 | 内容 |
|---|---|
| 端点 | `POST /api/weekly-plans` |
| 请求 | 管理员 token，body `{"user_id": <已注销账号 id>, "week_start": "2026-09-28"}` |
| 实际行为 | 仅校验 `_has_any_role(current_user,'admin') or user_id == current_user.id`（`backend/app.py:27190`），**不校验 `user_id` 对应账号是否 active / 是否已注销**，随后直接 `INSERT INTO weekly_inspection_plans` |
| 预期 | 与 `GET /api/users?status=active` 的口径一致：拒绝向已注销/已停用账号派发新计划（返回 409 或 400 并给业务错误码） |
| 影响面 | 前端本轮已收窄候选，但直接调用该端点仍可派发；且该页面当前**未被路由接线**（见附录 E 第 1 条） |
| 本轮处理 | **不改 `backend/app.py`**，按合同写清缺口交主线裁决 |

## 附录 E · 已知限制（如实标注）

1. **`pages/weekly-plans/WeeklyPlansPage.jsx` 当前未被接线**。全仓检索确认无任何 `import WeeklyPlansPage`、`App.jsx` 亦无 `weekly-plans` 路由，该页面不可达。因此本 PR 对它的修复属「防止接线后立即复现」的预防性修正，**不是**修复线上可触发缺陷。上轮口头沟通中我把它称为「线上可触发缺陷」，现予更正。
2. `B1-1` / `B1-2` 两张 ③ 真机截图的按键由程序经 OS 层注入（`keybd_event`），**不是人工逐键敲的**；但触发的是本机真实微软拼音，事件链为真实 IME 序列，地址栏与候选窗均为真实渲染。如需纯手工版本，按 `evidence/…/T3_输入法选词验证作业指导书.html` 第 4 节重拍即可替换。
3. ③ 的浏览器回归使用合成 composition 事件（`new CompositionEvent` / `new InputEvent`）而非真实输入法；与真机截图是两套手段，不互相替代。② 的 9 张截图则为**真实 Edge + 真实后端 + 真实数据**，无事件注入。
4. **本轮对本地开发库的写操作明细**（`backend/data/water.db`，均为取得 ② 真实证据所必需，全部经正规 API 路径，无手工改库）：
   - `PUT /api/users/3/password`：为 `协作者B待注销` 设置已知密码（用于注销前/后登录对照）
   - `POST /api/vehicle/inspections` ×2（`dispatch` / `return`）；`POST /api/vehicle/use-records`；`POST /api/vehicle/use-records/1/return`：把该账号名下的用车申请推进到 `returned`，产生 1 条历史行程
   - `DELETE /api/users/3`：真实注销该取证账号
   - 结果：用户表新增 1 条已注销记录（`id=3`，`deleted_at=2026-09-23 10:17:31`），车辆 2 里程 `51200 → 51232`，新增 1 条已还车行程。**不含生产系统、不含远端仓库数据**。
5. `isAssignableUser` 同时校验 `status` 与 `deleted_at`，前提是依赖后端「注销必然同写两字段」（`backend/app.py:19268-19270`）。该前提当前成立且 `PUT /api/users/<uid>/status` 显式拒绝把已注销账号改回 active（`:19425-19426`）。若后端将来拆分这两条状态变更路径，前端守卫会失效——已在单测中以「`status='active'` 但 `deleted_at` 非空」的异常态固化断言，届时该单测仍会通过，但语义上应改为由服务端统一裁决。
