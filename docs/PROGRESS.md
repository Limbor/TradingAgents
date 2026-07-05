# TradingAgents 开发进度汇总

> 最后更新: 2026-07-04
> 对应设计文档: [SPEC.md](./SPEC.md)

---

## 目录

1. [原始能力 (v0.2.5 基线)](#1-原始能力-v025-基线)
2. [目标能力 (v2.0 愿景)](#2-目标能力-v20-愿景)
3. [整体进度总览](#3-整体进度总览)
4. [Phase 1 — API 层 + 基础前端](#4-phase-1--api-层--基础前端)
5. [Phase 2 — 技能扩展 + 对话式交互](#5-phase-2--技能扩展--对话式交互)
6. [Phase 3 — MCP 集成 + 决策增强](#6-phase-3--mcp-集成--决策增强)
7. [Phase 4 — ChatAgent + 自动化 + 学习闭环](#7-phase-4--chatagent--自动化--学习闭环)
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
Phase 3 (MCP 集成 + 决策增强) ███████████████████░░░   ~85%  ← 可运行闭环
Phase 4 (ChatAgent + 反思闭环) █████████░░░░░░░░░░░░░   ~40%  ← 当前重点
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
| React Library 产物库 | ✅ 初版完成 | [frontend/src/pages/Library/](../frontend/src/pages/Library/)；旧 Reports API 保留兼容 |
| React Settings 配置页 | ✅ 完成 | [frontend/src/pages/Settings/](../frontend/src/pages/Settings/) |
| WebSocket 前端管理 | ✅ 完成 | [frontend/src/api/ws.ts](../frontend/src/api/ws.ts) |
| 单元测试 (5 个文件) | ✅ 完成 | [tests/unit/](../tests/unit/) |
| Orchestrator 意图路由 | ✅ 完成 | [tradingagents/core/orchestrator.py](../tradingagents/core/orchestrator.py) |
| WS /chat 对话端点 | ✅ 完成 | [tradingagents/api/ws/stream.py](../tradingagents/api/ws/stream.py) |
| Chat 前端页面 | ✅ 完成 | [frontend/src/pages/Chat/](../frontend/src/pages/Chat/) |
| Chat 任务流交互 | ✅ 初版完成 | Skill 按钮进入 Chat 自动执行；按 run 聚合进度步骤、结构化结果和 Library 详情链接 |
| Dashboard 账户驾驶舱 | ✅ 初版完成 | 持仓市值/成本/浮动盈亏/集中度/MCP 状态 |
| 额外 Skill (Portfolio/Scanner 等) | ✅ 完成 | portfolio_management / market_scanner |
| 集成/E2E 测试 | ✅ 完成 | [tests/integration/test_phase1_flow.py](../tests/integration/test_phase1_flow.py) |
| **Phase 3+ 新增模块** | | |
| MCP Client 基础设施 | ✅ 完成 | HTTP/Streamable MCP + health/capabilities + `/api/v1/health` |
| UserProfile 投资风格配置 | ✅ 初版完成 | 短线/中线/长线参数 + SQLite + `GET/PUT /api/v1/profile` + Settings 投资风格卡 |
| Scheduler 定时调度 | ✅ 初版完成 | `Scheduler` 已接入 FastAPI lifespan，默认注册每日 08:30 `daily_pipeline` |
| DailyPipeline 每日选股编排 | ✅ 可运行 | MCP 候选池 + 量化门控 + LLM 离散复核 + 状态机融合 + 价格/交易计划 + Artifact 入库 |
| RiskMonitor 风险监控 | ✅ 初版完成 | 持仓巡检 + MCP 公告扫描 + 报告事件；风险仪表盘分级待增强 |
| DailyReview 收盘复盘 | ✅ 初版完成 | 刷新持仓、风险扫描、反思批处理、每日选股、次日计划 Artifact |
| Reflection Cases / Strategy Lessons | ✅ 初版完成 | 因果归因框架、策略经验软注入、私人复盘隔离 |
| StockAnalysis 持仓上下文 | ✅ 完成 | 命中持仓标的时注入成本、盈亏、仓位占比和持仓备注 |
| Free ChatAgent | ❌ 待建 | 自由聊天、轻量工具增强、Skill Dispatcher 分层 |
| PositionAdvisor 持仓建议 | ◐ 部分具备 | StockAnalysis 已能注入持仓上下文；独立卖出/加仓建议 Skill 待建 |
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
| Library 统一产物库 | ✅ |
| SQLite 持久化 | ✅ |
| 磁盘报告自动导入 / Artifact 展示 | ✅ |
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
| 全市场初筛 | MCP `rank_factor_candidates(enable_decision=True)` → A股规则过滤（ST/停牌/低流动性/一字涨跌停/行业槽位） |
| 多因子打分 | MCP 输出 momentum/liquidity/quality/valuation/flow/risk_control，TradingAgents 保留字段 provenance 和 coverage |
| Evidence Card | 每只候选股返回可审计证据：数据截止日、复权口径、行业、量化门控、估值/资金流、关键风险 |
| LLM 快筛融合 | ✅ 初版接入：Top N 量化候选使用统一 LLM Backbone 输出 `llm_view/catalyst_strength/risk_assessment/invalidates_quant` |
| 状态机决策 | ✅ 完成：`quant_decision` 仅代表量化资格，最终输出 `final_decision=BUY/WATCHLIST/MONITOR/HOLD_REVIEW/SKIP` |
| 价格与交易计划 | ✅ 完成：选股候选补最新收盘价，并生成 entry_zone/stop_loss/targets/action_plan |
| Top 5 深度分析 | 后续增强：对得分最高的 5 只，跑完整 StockAnalysisSkill/13-Agent 管道 |
| Artifact 入库 | ✅ 完成：每日选股以一个主 item 进入 Library，signal/decision pack 在详情内展示 |
| 前端 | Chat/Library 候选表已展示最终决策、量化门控、LLM reasoning、价格计划、数据覆盖和反思命中 |

### Phase 3D / Week 12: RiskMonitor + 前端补全

| 项 | 说明 |
|----|------|
| RiskMonitor | 每日扫描持仓：ST 检测 / 停牌 / 涨跌停 / 质押预警 / 解禁提醒 / MCP `get_risk_announcements` |
| 前端 Portfolio 页 | 已支持持仓 CRUD、中文名称、最新收盘价刷新和 P&L；待补风险仪表盘（绿/黄/橙/红分级） |
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
| Chat | 当前 Chat 是“任务触发 + Skill 事件流”入口；LLM Router 可辅助 Skill 路由，但尚未提供自由聊天/工具增强回答 |
| 判股管道 | Analysts / Debate / Trader 使用 quick model，Research Manager / Portfolio Manager 使用 deep model |
| 可切换 Provider | DeepSeek、Qwen/Qwen-CN、OpenAI、OpenAI Compatible 等统一走同一套 Backbone 配置 |

### Free ChatAgent 后续方案（新增）

当前 Chat 还不是完整自由对话智能体。后续要把 Chat 层升级为四类意图分流：

| 意图 | 行为 | 是否创建 run |
|------|------|--------------|
| `chat_answer` | 自由聊天、解释已有结果、回答术语/流程问题 | 否 |
| `tool_answer` | 调用轻量工具后回复，如 MCP 因子快照、持仓摘要、Library 搜索 | 否 |
| `skill_run` | 触发 DailyPipeline/StockAnalysis/RiskMonitor 等长任务 | 是 |
| `clarify` | 参数不足时追问，如市场、板块、时间范围 | 否 |

第一版轻量工具注册表建议：

| Tool | 数据来源 | 用途 |
|------|----------|------|
| `get_portfolio_summary` | SQLite holdings | 回答持仓、盈亏、仓位集中度 |
| `search_artifacts` | artifacts 表 | 查询历史报告、每日选股、反思记录 |
| `get_recent_runs` | runs 表 | 解释最近任务状态 |
| `get_mcp_factor_snapshot` | StockManager MCP | 查询单票估值、资金流、动量、质量 |
| `get_strategy_lessons` | strategy_lessons 表 | 引用近期反思经验 |

验收标准：

1. 用户问“刚才为什么推荐生益科技”时，ChatAgent 能搜索最近 Library artifact 并自然解释，不启动新 run。
2. 用户问“中际旭创现在估值和资金流怎么样”时，ChatAgent 调 MCP 快照并回答，附 `as_of_date/source/warnings`。
3. 用户说“跑一下每日选股 top 5”时，仍触发 `daily_pipeline` Skill，并显示任务卡。
4. 用户说“帮我比较两只持仓哪个更该减仓”时，先读取持仓和最近风险，如果需要深度分析再建议触发 Skill。

### 验收标准

1. 系统启动后自动连接 StockManager MCP，连接失败时降级到本地数据源
2. `GET /api/v1/health` 可返回 StockManager MCP 连接状态和能力 flags
3. Settings 页面可配置投资风格（短线/长线），切换后影响 DailyPipeline 因子权重
4. 每日 8:30 自动运行选股 Pipeline；Watchlist 可手动触发和查看运行记录
5. Portfolio 页面展示持仓 P&L；风险预警由 `risk_monitor` Skill 生成报告，仪表盘分级待增强
6. Chat 输入"持仓风险/风险监控"可路由到 RiskMonitor；"宁德时代要不要卖"的 PositionAdvisor 待 Phase 4 实现

---

## 7. Phase 4 — ChatAgent + 自动化 + 学习闭环（当前重点）

### Week 13: Free ChatAgent

| 项 | 说明 |
|----|------|
| ChatAgent | 新增 `tradingagents/core/chat_agent.py`，输出 `chat_answer/tool_answer/skill_run/clarify` |
| Tool Registry | 新增轻量工具：持仓摘要、Artifact 搜索、最近 runs、MCP 因子快照、策略经验 |
| WS /chat | 非 Skill 回复直接返回 `chat_reply`；Skill run 继续创建 run 并流式展示 |
| 前端 | MessageList 支持普通 assistant 回复、工具引用卡、Skill 任务卡三种展示 |

### Week 14: PositionAdvisor 持仓建议

| 项 | 说明 |
|----|------|
| Skill | 新增 `tradingagents/skills/position_advisor/`；复用 StockAnalysis 持仓上下文（成本价/浮盈/仓位占比/备注） |
| Orchestrator / ChatAgent | 「卖出/加仓/减仓/止损」可由 ChatAgent 判断是轻量回答还是触发 PositionAdvisor |
| MCP 集成 | 可选调用 `generate_trading_plan` 获取 Flight Plan（吊灯止损价/跳空过滤区间） |

### Week 15: 回测 + 复盘

| 项 | 说明 |
|----|------|
| StrategyBacktest Skill | 封装 MCP `run_backtest`，前端表单 → 异步执行 → 结果展示 |
| DecisionAudit Skill | 月末复盘：系统推荐 vs 实际操作 vs 实际收益，归因分析 |
| MCP 集成 | `run_backtest` / `purged_cv_sharpe` / `analyze_execution_slippage` |

### Week 16: 记忆增强 + 财务预警

| 项 | 说明 |
|----|------|
| 增强记忆系统 | 已有 reflection cases / strategy lessons 初版；后续做跨标的模式提取（例如“高估值+资金流缺失的样本胜率下降”） |
| 财务风险预警 | MCP `get_financial_metrics`（ROE/利润增速恶化）+ `get_risk_announcements`（问询函/违规） |

### 验收标准

1. 普通聊天不再强行触发 Skill；可解释历史报告、候选标签、策略反思和持仓摘要。
2. 对持仓中的任意股票可说「XX 要不要卖」，系统输出含止损价和卖出原因的建议。
3. 月末可自动生成复盘报告：本月系统推荐胜率 vs 实际操作胜率。
4. 历史决策可回溯：某次推荐 Buy 的标的，N 天后实际涨跌幅。

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

- **自由 ChatAgent 尚未接入**: 当前 Chat 主要是 Skill 触发入口；普通问题不会自动调用 LLM/MCP/Library 形成自然回复。下一步需要引入 `chat_answer/tool_answer/skill_run/clarify` 分层。
- **LLM Router 仅用于路由增强**: 当前 Orchestrator 有规则路由和可选 LLM Router，但它只决定是否触发 Skill，不承担自由聊天和工具增强问答。
- **LLM 快筛依赖统一 Backbone 配置**: DailyPipeline 已有 LLM Reviewer 链路，但真实运行需要 `llm_provider/quick_think_llm/backend_url/API_KEY` 配好；未配置时结果会显式标记 `review_meta.available=false` 并降级为 `quant_only`。
- **Market Scanner 数据深度仍有限**: A 股候选池已切到 StockManager MCP 成分股；收益稳定性取决于 MCP `rank_factor_candidates` 的因子覆盖、行业分散、资金流/估值有效性和回测验证。
- **Phase 1/2 已收尾**: `RunManager` 已集成 SQLite 持久化，报告入库已使用真实 `run_id`，WebSocket 已统一终态事件，Chat 可自然语言触发技能。

### 9.2 StockManager MCP 依赖

- **通信方式已切换**: `mcp_client.py` 已改为 HTTP/Streamable MCP，默认连接 `http://127.0.0.1:8765/mcp`。
- **可用性风险**: StockManager MCP Server 不可用时，TradingAgents 降级到本地 AKShare/TuShare 数据源，但回测/因子/交易计划功能不可用。
- **版本兼容**: MCP Tool 接口变更时需同步更新 `mcp_adapter.py` 和 contract tests。
- **长任务阻塞**: 回测/因子实验必须使用 `job_id` 异步轮询，避免阻塞 Agent 管道和 WebSocket。
- **实盘 smoke 暴露的问题**: 2026-07-01 的 `rank_factor_candidates` 曾返回指数成分为空，MCP 侧需要支持成分日期回退；CSI800 同步排名在较大 `candidate_limit` 下耗时偏长，应缓存或转异步 job。

### 9.3 已修复（第一批 P0，2026-07-05）

- ✅ **MCP Client 断连后永不重连**: `_call_tool` 异常/超时分支现标记 `_connected=False`，下次调用触发 `connect()` 重建；`connect()` 开头清理残留 session/transport。(`tradingagents/core/mcp_client.py`)
- ✅ **Scheduler 时区错误**: `_seconds_until` 改用 `Asia/Shanghai` 时区，Docker/UTC 主机下 daily_pipeline 不再延迟到收盘后。(`tradingagents/core/scheduler.py`)
- ✅ **非交易日空跑 daily_pipeline/reflection**: 调度任务开头检查 `calendar_state`，周末/节假日跳过，避免浪费 token 和产生永远 pending 的反思 case。(`tradingagents/api/app.py`)
- ✅ **Orchestrator 无 ticker 静默回退茅台**: `_route_stock_analysis` / `_route_portfolio` 无标的时返回 `confidence=0.0` 走 LLM router/clarify，不再默认 `600519.SH`。(`tradingagents/core/orchestrator.py`)
- ✅ **Portfolio 数量提取吞入日期**: `_route_portfolio` 提取数字前剥离 `YYYY-MM-DD` 等日期格式，避免 `"2024-01-08 ... 100股 成本2000"` 被解析为 quantity=2024。(`tradingagents/core/orchestrator.py`)
- ✅ **MCP 初始化无超时挂死启动**: `get_mcp_client` 外包 `asyncio.wait_for(timeout=10s)`，超时降级为 `mcp_client=None` 继续启动。(`tradingagents/api/app.py`)

### 9.4 已修复（第二批 P0/P1，2026-07-05）

- ✅ **SQLite 未启用 WAL**: `_conn` 加 `PRAGMA journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=30s`，并发读写不再互斥或立即抛 `database is locked`。(`tradingagents/core/persistence.py`)
- ✅ **MCP `_call_lock` 串行所有调用**: 改为 `asyncio.Semaphore(8)`，RiskMonitor 多持仓扫描可并发，单次超时不再阻塞全部调用。(`tradingagents/core/mcp_client.py`)
- ✅ **反思样本无限累积**: case_id 去掉 `run_id` 改为 `daily_pipeline:{trade_date}:{symbol}`，同日重跑 `INSERT OR REPLACE` 去重；新增 `prune_reflection_cases` 在反思批处理末尾清理 90 天前已反思 case。(`tradingagents/skills/daily_pipeline/skill.py`, `tradingagents/core/persistence.py`, `tradingagents/core/reflection.py`)
- ✅ **反思 accuracy 虚高**: `evaluate_accuracy` 对 WATCHLIST/HOLD 中性决策返回 `None`；`get_reflection_summary` 将中性决策排除出分母并单独统计 `neutral` 计数。(`tradingagents/core/reflection.py`, `tradingagents/core/persistence.py`)
- ✅ **A股 reflection 用 yfinance 取价失效**: `_fetch_returns` 对 `cn_a` 市场改用 `load_ohlcv_cn`（AKShare qfq，与分析同源）取股价，benchmark 仍走 yfinance（CN 指数在 Yahoo 更可靠），benchmark 缺失时 alpha=None 不阻塞。(`tradingagents/graph/trading_graph.py`)
- ✅ **`investment_style` 未注入 13-agent 管道**: 新增 `get_investment_style_instruction(style)`；`create_initial_state` 接收并存储 `investment_style`；`trading_graph` 两处入口从 config 读取并传入；4 个分析师（market/fundamentals/news/sentiment）将风格指令拼入 system_message，短线/长线分析重心与止损/目标价措辞真正差异化。(`tradingagents/agents/utils/agent_utils.py`, `tradingagents/graph/propagation.py`, `tradingagents/agents/analysts/*.py`, `tradingagents/graph/trading_graph.py`)
- ✅ **`astream_propagate` 绕过 checkpointer**: Skill 主入口现镜像 `propagate()` 的 checkpointer 逻辑——`checkpoint_enabled` 时注入 `SqliteSaver` + `thread_id`，`try/finally` 保证 context 清理，成功完成时 `clear_checkpoint` 清除残留。(`tradingagents/graph/trading_graph.py`)

### 9.5 已修复（第三批 P1/P2，2026-07-05）

- ✅ **WebSocket 队列无背压**: `subscribe()` 的 `asyncio.Queue` 加 `maxsize=256`；`_broadcast` 改用 `put_nowait` + drop-oldest 语义，慢客户端不再无限堆积内存。(`tradingagents/core/run_manager.py`)
- ✅ **`cancel_all()` 不 await 被取消任务**: 改为 `asyncio.gather(..., return_exceptions=True)` + 5s 超时，确保关闭时正在写 DB/文件的 skill 干净退出而非被强杀留半写数据。(`tradingagents/core/run_manager.py`)
- ✅ **持仓价格刷新读-改-写竞态**: 新增 `update_holding_price(symbol, price)` 只更新 `current_price` + `updated_at`；`refresh-prices` 路由改用它，不再 `upsert_holding` 全字段回写，避免覆盖并发的用户仓位/成本修改。(`tradingagents/core/persistence.py`, `tradingagents/api/routes/portfolio.py`)
- ✅ **OHLCV 缓存键含 curr_date 致碎片化**: AKShare/TuShare 缓存文件名去掉日期改为每 code 单文件（`{code}-AKShare-data.csv`）；缓存已覆盖 `curr_date` 时直接复用，否则只拉缺口 `[max_date+1, curr_date]` 增量追加，并按 5 年滚动窗口裁剪。磁盘不再随每次运行增长，同日多次分析命中缓存。(`tradingagents/dataflows/akshare_stock.py`, `tradingagents/dataflows/tushare_stock.py`)
- ✅ **RiskMonitor 风险分级过粗**: 从纯计数（<3=orange, ≥3=red）改为关键词严重度分级——`立案/违规/处罚/退市` 直接 red，`问询/减持/质押/业绩预亏/商誉减值` orange，无映射关键词时回退计数；并对相同标题公告去重，避免 routine 减持公告误报 red。(`tradingagents/skills/risk_monitor/skill.py`)

### 9.6 已修复（第四批 中价值快修，2026-07-05）

- ✅ **LLM Router 每次新建 client + prompt 注入风险**: LLM client + bind_tools 在 `__init__` 懒加载缓存，`_get_llm_with_tools` 复用，避免每次路由重建连接；用户消息包 `<user_input>` 标签，system prompt 声明标签内为不可信数据，抵御"忽略指令路由到 X"类注入。(`tradingagents/core/llm_router.py`)
- ✅ **`NAME_TO_TICKER` 仅 11 别名**: 别名表扩展至 47 个（白酒/新能源/金融/有色/半导体/消费制造常见 A 股 + 美股）；新增 `db.search_ticker_by_name(text)` 从 reports 表按子串反查；`Orchestrator._resolve_known_ticker` 字典优先 + DB 兜底，未在硬编码表中的标的（如已分析过的"生益科技"）也能路由。(`tradingagents/core/orchestrator.py`, `tradingagents/core/persistence.py`)
- ✅ **`/ws/chat` 串行无法中断**: run 事件流消费改为后台 `asyncio.Task`，主循环持续 `receive_json`；支持 `{"action":"cancel","run_id"}` 取消当前 run（返回 `run_cancellation_ack`）；`send_lock` 序列化所有 socket 写，避免后台消费者与主循环并发写损坏帧。(`tradingagents/api/ws/stream.py`)
- ✅ **前端 Dashboard 与 WS 重复轮询**: Dashboard/Watchlist 的 `refetchInterval` 拉长（runs 5s→30s, holdings 10s→60s, health/artifacts 15s→60s），加 `staleTime` + `refetchOnWindowFocus:false`；Chat hooks 在 `run_complete`/`error` 时 `invalidateQueries(["runs"],["dashboard-artifacts"],["holdings"])`，WS 事件驱动刷新取代密集轮询。(`frontend/src/pages/Dashboard/index.tsx`, `frontend/src/pages/Watchlist/index.tsx`, `frontend/src/pages/Chat/hooks.ts`)

### 9.7 已修复（第五批 快修，2026-07-05）

- ✅ **无分页**: `list_runs`/`list_artifacts` 新增 `offset` 参数，`limit` 上限钳到 200 防止无界查询；`/runs`、`/artifacts` 路由暴露 `offset`。(`tradingagents/core/persistence.py`, `tradingagents/api/routes/runs.py`, `tradingagents/api/routes/artifacts.py`)
- ✅ **无认证/授权**: 新增 `AuthMiddleware` + `verify_ws_token`；配置 `api_auth_token`（env `TRADINGAGENTS_API_AUTH_TOKEN`）为空时放行（本地桌面默认），设置后 REST 要求 `Authorization: Bearer`、WS 要求 `?token=`；`/health` 豁免。(`tradingagents/api/middleware/auth.py`, `tradingagents/api/app.py`, `tradingagents/api/ws/stream.py`, `tradingagents/default_config.py`)
- ✅ **WebSocket 事件不持久化**: 新增 `run_events` 表；`RunManager._record_event` 把每个事件（含 seq）写入 DB；`/ws/run/{run_id}` 重连时若 run 不在内存（服务重启后），从 DB 回放持久化事件 + 合成终态事件，长跑 skill 中途重启不再丢失进度。(`tradingagents/core/persistence.py`, `tradingagents/core/run_manager.py`, `tradingagents/api/ws/stream.py`)
- ✅ **Artifact 无版本管理**: 新增 `artifact_versions` 表；`save_artifact` 覆盖前把旧版可变字段（title/subtitle/status/summary/content/payload）存档，版本号自增；新增 `GET /artifacts/{id}/versions` 端点查看历史，可回溯报告演化。(`tradingagents/core/persistence.py`, `tradingagents/api/routes/artifacts.py`)


---

## 10. 下一步建议

### 10.1 下一阶段优先推进

1. 实现 Free ChatAgent：意图四分类、轻量工具注册表、自由回复、工具引用卡、Skill Dispatcher。
2. 完善 Chat 上下文读取：Library artifact search、最近 runs、持仓摘要、策略反思摘要、MCP factor snapshot。
3. 将 RiskMonitor 结果沉淀为结构化风险事件，补 Portfolio 风险仪表盘（绿/黄/橙/红）。
4. 为 DailyPipeline 增加可选 Top N 深度 StockAnalysisSkill 串联，并把深度结论回写 candidate payload。
5. 建立更严格的 MCP contract tests：schema 固化、错误信封、timeout、非法返回、job_id 幂等。
6. 实现 PositionAdvisor：结合持仓成本、风险事件、交易计划输出卖出/加仓建议。
7. 实现 StrategyBacktest：封装 MCP `run_backtest` + `get_job_status` / `get_job_result`。

### 10.2 代码质量

1. 补充前端测试（vitest）— 覆盖 store 状态流转、WebSocket 重连
2. 增加浏览器 E2E 自动化 — 覆盖 Dashboard 创建运行、Chat 自然语言路由、Analysis 实时更新、Reports 详情查看
3. MCP Client 单元测试 — 覆盖连接失败降级、数据格式适配
4. ChatAgent 单元测试 — 覆盖 chat/tool/skill/clarify 四类意图和工具失败降级

### 10.3 后续规划

1. Phase 4: Free ChatAgent + PositionAdvisor + 回测 + 记忆增强
2. Phase 5: Tauri 桌面打包
3. 继续增强 Orchestrator/ChatAgent：更多中文股票别名、参数澄清、多轮上下文
