# 水质运维系统可用性整改交接

更新时间：2026-08-03

## 新任务首条指令

在新任务中使用以下指令开始：

> 继续维护水质运维系统本地版本。先完整读取 `docs/usability-testing/HANDOFF.md` 和 `docs/usability-testing/issue-log.csv`，再检查当前 `git diff` 与本地数据库状态。可用性台账 40 项已全部复测通过，UXTEMP 临时数据已清理；不要重复造数、重复整改、部署或回滚现有工作树，从新的使用反馈开始处理。

## 当前目标与边界

- 目标：根据问题台账完成所有问题的分析、修复和真实界面复测。
- 当前只修改和验证本地代码，不处理线上版本，不部署服务器。
- 当前工作树包含用户和此前开发任务的大量有效改动，禁止 `git reset --hard`、`git checkout --` 或批量回滚。
- 不要使用现有旧发布压缩包覆盖源码；当前工作树比 2026-07-30 的发布包更新。
- 测试人员并非都熟悉水质运维，整改判断要同时参考台账、真实业务流程和人因可用性，不机械照抄用户原话。
- 账号密码等敏感信息未写入本文。小程序实测账号为 `jiangwei`，密码需从用户或既有安全渠道取得。

## 台账状态

台账文件：`docs/usability-testing/issue-log.csv`

- 总计 40 项。
- 已复测通过 40 项，未完成 0 项。
- 2026-08-03 完成最后 7 项小程序复测：UX-008、UX-009、UX-011、UX-012、UX-013、UX-017、UX-023。
- UX-011：执行包滚动宽度 576px、视口 390px，滚至最右后标签完整可读。
- UX-013：完整显示台账登记 3 项、站房环境 4 项、质控校准 5 项，并可滚动到底。
- UX-017：待出车、已批准、已完成、已归还、使用中等三字状态均为单行，典型尺寸 47x18px。
- UX-023：待出车标签与出车登记按钮垂直中心线差值为 0px。

## 本轮最后新增的代码修复

### 结转文案

文件：`平台开发/miniprogram/pages/index/index.wxml`

- 由“补做 · 昨日遗留 N 项”改为“昨日未完成 · 需补检 N 项”。

### 执行包横向选择

文件：`平台开发/miniprogram/pages/inspection/inspection.wxss`

- `package-switch` 改为真正的横向滚动容器。
- `package-chip` 改为 `inline-flex`、禁止换行并保留完整标签。
- 修复前 4 个包纵向占用多行，修复后同一行横向排列。

### 车辆状态对齐

文件：

- `平台开发/miniprogram/pages/vehicle/vehicle.wxml`
- `平台开发/miniprogram/pages/vehicle/vehicle.wxss`

改动：

- 将状态标签从车辆标题内部移到 `application-row` 的直接子元素。
- 状态标签与“出车登记”按钮现在是同级元素，由行容器统一 `align-items: center`。
- 新增 `.application-status { flex: 0 0 auto; }`，避免状态标签被压缩换行。
- 最终结构已在开发者工具中实测：标签与按钮垂直中心线差值为 0px。

## 已完成的主要整改

- Web 管理端与后端台账问题已完成修复和浏览器复测，台账对应项均为“已复测通过”。
- 异常上报、异常闭环、工单证据、计划详情、设备/站点空值、车辆待办、用户角色显示等问题均已处理。
- 小程序异常上报已取消异常编码；类别选择、整卡进入详情、提交反馈和关联工单路径已修复。
- 小程序“负责站点”原为假入口，已新增独立列表页并可进入站点详情：
  - `平台开发/miniprogram/pages/responsible-sites/responsible-sites.js`
  - `平台开发/miniprogram/pages/responsible-sites/responsible-sites.wxml`
  - `平台开发/miniprogram/pages/responsible-sites/responsible-sites.wxss`
  - `平台开发/miniprogram/pages/responsible-sites/responsible-sites.json`
- “我的异常上报”导航标题、整卡详情入口、备件申请命名、菜单副文案字号等已在开发者工具中复测通过。
- 草稿删除二次确认和排程编辑库存空状态说明已实测通过。

## 临时数据库测试数据

数据库：`平台开发/backend/data/water.db`

唯一标识 `UXTEMP-20260731-1258` 的临时数据已于 2026-08-03 精确清理：

