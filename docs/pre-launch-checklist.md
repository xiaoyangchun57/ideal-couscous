# 水质智慧运维系统 · 发布前准备清单

> 生成日期：2026-07-29
> 适用架构：Flask(SQLite) + Vite 静态 + 微信原生小程序，容器化部署（docker-compose），国内云服务器 + 已备域名
> 结论先行：**系统功能主体已完成，但当前部署文件处于"能跑开发、不能上线"状态**。下方按"硬阻塞 → 数据 → 安全 → 韧性 → 验证"排序，前 5 条不解决无法上线。

---

## 一、发布硬阻塞（不解决无法上线）

### 1. 小程序 BASE_URL 改为 HTTPS 域名（真机全挂）
- 现状：`平台开发/miniprogram/utils/config.js:18`
  ```js
  BASE_URL: isDevtools ? 'http://127.0.0.1:5000' : 'http://192.168.2.110:5000'
  ```
- 问题：正式版小程序**强制 HTTPS**，且请求域名必须备案并加入小程序后台白名单。当前是局域网 IP + HTTP，真机所有接口必失败。
- 动作：
  - 改为 `https://你的域名`（建议用独立子域如 `https://api.你的域名`，与网页端同源或独立均可，但必须 HTTPS）。
  - 重新"构建 npm" + 上传代码包 + 提交审核。
  - 小程序后台「开发管理 → 开发设置 → 服务器域名」把 **request / uploadFile / downloadFile** 全加为 `https://你的域名`。

### 2. 配置 HTTPS + 反向代理（目前全明文）
- 现状：compose 仅 `8080:5000` 明文；无 Nginx/Caddy，无证书。
- 架构建议（最小改动，沿用"Flask 同时提供静态+API"现有设计）：
  - 在宿主机加 **Nginx 或 Caddy** 终止 443 TLS，反代到容器 5000。
  - Flask 已同时服务静态(`frontend/v2`)、`/api`、`/uploads`（见 `app.py:12573-12717`），所以 Nginx 只需 `location / { proxy_pass http://127.0.0.1:5000; }` 一把梭，无需再分静态。
  - 证书用 Let's Encrypt（certbot）或 Caddy 自动签发，**配置自动续期**。
- 最小 Nginx 片段：
  ```nginx
  server { listen 80; server_name 你的域名; return 301 https://$host$request_uri; }
  server {
    listen 443 ssl; server_name 你的域名;
    ssl_certificate     /etc/ssl/你的域名/fullchain.pem;
    ssl_certificate_key /etc/ssl/你的域名/privkey.pem;
    location / {
      proxy_pass http://127.0.0.1:5000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-Proto $scheme;
    }
  }
  ```

### 3. 微信服务器域名白名单 + ICP 备案
- 小程序后台白名单域名必须 **HTTPS + 已完成 ICP 备案**（国内服务器硬性要求）。
- 确认：域名备案已完成；备案主体与小程序主体一致（或已授权）。
- 微信订阅消息（已接通正式号 `wx1b28df61adae8ca1`）依赖该域名可达，白名单缺失会静默失败。

### 4. 修复 docker-compose.yml（文件本身是坏的）
- 现状：`平台开发/docker-compose.yml` 内容是 heredoc 外壳：
  ```
  cat > docker-compose.yml << 'EOF'
  services: ...
  EOF
  ```
  这不是合法 YAML，`docker compose up` 直接报错。
- 动作：改为合规 compose（见第五节参考），并**把 uploads 目录纳入持久卷**（见第 6 条）。

### 5. 上传照片目录持久化（上线即丢数据）
- 现状：`app.py:6203` `UPLOAD_DIR = ../frontend/uploads`；Flask 直接服务该目录（`app.py:12704`）。
- 但 compose 只挂载 `./backend/data:/app/data`，**`frontend/uploads` 未挂卷** → 容器重建/更新镜像后所有照片（站点/工单/附件/校准）丢失。
- 动作：compose 中新增卷映射，例如 `./uploads:/frontend/uploads`（容器内路径依 Dockerfile 的 `COPY frontend/ frontend/` 推算为 `/frontend/uploads`，需与 UPLOAD_DIR 对齐）。首次迁移把现有 `平台开发/frontend/uploads/*` 拷进宿主机挂载目录。

---

## 二、数据与持久化

### 6. SQLite 备份策略
- DB 已在 `./backend/data` 卷（好）。补充：
  - 定时备份：cron 每日 `sqlite3 water.db .backup /backup/water_$(date +%F).db`，或低峰期文件拷贝（含 `-wal`/`-shm`）。
  - 部署前、每次大改前手动备份一次。
  - WAL 模式下备份前先 `PRAGMA wal_checkpoint(TRUNCATE)` 或停写再拷。

