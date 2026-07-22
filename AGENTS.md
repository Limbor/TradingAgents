# AGENTS.md — TradingAgents 开发指南

多智能体 LLM 金融交易框架：Python 后端 (FastAPI + LangGraph) + React 前端 (Vite + Tailwind) + Tauri 桌面应用。

## 目录路由

| 目录 | 职责 | 代理关注点 |
|------|------|-----------|
| `tradingagents/agents/` | LLM 智能体（分析师、研究员、交易员、风控） | 智能体角色定义和工具绑定 |
| `tradingagents/core/` | 核心引擎（信号融合、回测、反思、调度） | 交易决策核心逻辑，改动需谨慎 |
| `tradingagents/dataflows/` | 数据源适配（AKShare、Tushare、yfinance、Alpha Vantage） | 供应商路由和速率限制 |
| `tradingagents/graph/` | LangGraph 工作流图 | 智能体编排和状态管理 |
| `tradingagents/skills/` | 可复用技能模块 | Skill 注册和原子能力 |
| `tradingagents/api/` | FastAPI 服务端 | API 路由和数据模型 |
| `tradingagents/llm_clients/` | LLM 提供商适配 | 模型路由和密钥管理 |
| `frontend/src/` | React 前端 | 页面、组件、状态管理 (zustand) |
| `cli/` | 交互式 CLI | 命令行入口 |
| `tests/` | 测试套件 | 单元测试 + 集成测试 |
| `scripts/` | 运维脚本 | 开发、构建、验证 |
| `docs/` | 架构文档 | SPEC.md、FEATURE_PLAN.md、PROGRESS.md |

## 环境准备

```bash
# 后端依赖（Python >= 3.10）
uv venv && source .venv/bin/activate
uv pip install -e ".[api,dev]"

# 前端依赖
cd frontend && npm install

# 环境变量（按需填写 API 密钥）
cp .env.example .env
```

## 开发命令

```bash
# 启动开发环境（前端 + 后端）
./scripts/dev.sh start    # 后台启动
./scripts/dev.sh          # 前台启动（Ctrl+C 退出）
./scripts/dev.sh status   # 查看状态
./scripts/dev.sh stop     # 停止

# 后端 CLI
tradingagents              # 交互式 CLI 入口
tradingagents-server       # 启动 API 服务
```

## 变更后验证

**每次代码变更后，必须运行与变更范围匹配的最小验证检查：**

```bash
# 后端变更 → 运行 pytest（排除 live 标记的外部调用测试）
pytest -q -m "not live"

# 后端特定模块变更 → 运行相关测试文件
pytest tests/unit/test_<module_name>.py -q

# 代码风格 → ruff
ruff check .

# 前端变更 → lint + 测试 + 类型检查
cd frontend && npm run lint && npm test && npm run build

# 前端 E2E（仅在前端交互逻辑变更时）
cd frontend && npm run test:e2e
```

## 高风险区域

- **数据源 API 密钥**：`.env` 文件包含多个 LLM 和数据源密钥，不得提交或泄露
- **供应商速率限制**：`tradingagents/dataflows/` 中的 AKShare/Tushare/yfinance 有调用频率限制，变更需保留降级逻辑
- **交易决策核心**：`tradingagents/core/` 中的信号融合、回测、反思模块直接影响交易建议质量
- **数据库迁移**：`tradingagents/core/persistence.py` 的 SQLite schema 变更需要迁移逻辑
- **MCP 服务**：`tradingagents/core/mcp_client.py` 和 `tradingagents/dataflows/mcp_adapter.py` 依赖外部 MCP 服务，超时配置需关注

## 交付流程

1. **分支**：从 `main` 创建 feature 分支（如 `feature/xxx`）
2. **变更**：在 feature 分支上开发和验证（遵循上方验证规则）
3. **提交**：`git add` + `git commit`（使用 conventional commit 格式）
4. **推送**：`git push origin <branch>`（触发 CI 流水线）
5. **CI 检查**：确认以下作业全部通过：
   - `test` — pytest（4 个 Python 版本）
   - `frontend` — lint + test + build + E2E
   - `e2e-smoke` — 真实后端冒烟
   - `lint` — ruff 全仓库检查
6. **审查**：创建 PR 并等待审查通过后合并到 `main`

## 深入文档

- [docs/SPEC.md](docs/SPEC.md) — 技术规格
- [docs/FEATURE_PLAN.md](docs/FEATURE_PLAN.md) — 功能规划
- [docs/PROGRESS.md](docs/PROGRESS.md) — 开发进度
- [docs/A股Agent架构说明.md](docs/A股Agent架构说明.md) — A 股 Agent 架构
- [docs/DESKTOP_PACKAGING.md](docs/DESKTOP_PACKAGING.md) — 桌面打包
- [docs/MCP_QUANT_SIGNAL_CONTRACT.md](docs/MCP_QUANT_SIGNAL_CONTRACT.md) — MCP 量化信号契约
