# 水质智慧运维平台

本仓库包含 Flask 后端、React 管理台和微信小程序。后端直接提供已构建的 React 管理台；日常开发可单独启动 Vite。

## 本地运行

### 后端和已构建管理台

```powershell
cd backend
pip install -r requirements.txt
python app.py
```

后端默认仅监听 `127.0.0.1`。微信真机调试需要同一 Wi-Fi 访问开发线时，启动后端前显式设置
`BACKEND_HOST=0.0.0.0`；默认运行档位为 `local`，不要把不可达的开发 API 静默切到线上。

访问 `http://127.0.0.1:5000`。后端使用 Waitress 监听 5000 端口，API 健康检查为 `http://127.0.0.1:5000/api/health`，本地数据库为 `backend/data/water.db`。

### React 管理台开发模式

```powershell
cd react-vite
npm.cmd install
npm.cmd run dev
```

开发服务器固定为 `http://127.0.0.1:5174`，`/api` 和 `/uploads` 均代理到本地后端。当前依赖基线为 React 19、React Router 7、Ant Design 5 和 Vite 8。构建产物输出到 `frontend/v2`，由后端直接提供。

### 微信小程序

微信开发者工具唯一导入本 README 所在的 `平台开发` 根目录，不要直接导入 `miniprogram` 子目录。
根 `project.config.json` 通过 `miniprogramRoot` 指向源码，是模拟器、真机调试、体验版和上传的唯一入口。

- `develop`：固定 `http://192.168.2.103:5000`，`API_PROFILE=local`，供模拟器和真机调试。
- `trial` / `release`：固定 `https://ops.hhyc-tec.cn`，`API_PROFILE=online`。

不使用存储覆盖、24 小时过期或静默回线上机制。

## 验收入口

推荐在提交前执行：

```powershell
python -m pytest backend -q --ignore=backend/test_api.py
cd react-vite; npm.cmd run test:api
cd react-vite; npm.cmd run build
node --test miniprogram/tests/*.test.js
```

`backend/test_api.py` 是依赖本地 5000 端口的服务集成测试，不纳入上述 pytest 收集。真实 UI 验收按
[`docs/UI_VALIDATION_WORKFLOW.md`](docs/UI_VALIDATION_WORKFLOW.md) 留证；完整交接要求见
`docs/RELEASE_TEST_HANDOFF.md`，开发约束见 `docs/DEVELOPMENT_STANDARDS.md`。
