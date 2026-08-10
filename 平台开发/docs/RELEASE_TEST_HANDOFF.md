# 上线测试交接

## 边界

本交接仅面向上线测试线。所有门槛通过前不得创建标签，也不得执行生产部署、修改线上数据库或切换线上容器。测试线必须使用独立数据和可回滚环境。门槛通过后创建的本地冻结标签只用于交给上线测试线，不代表生产部署授权。

旧提交 `c78de8c` 和标签 `release-20260810-cross-module-freeze` 已被产品评审打回，只保留历史追溯，禁止部署。r2 唯一候选标签为 `release-20260810-cross-module-freeze-r2`。

本地后端固定为 `http://127.0.0.1:5000`，本地数据库为 `backend/data/water.db`。网页端可直接访问后端提供的管理台；Vite 调试端口为 `5174`。

## 提交测试线前的验证命令

```powershell
python -m pytest backend -q --ignore=backend/test_api.py
cd react-vite; npm.cmd run test:api
cd react-vite; npm.cmd run build
node --test miniprogram/tests/executionState.test.js miniprogram/tests/inspectionSubmissionState.test.js miniprogram/tests/inspectionReviewDecision.test.js miniprogram/tests/reworkFlow.test.js miniprogram/tests/notificationTarget.test.js miniprogram/tests/pagedList.test.js miniprogram/tests/vehicleScope.test.js miniprogram/tests/partsReview.test.js
python -m py_compile backend/app.py
git diff --check
```

记录实际输出、日期和提交版本；未执行或失败的项目不得写为通过。

本地冻结 r2 的当前证据为：后端 `230 passed`（全局 `pytest-qt` / PySide6 / NumPy 兼容警告，退出码 0）、小程序 Node `8/8`、React `27/27`、React lint/build 通过、`backend/test_api.py` 在最新本地 5000 后端通过并读取 37 个站点、`python -m py_compile backend/app.py` 退出码 0、全工作区 `git diff --check` 退出码 0。`git diff --check` 仅报告 Windows LF→CRLF 提示，没有空白错误。

## 测试线验收重点

- 多角色和站点范围：普通角色不可跨站，管理员兼运维员按合并角色获得授权范围。
- 聚合审核：计划、影像、备件、工单和计划变更显示决策重点、详情和证据；批量跨范围审核必须失败且不产生部分写入。
- 通知：消息角标、current/history 切换、已读归档、同一影像批次去重，以及计划通知直达对应详情。
- 现场闭环：巡检签到/离站、影像审核驳回后的整改资源、车辆从批准至归还的占用与延期、备件实际领用扣减。
- 登录与密码：会话失效提示和安全返回地址、强制改密、管理员设置自定义密码。

## 微信开发者工具实测

自动化 9420 通道已恢复。在本地 5000 后端下，现有开发者工具实例完成重新编译并加载用户代码；admin 消息 current/history 为 `0/22`；点击计划 #38 的真实通知准确进入 `/pages/plan-detail/plan-detail?id=38`；系统标红影像显示逐张核对确认，取消后未提交。

浏览器端已实测计划审批表头、用车页签和消息 current/history `0/22`。上线测试线仍应使用独立数据复核完整业务闭环；真机及正式版本不得使用 `127.0.0.1`。

最终本地冻结证据补充：后端仅监听 `127.0.0.1:5000`，遗留的 `0.0.0.0` 进程已清理。浏览器实测计划 #38 通知定位、过期计划明确拒绝且保留待办、不存在的数据审核对象告警、消息 current/history `0/22`。微信开发者工具实测编译成功、计划 #38 真实通知直达和风险影像确认门禁。安全回归覆盖旧巡检照片与工单直达端点的角色/站点授权、批量原子校验和默认回环绑定。

r2 已完成本地冻结提交并创建 `release-20260810-cross-module-freeze-r2` 标签；未推送、未部署，未操作线上数据库或容器。该标签只允许交上线测试线复核。

## 部署线待确认项

不在本轮修改生产配置。`deploy/SERVER_PRECHECK.md` 与 Nginx 配置内容均使用 `ops.hhyc-tec.cn`，但配置文件名为 `deploy/nginx/ops.xiaoyangchun.space.conf`。部署线必须在发布前确认目标域名、Baota 站点、ESA/NAT 规则和证书归属，再执行既有备份流程。
