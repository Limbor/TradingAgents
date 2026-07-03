# TradingAgents 开发进度汇总

> 最后更新: 2026-06-28
> 对应设计文档: [SPEC.md](./SPEC.md)

---

## 目录

1. [原始能力 (v0.2.5 基线)](#1-原始能力-v025-基线)
2. [目标能力 (v2.0 愿景)](#2-目标能力-v20-愿景)
3. [整体进度总览](#3-整体进度总览)
4. [Phase 1 — API 层 + 基础前端](#4-phase-1--api-层--基础前端)
5. [Phase 2 — 技能扩展 + 对话式交互](#5-phase-2--技能扩展--对话式交互)
6. [Phase 3 — MCP 集成 + 决策增强](#6-phase-3--mcp-集成--决策增强)
7. [Phase 4 — 自动化 + 学习闭环](#7-phase-4--自动化--学习闭环)
8. [Phase 5 — 桌面打包](#8-phase-5--桌面打包)
9. [已知问题](#9-已知问题)
10. [下一步建议](#10-下一步建议)

---

## 1. 原始能力 (v0.2.5 基线)

TradingAgents 最初是一个基于 **LangGraph** 的多智能体金融分析框架，仅通过 Rich CLI 终端界面交互。

### 1.1 核心 Agent 管道

13 个 agent 组成 5 阶段管道：

| 阶段 | Agents | 职责 |
|------|--------|------|
| **① 分析** | Market / Sentiment / News / Fundamentals Analyst | 市场技术、舆情、新闻、基本面分析 |
| **② 研究辩论** | Bull Researcher ↔ Bear Researcher → Research Manager | 多空结构化辩论 + 汇总 |
| **③ 交易** | Trader | 整合分析结果生成交易决策 |
| **④ 风险辩论** | Aggressive / Conservative / Neutral Analyst | 三方风险辩论 |
| **⑤ 组合决策** | Portfolio Manager | 最终批准/拒绝，输出评级 |

### 1.2 基础设施能力

- **LLM 提供商**: 支持 18+ 提供商（OpenAI、Anthropic、Google、xAI、DeepSeek、Qwen、GLM、MiniMax、OpenRouter、Ollama、Azure、Bedrock 等），国际/中国双端适配
- **数据源**: yfinance / AKShare / TuShare / FRED / Reddit / StockTwits / Polymarket
- **市场**: A 股 / 美股双市场，非 US 市场使用对应 Alpha 基准（^NSEI/^N225/^HSI 等）
- **持久化**: LangGraph 检查点恢复、决策日志（自动注入历史经验）、SQLite 缓存
- **安全**: Ticker 路径遍历防护、API 密钥交互式检测
- **CLI**: Rich 终端界面（`cli/` 目录），支持流式展示

### 1.3 入口方式

唯一入口为 CLI（`cli/main.py`），通过 `python -m cli` 或打包后的命令行工具使用。

---

## 2. 目标能力 (v2.0 愿景)

将 TradingAgents 升级为**可视化的多技能 Agent 平台**：

| 维度 | 目标 |
|------|------|
| **界面** | Web-first 可视化工作台，远期打包为 macOS 原生桌面应用（Tauri v2） |
| **架构** | "单一分析管道" → "可插拔技能框架"，并通过 StockManager MCP 委托 A 股量化计算 |
| **交互** | Chat-first 任务流（用户输入或按钮均进入 Chat 执行，Analysis 作为深度详情页） |
| **扩展** | 新 Skill 只需实现标准接口即可接入，无需改动核心 |

### 2.1 六层架构

| 层级 | 职责 | 关键技术 |
|------|------|----------|
| Presentation | 用户交互、可视化 | React 18, TypeScript, @xyflow/react, Recharts |
| API Gateway | HTTP/WS 接口 | FastAPI, Uvicorn, WebSocket |
| Orchestration | 意图识别、技能路由 | LLM Router, Skill Registry |
| Skill | 独立 agent 工作流 | LangGraph StateGraph |
| Core | 共享基础能力 | LLM factory, DataFlows, Memory, Config |
| Infrastructure | 打包分发 | Tauri v2, PyInstaller sidecar |

---

## 3. 整体进度总览

```
Phase 1 (API + 基础前端)    ██████████████████████  100%  ← 已完成
Phase 2 (技能扩展 + 对话)    ██████████████████████  100%  ← 已完成
Phase 3 (MCP 集成 + 决策增强) ███████████████░░░░░░░   ~70%  ← 可运行初版
Phase 4 (自动化 + 学习闭环)   ░░░░░░░░░░░░░░░░░░░░░░   ~0%  ← 后续
Phase 5 (桌面打包)            ░░░░░░░░░░░░░░░░░░░░░░   ~0%  ← 远期
```

| 模块 | 状态 | 说明 |
|------|------|------|
| FastAPI 应用工厂 | ✅ 完成 | [tradingagents/api/app.py](../tradingagents/api/app.py) |
| REST 路由 (health/skills/runs/reports/config) | ✅ 完成 | [tradingagents/api/routes/](../tradingagents/api/routes/) |
| WebSocket 实时流 (/ws/run/{run_id}) | ✅ 完成 | [tradingagents/api/ws/stream.py](../tradingagents/api/ws/stream.py) |
| Skill 框架 (BaseSkill + Registry) | ✅ 完成 | [tradingagents/skills/](../tradingagents/skills/) |
| StockAnalysisSkill 封装 | ✅ 完成 | [tradingagents/skills/stock_analysis/skill.py](../tradingagents/skills/stock_analysis/skill.py) |
| astream_propagate 异步桥接 | ✅ 完成 | [tradingagents/graph/trading_graph.py](../tradingagents/graph/trading_graph.py#L483) |
| RunManager 运行生命周期 | ✅ 完成 | [tradingagents/core/run_manager.py](../tradingagents/core/run_manager.py) |
| EventBus 进程内事件总线 | ✅ 完成 | [tradingagents/core/event_bus.py](../tradingagents/core/event_bus.py) |
| SQLite 持久化 | ✅ 完成 | [tradingagents/core/persistence.py](../tradingagents/core/persistence.py) |
| React Dashboard | ✅ 完成 | [frontend/src/pages/Dashboard/](../frontend/src/pages/Dashboard/) |
| React Analysis (Agent 图 + 报告) | ✅ 完成 | [frontend/src/pages/Analysis/](../frontend/src/pages/Analysis/) |
| React Reports 历史页 | ✅ 完成 | [frontend/src/pages/Reports/](../frontend/src/pages/Reports/) |
| React Settings 配置页 | ✅ 完成 | [frontend/src/pages/Settings/](../frontend/src/pages/Settings/) |
| WebSocket 前端管理 | ✅ 完成 | [frontend/src/api/ws.ts](../frontend/src/api/ws.ts) |
| 单元测试 (5 个文件) | ✅ 完成 | [tests/unit/](../tests/unit/) |
| Orchestrator 意图路由 | ✅ 完成 | [tradingagents/core/orchestrator.py](../tradingagents/core/orchestrator.py) |
| WS /chat 对话端点 | ✅ 完成 | [tradingagents/api/ws/stream.py](../tradingagents/api/ws/stream.py) |
| Chat 前端页面 | ✅ 完成 | [frontend/src/pages/Chat/](../frontend/src/pages/Chat/) |
| Chat 任务流交互 | ✅ 初版完成 | Skill 按钮进入 Chat 自动执行；按 run 聚合进度步骤、结构化结果和详情链接 |
| Dashboard 账户驾驶舱 | ✅ 初版完成 | 持仓市值/成本/浮动盈亏/集中度/MCP 状态 |
| 额外 Skill (Portfolio/Scanner 等) | ✅ 完成 | portfolio_management / market_scanner |
| 集成/E2E 测试 | ✅ 完成 | [tests/integration/test_phase1_flow.py](../tests/integration/test_phase1_flow.py) |
| **Phase 3+ 新增模块** | | |
| MCP Client 基础设施 | ✅ 完成 | HTTP/Streamable MCP + health/capabilities + `/api/v1/health` |
| UserProfile 投资风格配置 | ✅ 初版完成 | 短线/中线/长线参数 + SQLite + `GET/PUT /api/v1/profile` + Settings 投资风格卡 |
| Scheduler 定时调度 | ✅ 初版完成 | `Scheduler` 已接入 FastAPI lifespan，默认注册每日 08:30 `daily_pipeline` |
| DailyPipeline 每日选股编排 | ✅ 初版完成 | MCP 候选池 + 风格权重打分 + 早报事件 + SQLite 报告入库；Top5 深度 Agent 分析待补 |
| RiskMonitor 风险监控 | ✅ 初版完成 | 持仓巡检 + MCP 公告扫描 + 报告事件；风险仪表盘分级待增强 |
| PositionAdvisor 持仓建议 | ❌ 待建 | 卖出/加仓建议（复用 Agent 管道） |
| StrategyBacktest 回测 Skill | ❌ 待建 | 委托 MCP `run_backtest()` |
| DecisionAudit 决策复盘 | ❌ 待建 | 历史决策 vs 实际收益追踪 |
| 前端 Portfolio 持仓页 | ✅ 初版完成 | 持仓 CRUD + P&L 展示；风险仪表盘待增强 |
| 前端 Watchlist 关注页 | ✅ 初版完成 | 手动触发 `daily_pipeline` 进入 Chat + 近期运行列表；候选明细历史化待增强 |
| 前端 Settings MCP 配置 | ✅ 完成 | MCP URL / enabled / timeout |
| Tauri 桌面打包 | ❌ 远期 | desktop/ 目录不存在 |

---

## 4. Phase 1 — API 层 + 基础前端

### Week 1: API 骨架 ✅

已实现的 REST 端点（前缀 `/api/v1`）：

| 端点 | 方法 | 文件 | 状态 |
|------|------|------|------|
| `/health` | GET | [health.py](../tradingagents/api/routes/health.py) | ✅ |
| `/skills` | GET | [skills.py](../tradingagents/api/routes/skills.py) | ✅ |
| `/skills/{id}/schema` | GET | skills.py | ✅ |
| `/runs` | POST | [runs.py](../tradingagents/api/routes/runs.py) | ✅ |
| `/runs` | GET | runs.py | ✅ |
| `/runs/{id}` | GET | runs.py | ✅ |
| `/runs/{id}` | DELETE | runs.py | ✅ |
| `/reports` | GET | [reports.py](../tradingagents/api/routes/reports.py) | ✅ |
| `/reports/{id}` | GET | reports.py | ✅ |
| `/config` | GET/PUT | [config.py](../tradingagents/api/routes/config.py) | ✅ |
| `/config/providers` | GET | config.py | ✅ |

WebSocket 端点：
- `WS /ws/run/{run_id}` — ✅ 已实现，支持历史事件回放 + 实时流 + 心跳

### Week 2: Skill 框架 ✅

- **BaseSkill ABC**: 定义 metadata / input_schema / output_schema / execute / cancel 抽象接口
- **SkillRegistry**: register / get / list_all / find_by_trigger / auto_discover
- **StockAnalysisSkill**: 封装 TradingAgentsGraph，桥接 astream_propagate 事件流
- **astream_propagate**: 在 [trading_graph.py](../tradingagents/graph/trading_graph.py#L483) 中新增异步流式方法，通过 LangGraph `astream_events` 捕获 agent 状态、报告片段、工具调用

### Week 3: 前端骨架 ✅

| 页面 | 路由 | 状态 | 关键组件 |
|------|------|------|----------|
| Dashboard | `/` | ✅ | SkillCard, RecentRuns |
| Analysis | `/analysis/:runId` | ✅ | AgentGraph, ReportPanel, ProgressTracker |
| Reports | `/reports` | ✅ | 列表 + 详情双栏，Markdown 渲染 |
| Settings | `/settings` | ✅ | LLM Provider/Model/语言/辩论轮次配置 |

基础设施：
- Vite + React 18 + TypeScript
- Zustand 状态管理 ([useRunStore](../frontend/src/stores/useRunStore.ts), [useConfigStore](../frontend/src/stores/useConfigStore.ts))
- @tanstack/react-query 数据获取
- WebSocket 管理器 ([ws.ts](../frontend/src/api/ws.ts))，含自动重连
- HTTP client ([client.ts](../frontend/src/api/client.ts))

### Week 4: 联调 ✅

- WebSocket 实时更新（agent_status / report_chunk / tool_call / skill_complete）✅
- WebSocket 终态事件（run_complete / run_cancelled / error）✅
- 报告分 Tab 展示（Market/Sentiment/News/Fundamentals/Research/Trading/Decision）✅
- 配置热更新（切换 Provider 自动联动模型列表）✅
- 报告落盘 + SQLite 索引 + 磁盘报告导入 ✅
- RunManager 运行状态落库 + 报告 run_id 关联 ✅
- Phase 1 API/WS/Reports 集成测试 ✅

---

## 5. Phase 2 — 技能扩展 + 对话式交互

### Week 5: 意图路由 ✅

已实现：

| 项 | SPEC 位置 | 当前状态 |
|----|-----------|----------|
| [Orchestrator](../tradingagents/core/orchestrator.py) | §5.6 | ✅ 规则路由 + 中文股票名映射 |
| WS `/ws/chat` 端点 | §4.2 | ✅ 自动创建 run 并流式回传事件 |
| Chat 前端页面 | §6.1 | ✅ `/chat` 命令中心 |
| useChatStore | §6.1 | ✅ 聊天状态管理 |

**说明**: 当前优先采用离线稳定的规则路由，保证无 API key / 无网络时仍能通过自然语言触发技能；LLM 路由可作为后续增强。

### Week 6: Portfolio Skill ✅

已实现 `portfolio_management` Skill：
- 持仓新增/更新/删除/列表/分析
- SQLite `holdings` 表持久化
- 本地 P&L、集中度、风险等级和再平衡建议

### Week 7: Market Scanner ✅

已实现 `market_scanner` Skill：
- A 股优先使用 StockManager MCP 动态成分股；美股暂用本地样本池
- 动量、流动性、质量、风险综合评分
- 输出候选列表和 scanner report
- MCP 不可用时默认不伪装真实筛选结果，仅在测试/演示显式开启 `market_scanner_demo_fallback` 时使用样本池

### Week 8: 设置 + 历史 ✅

| 项 | 状态 |
|----|------|
| Settings 页（LLM/数据源配置）| ✅ |
| Reports 历史页 | ✅ |
| SQLite 持久化 | ✅ |
| 磁盘报告自动导入 | ✅ |
| Ticker 名称解析回填 | ✅ |

---

## 6. Phase 3 — MCP 集成 + 决策增强（当前重点）

> **核心策略**: TradingAgents 定位为「决策层 Agent」，量化计算（回测/因子/选股/数据）委托给 StockManager MCP Server，自身专注于 LLM 驱动的分析、辩论、决策生成。

### 架构分工

```
┌──────────────────────────────────────────────────────────────┐
│                    TradingAgents (决策层)                      │
│                                                              │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │ Agent 管道   │  │ Skill Framework   │  │ 前端 UI        │  │
│  │ (13 agents)  │  │ (Analysis/Advisor │  │ (React 页面)   │  │
│  │ LLM 分析决策 │  │  /Pipeline/       │  │                │  │
│  │              │  │  RiskMonitor/     │  │                │  │
│  │              │  │  DecisionAudit)   │  │                │  │
│  └──────┬───────┘  └────────┬─────────┘  └───────┬────────┘  │
│         │                   │                    │           │
│         │     ┌─────────────┴─────────────┐      │           │
│         │     │  MCP Client 基础设施      │      │           │
│         │     │  (StockManagerMCPClient)  │      │           │
│         │     └─────────────┬─────────────┘      │           │
│         │                   │                    │           │
├─────────┼───────────────────┼────────────────────┼───────────┤
│         │          MCP JSON-RPC                 │           │
│         │                   │                    │           │
│  ┌──────┴───────────────────┴────────────────────┴───────┐   │
│  │              StockManager MCP Server                   │   │
│  │                                                        │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────────┐   │   │
│  │  │ 数据查询  │  │ 因子实验      │  │ 实盘顾问        │   │   │
│  │  │ OHLCV    │  │ eval_formula  │  │ trading_plan   │   │   │
│  │  │ 财务指标  │  │ run_backtest  │  │ stock_name     │   │   │
│  │  │ 行业分类  │  │ ablation      │  │ slippage       │   │   │
│  │  │ 风控公告  │  │ factor_exper  │  │ purged_cv      │   │   │
│  │  │ 资金流    │  │               │  │                │   │   │
│  │  └──────────┘  └──────────────┘  └────────────────┘   │   │
│  └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**TradingAgents 不自建、委托 StockManager MCP 的能力：**

| 能力 | 委托方式 | 原因 |
|------|---------|------|
| A股 OHLCV / 财务指标 / 行业分类 | `get_stock_daily` / `get_financial_metrics` / `get_industry_map` | 数据质量更高（TuShare + 复权重建） |
| 风控公告扫描 | `get_risk_announcements` | 已有立案/问询/违规关键词扫描 |
| 机构资金流 | `get_institutional_flow` | 已有四路资金 Parquet 缓存 |
| 指数成分股 | `get_index_constituents` | 沪深300/中证800 候选池 |
| 回测引擎 | `run_backtest` | T+1/涨跌停/摩擦模型完整实现 |
| 因子研究 | `evaluate_signal_formula` / `run_factor_experiment` | DSL + IC评估 + 消融实验 |
| 交易计划生成 | `generate_trading_plan` | Flight Plan（吊灯止损/跳空过滤） |

**TradingAgents 保留自建的能力：**

| 能力 | 原因 |
|------|------|
| Agent 管道（13 agents） | 核心差异化，LLM 驱动的多智能体分析决策 |
| AKShare 新闻/舆情/公告/主题热度/微观结构 | StockManager 不提供这些数据 |
| 长线/短线风格感知 | 参数注入 Prompt + 因子权重调整 |
| 自然语言意图路由 | Orchestrator 规则路由 + Chat 交互 |
| 报告生成与展示 | 结构化决策输出 + 前端可视化 |

### Phase 3A / Week 9: MCP Contract + Client 基础设施

| 项 | 说明 |
|----|------|
| 通信策略 | TradingAgents 仅作为 MCP Client，通过 `http://127.0.0.1:8765/mcp` 连接 StockManager；不在 Agent 进程内 `uv run python stockmanager-mcp/server.py` |
| MCP Client | `tradingagents/core/mcp_client.py` — 从 stdio 草案改为 HTTP/Streamable MCP Client |
| 配置项 | `default_config.py` 新增 `stockmanager_mcp_url`（默认 `http://127.0.0.1:8765/mcp`）、`stockmanager_mcp_timeout`、`stockmanager_mcp_enabled`、`scheduler_enabled` |
| 合约冻结 | 固化 tool schema、错误信封、版本号、health/capabilities、超时策略和返回字段 provenance |
| 连接管理 | FastAPI lifespan 中自动 health check + capability discovery，失败时进入能力降级模式 |
| 数据适配层 | `tradingagents/dataflows/mcp_adapter.py` — 将 MCP 返回数据转换为现有 `interface.py` 兼容格式 |
| 测试 | 单元/集成覆盖 env 覆盖、MCP adapter、API 路由、orchestrator、WebSocket、DailyPipeline/RiskMonitor |
| 当前验证 | StockManager MCP `list_tools` 21 个工具通过；TradingAgents `/health` 返回 connected + capability flags；live smoke 下 `daily_pipeline` / `risk_monitor` 完成 |

### Phase 3B / Week 10: UserProfile + Scheduler

| 项 | 说明 |
|----|------|
| UserProfile | `tradingagents/core/user_profile.py` — Pydantic Schema（investment_style / risk_tolerance / sector_prefs） |
| SQLite 表 | `user_profile` 表（通过 `persistence.py` Schema 扩展） |
| API 端点 | `GET/PUT /api/v1/profile` |
| Scheduler | `tradingagents/core/scheduler.py` — asyncio 后台协程，FastAPI lifespan 默认注册每日 08:30 `daily_pipeline` |
| 前端 | Settings 页面已新增 MCP 连接配置和投资风格配置卡片 |

### Phase 3C / Week 11: DailyPipeline 每日选股

| 项 | 说明 |
|----|------|
| 全市场初筛 | MCP `get_index_constituents` → A股规则过滤（ST/停牌/低流动性/一字涨跌停/近期风险公告） |
| 多因子打分 | 短线因子（动量/换手/北向/板块热度）vs 长线因子（ROE/PE分位/毛利率/营收增速），权重由 `UserProfile.investment_style` 决定 |
| Evidence Card | 每只候选股返回可审计证据：数据截止日、复权口径、行业、流动性、风险扣分、因子分位、资金流摘要 |
| LLM 快筛融合 | ✅ 初版接入：Top N 量化候选使用统一 LLM Backbone 快速复核催化剂/风险，并通过 `signal_fusion` 输出 `quant_llm_fused`；无 API key 时显式降级为 `quant_only` |
| Top 5 深度分析 | 后续增强：对得分最高的 5 只，跑完整 StockAnalysisSkill/13-Agent 管道 |
| 早报生成 | 当前以 `report_chunk` 事件输出 Markdown，并以 `DAILY_PIPELINE` 报告入库 |
| 前端 | Watchlist 页面已支持手动触发每日选股和查看近期运行；Dashboard 卡片待增强 |

### Phase 3D / Week 12: RiskMonitor + 前端补全

| 项 | 说明 |
|----|------|
| RiskMonitor | 每日扫描持仓：ST 检测 / 停牌 / 涨跌停 / 质押预警 / 解禁提醒 / MCP `get_risk_announcements` |
| 前端 Portfolio 页 | 已支持持仓 CRUD 和 P&L；待补风险仪表盘（绿/黄/橙/红分级） |
| 前端增强 | 待补：Chat 页面新增「卖出建议」「加仓建议」快捷触发按钮 |

### MCP / Agent 通信原则

| 原则 | 说明 |
|------|------|
| 服务边界 | StockManager 是独立本地服务，负责数据、因子、回测、交易计划；TradingAgents 负责意图、分析、辩论、解释和报告 |
| 调用协议 | 优先使用 HTTP/Streamable MCP，默认 `http://127.0.0.1:8765/mcp`；stdio 仅保留给 Claude Desktop 等外部 MCP 客户端，不作为 TradingAgents 内部默认方式 |
| 能力发现 | TradingAgents 启动时调用 health/capabilities，生成 `market_data_available`、`factor_available`、`backtest_available`、`trading_plan_available`、`risk_announcement_available` |
| 异步任务 | `run_backtest`、`run_factor_experiment`、`run_ablation_study` 返回 `job_id`，TradingAgents 轮询 `get_job_status` / `get_job_result` 并通过 WebSocket 推送进度 |
| 可审计输出 | 所有 A 股数据类 tool 返回 `as_of_date`、`source`、`adj_type`、`calendar`、`warnings`；Agent 报告只引用这些结构化证据 |
| 降级策略 | MCP 不可用时仍可使用本地 AKShare/TuShare 做基础分析；回测、因子实验、交易计划和风险公告能力在 UI 中置灰并给出原因 |
| 缓存与幂等 | 对高频查询使用 request hash + TTL 缓存；长任务使用 `job_id` 幂等恢复，避免 LLM 重复触发重计算 |

### 新闻 / 公告 / 情绪内容摄取策略

| 层级 | 说明 |
|------|------|
| 快速层 | 默认拉取标题、摘要/摘录、来源、链接、发布时间、风险标签，控制 token 和延迟 |
| 深度层 | 对 Top N 相关新闻、公告、问询函、业绩预告、处罚、减持等拉取正文或 PDF 文本，并生成 Evidence Card |
| 原文层 | 原文 URL / PDF / HTML 进入缓存和审计链路，不直接整篇塞进 Agent prompt |
| 当前落地 | A 股 `stock_news_em` 已在有“新闻内容/正文”列时输出前 N 条受限正文摘录；公告和研报全文后续通过 MCP/document fetcher 接入 |

### 统一 LLM Backbone

| 项 | 说明 |
|----|------|
| 全局配置 | 系统只保留一套 `llm_provider` + `quick_think_llm` + `deep_think_llm` + `backend_url` |
| Chat | 当前 Chat 是规则路由，不单独调用 LLM；未来 LLM fallback router 默认复用 quick model |
| 判股管道 | Analysts / Debate / Trader 使用 quick model，Research Manager / Portfolio Manager 使用 deep model |
| 可切换 Provider | DeepSeek、Qwen/Qwen-CN、OpenAI、OpenAI Compatible 等统一走同一套 Backbone 配置 |

### 验收标准

1. 系统启动后自动连接 StockManager MCP，连接失败时降级到本地数据源
2. `GET /api/v1/health` 可返回 StockManager MCP 连接状态和能力 flags
3. Settings 页面可配置投资风格（短线/长线），切换后影响 DailyPipeline 因子权重
4. 每日 8:30 自动运行选股 Pipeline；Watchlist 可手动触发和查看运行记录
5. Portfolio 页面展示持仓 P&L；风险预警由 `risk_monitor` Skill 生成报告，仪表盘分级待增强
6. Chat 输入"持仓风险/风险监控"可路由到 RiskMonitor；"宁德时代要不要卖"的 PositionAdvisor 待 Phase 4 实现

---

## 7. Phase 4 — 自动化 + 学习闭环（后续）

### Week 13: PositionAdvisor 持仓建议

| 项 | 说明 |
|----|------|
| Skill | 新增 `tradingagents/skills/position_advisor/` — 注入持仓上下文（成本价/浮盈/持有时长）到 Agent 管道 |
| Orchestrator | 新增意图路由「卖出/加仓/减仓/止损」→ PositionAdvisor |
| MCP 集成 | 可选调用 `generate_trading_plan` 获取 Flight Plan（吊灯止损价/跳空过滤区间） |

### Week 14: 回测 + 复盘

| 项 | 说明 |
|----|------|
| StrategyBacktest Skill | 封装 MCP `run_backtest`，前端表单 → 异步执行 → 结果展示 |
| DecisionAudit Skill | 月末复盘：系统推荐 vs 实际操作 vs 实际收益，归因分析 |
| MCP 集成 | `run_backtest` / `purged_cv_sharpe` / `analyze_execution_slippage` |

### Week 15: 记忆增强 + 财务预警

| 项 | 说明 |
|----|------|
| 增强记忆系统 | 跨标的模式提取（「当前市场环境下低估值+高ROE+北向流入的标的历史胜率 X%」） |
| 财务风险预警 | MCP `get_financial_metrics`（ROE/利润增速恶化）+ `get_risk_announcements`（问询函/违规） |

### 验收标准

1. 对持仓中的任意股票可说「XX 要不要卖」，系统输出含止损价和卖出原因的建议
2. 月末可自动生成复盘报告：本月系统推荐胜率 vs 实际操作胜率
3. 历史决策可回溯：某次推荐 Buy 的标的，N 天后实际涨跌幅

---

## 8. Phase 5 — 桌面打包（远期）

### 全部未开始 ❌

| Week | 里程碑 | 状态 |
|------|--------|------|
| W16 | Tauri 集成 (Sidecar/窗口/托盘) | ❌ desktop/ 不存在 |
| W17 | macOS 通知推送 | ❌ |
| W18 | PyInstaller sidecar + .dmg 构建 | ❌ |

---

## 9. 已知问题

### 9.1 架构偏差（功能性缺口）

- **LLM 意图路由尚未接入**: 当前 Orchestrator 使用离线规则路由，覆盖验收用例和常见技能触发。后续可加入 LLM fallback，但不作为当前正确性的依赖。
- **LLM 快筛依赖统一 Backbone 配置**: DailyPipeline 已有 LLM Reviewer 链路，但真实运行需要 `llm_provider/quick_think_llm/backend_url/API_KEY` 配好；未配置时结果会显式标记 `review_meta.available=false` 并降级为 `quant_only`。
- **Market Scanner 数据深度仍有限**: A 股候选池已切到 StockManager MCP 成分股；`get_factor_snapshot` 只提供快照，不是选股策略。收益稳定性取决于 MCP `rank_factor_candidates` 的因子覆盖、行业分散、资金流/估值有效性和回测验证。
- **Phase 1/2 已收尾**: `RunManager` 已集成 SQLite 持久化，报告入库已使用真实 `run_id`，WebSocket 已统一终态事件，Chat 可自然语言触发技能。

### 9.2 StockManager MCP 依赖

- **通信方式已切换**: `mcp_client.py` 已改为 HTTP/Streamable MCP，默认连接 `http://127.0.0.1:8765/mcp`。
- **可用性风险**: StockManager MCP Server 不可用时，TradingAgents 降级到本地 AKShare/TuShare 数据源，但回测/因子/交易计划功能不可用。
- **版本兼容**: MCP Tool 接口变更时需同步更新 `mcp_adapter.py` 和 contract tests。
- **长任务阻塞**: 回测/因子实验必须使用 `job_id` 异步轮询，避免阻塞 Agent 管道和 WebSocket。
- **实盘 smoke 暴露的问题**: 2026-07-01 的 `rank_factor_candidates` 曾返回指数成分为空，MCP 侧需要支持成分日期回退；CSI800 同步排名在较大 `candidate_limit` 下耗时偏长，应缓存或转异步 job。

---

## 10. 下一步建议

### 10.1 Phase 3 优先推进

1. 修 MCP `rank_factor_candidates`：成分日期 fallback、flow/valuation 覆盖率、行业分散、CSI800 缓存/异步 job
2. 为 DailyPipeline 增加 Top 5 深度 StockAnalysisSkill 串联，并把候选明细结构化入库供 Watchlist 展示
3. 将 RiskMonitor 结果沉淀为结构化风险事件，补 Portfolio 风险仪表盘（绿/黄/橙/红）
4. 建立更严格的 MCP contract tests：schema 固化、错误信封、timeout、非法返回、job_id 幂等
5. 实现 PositionAdvisor：结合持仓成本、风险事件、交易计划输出卖出/加仓建议
6. 实现 StrategyBacktest：封装 MCP `run_backtest` + `get_job_status` / `get_job_result`

### 10.2 代码质量

1. 补充前端测试（vitest）— 覆盖 store 状态流转、WebSocket 重连
2. 增加浏览器 E2E 自动化 — 覆盖 Dashboard 创建运行、Chat 自然语言路由、Analysis 实时更新、Reports 详情查看
3. MCP Client 单元测试 — 覆盖连接失败降级、数据格式适配

### 10.3 后续规划

1. Phase 4: PositionAdvisor + 回测 + 记忆增强
2. Phase 5: Tauri 桌面打包
3. 继续增强 Orchestrator：加入 LLM fallback、更多中文股票别名、参数澄清
