#!/usr/bin/env bash
#
# TradingAgents 开发服务器一键管理脚本
#
# 用法:
#   ./scripts/dev.sh          启动前后端（前台，Ctrl+C 退出）
#   ./scripts/dev.sh start     后台启动
#   ./scripts/dev.sh stop      停止
#   ./scripts/dev.sh restart   重启
#   ./scripts/dev.sh status    查看状态
#   ./scripts/dev.sh logs      查看日志（backend/frontend/all）
#
set -euo pipefail

# ── 路径配置 ──────────────────────────────────────────────
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT_DIR/.dev-pids"
LOG_DIR="$ROOT_DIR/.dev-logs"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

# ── 端口配置 ──────────────────────────────────────────────
BACKEND_PORT="${TRADINGAGENTS_API_PORT:-8422}"
FRONTEND_PORT="${VITE_PORT:-5173}"

# ── Python 配置 ──────────────────────────────────────────
PYTHON=""
for candidate in "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/.venv/bin/python3"; do
  if [ -x "$candidate" ]; then
    PYTHON="$candidate"
    break
  fi
done

# ── 颜色 ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
header(){ echo -e "\n${CYAN}═══ $* ═══${NC}\n"; }

# ── 前置检查 ──────────────────────────────────────────────
precheck() {
  mkdir -p "$PID_DIR" "$LOG_DIR"

  if [ -z "$PYTHON" ]; then
    error "未找到 Python 虚拟环境 (.venv)"
    echo "  请先运行: uv venv && source .venv/bin/activate && uv pip install -e ."
    exit 1
  fi

  if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    warn "前端依赖未安装，正在执行 npm install ..."
    (cd "$ROOT_DIR/frontend" && npm install)
  fi
}

# ── 检查端口是否被占用 ───────────────────────────────────
port_in_use() {
  local port="$1"
  lsof -ti:"$port" >/dev/null 2>&1
}

