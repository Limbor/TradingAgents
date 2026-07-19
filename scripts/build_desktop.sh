#!/usr/bin/env bash
#
# TradingAgents 桌面版一键构建（macOS 优先，PyInstaller sidecar + Tauri）。
#
# 流程：
#   1. 用 PyInstaller 把 FastAPI 后端打成单文件二进制
#   2. 按 Rust target triple 重命名并放入 src-tauri/binaries/（Tauri sidecar 约定）
#   3. 生成应用图标（若缺失）
#   4. tauri build：前端 build:tauri → 打出 .app / .dmg
#
# 前置依赖（本脚本会检查并在缺失时给出安装指引，但不自动安装系统级工具）：
#   - Rust 工具链（rustup / cargo / rustc）        https://rustup.rs
#   - 项目 Python venv 且已 `pip install pyinstaller`
#   - frontend 依赖已安装（含 @tauri-apps/cli）
#
# 用法：
#   ./scripts/build_desktop.sh            # 完整构建
#   ./scripts/build_desktop.sh backend    # 只打后端 sidecar
#   ./scripts/build_desktop.sh icons      # 只生成图标
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_TAURI="$ROOT_DIR/src-tauri"
FRONTEND="$ROOT_DIR/frontend"
ICON_SOURCE="$ROOT_DIR/assets/TauricResearch.png"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
header(){ echo -e "\n${CYAN}═══ $* ═══${NC}\n"; }

# ── Python venv 探测 ─────────────────────────────────────
PYTHON=""
for candidate in "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/.venv/bin/python3"; do
  [ -x "$candidate" ] && { PYTHON="$candidate"; break; }
done

# ── 前置检查 ─────────────────────────────────────────────
require_rust() {
  if ! command -v cargo >/dev/null 2>&1 || ! command -v rustc >/dev/null 2>&1; then
    error "未找到 Rust 工具链（cargo/rustc）。Tauri 构建强依赖 Rust。"
    echo  "  安装: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
  fi
}

require_python() {
  if [ -z "$PYTHON" ]; then
    error "未找到 Python venv (.venv)。请先: uv venv && uv pip install -e . pyinstaller"
    exit 1
  fi
  if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    error "venv 中未安装 PyInstaller。请: $PYTHON -m pip install pyinstaller"
    exit 1
  fi
}

require_tauri_cli() {
  if [ ! -x "$FRONTEND/node_modules/.bin/tauri" ]; then
    warn "未找到 @tauri-apps/cli，正在安装前端依赖 ..."
    (cd "$FRONTEND" && npm install)
  fi
}

target_triple() {
  # e.g. aarch64-apple-darwin / x86_64-apple-darwin
  rustc -Vv | awk '/^host:/ {print $2}'
}

# ── 步骤：打后端 sidecar ─────────────────────────────────
build_backend() {
  header "① PyInstaller 打包后端 sidecar"
  require_python
  require_rust

  cd "$ROOT_DIR"
  info "运行 PyInstaller ..."
  "$PYTHON" -m PyInstaller packaging/tradingagents-backend.spec \
    --noconfirm \
    --distpath "$ROOT_DIR/dist" \
    --workpath "$ROOT_DIR/build/pyinstaller"

  local triple binary_src binary_dst
  triple="$(target_triple)"
  binary_src="$ROOT_DIR/dist/tradingagents-backend"
  binary_dst="$SRC_TAURI/binaries/tradingagents-backend-$triple"

  if [ ! -f "$binary_src" ]; then
    error "未找到 PyInstaller 产物: $binary_src"
    exit 1
  fi

  mkdir -p "$SRC_TAURI/binaries"
  cp "$binary_src" "$binary_dst"
  chmod +x "$binary_dst"
  info "后端 sidecar 就绪 → $binary_dst"
}

# ── 步骤：生成图标 ───────────────────────────────────────
build_icons() {
  header "② 生成应用图标"
  require_tauri_cli
  if [ ! -f "$ICON_SOURCE" ]; then
    warn "未找到图标源 $ICON_SOURCE，跳过（tauri.conf 引用的 icons/ 需手动提供）"
    return 0
  fi
  cd "$ROOT_DIR"
  "$FRONTEND/node_modules/.bin/tauri" icon "$ICON_SOURCE" --output "$SRC_TAURI/icons"
  info "图标已生成 → $SRC_TAURI/icons"
}

# ── 步骤：tauri build ────────────────────────────────────
build_app() {
  header "③ Tauri 构建（前端 build:tauri + 原生打包）"
  require_rust
  require_tauri_cli
  # 从仓库根运行，Tauri CLI 才能定位 ./src-tauri
  cd "$ROOT_DIR"
  "$FRONTEND/node_modules/.bin/tauri" build
  info "构建完成。产物位于 $SRC_TAURI/target/release/bundle/"
}

# ── 主入口 ───────────────────────────────────────────────
case "${1:-all}" in
  backend) build_backend ;;
  icons)   build_icons ;;
  app)     build_app ;;
  all)
    build_backend
    [ -d "$SRC_TAURI/icons" ] || build_icons
    build_app
    header "桌面版构建完成 🎉"
    echo -e "  ${CYAN}安装包${NC}: $SRC_TAURI/target/release/bundle/dmg/"
    echo -e "  ${CYAN}App${NC}:    $SRC_TAURI/target/release/bundle/macos/"
    ;;
  *)
    echo "用法: $0 {all|backend|icons|app}"
    exit 1
    ;;
esac
