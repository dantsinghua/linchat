#!/bin/bash
# batch-01 worktree 测试启动脚本
# 用法: ./scripts/start-test.sh [start|stop|status]
set -e

WORKTREE="/home/dantsinghua/work/linchat-p0-fix"
VENV="/home/dantsinghua/work/linchat/linchat/bin/activate"
BACKEND_PORT=8002
FRONTEND_PORT=3784
BACKEND_LOG="/tmp/batch01-backend.log"
FRONTEND_LOG="/tmp/batch01-frontend.log"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

stop_services() {
    log "停止服务..."
    lsof -ti:$BACKEND_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
    lsof -ti:$FRONTEND_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
    sleep 1
    log "服务已停止"
}

start_services() {
    stop_services

    log "启动后端 (port $BACKEND_PORT)..."
    source "$VENV"
    cd "$WORKTREE/backend"
    PYTHONUNBUFFERED=1 nohup uvicorn core.asgi:application --host 0.0.0.0 --port $BACKEND_PORT > "$BACKEND_LOG" 2>&1 &
    log "后端 PID=$!"

    log "准备前端 standalone..."
    cd "$WORKTREE/frontend"
    # standalone 模式需要手动复制静态文件
    cp -r .next/static .next/standalone/.next/static 2>/dev/null || true
    cp -r public .next/standalone/public 2>/dev/null || true

    log "启动前端 (port $FRONTEND_PORT)..."
    cd "$WORKTREE/frontend/.next/standalone"
    PORT=$FRONTEND_PORT HOSTNAME=0.0.0.0 nohup node server.js > "$FRONTEND_LOG" 2>&1 &
    log "前端 PID=$!"

    sleep 5

    log "=== 状态检查 ==="
    if fuser $BACKEND_PORT/tcp > /dev/null 2>&1; then
        log "后端: 运行中 ✅"
    else
        log "后端: 启动失败! 查看 $BACKEND_LOG"
    fi
    if fuser $FRONTEND_PORT/tcp > /dev/null 2>&1; then
        log "前端: 运行中 ✅"
    else
        log "前端: 启动失败! 查看 $FRONTEND_LOG"
    fi
}

show_status() {
    echo "=== 端口监听 ==="
    lsof -i:$BACKEND_PORT -i:$FRONTEND_PORT -sTCP:LISTEN 2>/dev/null || echo "无服务运行"
    echo ""
    echo "=== 后端日志 (最后 5 行) ==="
    tail -5 "$BACKEND_LOG" 2>/dev/null || echo "无日志"
    echo ""
    echo "=== 前端日志 (最后 5 行) ==="
    tail -5 "$FRONTEND_LOG" 2>/dev/null || echo "无日志"
}

case "${1:-start}" in
    start)  start_services ;;
    stop)   stop_services ;;
    status) show_status ;;
    *)      echo "用法: $0 [start|stop|status]" ;;
esac