# ── 检查进程是否存活 ─────────────────────────────────────
is_running() {
  local pid_file="$1"
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

# ── 启动后端 ──────────────────────────────────────────────
start_backend() {
  if is_running "$BACKEND_PID_FILE"; then
    warn "后端已在运行 (PID: $(cat "$BACKEND_PID_FILE"))"
    return 0
  fi

  # 如果端口被其他进程占用，提示
  if port_in_use "$BACKEND_PORT"; then
    warn "端口 $BACKEND_PORT 已被占用，尝试使用（可能是之前的残留进程）"
  fi

  info "启动后端 → http://127.0.0.1:${BACKEND_PORT}"
  cd "$ROOT_DIR"
  nohup "$PYTHON" -m tradingagents.api.server \
    > "$BACKEND_LOG" 2>&1 &
  local pid=$!
  echo "$pid" > "$BACKEND_PID_FILE"

  # 等待后端就绪（最多 15 秒）
  info "等待后端就绪 ..."
  local i=0
  while [ $i -lt 30 ]; do
    if curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/v1/health" >/dev/null 2>&1; then
      info "后端就绪 ✓ (PID: $pid)"
      return 0
    fi
    # 检查进程是否意外退出
    if ! kill -0 "$pid" 2>/dev/null; then
      error "后端启动失败，查看日志: $BACKEND_LOG"
      tail -20 "$BACKEND_LOG" 2>/dev/null
      rm -f "$BACKEND_PID_FILE"
      return 1
    fi
    sleep 0.5
    i=$((i + 1))
  done

  warn "后端启动超时（进程仍在运行，可能还在初始化）"
  warn "日志: $BACKEND_LOG"
}

# ── 启动前端 ──────────────────────────────────────────────
start_frontend() {
  if is_running "$FRONTEND_PID_FILE"; then
    warn "前端已在运行 (PID: $(cat "$FRONTEND_PID_FILE"))"
    return 0
  fi

  info "启动前端 → http://localhost:${FRONTEND_PORT}"
  cd "$ROOT_DIR/frontend"
  nohup npx vite --port "$FRONTEND_PORT" \
    > "$FRONTEND_LOG" 2>&1 &
  local pid=$!
  echo "$pid" > "$FRONTEND_PID_FILE"

  # 等待前端就绪（最多 10 秒）
  info "等待前端就绪 ..."
  local i=0
  while [ $i -lt 20 ]; do
    if curl -sf "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
      info "前端就绪 ✓ (PID: $pid)"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      error "前端启动失败，查看日志: $FRONTEND_LOG"
      tail -20 "$FRONTEND_LOG" 2>/dev/null
      rm -f "$FRONTEND_PID_FILE"
      return 1
    fi
    sleep 0.5
    i=$((i + 1))
  done

  warn "前端启动超时（进程仍在运行，可能还在构建）"
  warn "日志: $FRONTEND_LOG"
}

# ── 停止单个进程 ─────────────────────────────────────────
stop_pid() {
  local pid_file="$1"
  local name="$2"

  if [ ! -f "$pid_file" ]; then
    return 0
  fi

  local pid
  pid="$(cat "$pid_file")"

  if kill -0 "$pid" 2>/dev/null; then
    info "停止 $name (PID: $pid) ..."
    # 优雅关闭：先 SIGTERM，等 3 秒，再 SIGKILL
    kill -TERM "$pid" 2>/dev/null || true
    local i=0
    while [ $i -lt 30 ]; do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
      i=$((i + 1))
    done
    # 如果还活着，强制杀
    if kill -0 "$pid" 2>/dev/null; then
      warn "$name 未响应 SIGTERM，强制终止"
      kill -9 "$pid" 2>/dev/null || true
      sleep 0.5
      # 杀掉子进程
      pkill -P "$pid" 2>/dev/null || true
    fi
    info "$name 已停止 ✓"
  else
    info "$name 未在运行"
  fi

  rm -f "$pid_file"
}

# ── 命令: start ───────────────────────────────────────────
cmd_start() {
  header "启动 TradingAgents 开发服务"
  precheck
  start_backend
  start_frontend
  echo
  info "全栈已启动！"
  echo -e "  ${CYAN}前端${NC}:  http://localhost:${FRONTEND_PORT}"
  echo -e "  ${CYAN}后端${NC}:  http://127.0.0.1:${BACKEND_PORT}"
  echo -e "  ${CYAN}API 文档${NC}: http://127.0.0.1:${BACKEND_PORT}/docs"
  echo -e "  日志: tail -f $LOG_DIR/{backend,frontend}.log"
  echo -e "  停止: ./scripts/dev.sh stop"
  echo
}

# ── 命令: stop ────────────────────────────────────────────
cmd_stop() {
  header "停止 TradingAgents 开发服务"
  stop_pid "$FRONTEND_PID_FILE" "前端"
  stop_pid "$BACKEND_PID_FILE"  "后端"
  echo
  info "所有服务已停止"
  echo
}

# ── 命令: restart ─────────────────────────────────────────
cmd_restart() {
  cmd_stop
  cmd_start
}

# ── 命令: status ──────────────────────────────────────────
cmd_status() {
  header "服务状态"

  # 后端
  if is_running "$BACKEND_PID_FILE"; then
    local bpid
    bpid="$(cat "$BACKEND_PID_FILE")"
    if curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/v1/health" >/dev/null 2>&1; then
      echo -e "  ${GREEN}●${NC} 后端  PID:$bpid  端口:$BACKEND_PORT  状态: healthy"
    else
      echo -e "  ${YELLOW}●${NC} 后端  PID:$bpid  端口:$BACKEND_PORT  状态: 运行中但未响应"
    fi
  else
    echo -e "  ${RED}○${NC} 后端  未运行"
  fi

  # 前端
  if is_running "$FRONTEND_PID_FILE"; then
    local fpid
    fpid="$(cat "$FRONTEND_PID_FILE")"
    if curl -sf "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
      echo -e "  ${GREEN}●${NC} 前端  PID:$fpid  端口:$FRONTEND_PORT  状态: serving"
    else
      echo -e "  ${YELLOW}●${NC} 前端  PID:$fpid  端口:$FRONTEND_PORT  状态: 运行中但未响应"
    fi
  else
    echo -e "  ${RED}○${NC} 前端  未运行"
  fi
  echo
}

# ── 命令: logs ────────────────────────────────────────────
cmd_logs() {
  local target="${1:-all}"
  case "$target" in
    backend|b)
      info "后端日志 (Ctrl+C 退出):"
      tail -f "$BACKEND_LOG"
      ;;
    frontend|f)
      info "前端日志 (Ctrl+C 退出):"
      tail -f "$FRONTEND_LOG"
      ;;
    all|*)
      info "全部日志 (Ctrl+C 退出):"
      tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
      ;;
  esac
}

# ── 命令: 前台运行 ────────────────────────────────────────
cmd_foreground() {
  header "前台启动 TradingAgents（Ctrl+C 退出）"
  precheck

  cleanup() {
    echo
    info "收到退出信号，正在清理 ..."
    stop_pid "$FRONTEND_PID_FILE" "前端"
    stop_pid "$BACKEND_PID_FILE"  "后端"
    exit 0
  }
  trap cleanup INT TERM

  # 后台启动后端
  start_backend

  # 前台启动前端（日志直接输出到终端）
  info "启动前端（前台模式）→ http://localhost:${FRONTEND_PORT}"
  cd "$ROOT_DIR/frontend"
  echo "$$" > "$FRONTEND_PID_FILE"
  npx vite --port "$FRONTEND_PORT" &
  local fe_pid=$!
  echo "$fe_pid" > "$FRONTEND_PID_FILE"

  # 等待前端退出
  wait "$fe_pid" 2>/dev/null || true
  cleanup
}

# ── 主入口 ────────────────────────────────────────────────
case "${1:-fg}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "${1:-all}" ;;
  fg|"")   cmd_foreground ;;
  *)
    echo "用法: $0 {fg|start|stop|restart|status|logs [backend|frontend|all]}"
    echo
    echo "命令:"
    echo "  fg (默认)     前台启动前后端，Ctrl+C 统一退出"
    echo "  start         后台启动，日志写入 .dev-logs/"
    echo "  stop          停止所有服务"
    echo "  restart       重启所有服务"
    echo "  status        查看运行状态"
    echo "  logs [target] 实时查看日志 (backend/frontend/all)"
    exit 1
    ;;
esac
