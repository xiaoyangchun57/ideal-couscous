#!/bin/sh
# 飞牛「零令牌轮询部署器」入口脚本
#
# 作用：定时检查 GitHub 仓库 acceptance 分支是否有新提交，有则拉代码并重建验收容器。
# 前提：本仓库是 PUBLIC，git clone / fetch 不需要任何凭证；因此不需要 GitHub runner 注册令牌，
#       也不需要仓库管理员权限。
#
# 关键约定：挂载时把飞牛宿主机目录挂到「容器内同名路径」
#   /vol1/@team/水质设备运维 : /vol1/@team/水质设备运维
# 这样容器内 docker compose 传给宿主机 Docker daemon 的 build context 路径，
# 在宿主机上也是真实存在的，不会因为「容器内路径 ≠ 宿主机路径」而构建失败。

set -eu
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

BASE="/vol1/@team/水质设备运维"
REPO="$BASE/repo"
COMPOSE="$REPO/平台开发/docker-compose.acceptance.yml"
REMOTE="https://github.com/xiaoyangchun57/ideal-couscous.git"
BRANCH="acceptance"
INTERVAL="${POLL_INTERVAL:-60}"
ACCEPT_URL="http://192.168.2.250:8080/api/health"

echo "[poller] 启动：分支=$BRANCH 轮询间隔=${INTERVAL}s 目标=$BASE"

# 1) alpine 基础镜像很小，首次启动装 git + docker CLI + compose 插件（之后容器不重启就不会重复）
while ! apk add --no-cache --quiet git docker-cli docker-cli-compose curl; do
  echo "[poller] 安装依赖失败，30 秒后重试"
  sleep 30
done
echo "[poller] 依赖就绪：$(docker compose version 2>/dev/null || echo 'compose 插件待确认')"

mkdir -p "$BASE/data" "$BASE/uploads"

# 2) 首次克隆（公开仓库，无需 token）
if [ ! -d "$REPO/.git" ]; then
  until git clone --depth 1 --branch "$BRANCH" "$REMOTE" "$REPO"; do
    echo "[poller] clone 失败，30 秒后重试"
    sleep 30
  done
  echo "[poller] 首次克隆完成"
fi

# 3) 轮询：只在远端 HEAD 变化时才重建，避免无谓构建
LAST=""
while true; do
  if git -C "$REPO" fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1; then
    HEAD=$(git -C "$REPO" rev-parse FETCH_HEAD)
    if [ "$HEAD" != "$LAST" ]; then
      echo "[poller] 检测到新提交 ${HEAD}，开始重建验收容器"
      git -C "$REPO" reset --hard FETCH_HEAD >/dev/null 2>&1 || true
      if docker compose -f "$COMPOSE" -p water-accept up -d --build; then
        echo "[poller] 构建命令已成功，等待健康检查 $ACCEPT_URL"
        OK=0
        i=0
        while [ "$i" -lt 36 ]; do
          if curl -fsS "$ACCEPT_URL" >/dev/null 2>&1; then OK=1; break; fi
          i=$((i + 1))
          sleep 5
        done
        if [ "$OK" = "1" ]; then
          echo "[poller] DEPLOY_OK  ${HEAD}  验收地址 http://192.168.2.250:8080"
          LAST="$HEAD"
        else
          echo "[poller] 容器已启动但健康检查未通过，输出最近日志："
          docker compose -f "$COMPOSE" -p water-accept logs --tail=40 || true
          echo "[poller] 保留 LAST 为空，下轮将重试"
        fi
      else
        echo "[poller] docker compose 构建失败，下轮重试"
        LAST=""
      fi
    fi
  else
    echo "[poller] git fetch 失败（网络波动？），下轮重试"
  fi
  sleep "$INTERVAL"
done
