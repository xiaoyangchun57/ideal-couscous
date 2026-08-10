# 水质智慧运维平台

本仓库包含 Flask 后端、React 管理台和微信小程序。后端直接提供已构建的 React 管理台；日常开发可单独启动 Vite。

## 本地运行

### 后端和已构建管理台

```powershell
cd backend
pip install -r requirements.txt
python app.py
```

后端和 Vite 开发服务器默认仅监听 `127.0.0.1`；如部署环境明确需要对外绑定，启动后端前显式设置 `BACKEND_HOST=0.0.0.0`，本地开发不要设置该变量。

访问 `http://127.0.0.1:5000`。后端使用 Waitress 监听 5000 端口，API 健康检查为 `http://127.0.0.1:5000/api/health`，本地数据库为 `backend/data/water.db`。

### React 管理台开发模式

```powershell
cd react-vite
npm.cmd install
npm.cmd run dev
```

开发服务器为 `http://127.0.0.1:5174`，`/api` 和 `/uploads` 均代理到本地后端。当前依赖基线为 React 19、React Router 7、Ant Design 5 和 Vite 8。构建产物输出到 `frontend/v2`，由后端直接提供。

### 微信小程序

在微信开发者工具中打开 `miniprogram` 目录。该目录包含受版本控制的 `app.json` 和 `project.config.json`。桌面开发者工具会由 `miniprogram/utils/config.js` 使用 `http://127.0.0.1:5000`；真机预览和正式版本使用已配置的 HTTPS 地址。不要将本地地址带入真机或正式版本。

## 验收入口

推荐在提交前执行：

```powershell
python -m pytest backend -q --ignore=backend/test_api.py
cd react-vite; npm.cmd run test:api
cd react-vite; npm.cmd run build
node --test miniprogram/tests/executionState.test.js miniprogram/tests/inspectionSubmissionState.test.js miniprogram/tests/inspectionReviewDecision.test.js miniprogram/tests/reworkFlow.test.js miniprogram/tests/notificationTarget.test.js miniprogram/tests/pagedList.test.js miniprogram/tests/vehicleScope.test.js
```

`backend/test_api.py` 是依赖本地 5000 端口的服务集成测试，不纳入上述 pytest 收集。完整交接要求见 `docs/RELEASE_TEST_HANDOFF.md`，开发约束见 `docs/DEVELOPMENT_STANDARDS.md`。
