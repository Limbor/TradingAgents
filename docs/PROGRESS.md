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
| **界面** | macOS 原生桌面应用（Tauri v2），实时流式展示 agent 执行 |
| **架构** | "单一分析管道" → "可插拔技能框架"，支持动态加载多个 Skill |
| **交互** | 自然语言意图路由（用户说话即可触发对应技能） |
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
Phase 3 (MCP 集成 + 决策增强) ░░░░░░░░░░░░░░░░░░░░░░   ~0%  ← 当前重点
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
| 额外 Skill (Portfolio/Scanner 等) | ✅ 完成 | portfolio_management / market_scanner |
| 集成/E2E 测试 | ✅ 完成 | [tests/integration/test_phase1_flow.py](../tests/integration/test_phase1_flow.py) |
| **Phase 3+ 新增模块** | | |
| MCP Client 基础设施 | ❌ 待建 | 对接 StockManager MCP Server |
| UserProfile 投资风格配置 | ❌ 待建 | 短线/长线参数 + API + 前端 |
| Scheduler 定时调度 | ❌ 待建 | asyncio 后台协程 + FastAPI lifespan |
| DailyPipeline 每日选股编排 | ❌ 待建 | MCP 筛选 + Agent 分析 + 早报 |
| RiskMonitor 风险监控 | ❌ 待建 | MCP 公告扫描 + 持仓巡检 |
| PositionAdvisor 持仓建议 | ❌ 待建 | 卖出/加仓建议（复用 Agent 管道） |
| StrategyBacktest 回测 Skill | ❌ 待建 | 委托 MCP `run_backtest()` |
| DecisionAudit 决策复盘 | ❌ 待建 | 历史决策 vs 实际收益追踪 |
| 前端 Portfolio 持仓页 | ❌ 待建 | 持仓列表 + P&L + 风险仪表盘 |
| 前端 Watchlist 关注页 | ❌ 待建 | 每日选股结果 + 评分展示 |
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
- A 股 / 美股候选池筛选
- 动量、流动性、质量、风险综合评分
- 输出候选列表和 scanner report
- 离线确定性实现，后续可替换为 AKShare/yfinance 实时筛选源

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

### Week 9: MCP Client 基础设施

| 项 | 说明 |
|----|------|
| MCP Client | `tradingagents/core/mcp_client.py` — 封装 `mcp` SDK，连接 StockManager MCP Server |
| 配置项 | `default_config.py` 新增 `mcp_stockmanager_url`（默认 `http://localhost:8000/mcp`） |
| 连接管理 | lifespan 中自动连接，失败时降级到本地 AKShare/TuShare |
| 数据适配层 | `tradingagents/dataflows/mcp_adapter.py` — 将 MCP 返回数据转换为现有 `interface.py` 兼容格式 |

### Week 10: UserProfile + Scheduler

| 项 | 说明 |
|----|------|
| UserProfile | `tradingagents/core/user_profile.py` — Pydantic Schema（investment_style / risk_tolerance / sector_prefs） |
| SQLite 表 | `user_profile` 表（通过 `persistence.py` Schema 扩展） |
| API 端点 | `GET/PUT /api/v1/profile` |
| Scheduler | `tradingagents/core/scheduler.py` — asyncio 后台协程，注册定时任务，输出 Markdown 报告 |
| 前端 | Settings 页面新增「投资风格」配置卡片 |

### Week 11: DailyPipeline 每日选股

| 项 | 说明 |
|----|------|
| 全市场初筛 | MCP `get_index_constituents` → 规则过滤（排除 ST/低流动性） |
| 多因子打分 | 短线因子（动量/换手/北向/板块热度）vs 长线因子（ROE/PE分位/毛利率/营收增速），权重由 `UserProfile.investment_style` 决定 |
| Top 5 深度分析 | 对得分最高的 5 只，跑完整 StockAnalysisSkill |
| 早报生成 | Markdown 格式，存入 `reports/` + SQLite |
| 前端 | Dashboard 新增「每日选股」卡片 + Watchlist 页面展示历史选股结果 |

### Week 12: RiskMonitor + 前端补全

| 项 | 说明 |
|----|------|
| RiskMonitor | 每日扫描持仓：ST 检测 / 质押预警 / 解禁提醒 / MCP `get_risk_announcements` |
| 前端 Portfolio 页 | 持仓列表 + P&L + 风险仪表盘（绿/黄/橙/红分级） |
| 前端增强 | Chat 页面新增「卖出建议」「加仓建议」快捷触发按钮 |

### 验收标准

1. 系统启动后自动连接 StockManager MCP，连接失败时降级到本地数据源
2. Settings 页面可配置投资风格（短线/长线），切换后影响选股因子权重和 Agent Prompt
3. 每日 8:30 自动运行选股 Pipeline，Dashboard 可见最新结果
4. Portfolio 页面展示持仓 P&L 和风险预警
5. Chat 输入"宁德时代要不要卖"可路由到 PositionAdvisor，输出含成本价上下文的建议

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
- **Market Scanner 硬编码**: 当前 `market_scanner` Skill 的候选池为硬编码 6 只 A 股，Phase 3 DailyPipeline 将替换为 MCP 驱动的动态筛选。
- **Phase 1/2 已收尾**: `RunManager` 已集成 SQLite 持久化，报告入库已使用真实 `run_id`，WebSocket 已统一终态事件，Chat 可自然语言触发技能。

### 9.2 StockManager MCP 依赖

- **可用性风险**: StockManager MCP Server 不可用时，TradingAgents 降级到本地 AKShare/TuShare 数据源，但回测/因子/交易计划功能不可用。
- **版本兼容**: MCP Tool 接口变更时需同步更新 `mcp_adapter.py`。

---

## 10. 下一步建议

### 10.1 Phase 3 优先推进

1. 建立 MCP Client 基础设施（`mcp_client.py` + `mcp_adapter.py`）
2. 实现 UserProfile 配置系统（SQLite + API + 前端）
3. 实现 Scheduler 框架（asyncio 后台协程）
4. 实现 DailyPipeline 初版（MCP 筛选 + 规则打分，先不做 Top 5 深度分析）
5. 补全前端 Portfolio 页 + Settings 投资风格卡片

### 10.2 代码质量

1. 补充前端测试（vitest）— 覆盖 store 状态流转、WebSocket 重连
2. 增加浏览器 E2E 自动化 — 覆盖 Dashboard 创建运行、Chat 自然语言路由、Analysis 实时更新、Reports 详情查看
3. MCP Client 单元测试 — 覆盖连接失败降级、数据格式适配

### 10.3 后续规划

1. Phase 4: PositionAdvisor + 回测 + 记忆增强
2. Phase 5: Tauri 桌面打包
3. 继续增强 Orchestrator：加入 LLM fallback、更多中文股票别名、参数澄清
