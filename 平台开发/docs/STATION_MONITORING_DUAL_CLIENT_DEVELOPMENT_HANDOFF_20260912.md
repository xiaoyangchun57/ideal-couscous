# 站点监测后端与小程序检查点提交交接

> 状态：`READY_FOR_CHECKPOINT_COMMIT`
> 唯一写入者：开发经理
> 停点：提交完成后停止，返回产品只读 Review。

## 目标与基线

本轮后端与小程序站点监测已完成产品 Code Review、设计静态 Review 和使用方真实 UI 验收，均为 `PASS`。现在只形成一个便于后续集成的独立检查点提交；Web 仍由外部协作者负责，不进入本提交。

已确认功能：管理员默认查看“我负责”并可切换“全部站点”；普通人员不能请求全部站点；范围、数量、本人负责标识、搜索和权限由服务端权威提供；全部站点只按名称/编号搜索，不展示或搜索 MN；站点列表、详情、分轴状态、位置校准低频层级及首页两个图标已闭环。

真实站点数据接入后的数值与加载表现仍为 `NOT RUN`，不影响本次检查点提交，但不得写成已通过。

## 唯一允许提交范围

- `平台开发/backend/app.py`
- `平台开发/backend/test_station_monitoring_normalization.py`
- `平台开发/docs/STATION_MONITORING_DUAL_CLIENT_DEVELOPMENT_HANDOFF_20260912.md`
- `平台开发/docs/STATION_MONITORING_MINIPROGRAM_DESIGN_HANDOFF_20260915.md`
- `平台开发/miniprogram/services/api.js`
- `平台开发/miniprogram/pages/responsible-sites/responsible-sites.js`
- `平台开发/miniprogram/pages/responsible-sites/responsible-sites.json`
- `平台开发/miniprogram/pages/responsible-sites/responsible-sites.wxml`
- `平台开发/miniprogram/pages/responsible-sites/responsible-sites.wxss`
- `平台开发/miniprogram/pages/site/site.js`
- `平台开发/miniprogram/pages/site/site.wxml`
- `平台开发/miniprogram/pages/site/site.wxss`
- `平台开发/miniprogram/pages/index/index.wxml`
- `平台开发/miniprogram/pages/index/index.wxss`
- `平台开发/miniprogram/tests/responsibleSitesMonitoring.test.js`
- `平台开发/miniprogram/tests/siteReadonlySource.test.js`
- `平台开发/miniprogram/images/station-monitoring/*.svg`
- `平台开发/miniprogram/images/icon-bell.svg`
- `平台开发/miniprogram/images/icon-clipboard-check.svg`

## 明确排除

- 所有 `平台开发/react-vite/` 差异，Web 由外部协作者负责。
- `平台开发/miniprogram/utils/config.js`
- `平台开发/miniprogram/tests/config.test.js`
- `平台开发/miniprogram/tests/requestLocalApi.test.js`
- `AGENTS.md`
- `平台开发/docs/PRODUCT_WORK_LEDGER.md`
- `平台开发/docs/STATION_MONITORING_WEB_COLLABORATOR_HANDOFF_20260914.md`
- 本地数据库、备份、日志、临时目录、私有配置、设计库、生成文件及其他未跟踪文件。

本轮只提交；不推送、不打标签、不冻结、不打包、不部署。不得为清洁工作区而回退、删除或改写任何排除项。

## 提交前检查

1. 只暂存上述白名单；逐项核对 `git diff --cached --name-only`，不得出现白名单外文件。
2. 保持已通过证据：后端监测模块 `34/34 PASS`；`responsibleSitesMonitoring.test.js`、`siteReadonlySource.test.js`、`homeTaskState.test.js` `17/17`、`notificationTarget.test.js`、Python/JS 语法及定向 `diff-check` 均为 `PASS`。本次不重复跑无关全量门禁。
3. 提交信息应准确表达“站点监测范围、检索与小程序呈现检查点”，不能表述为候选冻结或已发布。

## 回传

提交后只回传：

- 提交哈希与提交信息；
- 实际提交文件清单；
- 白名单校验结果；
- 提交后剩余差异清单；
- 明确说明未推送、未打标签、未冻结、未部署。
