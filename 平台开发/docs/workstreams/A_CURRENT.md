# 协作者 A 当前任务：站点 Tab 读取链

> 基线：开始任务时的 `origin/main`；在 PR 中记录任务起点 SHA
> 分支建议：`collab/a-station-tab-read`
> 状态：`PR #1 / CODE_REVIEW_REWORK_REQUIRED`
> 停点：完成下述单点返修并更新原 PR，等待复审；不合并、不部署

## 目标

把已完成静态结构的“站点”模式接入真实站点目录和监测读取能力。管理员可以切换本人/全部并搜索，其他角色只看授权站点；站点详情沿用既有路由。不得把数据接收状态描述成 RTU 或仪器健康。

## 允许范围

- `平台开发/miniprogram/pages/responsible-sites/`
- `平台开发/miniprogram/services/api.js` 中站点读取相关部分
- 对应小程序测试
- 必要的纯展示绑定修正；不得重新设计已通过页面

本轮不修改 `app.json`、首页、“我的”、试剂后端和 `backend/app.py`。这些由其他单元负责。

## 契约与验收

- 阅读 `平台开发/docs/SITE_TAB_DEV_WIRING_20260921.md` 和其上位合同。
- 接口范围、错误文案、过期响应、返回详情后的状态保持、正确详情路由全部落实。
- 覆盖管理员/非管理员、本人/全部、搜索清除、无权限、空态、首次失败和保留旧数据后的刷新失败。
- 回传产品判断、修改文件、测试结果、真实 UI `PASS/NOT RUN` 和残余依赖。

## PR #1 单点返修

监测列表曾成功加载后，如果 `/api/station-monitoring/sites` 明确返回 `STATION_MONITORING_PUBLIC_DISABLED` 或 `STATION_MONITORING_ADMIN_ONLY`，页面已得到“不得继续展示监测字段”的权威结论。当前代码只有目录回退成功时才把 `monitoringEnabled/monitoringPublic` 设为 `false`；若随后目录请求失败，旧监测状态仍会继续显示。

- 在确认是上述门禁响应且请求仍为当前请求时，立即关闭监测展示，再发起目录回退；保留旧站点目录和可重试错误，不保留旧监测字段。
- 增加一条回归：先成功加载监测列表，再模拟门禁 403 + 目录回退失败；断言两个监测开关均为 `false`、旧站点目录可保留、错误可重试，详情来源按档案模式处理。
- 只运行 `responsibleSitesMonitoring.test.js`、相关 JS 语法检查和 `git diff --check`；无需重复小程序 230 项完整测试。
- 不扩展文件范围，不顺手接试剂、第五 Tab、首页或“我的”。