- `plan_schedules` 4 条、`insp_plans` 4 条、`insp_plan_items` 60 条、`vehicle_applications` 1 条均已删除。
- `vehicle_use_records`、`plan_departure_confirmations`、`plan_resource_reservations`、`plan_schedule_events` 等关联计数均为 0。
- 全库标识扫描结果为空，`PRAGMA integrity_check` 返回 `ok`。
- 清理前一致性备份：`平台开发/backend/data/water.db.before_uxtemp_cleanup_20260803.bak`。
- 姜伟账号原有真实草稿计划未删除。不要恢复旧备份覆盖当前数据库，除非明确需要事故恢复。

## 开发者工具与本地服务

- 微信开发者工具项目：`E:\杂七杂八\水质运维\平台开发\miniprogram`
- CLI：`D:\工具\微信web开发者工具\cli.bat`
- 用户确认开发者工具位于屏幕 1。
- PID 会变化，不要复用本文中的旧 PID；按主窗口标题 `miniprogram - 微信开发者工具` 查找。
- 本轮复测使用 `http://127.0.0.1:5000`；测试结束后不应依赖本文中的旧 PID。
- Web `http://127.0.0.1:5173` 本轮未启动，使用生产构建完成验证。

自动编译命令：

```powershell
& 'D:\工具\微信web开发者工具\cli.bat' auto `
  --project 'E:\杂七杂八\水质运维\平台开发\miniprogram' `
  --trust-project --lang zh
```

编译通常会清除小程序登录态。登录输入框的原生自动输入不稳定，可在开发者工具 Console 对当前登录页调用 `setData` 填充账号和密码，再点击真实“登录”按钮；密码不要写入脚本文件或交接文档。

开发者工具窗口曾被其他桌面应用遮挡。不要把粘贴到 WPS 或其他窗口的脚本当作已执行，操作后必须查看小程序页面路径和截图确认。

## 关键截图证据

目录：`docs/usability-testing/`

- `wechat-devtools-package-rightmost-20260803.png`：横滑后最右侧执行包标签完整可读。
- `wechat-devtools-inspection-groups-20260803.png`：巡检页首个语义分组。
- `wechat-devtools-inspection-bottom-20260803.png`：滚动到底后的站房环境、仪器质控与校准分组。
- `wechat-devtools-status-tags-trips-20260803.png`：已归还、使用中状态标签单行展示。

上一轮交接中提到的其他截图文件当前不在工作区，不能作为可打开的证据；对应结论保留在问题台账的复测结果中。UX-023 以 2026-08-03 自动化测得中心线差值 0px 为准。

## 建议的新任务执行顺序

1. 读取本文、台账和当前 `git diff`，确认不回滚现有改动。
2. 不要重复处理已关闭的 40 项台账问题，从新的用户反馈建立新编号和复现证据。
3. 继续坚持本地修改、本地复测；用户明确要求前不要部署或覆盖服务器。
4. 新问题修复后运行对应端测试，并保持台账 GBK 编码可正常回读。

## 建议验证命令

小程序状态逻辑测试：

```powershell
node 平台开发/miniprogram/tests/executionState.test.js
node 平台开发/miniprogram/tests/inspectionSubmissionState.test.js
```

Web：

```powershell
Set-Location 平台开发/react-vite
npm.cmd run build
```

后端至少覆盖本轮相关链路：

```powershell
Set-Location 平台开发/backend
python -m pytest -q `
  test_departure_confirmation.py `
  test_manual_report_closure.py `
  test_manual_report_evidence.py `
  test_mobile_checkin_integrity.py `
  test_mobile_my_today_scope.py `
  test_vehicle_lifecycle.py `
  test_workorder_evidence_flow.py
```

若环境允许，最终再运行全部后端测试。

2026-08-03 最终结果：小程序 2 组状态测试通过；后端关键链路 28 项通过、全量 75 项通过；Web lint 0 错误并成功构建（保留 154 条既有 warning）。

## 工作树注意事项

- 当前有 53 个已跟踪文件发生修改，约 1204 行新增、1374 行删除，另有多个新增文件与目录。
- 改动覆盖后端、小程序、Web、部署配置和文档，不应假定未提交文件都是临时垃圾。
- 不要删除发布压缩包、数据库备份或截图证据，除非用户明确要求。
- 台账是 GBK 编码；更新后必须使用 `Import-Csv -Encoding Default` 回读中文和状态计数，防止乱码。
