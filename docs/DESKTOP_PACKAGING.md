# 桌面打包（Phase 5）

TradingAgents 桌面版把 **React 工作台** 与 **FastAPI 后端** 打进一个自包含的
[Tauri](https://tauri.app) 应用：用户无需预装 Python，双击即用。后端通过
[PyInstaller](https://pyinstaller.org) 冻结成单文件二进制，作为 Tauri **sidecar**
被壳进程拉起。首轮目标平台为 **macOS**。

## 架构

```
┌────────────────────────── TradingAgents.app ──────────────────────────┐
│                                                                        │
│  Tauri 壳 (Rust, src-tauri/src/main.rs)                                │
│    ├─ WebView 窗口  ← 加载打包后的前端静态资源 (frontend/dist)          │
│    └─ setup(): spawn sidecar ─┐                                        │
│                               ▼                                        │
│  tradingagents-backend (PyInstaller 冻结的 FastAPI)                    │
│    └─ uvicorn @ 127.0.0.1:8422                                         │
│                                                                        │
│  前端 REST/WS 直连 127.0.0.1:8422（构建期由 .env.tauri 注入绝对地址）  │
│  数据默认写 ~/.tradingagents （与 CLI / dev server 共享状态）          │
│                                                                        │
│  退出时壳进程 kill sidecar，避免残留 Python 进程                       │
└────────────────────────────────────────────────────────────────────────┘
```

**关键设计**

- **共享数据目录**：后端仍默认写 `~/.tradingagents`（DB、缓存、报告），所以桌面版
  与命令行、开发服务器看到同一份持仓/报告/反思数据。
- **构建期连线**：打包前端用 `vite build --mode tauri`，加载 `frontend/.env.tauri`
  把 `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` 指向 `127.0.0.1:8422`。普通
  `npm run build`（web 部署）不受影响，仍走相对地址。
- **生命周期**：sidecar 在 `setup` 阶段启动，`RunEvent::Exit` 时被 `kill`。

## 前置依赖

| 依赖 | 安装 | 说明 |
|---|---|---|
| Rust 工具链 | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` | Tauri 壳编译必需 |
| PyInstaller | `.venv/bin/pip install "tradingagents[desktop]"` | 冻结后端 |
| 前端依赖 | `cd frontend && npm install` | 含 `@tauri-apps/cli` |
| Xcode CLT | `xcode-select --install` | macOS 原生打包/签名 |

> 本仓库当前环境未安装 Rust，故 `cargo build` / `.dmg` 产物需在具备 Rust 的
> 机器上执行 `scripts/build_desktop.sh` 生成。脚手架、进程管理、打包规格与
> 构建脚本均已就绪并可验证。

## 一键构建

```bash
# 完整构建（后端 sidecar → 图标 → .app/.dmg）
./scripts/build_desktop.sh

# 分步
./scripts/build_desktop.sh backend   # 只打后端 sidecar
./scripts/build_desktop.sh icons     # 只生成图标
./scripts/build_desktop.sh app        # 只跑 tauri build
```

产物：

- `.app` → `src-tauri/target/release/bundle/macos/TradingAgents.app`
- `.dmg` → `src-tauri/target/release/bundle/dmg/`

## 开发模式

热重载调试（前端 vite dev + 壳窗口，后端 sidecar 由壳拉起）：

```bash
cd /path/to/TradingAgents          # 从仓库根运行，Tauri 才能定位 ./src-tauri
frontend/node_modules/.bin/tauri dev
```

> `tauri dev` 会执行 `beforeDevCommand`（`npm --prefix ../frontend run dev`）并加载
> `devUrl`（http://localhost:5173）。sidecar 需先构建过一次（`build_desktop.sh backend`）。

## 目录与文件

| 路径 | 作用 |
|---|---|
| `src-tauri/Cargo.toml` | Rust 壳依赖（tauri 2 + shell 插件） |
| `src-tauri/tauri.conf.json` | 窗口、bundle 目标、`externalBin` sidecar、图标 |
| `src-tauri/src/main.rs` | 壳入口：spawn/kill 后端 sidecar，转发日志 |
| `src-tauri/capabilities/default.json` | Tauri 2 权限（允许执行 sidecar） |
| `src-tauri/binaries/` | PyInstaller 产物（按 target triple 命名，git 忽略） |
| `src-tauri/icons/` | `tauri icon` 生成（git 忽略） |
| `packaging/backend_entry.py` | PyInstaller 分析入口，转发 `server.main()` |
| `packaging/tradingagents-backend.spec` | PyInstaller 规格（含动态导入收集） |
| `scripts/build_desktop.sh` | 一键编排 |
| `frontend/.env.tauri` | 构建期后端地址注入 |

## 常见问题

- **`sidecar not found`**：`externalBin` 的实际文件名必须带 target triple 后缀
  （如 `tradingagents-backend-aarch64-apple-darwin`）。`build_desktop.sh backend`
  会自动按 `rustc -Vv` 的 host 三元组重命名。
- **冻结后 `ModuleNotFoundError`**：skill 是运行时动态发现的，spec 已用
  `collect_submodules("tradingagents")` 收全；若新增第三方 provider 报缺模块，
  在 spec 的 `collect_all` 列表里补一行即可。
- **端口占用**：sidecar 固定 `127.0.0.1:8422`（与 `main.rs` 常量、`.env.tauri` 一致）。
  改端口需三处同步。

## 后续（不在首轮范围）

- 代码签名 + 公证（`APPLE_CERTIFICATE` 等，Tauri `macOS.signingIdentity`）
- Windows（`.msi`/NSIS）与 Linux（`.AppImage`/`.deb`）交叉构建 + CI 矩阵
- 自动更新（`tauri-plugin-updater`）
