# 主线当前任务：第五 Tab 与跨域集成

> 基线：`011b39667b40556b7409a0e3cbfcb76986d565ca`
> 分支建议：`collab/main-station-navigation`
> 停点：等待 A/C PR 后完成集成 Review 和真实 UI 交接；不部署

## 目标

我们主线承担实际开发，不只做分配：注册第五个“站点”Tab，完成首页站点/试剂摘要的一次性目标跳转，移除“我的”重复入口，并负责 A、B、C 与设计资产的依赖排序和最终整合。

## 允许范围

- `平台开发/miniprogram/app.js`
- `平台开发/miniprogram/app.json`
- `平台开发/miniprogram/pages/index/`
- `平台开发/miniprogram/pages/mine/`
- `平台开发/miniprogram/utils/homeTaskState.js`
- 对应小程序测试和本目录协作合同

不抢写 A 的 `responsible-sites`、B 的 React 范围或 C 的 `backend/app.py`。

## 验收

- 导航固定为 `首页｜巡检｜站点｜告警｜我的`。
- 首页两条摘要使用 `wx.switchTab + stationHubTarget`；失败清除本次目标，无 URL 参数和陈旧目标。
- 无新目标时站点 Tab 保留用户状态；“我的”不保留重复主入口。
- 合并后运行冲突相关测试并形成独立真实微信 UI 验收清单。
