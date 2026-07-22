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
│    └─ uvicorn @ 127.0.0.1:<随机端口>                                   │
│                                                                        │
│  前端通过 Tauri command 获取本次端口与临时令牌，等待健康检查后启动 UI │
│  数据默认写 ~/.tradingagents （与 CLI / dev server 共享状态）          │
│                                                                        │
│  退出时壳进程 kill sidecar，避免残留 Python 进程                       │
└────────────────────────────────────────────────────────────────────────┘
```

**关键设计**

- **共享数据目录**：后端仍默认写 `~/.tradingagents`（DB、缓存、报告），所以桌面版
  与命令行、开发服务器看到同一份持仓/报告/反思数据。
- **运行时安全连线**：Rust 壳每次启动选择随机 loopback 端口并生成 256-bit 临时
  bearer token；前端通过只读 Tauri command 获取连接信息，等待 sidecar 健康后再
  加载工作台。端口和令牌均不写入静态资源或本地存储。
- **边界防护**：后端校验 REST token 与 WebSocket Origin，桌面 WebView 使用 CSP
  仅允许连接本机随机端口；前端没有 shell spawn/execute 权限。
- **生命周期**：sidecar 在 `setup` 阶段启动，`RunEvent::Exit` 时被 `kill`。

## 前置依赖

| 依赖 | 安装 | 说明 |
|---|---|---|
| Rust 工具链 | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` | Tauri 壳编译必需 |
| PyInstaller | `.venv/bin/pip install "tradingagents[desktop]"` | 冻结后端 |
| 前端依赖 | `cd frontend && npm install` | 含 `@tauri-apps/cli` |
| Xcode CLT | `xcode-select --install` | macOS 原生打包/签名 |

> CI 在 macOS runner 上执行完整 PyInstaller + Tauri `.app` / `.dmg` 构建；本地
> 仍可用同一脚本复现。代码签名与 Apple 公证凭据尚未接入。

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
| `src-tauri/capabilities/default.json` | Tauri 2 前端权限（仅 `core:default`） |
| `src-tauri/binaries/` | PyInstaller 产物（按 target triple 命名，git 忽略） |
| `src-tauri/icons/` | `tauri icon` 生成（git 忽略） |
| `packaging/backend_entry.py` | PyInstaller 分析入口，转发 `server.main()` |
| `packaging/tradingagents-backend.spec` | PyInstaller 规格（含动态导入收集） |
| `scripts/build_desktop.sh` | 一键编排 |
| `frontend/.env.tauri` | 桌面构建模式说明（不含固定地址或凭据） |

## 常见问题

- **`sidecar not found`**：`externalBin` 的实际文件名必须带 target triple 后缀
  （如 `tradingagents-backend-aarch64-apple-darwin`）。`build_desktop.sh backend`
  会自动按 `rustc -Vv` 的 host 三元组重命名。
- **冻结后 `ModuleNotFoundError`**：skill 是运行时动态发现的，spec 已用
  `collect_submodules("tradingagents")` 收全；若新增第三方 provider 报缺模块，
  在 spec 的 `collect_all` 列表里补一行即可。
- **后端启动超时**：前端最多等待 sidecar 20 秒并展示明确错误。端口每次随机选择；
  若极低概率发生端口竞争，退出应用后重试即可。

## 后续（不在首轮范围）

- 代码签名 + 公证（`APPLE_CERTIFICATE` 等，Tauri `macOS.signingIdentity`）
- Windows（`.msi`/NSIS）与 Linux（`.AppImage`/`.deb`）交叉构建 + CI 矩阵
- 自动更新（`tauri-plugin-updater`）
