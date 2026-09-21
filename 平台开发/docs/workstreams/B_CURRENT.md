# 协作者 B 当前任务：Web 收口与真实体验

> 基线：`011b39667b40556b7409a0e3cbfcb76986d565ca`
> 分支建议：`collab/b-web-closeout`
> 停点：提交并推送分支，创建 PR，等待主线 Review；不部署

## 目标

以当前代码为起点，核对并收口三项 Web 行为：站点全景保留“最后数据”和“关键时间”；人员退出后不再出现在登录、分配和候选列表中但历史业务仍可读；中文搜索输入法选词期间不被 URL 回写覆盖。

## 允许范围

- `平台开发/react-vite/src/pages/sites/`
- `平台开发/react-vite/src/pages/users/`
- `平台开发/react-vite/src/hooks/useUrlSyncedSearch.js`
- 对应 React 测试

本轮不修改 `backend/app.py`。发现服务端仍缺失契约时，在 PR 中给出精确端点、请求、实际响应和预期，不与 C 并发写后端。

## 验收

- 相关 Node 测试、lint、build 通过。
- 当前真实 Web 覆盖站点列、账号退出后的候选列表和至少一个中文输入法实时搜索场景。
- 保留业务历史，不用前端隐藏冒充退出成功。
- 回传修改文件、测试、真实 UI 证据、后端残余缺口和 `NOT RUN`。
