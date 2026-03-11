#!/usr/bin/env bash
# LinChat 服务管理脚本
# 用法: ./scripts/services.sh {start|stop|restart|status}
#
# 通过 PID 文件追踪所有进程，避免孤儿进程积累

set -euo pipefail

PROJECT_DIR="/home/dantsinghua/work/linchat"
VENV_DIR="$PROJECT_DIR/linchat"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
PID_DIR="$PROJECT_DIR/.pids"
LOG_DIR="/tmp"

# PID 文件
PID_BACKEND="$PID_DIR/backend.pid"
PID_CELERY_WORKER="$PID_DIR/celery-worker.pid"
PID_CELERY_BEAT="$PID_DIR/celery-beat.pid"
PID_FRONTEND="$PID_DIR/frontend.pid"

mkdir -p "$PID_DIR"

# ============ 工具函数 ============

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# 杀掉 PID 文件指向的进程及其子进程
kill_by_pidfile() {
    local pidfile="$1"
    local name="$2"
    if [[ -f "$pidfile" ]]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            # 杀掉进程组（含子进程）
            kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
            sleep 1
            # 如果还活着，强制杀
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 -- -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
            fi
            log "$name (PID $pid) 已停止"
        else
            log "$name (PID $pid) 已不存在"
        fi
        rm -f "$pidfile"
    fi
}

# 清理所有可能的孤儿进程（兜底）
kill_orphans() {
    local pids
    pids=$(pgrep -f "uvicorn core.asgi.*8002" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "清理孤儿 uvicorn: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
    fi

    pids=$(pgrep -f "celery -A core" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "清理孤儿 celery: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
    fi

    # 只杀 linchat 前端的 next-server (v14)
    pids=$(pgrep -f "next-server.*v14" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "清理孤儿 frontend: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
    fi
}

check_process() {
    local pidfile="$1"
    local name="$2"
    if [[ -f "$pidfile" ]]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  $name: 运行中 (PID $pid)"
            return 0
        else
            echo "  $name: 已停止 (PID 文件过期)"
            rm -f "$pidfile"
            return 1
        fi
    else
        echo "  $name: 未启动"
        return 1
    fi
}

# ============ 启动 ============

do_start() {
    log "启动 LinChat 服务..."

    # 激活虚拟环境
    source "$VENV_DIR/bin/activate"

    # 后端
    if [[ -f "$PID_BACKEND" ]] && kill -0 "$(cat "$PID_BACKEND")" 2>/dev/null; then
        log "后端已在运行 (PID $(cat "$PID_BACKEND"))"
    else
        cd "$BACKEND_DIR"
        PYTHONUNBUFFERED=1 setsid uvicorn core.asgi:application \
            --host 0.0.0.0 --port 8002 \
            > "$LOG_DIR/linchat-backend.log" 2>&1 &
        echo $! > "$PID_BACKEND"
        log "后端已启动 (PID $!)"
    fi

    # Celery Worker
    if [[ -f "$PID_CELERY_WORKER" ]] && kill -0 "$(cat "$PID_CELERY_WORKER")" 2>/dev/null; then
        log "Celery Worker 已在运行 (PID $(cat "$PID_CELERY_WORKER"))"
    else
        cd "$BACKEND_DIR"
        setsid celery -A core worker --loglevel=info \
            > "$LOG_DIR/linchat-celery-worker.log" 2>&1 &
        echo $! > "$PID_CELERY_WORKER"
        log "Celery Worker 已启动 (PID $!)"
    fi

    # Celery Beat
    if [[ -f "$PID_CELERY_BEAT" ]] && kill -0 "$(cat "$PID_CELERY_BEAT")" 2>/dev/null; then
        log "Celery Beat 已在运行 (PID $(cat "$PID_CELERY_BEAT"))"
    else
        cd "$BACKEND_DIR"
        setsid celery -A core beat --loglevel=info \
            > "$LOG_DIR/linchat-celery-beat.log" 2>&1 &
        echo $! > "$PID_CELERY_BEAT"
        log "Celery Beat 已启动 (PID $!)"
    fi

    # 前端
    if [[ -f "$PID_FRONTEND" ]] && kill -0 "$(cat "$PID_FRONTEND")" 2>/dev/null; then
        log "前端已在运行 (PID $(cat "$PID_FRONTEND"))"
    else
        cd "$FRONTEND_DIR"
        setsid npm run start -- -p 3784 \
            > "$LOG_DIR/linchat-frontend.log" 2>&1 &
        echo $! > "$PID_FRONTEND"
        log "前端已启动 (PID $!)"
    fi

    sleep 2
    log "所有服务已启动"
}

# ============ 停止 ============

do_stop() {
    log "停止 LinChat 服务..."
    kill_by_pidfile "$PID_FRONTEND" "前端"
    kill_by_pidfile "$PID_CELERY_BEAT" "Celery Beat"
    kill_by_pidfile "$PID_CELERY_WORKER" "Celery Worker"
    kill_by_pidfile "$PID_BACKEND" "后端"
    # 兜底清理孤儿进程
    kill_orphans
    sleep 1
    log "所有服务已停止"
}

# ============ 状态 ============

do_status() {
    echo "LinChat 服务状态:"
    check_process "$PID_BACKEND" "后端 (uvicorn:8002)" || true
    check_process "$PID_CELERY_WORKER" "Celery Worker" || true
    check_process "$PID_CELERY_BEAT" "Celery Beat" || true
    check_process "$PID_FRONTEND" "前端 (next:3784)" || true

    echo ""
    echo "Docker 服务:"
    cd "$PROJECT_DIR"
    docker compose ps --format "  {{.Name}}: {{.Status}}" 2>/dev/null || echo "  (docker compose 不可用)"
}

# ============ 主入口 ============

case "${1:-}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    status)  do_status ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
