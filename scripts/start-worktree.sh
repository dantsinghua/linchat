#!/usr/bin/env bash
# 从当前 worktree 启动 LinChat 前后端服务（验证期使用）
#
# 场景：refactor 批次验证。当前 worktree 代码独立运行，
# 但不写新 nohup（CLAUDE.md 约定），不污染主 services.sh。
#
# 实现：sed 动态替换主 services.sh 中 PROJECT_DIR → 当前 worktree，
# 但保持 VENV_DIR 指向主 worktree（venv 只存在于主 worktree）。
# 临时 services 脚本写到 /tmp，exit 时清理。
#
# 用法:
#   ./scripts/start-worktree.sh [start|stop|restart|status]
#   默认 restart
#
# 固化的前置自动修复（按历史踩坑清单）:
#   1. backend/.env 缺 → symlink 主 worktree 的
#   2. frontend/node_modules 缺 → symlink 主 worktree 的
#   3. frontend/.env.local 缺 → 复制（不 symlink，历史上 ln 不灵）
#   4. frontend/.next 缺 BUILD_ID（ESLint fail 残缺产物）→ rm -rf + rebuild
#   5. frontend/.env.local mtime > .next 构建时间 → rebuild（NEXT_PUBLIC_* 是 build-time 注入）
#   6. 基础设施健康检查（docker postgres/redis + frpc visitor 8100）
#
# 不自动修复、仅诊断：
#   - RegisteredDevice token SM4 解密异常 → 日志警告，让人工处理
#   - ESLint 未用 import（batch 合并残留）→ 日志警告，让人工处理

set -euo pipefail

WORKTREE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAIN_DIR="/home/dantsinghua/work/linchat"
MAIN_VENV="$MAIN_DIR/linchat"
MAIN_SVC="$MAIN_DIR/scripts/services.sh"
ACTION="${1:-restart}"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
warn() { echo "[$(date '+%H:%M:%S')] ⚠️  $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ❌ $*" >&2; exit 1; }

# ============ 前置检查 ============

[[ -f "$MAIN_SVC" ]]              || die "主 services.sh 不存在：$MAIN_SVC"
[[ -f "$MAIN_VENV/bin/activate" ]] || die "主 venv 不存在：$MAIN_VENV"

if [[ "$WORKTREE_DIR" == "$MAIN_DIR" ]]; then
    warn "当前在主 worktree，直接用 $MAIN_SVC 即可"
    exec "$MAIN_SVC" "$ACTION"
fi

# ============ 基础设施健康检查（仅 start/restart 前做） ============

check_infrastructure() {
    log "==> [0/5] 基础设施健康检查"

    # Docker 基础服务（postgres/redis/clickhouse 等容器）
    local dc_dir="$MAIN_DIR"
    if [[ -f "$dc_dir/docker-compose.yml" ]]; then
        local running unhealthy
        running=$(cd "$dc_dir" && docker compose ps --services --filter "status=running" 2>/dev/null | wc -l)
        unhealthy=$(docker ps --filter "health=unhealthy" --format '{{.Names}}' 2>/dev/null | grep -c linchat- || true)
        log "    docker 服务：$running 个容器运行中"
        [[ "$unhealthy" -gt 0 ]] && warn "$unhealthy 个容器 unhealthy（可能影响 DB/Redis）"
    fi

    # PostgreSQL 端口
    if ! timeout 2 bash -c '</dev/tcp/127.0.0.1/5432' 2>/dev/null; then
        warn "PostgreSQL (5432) 不可达，登录/聊天/声纹会失败"
    fi

    # Redis 端口
    if ! timeout 2 bash -c '</dev/tcp/127.0.0.1/6379' 2>/dev/null; then
        warn "Redis (6379) 不可达，Session/SSE/Channels 会失败"
    fi

    # MinIO 端口（9000）
    if ! timeout 2 bash -c '</dev/tcp/127.0.0.1/9000' 2>/dev/null; then
        warn "MinIO (9000) 不可达，媒体上传会失败（影响 ambient 音频持久化）"
    fi

    # LLM Gateway STCP visitor（127.0.0.1:8100）
    if ! timeout 2 bash -c '</dev/tcp/127.0.0.1/8100' 2>/dev/null; then
        warn "LLM Gateway (8100) 不可达，ASR/TTS/文档解析/Embedding 全部失败"
        warn "  → 检查 frpc：pgrep -a frpc；tail /home/dantsinghua/frp/frpc.log"
    fi
}

# ============ worktree env 文件修复 ============

ensure_backend_env() {
    local target="$WORKTREE_DIR/backend/.env"
    local source="$MAIN_DIR/backend/.env"
    if [[ -L "$target" && ! -e "$target" ]]; then
        log "    backend/.env symlink 悬空 → 删除重建"
        rm -f "$target"
    fi
    if [[ ! -e "$target" ]]; then
        [[ -f "$source" ]] || die "主 worktree backend/.env 不存在：$source"
        log "    backend/.env 缺失 → symlink 主 worktree"
        ln -s "$source" "$target"
    fi
}