### 7. 镜像内 baked DB 隐患
- Dockerfile `COPY backend/data/ data/` 把 DB 烤进镜像；若宿主机 `./backend/data` 初始为空，Docker 会建空目录挂载覆盖 → 容器内无库。
- 现状 `init_db()` 启动即执行建表（记忆已记），应可自建；但**种子数据可能不会自动跑**。建议：镜像不烤 DB，部署时宿主机挂载目录预置 `water.db`，或启动脚本显式执行 seed。

### 8. 前端构建产物确认
- `vite.config.js` 输出到 `../frontend/v2`；Dockerfile `COPY frontend/ frontend/` 依赖该目录已存在真实构建。
- 动作：上线前在 CI/本地跑一次 `vite build`，确认 `平台开发/frontend/v2/index.html` 存在且非空。
- 好消息：网页端 API base 已是**相对路径** `const API_BASE = '/api'`（`react-vite/src/services/api.js:1`），Flask 同源提供静态+API，无需改前端即可工作。

---

## 三、安全与配置

### 9. 固定 SECRET_KEY / 微信密钥走环境变量
- `app.py:18357` 仅 `app.run(...)`，未见显式 `SECRET_KEY`。Flask 未设则每次随机 → 会话/JWT 签名不稳定、重启即失效。
- 微信 AppSecret / Token 同样应走 env，**勿硬编码、勿入库**。
- 动作：compose `environment` 注入 `SECRET_KEY`、`WECHAT_APPSECRET` 等；`.env` 加入 `.gitignore`。

### 10. 收紧 CORS
- `app.py:195` `CORS(app)` 默认放行所有源（`*`）。
- 生产若网页端与 API 同源（均由 Flask/Nginx 同域提供）可关闭 CORS；若跨域则 `CORS(app, origins=["https://你的域名"])`。

### 11. 主数据写接口门禁复核
- 既有约定：设备/试剂/车辆/备件库存/回收/计划配置等写操作 = admin 专属（`require_admin()`）。
- 上线前回归确认所有写端点门禁生效；复核文件上传大小/类型限制（记忆：<200 字节拒、解码后大小校验）。

### 12. 初始管理员账号
- 确认存在已知 admin 账号与强密码；清理/改掉任何默认或弱口令。

---

## 四、韧性与运维

### 13. 进程自愈与健康检查
- compose 已有 `restart: always`（好）。补充：
  - 加 `healthcheck` 探 `http://localhost:5000/api/health`（该端点已存在）。
  - Flask dev server（`app.run`）建议换 **gunicorn**（`gunicorn -w 4 -b 0.0.0.0:5000 app:app`）提升并发与稳定。

### 14. 日志与监控
- 结构化日志 + 轮转（避免单文件无限增长）。
- 外部 uptime 监控定时探 `/api/health` 并告警（如 UptimeRobot / 云监控）。

### 15. 回滚方案
- `git tag` 打 release 版本；部署前备份 DB + uploads。
- 镜像打 tag（如 `water-monitor-app:2026.07.29`），异常时 `docker compose up` 回退上一镜像。

### 16. 基础限流
- 登录、订阅等端点加基础限流，防爆破。

---

## 五、上线前验证（收尾）

### 17. 端到端冒烟（走 HTTPS 域名）
- 完整链路：数据产生 → 审核 → 告警 → 工单 → 关单，网页端 + 小程序真机各跑一遍。
- 重点验证：照片上传落在持久卷（重建容器后仍在）、订阅消息可达。

### 18. 微信订阅消息真机验证
- 确认正式号推送可达（记忆：刘娜需真机重新点订阅模板，额度曾耗光）。

### 19. DNS 与证书
- A 记录指向服务器 IP，等待生效；证书签发 + 自动续期验证。

### 20. 交付文档
- 部署手册（compose 启动 / 备份 / 回滚）、账号清单、运维须知。

---

## 附：合规 docker-compose.yml 参考（替换坏文件）
```yaml
services:
  water-monitor:
    build: .
    image: water-monitor-app:latest
    container_name: water-monitor
    restart: always
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - WECHAT_APPSECRET=${WECHAT_APPSECRET}
    ports:
      - "5000:5000"
    volumes:
      - ./backend/data:/app/data          # SQLite
      - ./uploads:/frontend/uploads       # 上传照片（关键！原文件漏了）
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```
> 注：容器内 uploads 路径需与实际 `UPLOAD_DIR`（`/frontend/uploads`）对齐；若改用 Nginx 反代，宿主机端口可不对外暴露 5000，仅暴露 443。
