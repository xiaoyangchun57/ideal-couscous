#!/bin/sh
# 飞牛「零令牌轮询部署器」入口脚本
#
# 作用：定时检查 GitHub 仓库 acceptance 分支是否有新提交，有则拉代码并重建验收容器。
# 前提：本仓库是 PUBLIC，git clone / fetch 不需要任何凭证；因此不需要 GitHub runner 注册令牌，
#       也不需要仓库管理员权限。
#
# 关键约定：挂载时把飞牛宿主机目录挂到「容器内同名路径」
# 2026-11-06：验收根目录迁到 /vol2/@team/Docker/水质智慧运维平台本地验收，
# 并由本脚本统一注入 ACCEPT_DATA_ROOT（compose 里不再硬编码挂载源路径）。
#   /vol2/@team/Docker/水质智慧运维平台本地验收 : /vol2/@team/Docker/水质智慧运维平台本地验收
# 这样容器内 docker compose 传给宿主机 Docker daemon 的 build context 路径，
# 在宿主机上也是真实存在的，不会因为「容器内路径 ≠ 宿主机路径」而构建失败。
#
# 2026-11-05 更新（镜像生命周期）：
#   - 每次部署成功后自动清理悬空镜像与过期构建缓存，避免 --build 反复留下 <none> 镜像占磁盘。
#   - 部署成功的 commit SHA 落盘，轮询器容器重启后不会无谓重建，也就不会多产生悬空镜像。
#   - 注意：容器 CMD 指向飞牛本地本文件，改脚本必须同时更新仓库副本并覆盖本文件。

set -eu
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

BASE="/vol2/@team/Docker/水质智慧运维平台本地验收"
# 数据根目录只在这里定义一次。compose 用 ${ACCEPT_DATA_ROOT:-<默认值>} 插值，
# CI 把它设成 /tmp/water-accept-ci。以后再换目录，只改这一行
# （外加 deploy-feiniu-poller.yml 的同名挂载与容器 CMD），仓库与 CI 都不用动。
export ACCEPT_DATA_ROOT="$BASE"

REPO="$BASE/repo"
COMPOSE="$REPO/平台开发/docker-compose.acceptance.yml"
STATE="$BASE/.accept-deployed-sha"
REMOTE="https://github.com/xiaoyangchun57/ideal-couscous.git"
BRANCH="acceptance"
INTERVAL="${POLL_INTERVAL:-60}"
ACCEPT_URL="http://192.168.2.250:8080/api/health"

# 目录若是用飞牛「文件管理」复制/搬迁过来的，文件属主会变成 NAS 用户（如 uid 1005）；
# 而容器内 git 以 root 运行，Git 2.35+ 会因 "dubious ownership" 拒绝操作，
# 表现为轮询器一直打印 git fetch 失败却永不重建。这里显式放行本仓库，
# 以后再换目录也不会因为属主变化把部署卡死。
git config --global --add safe.directory "$REPO" 2>/dev/null || true

echo "[poller] 启动：分支=$BRANCH 轮询间隔=${INTERVAL}s 目标=$BASE"

# 1) alpine 基础镜像很小，首次启动装 git + docker CLI + compose 插件（之后容器不重启就不会重复）
while ! apk add --no-cache --quiet git docker-cli docker-cli-compose curl; do
  echo "[poller] 安装依赖失败，30 秒后重试"
  sleep 30
done
echo "[poller] 依赖就绪：$(docker compose version 2>/dev/null || echo 'compose 插件待确认')"

# R33 之后私有证据目录也要预建（compose 会挂载到 /app/private_uploads）
mkdir -p "$BASE/data" "$BASE/uploads" "$BASE/private_uploads/site_photo_originals"

# 2) 首次克隆（公开仓库，无需 token）
if [ ! -d "$REPO/.git" ]; then
  until git clone --depth 1 --branch "$BRANCH" "$REMOTE" "$REPO"; do
    echo "[poller] clone 失败，30 秒后重试"
    sleep 30
  done
  echo "[poller] 首次克隆完成"
fi

# 3) 重启后恢复上次成功部署的 SHA，避免重复构建（也就不会产生多余悬空镜像）
LAST=""
if [ -f "$STATE" ]; then
  LAST=$(cat "$STATE" 2>/dev/null || true)
  echo "[poller] 恢复上次成功部署的提交：${LAST:-<空>}"
fi

# 只删「无 tag 且无容器引用」的悬空镜像；现役的
# water-monitor-app:acceptance 与 alpine:3.20 都有 tag 且被容器引用，不会被删。
cleanup_images() {
  echo "[poller] 清理悬空镜像与过期构建缓存"
  docker image prune -f --filter "dangling=true" 2>&1 | tail -3 || true
  # 只清理 72 小时前的构建缓存，保留近期缓存加速下次构建
  docker builder prune -f --filter "until=72h" 2>&1 | tail -3 || true
}

# 4) 轮询：只在远端 HEAD 变化时才重建，避免无谓构建
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
          echo "$HEAD" > "$STATE"
          LAST="$HEAD"
          echo "[poller] DEPLOY_OK  ${HEAD}  验收地址 http://192.168.2.250:8080"
          cleanup_images
        else
          echo "[poller] 容器已启动但健康检查未通过，输出最近日志："
          docker compose -f "$COMPOSE" -p water-accept logs --tail=40 || true
          echo "[poller] 不更新部署状态，下轮将重试"
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