ensure_frontend_node_modules() {
    local target="$WORKTREE_DIR/frontend/node_modules"
    local source="$MAIN_DIR/frontend/node_modules"
    if [[ -L "$target" && ! -e "$target" ]]; then
        log "    node_modules symlink 悬空 → 删除重建"
        rm -f "$target"
    fi
    if [[ ! -e "$target" ]]; then
        [[ -d "$source" ]] || die "主 worktree frontend/node_modules 不存在，请先到主 worktree: cd frontend && npm install"
        log "    node_modules 缺失 → symlink 主 worktree"
        ln -s "$source" "$target"
    fi
}

ensure_frontend_env_local() {
    local target="$WORKTREE_DIR/frontend/.env.local"
    local source="$MAIN_DIR/frontend/.env.local"
    # 历史上 .env.local symlink 曾失败（即使 Next.js 理论支持），这里强制用真实文件
    if [[ -L "$target" ]]; then
        log "    .env.local 是 symlink（历史上不稳），改为真实文件副本"
        local tmp="$target.tmp.$$"
        cp -L "$target" "$tmp" && mv "$tmp" "$target"
    fi
    if [[ ! -f "$target" ]]; then
        [[ -f "$source" ]] || die "主 worktree frontend/.env.local 不存在：$source"
        log "    .env.local 缺失 → 复制主 worktree（真实文件）"
        cp "$source" "$target"
    fi
}

# ============ 前端 build 判定（三条独立条件） ============

need_rebuild_frontend() {
    local nextdir="$WORKTREE_DIR/frontend/.next"
    local envfile="$WORKTREE_DIR/frontend/.env.local"

    # 条件 1：.next 目录不存在
    [[ ! -d "$nextdir" ]] && { echo ".next 目录不存在"; return 0; }

    # 条件 2：缺 BUILD_ID（上次 ESLint fail 留下的残缺产物）
    [[ ! -f "$nextdir/BUILD_ID" ]] && { echo ".next 残缺（缺 BUILD_ID，可能上次 build 中断）"; return 0; }

    # 条件 3：.env.local 比 .next 新（NEXT_PUBLIC_* 是 build-time 注入，必须重 build）
    if [[ -f "$envfile" && "$envfile" -nt "$nextdir/BUILD_ID" ]]; then
        echo ".env.local 比上次 build 新（NEXT_PUBLIC_* 改过需重 build）"
        return 0
    fi

    return 1
}

rebuild_frontend() {
    local reason="$1"
    log "    原因：$reason"
    log "    执行 rm -rf .next && npm run build（1-2 分钟）"
    rm -rf "$WORKTREE_DIR/frontend/.next"
    (cd "$WORKTREE_DIR/frontend" && npm run build) || die "npm run build 失败（检查 ESLint 残留、TS 错误）"
}

# ============ 主流程 ============

if [[ "$ACTION" != "status" && "$ACTION" != "stop" ]]; then
    check_infrastructure

    log "==> [1/5] 修复 worktree 配置文件"
    ensure_backend_env
    ensure_frontend_node_modules
    ensure_frontend_env_local

    log "==> [2/5] 前端 build 判定"
    if reason=$(need_rebuild_frontend); then
        rebuild_frontend "$reason"
    else
        log "    .next 已存在且完整，跳过 build"
    fi

    log "==> [3/5] 停掉主 worktree 的现有服务（防端口冲突）"
    "$MAIN_SVC" stop || true
fi

# 生成临时 services 脚本（PROJECT_DIR → worktree，VENV_DIR → 主 venv）
TEMP_SVC="$(mktemp /tmp/services-worktree.XXXXXX.sh)"
trap 'rm -f "$TEMP_SVC"' EXIT

sed \
    -e "s|^PROJECT_DIR=.*|PROJECT_DIR=\"$WORKTREE_DIR\"|" \
    -e "s|^VENV_DIR=.*|VENV_DIR=\"$MAIN_VENV\"|" \
    "$MAIN_SVC" > "$TEMP_SVC"
chmod +x "$TEMP_SVC"

log "==> [4/5] 执行 $ACTION（代码来源：$WORKTREE_DIR）"
"$TEMP_SVC" "$ACTION"

if [[ "$ACTION" == "start" || "$ACTION" == "restart" ]]; then
    sleep 2
    log "==> [5/5] 启动后 3 秒健康检查"
    # 后端端口
    if timeout 3 bash -c '</dev/tcp/127.0.0.1/8002' 2>/dev/null; then
        log "    后端 8002 ✅"
    else
        warn "后端 8002 未监听，检查 /tmp/linchat-backend.log"
    fi
    # 前端端口
    if timeout 3 bash -c '</dev/tcp/127.0.0.1/3784' 2>/dev/null; then
        log "    前端 3784 ✅"
    else
        warn "前端 3784 未监听，检查 /tmp/linchat-frontend.log（典型：.next 缺 BUILD_ID）"
    fi
    echo ""
    log "✅ 启动完成（代码版本：$(cd "$WORKTREE_DIR" && git log -1 --oneline)）"
    echo ""
    echo "日志监控："
    echo "  tail -f /tmp/linchat-backend.log | grep -E 'ASR reconnect|ASR WS closed|ASR error'"
    echo ""
    echo "停止服务："
    echo "  $WORKTREE_DIR/scripts/start-worktree.sh stop"
    echo ""
    echo "PID 文件: $WORKTREE_DIR/.pids/"
fi
