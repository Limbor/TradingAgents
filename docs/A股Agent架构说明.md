# TradingAgents · A股决策Agent架构说明

> 版本：v2.0 (2026-07)
> 定位：面向个人A股投资者的多智能体LLM交易决策平台

---

## 目录

1. [项目概述](#1-项目概述)
2. [产品形态：Chat-First + 今日驾驶舱](#2-产品形态chat-first--今日驾驶舱)
3. [整体架构](#3-整体架构)
4. [核心Agent管道](#4-核心agent管道)
5. [Skill技能框架](#5-skill技能框架)
6. [每日选股流水线 (DailyPipeline)](#6-每日选股流水线-dailypipeline)
7. [信号融合引擎 (SignalFusion)](#7-信号融合引擎-signalfusion)
8. [候选股实时增强 (CandidateEnrichment)](#8-候选股实时增强-candidateenrichment)
9. [持仓风险监控 (RiskMonitor)](#9-持仓风险监控-riskmonitor)
10. [MCP集成：与StockManager的协作](#10-mcp集成与stockmanager的协作)
11. [用户画像与调度 (UserProfile + Scheduler)](#11-用户画像与调度-userprofile--scheduler)
12. [前端页面](#12-前端页面)
13. [配置与环境变量](#13-配置与环境变量)
14. [使用场景](#14-使用场景)
15. [当前进度与路线图](#15-当前进度与路线图)

---

## 1. 项目概述

TradingAgents 是一个基于LangGraph的**多智能体金融交易分析框架**，模拟真实交易公司的组织结构——

| 团队 | 成员 | 职责 |
|------|------|------|
| **分析师团队** | 市场 / 舆情 / 新闻 / 基本面分析师 | 从四个维度独立产出分析报告 |
| **研究员团队** | 多头研究员 ↔ 空头研究员 → 研究经理 | 结构化多空辩论 + 汇总裁决 |
| **交易员** | Trader | 整合分析结果生成交易提案 |
| **风控团队** | 激进 / 保守 / 中性风控分析师 | 三方风险辩论 |
| **组合经理** | Portfolio Manager | 最终批准/拒绝，输出评级（Buy → Sell 五档） |

v2.0正在将项目从单一CLI工具升级为**可插拔技能Agent平台**：通过 Chat-First 对话界面 + Dashboard 驾驶舱，用户可以自然语言触发A股选股、风险扫描、持仓分析等技能，系统自动编排LangGraph Agent管道和StockManager量化计算引擎。

---

## 2. 产品形态：Chat-First + 今日驾驶舱

```
┌─────────────────────────────────────────────────────┐
│  Chat（动作中枢）           Dashboard（感知中枢）     │
│  ┌───────────────────────┐ ┌───────────────────────┐│
│  │ "分析宁德时代"          │ │ 持仓 KPI · 盈亏概览    ││
│  │ "每日选股 top 10"      │ │ 今日选股结果 · 候选排名 ││
│  │ "持仓风险扫描"          │ │ 风险预警 · 时间线动态   ││
│  │ "筛选新能源板块"        │ │ 快捷Skill按钮          ││
│  │ "贵州茅台要不要卖"      │ │ ─ 每日选股             ││
│  │                        │ │ ─ 风险扫描             ││
│  │ 自然语言 → 意图路由    │ │ ─ 分析个股             ││
│  │ → Skill执行 → 流式结果 │ │                        ││
│  └───────────────────────┘ └───────────────────────┘│
└─────────────────────────────────────────────────────┘
```

- **Chat** 是核心工作区：所有操作指令（选股、分析、风控）通过对话触发
- **Dashboard** 是感知中枢：聚合持仓KPI、选股结果、风险事件、快捷操作入口
- 导航精简为四个入口：Dashboard / Chat / Reports / Settings

---

## 3. 整体架构

### 3.1 六层架构

```
┌──────────────────────────────────────────────────────────┐
│ Presentation    React 18 + TypeScript + Zustand + Recharts│
├──────────────────────────────────────────────────────────┤
│ API Gateway     FastAPI + WebSocket (REST / WS)           │
├──────────────────────────────────────────────────────────┤
│ Orchestration   Orchestrator 意图路由 + SkillRegistry     │
├──────────────────────────────────────────────────────────┤
│ Skill           可插拔技能 (DailyPipeline / RiskMonitor    │
│                  / StockAnalysis / Portfolio / Scanner)   │
├──────────────────────────────────────────────────────────┤
│ Core            LLM Factory · DataFlows · SignalFusion    │
│                 · Persistence · EventBus · Scheduler      │
├──────────────────────────────────────────────────────────┤
│ Infrastructure  Tauri v2 (远期) · MCP Client · SQLite     │
└──────────────────────────────────────────────────────────┘
```

### 3.2 决策层与量化层的分工

TradingAgents定位为"决策层Agent"，量化计算（回测/因子/选股/数据）委托给 StockManager MCP Server：

```
TradingAgents (决策层)              StockManager MCP (量化层)
┌─────────────────────────┐       ┌──────────────────────────┐
│ Agent管道 (13 agents)    │       │ 数据查询: OHLCV/财务/行业  │
│ LLM分析/辩论/决策         │ MCP   │ 因子实验: eval_formula     │
│                          │◄─────►│ 回测引擎: run_backtest     │
│ Skill框架                │ HTTP  │ 交易计划: trading_plan     │
│ 信号融合/候选增强          │       │ 风控公告: risk_announce   │
│ 用户画像/调度器            │       │ 成分股: index_constituents │
└─────────────────────────┘       └──────────────────────────┘
```

---

## 4. 核心Agent管道

### 4.1 管道流程

```
用户输入(ticker+date)
    │
    ▼
┌───────────────┐
│ ① 分析阶段     │  四个分析师并发执行：
│               │  Market / Sentiment / News / Fundamentals
│               │  各自调用对应的数据工具获取A股数据
└───────┬───────┘
        ▼
┌───────────────┐
│ ② 研究辩论     │  Bull Researcher ↔ Bear Researcher
│               │  多轮结构化辩论 → Research Manager 裁决
└───────┬───────┘
        ▼
┌───────────────┐
│ ③ 交易决策     │  Trader 整合分析报告 → 生成交易提案
│               │  输出：Buy / Hold / Sell + 止损价/仓位
└───────┬───────┘
        ▼
┌───────────────┐
│ ④ 风险辩论     │  Aggressive / Conservative / Neutral
│               │  三方风险辩论 → 风险评估
└───────┬───────┘
        ▼
┌───────────────┐
│ ⑤ 组合决策     │  Portfolio Manager 最终裁决
│               │  输出：Buy/Overweight/Hold/Underweight/Sell
└───────────────┘
```

### 4.2 A股特有的数据工具

基础分析工具：
| 工具 | 数据来源 | 说明 |
|------|---------|------|
| `get_stock_data` | AKShare / TuShare | A股OHLCV行情 |
| `get_indicators` | AKShare / TuShare | 技术指标(MACD/RSI/布林带等) |
| `get_fundamentals` | TuShare | 财务指标(ROE/PE/毛利率等) |
| `get_news` | AKShare | 个股新闻 |
| `get_balance_sheet` / `get_income_statement` / `get_cashflow` | TuShare | 三大报表 |

A股专属工具：
| 工具 | 说明 |
|------|------|
| `get_social_sentiment` | A股社交媒体舆情（雪球/东方财富） |
| `get_announcements` | 公司公告（业绩预告/问询函/减持等） |
| `get_macro_calendar` | 宏观事件日历（LPR/PMI/CPI等） |
| `get_market_structure_snapshot` | 市场微观结构（涨跌停/封单/炸板） |
| `get_theme_heat` | 板块热度排名 |
| `get_lhb_detail` | 龙虎榜明细 |
| `get_limit_status` | 涨跌停状态检测 |
| `get_northbound_flow` | 北向资金（沪股通/深股通）流向 |
| `get_margin_balance` | 融资融券余额 |
| `get_unlock_schedule` | 限售股解禁时间表 |

### 4.3 结构化输出

三个关键决策Agent使用Pydantic Schema确保输出一致性：

- **ResearchPlan**：推荐评级(Buy→Sell五档) + 理由 + 策略行动
- **TraderProposal**：交易方向 + 理由 + 入场价/止损价/仓位
- **PortfolioDecision**：最终评级 + 执行摘要 + 投资论据 + 目标价/时间周期

同时，**SentimentReport** 使用结构化输出（Bullish→Bearish六档 + 0-10分 + 信心等级），替代了原来的自由格式文本。

### 4.4 长短线差异

Agent行为通过 `investment_style` 参数驱动差异化：

| 维度 | 短线 (short_term) | 中线 (medium_term) | 长线 (long_term) |
|------|-------------------|-------------------|-------------------|
| 持有周期 | 3-10个交易日 | 2-4周 | 1-3个月 |
| 分析重心 | 动量/换手/资金流 | 动量+质量平衡 | ROE/毛利率/营收增速 |
| 因子权重(量化) | α=0.70 | α=0.55 | α=0.35 |
| 止损方式 | ATR×2 / -8% | ATR×2 / -8% | ATR×2 / -8% |
| 目标价 | ATR×2,4 / +8%,+16% | 同左 | 同左 |

---

## 5. Skill技能框架

### 5.1 已注册的A股相关技能

| Skill ID | 名称 | 类别 | 触发词 | 状态 |
|----------|------|------|--------|------|
| `stock_analysis` | 个股深度分析 | analysis | 分析/看看/研报 | ✅ |
| `daily_pipeline` | 每日选股流水线 | scanner | 每日选股/早报/今日机会 | ✅ 初版 |
| `risk_monitor` | 持仓风险监控 | portfolio | 风险监控/持仓风险/预警 | ✅ 初版 |
| `portfolio_management` | 持仓管理 | portfolio | 持仓/仓位/组合 | ✅ |
| `market_scanner` | 市场筛选 | scanner | 筛选/选股/扫描 | ✅ |
| `position_advisor` | 持仓建议 | portfolio | 卖出/加仓建议 | ❌ 待建 |
| `strategy_backtest` | 策略回测 | experiment | 回测 | ❌ 待建 |
| `decision_audit` | 决策复盘 | analysis | 复盘 | ❌ 待建 |

### 5.2 Skill接口

每个Skill实现标准接口：

```python
class BaseSkill(ABC):
    metadata: SkillMetadata          # id, name, description, triggers, icon, category
    input_schema: type[BaseModel]    # Pydantic输入参数模型
    output_schema: type[BaseModel]   # Pydantic输出结果模型

    async def execute(params, config) -> AsyncIterator[SkillEvent]:
        """执行技能，流式返回 SkillEvent"""
        ...

    async def cancel(): ...
```

Skill通过 `SkillRegistry.auto_discover()` 自动发现和注册（约定：每个子包的 `__init__.py` 导出 `skill` 实例）。

### 5.3 意图路由 (Orchestrator)

当用户在Chat中输入自然语言时，`Orchestrator` 将文本路由到对应Skill：

| 用户输入示例 | 路由到 | 提取参数 |
|-------------|--------|---------|
| "每日选股 top 10" | daily_pipeline | limit=10 |
| "分析宁德时代" | stock_analysis | ticker=300750.SZ |
| "持仓风险扫描" | risk_monitor | lookback_days=30 |
| "白酒板块筛选" | market_scanner | theme=白酒, market=cn_a |
| "添加100股茅台成本1800" | portfolio_management | action=upsert, qty=100, cost=1800 |

路由优先级：关键词匹配 → ticker提取 → trigger匹配。

---

## 6. 每日选股流水线 (DailyPipeline)

这是v2.0最核心的A股新增功能——每日自动化从全市场筛出最优候选股。

### 6.1 执行流程

```
┌──────────────────────────────────────────────────────────────────┐
│ ① 候选池获取（Quant层）                                          │
│   MCP rank_factor_candidates(universe=CSI800, style=投资风格)     │
│   → 全市场初筛：过滤ST/停牌/低流动性/一字涨跌停                   │
│   ↓                                                              │
│ ② 多因子打分（Quant层）                                          │
│   短线因子：动量/换手/北向/板块热度                                │
│   长线因子：ROE/PE分位/毛利率/营收增速                             │
│   权重由 UserProfile.investment_style 决定                        │
│   ↓                                                              │
│ ③ 板块过滤（本地）                                               │
│   board_filter: all / main_board / dual_growth_only              │
│   根据代码前缀自动识别板块(300→创业板, 688→科创板, 83→北交所)     │
│   ↓                                                              │
│ ④ 信号融合（本地）                                               │
│   fuse_candidate_signal() → quant_decision + 入场区/止损/目标价   │
│   ↓                                                              │
│ ⑤ 实时增强（本地，并发）                                         │
│   并发拉取每只候选的新闻/公告/北向资金 → CandidateContext         │
│   ↓                                                              │
│ ⑥ LLM复核（并发 asyncio.gather）                                 │
│   注入实时上下文(CandidateContext) → LLM评估催化剂/风险/可交易性  │
│   无API key时降级为 quant_only                                    │
│   ↓                                                              │
│ ⑦ 决策门控（decision_gate）                                      │
│   quant_decision + llm_review → final_decision:                  │
│   BUY / WATCHLIST / MONITOR / HOLD_REVIEW / SKIP                 │
│   ↓                                                              │
│ ⑧ 报告生成 + 信号入库                                            │
│   Markdown早报 + 候选决策包 → WebSocket推送到前端                 │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 输入参数

```python
class DailyPipelineInput(BaseModel):
    trade_date: str = "2026-07-04"       # 交易日
    universe_index: str = "000906.SH"    # 股票池指数(默认中证800)
    limit: int = 5                       # 最终返回候选数(1-20)
    candidate_limit: int = 80            # MCP层候选池大小(5-800)
    board_filter: Literal["all", "main_board", "dual_growth_only"] = "all"
    exclude_boards: list[str] = []       # 附加板块排除：star/chinext/beijing/main
```

### 6.3 决策状态机

每只候选股经过以下状态流转：

```
quant_decision = BUY ──→ LLM Review ──┬── llm confirms (view=positive + catalyst=confirmed/likely)
                                      │    → final_decision = BUY + position_pct > 0
                                      ├── llm neutral
                                      │    → final_decision = WATCHLIST
                                      ├── llm negative
                                      │    → final_decision = HOLD_REVIEW
                                      └── llm unavailable
                                           → final_decision = WATCHLIST (保守默认)

quant_decision ≠ BUY ──→ 不再递进，维持MONITOR/SKIP
```

- **BUY**：量化buy + LLM确认，有仓位分配（4%-10%按分数梯度）
- **WATCHLIST**：值得关注，待进一步确认
- **MONITOR**：保持观察
- **HOLD_REVIEW**：有矛盾信号，需人工复核
- **SKIP**：ST/停牌/一字板/LLM推翻量化判断

---

## 7. 信号融合引擎 (SignalFusion)

`tradingagents/core/signal_fusion.py` 实现了v2版的量化×LLM信号融合。

### 7.1 融合公式

```
当LLM可用时（quant_llm_fused）:
  raw_score = α × quant_score + (1-α) × llm_confidence + catalyst_bonus

当LLM不可用时（quant_only）:
  final_score = quant_score

α值（由投资风格决定）:
  short_term  → α = 0.70  (量化主导)
  medium_term → α = 0.55  (均衡)
  long_term   → α = 0.35  (LLM主导)
```

### 7.2 催化剂加成

```
catalyst_score ≥ 80 → +5.0  （高确定性催化剂）
catalyst_score ≥ 65 → +2.0
catalyst_score ≤ 20 → -3.0  （无催化剂）
catalyst_score ≤ 35 → -1.0
其他               →  0.0
```

### 7.3 风险分级

风险标记按严重程度自动分级：

| 级别 | 关键词示例 | 仓位影响 |
|------|-----------|---------|
| **critical** | ST、退市预警、一字跌停、违规处罚 | 仓位归零，final_decision=SKIP |
| **moderate** | 大股东减持、质押比例高、商誉减值、业绩预亏 | 仓位减半(×0.5) |
| **info** | 其他警告标记 | 仓位打八折(×0.8) |

### 7.4 ATR自适应定价

当StockManager返回ATR数据时，使用ATR计算入场区/止损/目标价；否则使用固定百分比回退：

| 参数 | ATR模式 | 固定%回退 |
|------|---------|----------|
| 入场区 | [price - 0.5×ATR, price + 0.3×ATR] | [price×0.985, price×1.01] |
| 止损价 | price - 2.0×ATR | price×0.92 |
| 目标价 | [price + 2.0×ATR, price + 4.0×ATR] | [price×1.08, price×1.16] |

---

## 8. 候选股实时增强 (CandidateEnrichment)

`tradingagents/core/candidate_enrichment.py` 在LLM复核前为每只候选股并发拉取最新市场信息。

### 8.1 拉取策略

```
每个候选股并发拉取（asyncio.gather）：
┌──────────────────────────────────────────────┐
│ 新闻 (AKShare)          近3天 · 最多3条      │
│ 公告 (AKShare)          近7天 · 最多3条      │
│ 风险公告 (MCP)          近30天 · 关键词扫描   │
│ 北向资金 (AKShare)       全局共享 · 仅拉1次   │
└──────────────────────────────────────────────┘
超时控制：每个拉取8秒超时，失败静默降级
```

### 8.2 CandidateContext结构

```python
@dataclass
class CandidateContext:
    news_summary: str           # 近期新闻摘要
    announcement_summary: str   # 重要公告摘要
    northbound_signal: str      # 北向资金流向
    risk_events: list[str]      # 风险事件列表

    def to_prompt_section(self) -> str:
        """渲染为LLM prompt注入段"""
```

这些实时上下文被注入到LLM Review的prompt中，让LLM能够基于最新信息做出催化剂判断和风险评估。

---

## 9. 持仓风险监控 (RiskMonitor)

`tradingagents/skills/risk_monitor/skill.py` 实现对持仓标的的每日风险扫描。

### 9.1 执行流程

```
① 读取持仓列表（SQLite holdings表）
    ↓
② 对每只持仓，调用 MCP get_risk_announcements
   关键词：立案/问询/违规/处罚/减持
   回看天数：默认30天（可配置1-365天）
    ↓
③ 风险分级
   0条匹配 → green   （无风险）
   1-2条   → orange  （关注）
   3条以上 → red     （警告）
   MCP不可用 → unknown
    ↓
④ 生成风险报告 → WebSocket推送到前端
```

### 9.2 输入参数

```python
class RiskMonitorInput(BaseModel):
    lookback_days: int = 30                          # 回看天数
    keywords: list[str] = ["立案","问询","违规","处罚","减持"]  # 扫描关键词
```

---

## 10. MCP集成：与StockManager的协作

### 10.1 通信方式

| 项目 | 说明 |
|------|------|
| 协议 | HTTP/Streamable MCP (Model Context Protocol) |
| 地址 | `http://127.0.0.1:8765/mcp`（默认，可配置） |
| 客户端 | `tradingagents/core/mcp_client.py` — 单例、异步、自动重连 |
| 能力发现 | 启动时调用 `/health` + `/capabilities` 获取能力标志位 |

### 10.2 委托给StockManager的能力

| 能力 | MCP Tool | TradingAgents用途 |
|------|----------|------------------|
| A股OHLCV | `get_stock_daily` | Agent分析的行情数据 |
| 财务指标 | `get_financial_metrics` | 基本面分析 |
| 行业分类 | `get_industry_map` | 行业分布分析 |
| 指数成分股 | `get_index_constituents` | DailyPipeline候选池 |
| 因子排名 | `rank_factor_candidates` | DailyPipeline核心选股 |
| 因子快照 | `get_factor_snapshot` | 技术指标补充 |
| 风控公告 | `get_risk_announcements` | RiskMonitor + CandidateEnrichment |
| 回测引擎 | `run_backtest` | StrategyBacktest(待建) |
| 交易计划 | `generate_trading_plan` | PositionAdvisor(待建) |

### 10.3 降级策略

MCP不可用时：
- DailyPipeline → `mcp_used=False`，不返回量化候选（或使用demo fallback）
- RiskMonitor → 标记为 `unknown` 级别
- 基础Agent分析 → 回退到本地AKShare/TuShare数据源
- UI中不可用的能力（回测/因子实验）置灰并显示原因

### 10.4 能力标志位

```python
capability_flags = {
    "market_data_available": bool,        # 行情数据可用
    "factor_available": bool,             # 因子计算可用
    "backtest_available": bool,           # 回测引擎可用
    "trading_plan_available": bool,       # 交易计划可用
    "risk_announcement_available": bool,  # 风控公告可用
}
```

---

## 11. 用户画像与调度 (UserProfile + Scheduler)

### 11.1 用户画像

```python
class UserProfile(BaseModel):
    investment_style: Literal["short_term", "medium_term", "long_term"] = "long_term"
    risk_tolerance: Literal["low", "moderate", "high"] = "moderate"
    sector_prefs: list[str] = []          # 行业偏好
    updated_at: str | None = None
```

画像影响范围：
- DailyPipeline因子权重（短线=动量主导，长线=质量主导）
- Agent管道prompt注入（分析重心/风险框架/持仓周期措辞）
- 信号融合的α值

通过 `GET/PUT /api/v1/profile` API管理，存储在SQLite `user_profile` 表。

### 11.2 调度器

```python
class Scheduler:
    """单进程定时调度器，在FastAPI lifespan中运行"""
    register_daily("daily_pipeline", time(8, 30), run_daily_pipeline)
```

启动时自动注册：**每个交易日8:30执行每日选股Pipeline**

使用asyncio事件循环实现，不依赖外部cron，适合桌面/本地部署。

---

## 12. 前端页面

| 页面 | 路由 | 核心功能 |
|------|------|---------|
| **Dashboard** | `/` | 持仓KPI概览、今日选股结果、风险预警时间线、快捷Skill按钮 |
| **Chat** | `/chat` | 自然语言命令中心：选股/分析/风控/持仓操作 |
| **Analysis** | `/analysis/:runId` | Agent管道可视化（节点图）+ 分Tab报告详情 |
| **Reports** | `/reports` | 历史报告列表 + Markdown渲染 |
| **Portfolio** | `/portfolio` | 持仓CRUD + P&L展示（待增强风险仪表盘） |
| **Watchlist** | `/watchlist` | 手动触发每日选股 + 近期运行列表 |
| **Settings** | `/settings` | LLM配置 / MCP连接 / 投资风格 / 数据源 |

前端技术栈：React 18 + TypeScript + Vite + Zustand + @tanstack/react-query + Tailwind CSS

WebSocket实时通信：通过 `ws://localhost:8765/ws/run/{run_id}` 流式推送Agent状态、报告片段、工具调用、Skill进度。

---

## 13. 配置与环境变量

### 13.1 关键A股配置

```python
# default_config.py 中的A股相关配置

# 投资风格
"investment_style": "long_term",               # TRADINGAGENTS_INVESTMENT_STYLE

# A股数据供应商
"data_vendors": {
    "core_stock_apis":      {"cn_a": "akshare, tushare"},
    "technical_indicators": {"cn_a": "akshare, tushare"},
    "fundamental_data":     {"cn_a": "tushare"},
    "news_data":            {"cn_a": "akshare"},
    "cn_market_specific":   {"cn_a": "akshare"},
}

# StockManager MCP
"stockmanager_mcp_url":     "http://127.0.0.1:8765/mcp",  # STOCKMANAGER_MCP_URL
"stockmanager_mcp_enabled": True,                          # STOCKMANAGER_MCP_ENABLED
"stockmanager_mcp_timeout": 120.0,                         # STOCKMANAGER_MCP_TIMEOUT

# 调度器
"scheduler_enabled": True,                    # TRADINGAGENTS_SCHEDULER_ENABLED

# 每日选股
"daily_pipeline_llm_review_enabled": True,    # TRADINGAGENTS_DAILY_PIPELINE_LLM_REVIEW_ENABLED
"daily_pipeline_llm_review_limit": 5,         # TRADINGAGENTS_DAILY_PIPELINE_LLM_REVIEW_LIMIT
"daily_pipeline_board_filter": "all",         # TRADINGAGENTS_DAILY_PIPELINE_BOARD_FILTER
```

### 13.2 A股基准指数

```python
"benchmark_map": {
    ".SH": "000001.SS",    # 上证综指
    ".SZ": "399001.SZ",    # 深证成指
}
```

A股分析自动使用对应的中国基准指数进行Alpha计算。

---

## 14. 使用场景

### 14.1 每日自动化（无需用户触发）

| 时间 | 任务 | Skill | 说明 |
|------|------|-------|------|
| 08:30 | 每日选股 | DailyPipeline | 中证800候选池 → 量化排名 → LLM复核 → 生成早报 |
| 盘中 | 风险巡检 | RiskMonitor | 扫描持仓公告/ST/停牌/违规等风险 |

### 14.2 盘中按需触发（Chat对话）

| 用户输入 | 路由 | 输出 |
|---------|------|------|
| "分析宁德时代" | StockAnalysis | 13-agent完整管道分析 + 结构化决策报告 |
| "每日选股 top 10 只看主板" | DailyPipeline | 主板候选排名 + BUY/WATCHLIST/MONITOR |
| "持仓风险扫描最近60天" | RiskMonitor | 风险分级报告(green/orange/red) |
| "添加100股茅台成本1800" | PortfolioManagement | 持仓入库 + P&L计算 |
| "新能源板块筛选" | MarketScanner | 板块候选 + 综合评分 |

### 14.3 周期性复盘（后续规划）

- 持仓周报：持仓表现、策略回顾
- 月末复盘：系统推荐 vs 实际操作 vs 实际收益
- 策略回测：委托MCP `run_backtest` 验证选股策略

---

## 15. 当前进度与路线图

```
Phase 1 (API + 基础前端)     ██████████████████████  100%  已完成
Phase 2 (技能扩展 + 对话)     ██████████████████████  100%  已完成
Phase 3 (MCP集成 + 决策增强)  ███████████████░░░░░░░  ~70%  可运行初版
Phase 4 (自动化 + 学习闭环)    ░░░░░░░░░░░░░░░░░░░░░░   ~0%  后续
Phase 5 (桌面打包)             ░░░░░░░░░░░░░░░░░░░░░░   ~0%  远期
```

### 当前优先推进项

1. **DailyPipeline Top5深度分析**：将得分最高的候选送入完整的13-Agent管道
2. **Portfolio风险仪表盘**：绿/黄/橙/红四级风险可视化
3. **PositionAdvisor**：结合持仓成本+风险事件+交易计划的卖出/加仓建议
4. **StrategyBacktest**：封装MCP回测引擎的前端交互
5. **MCP合约测试**：schema固化、错误信封、timeout、job_id幂等

---

## 附录：关键文件索引

| 模块 | 文件 | 说明 |
|------|------|------|
| **Agent管道** | `tradingagents/graph/trading_graph.py` | LangGraph工作流编排 |
| **Agent Schema** | `tradingagents/agents/schemas.py` | 结构化输出Pydantic模型 |
| **Skill框架** | `tradingagents/skills/base.py` | BaseSkill抽象 + SkillEvent |
| **Skill注册** | `tradingagents/skills/registry.py` | SkillRegistry自动发现 |
| **每日选股** | `tradingagents/skills/daily_pipeline/skill.py` | 全流程编排(802行) |
| **风险监控** | `tradingagents/skills/risk_monitor/skill.py` | 持仓风险扫描(223行) |
| **信号融合** | `tradingagents/core/signal_fusion.py` | 量化×LLM融合引擎(485行) |
| **候选增强** | `tradingagents/core/candidate_enrichment.py` | 实时上下文拉取(230行) |
| **LLM复核** | `tradingagents/core/llm_candidate_review.py` | 候选股LLM评估 |
| **用户画像** | `tradingagents/core/user_profile.py` | 投资风格/风险偏好 |
| **调度器** | `tradingagents/core/scheduler.py` | 定时任务调度(99行) |
| **MCP客户端** | `tradingagents/core/mcp_client.py` | StockManager HTTP/Streamable MCP(513行) |
| **数据适配** | `tradingagents/dataflows/mcp_adapter.py` | MCP数据格式转换 |
| **意图路由** | `tradingagents/core/orchestrator.py` | 自然语言→Skill路由(199行) |
| **API应用** | `tradingagents/api/app.py` | FastAPI lifespan + 路由注册 |
| **配置** | `tradingagents/default_config.py` | 所有默认配置 + 环境变量映射 |
| **进度文档** | `docs/PROGRESS.md` | 详细开发进度与已知问题 |
