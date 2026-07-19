# StockManager MCP Quant Signal Contract

本文定义 StockManager MCP 侧需要提供的量化选股服务合约，用于替换 TradingAgents 中的伪因子评分，并支撑后续 Quant Signal × LLM Insight 的融合决策链路。

目标不是让 TradingAgents 在 Agent 进程内重写量化框架，而是让 MCP 侧提供稳定、可回测、可解释的横截面量化信号；TradingAgents 负责调度、Agent 深度分析、信号融合、前端展示和历史闭环。

## 1. 通信与基础约定

### 1.1 服务地址

默认服务：

```text
http://127.0.0.1:8765
```

MCP Streamable HTTP endpoint：

```text
http://127.0.0.1:8765/mcp
```

REST 健康检查：

```text
GET /health
GET /capabilities
```

TradingAgents 会将本地服务加入 `NO_PROXY/no_proxy`，并在 MCP health/capability 探测中使用直连方式。MCP 服务不应依赖系统代理访问本地调用链路。

### 1.2 数据格式

- 日期统一使用 `YYYY-MM-DD`。
- A 股代码统一使用 TuShare 风格：`600519.SH`、`300750.SZ`、`835185.BJ`。
- 指数代码统一使用：`000906.SH`、`000300.SH`、`000852.SH` 等。
- 价格默认使用前复权 `qfq`，字段中必须显式返回 `adj_type`。
- 所有数值字段使用 number；缺失值使用 `null`，不要使用 `"N/A"`。
- 工具返回 JSON，不返回 Markdown。
- 所有响应必须包含 `status`，取值为 `success`、`partial`、`error`。
- `partial` 表示有可用结果，但存在缺失数据、覆盖率不足或部分数据源失败。

### 1.3 通用响应字段

所有工具建议包含以下元数据：

```json
{
  "status": "success",
  "request_id": "uuid-or-trace-id",
  "as_of_date": "2026-06-30",
  "generated_at": "2026-06-30T15:30:00+08:00",
  "source": "stockmanager",
  "method": "method_name_v1",
  "data_version": "2026.06.30",
  "warnings": []
}
```

错误响应：

```json
{
  "status": "error",
  "error_code": "DATA_UNAVAILABLE",
  "message": "No daily bars available for requested universe/date.",
  "warnings": ["calendar fallback used"]
}
```

常见 `error_code`：

| code | 含义 |
|---|---|
| `INVALID_ARGUMENT` | 入参非法 |
| `DATA_UNAVAILABLE` | 核心行情/财务数据不可用 |
| `UNIVERSE_EMPTY` | 指数成分股为空 |
| `FACTOR_UNAVAILABLE` | 因子计算失败或缺失过多 |
| `BACKTEST_FAILED` | 回测任务失败 |
| `TIMEOUT` | 工具执行超时 |
| `INTERNAL_ERROR` | 未分类内部错误 |

## 2. Capabilities

`GET /capabilities` 至少返回：

```json
{
  "service": "stockmanager-mcp",
  "version": "0.1.0",
  "tools": [
    "get_index_constituents",
    "get_factor_snapshot",
    "rank_factor_candidates",
    "evaluate_signal_formula",
    "run_signal_backtest"
  ],
  "market_data_available": true,
  "factor_available": true,
  "signal_rank_available": true,
  "backtest_available": true,
  "risk_announcement_available": true
}
```

TradingAgents 会重点检查：

- `factor_available`
- `signal_rank_available`
- `backtest_available`

如果 `signal_rank_available=false`，DailyPipeline 不应输出真实推荐分，只能提示量化服务不可用。

## 3. 必需工具一：get_factor_snapshot

### 3.1 用途

返回一组股票在某个交易日的原始因子、标准化因子分、交易可行性和风险标记。

注意：`get_factor_snapshot` **不是选股/排序工具**。它只对调用方给定的 `ts_codes`
返回因子快照，不能自行决定股票池，也不应被前端或 Agent 误读为「策略筛选结果」。
真实选股必须由 `rank_factor_candidates`、`evaluate_signal_formula` 或回测后的策略配置完成。

该工具用于：

- 个股 Agent 分析前的量化上下文注入。
- 前端展示单票因子雷达图。
- Debug `rank_factor_candidates` 的评分来源。

### 3.2 入参

```json
{
  "ts_codes": ["600519.SH", "300750.SZ"],
  "trade_date": "2026-06-30",
  "lookback_days": 120,
  "factors": [
    "momentum_20d",
    "turnover_rate_20d",
    "amount_20d",
    "volatility_20d",
    "max_drawdown_60d",
    "roe_ttm",
    "gross_margin_ttm",
    "pe_percentile_3y",
    "northbound_net_20d",
    "risk_announcements_count_90d"
  ],
  "adj_type": "qfq",
  "include_raw": true,
  "include_percentiles": true
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `ts_codes` | list[string] | 是 | 股票代码，最多建议 200 个 |
| `trade_date` | string | 是 | 信号日期，不得使用未来数据 |
| `lookback_days` | int | 否 | 技术因子回看窗口，默认 120 |
| `factors` | list[string] | 否 | 为空时返回默认因子集 |
| `adj_type` | string | 否 | `qfq`、`hfq`、`none`，默认 `qfq` |
| `include_raw` | bool | 否 | 是否返回原始因子 |
| `include_percentiles` | bool | 否 | 是否返回横截面分位/标准化分 |

### 3.3 出参

```json
{
  "status": "success",
  "as_of_date": "2026-06-30",
  "source": "stockmanager",
  "method": "factor_snapshot_v1",
  "adj_type": "qfq",
  "coverage": {
    "requested": 2,
    "returned": 2,
    "factor_coverage_pct": 0.96
  },
  "rows": [
    {
      "ts_code": "600519.SH",
      "name": "贵州茅台",
      "industry": "白酒",
      "trade_date": "2026-06-30",
      "latest_price": 1520.3,
      "raw_factors": {
        "momentum_20d": 0.083,
        "turnover_rate_20d": 0.72,
        "amount_20d": 8200000000,
        "volatility_20d": 0.018,
        "max_drawdown_60d": -0.092,
        "roe_ttm": 0.31,
        "gross_margin_ttm": 0.91,
        "pe_ttm": 24.2,
        "pe_percentile_3y": 0.35,
        "northbound_net_20d": 230000000
      },
      "factor_scores": {
        "momentum": 72,
        "liquidity": 91,
        "quality": 96,
        "valuation": 64,
        "flow": 69,
        "risk_control": 82
      },
      "tradability": {
        "is_tradable": true,
        "suspended": false,
        "st_flag": false,
        "limit_status": "normal",
        "one_price_limit": false,
        "reason": null
      },
      "risk_flags": [],
      "warnings": []
    }
  ],
  "warnings": []
}
```

### 3.4 因子语义要求

`factor_scores` 使用 0-100，越高越好。

| 分组 | 说明 |
|---|---|
| `momentum` | 趋势/动量强度，越高越强 |
| `liquidity` | 成交额、换手、冲击成本友好度，越高越好 |
| `quality` | ROE、毛利率、利润/收入增长、资产负债质量，越高越好 |
| `valuation` | 估值吸引力，越高代表越便宜或性价比越高 |
| `flow` | 北向/主力/机构资金趋势，越高越好 |
| `risk_control` | 波动、回撤、ST、公告风险等控制效果，越高越安全 |

注意：不要把 `risk` 设计成“越高越危险”的分数。为方便融合，统一使用 `risk_control`，越高越好。原始风险值放在 `raw_factors`。

## 4. 必需工具二：rank_factor_candidates

### 4.1 用途

对一个股票池进行批量因子计算、交易过滤、横截面标准化和综合打分，输出候选股排名。

这是替换 `DailyPipelineSkill` / `MarketScannerSkill` 伪因子的核心工具。

### 4.2 入参

```json
{
  "universe_index": "000906.SH",
  "trade_date": "2026-06-30",
  "style": "medium_term",
  "limit": 20,
  "candidate_limit": 200,
  "factor_profile": "medium_term_balanced",
  "weights": {
    "momentum": 0.25,
    "liquidity": 0.15,
    "quality": 0.25,
    "valuation": 0.15,
    "flow": 0.10,
    "risk_control": 0.10
  },
  "filters": {
    "exclude_st": true,
    "exclude_suspended": true,
    "exclude_one_price_limit": true,
    "min_amount_20d": 300000000,
    "max_risk_announcements_90d": 2
  },
  "sector_prefs": ["新能源", "消费"],
  "return_factor_snapshot": true
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `universe_index` | string | 是 | 股票池指数，例如 `000906.SH` |
| `trade_date` | string | 是 | 信号日期 |
| `style` | string | 是 | `short_term`、`medium_term`、`long_term` |
| `limit` | int | 否 | 最终返回数量，默认 20 |
| `candidate_limit` | int | 否 | 过滤后进入排序的上限，默认 200 |
| `factor_profile` | string | 否 | 预设因子组合 |
| `weights` | object | 否 | 覆盖默认权重 |
| `filters` | object | 否 | 交易可行性过滤 |
| `sector_prefs` | list[string] | 否 | 用户偏好行业，可做轻微加分，但必须返回加分说明 |
| `return_factor_snapshot` | bool | 否 | 是否携带原始因子快照 |

实现要求：

- `trade_date` 当天指数成分缺失时，必须回退到不晚于 `trade_date` 的最近可用成分日期，并在 `universe.constituent_as_of_date` 和 `warnings` 中说明；不得直接返回 `UNIVERSE_EMPTY`。
- `candidate_limit` 是性能上限，不应改变排序语义。CSI800/中证全指这类大池子应使用缓存、增量更新或异步 job，避免前端同步等待数分钟。
- `flow`、`valuation`、`risk_control` 不应在数据缺失时静默填 50。必须返回 `coverage` / `missing_factors`，TradingAgents 会把低覆盖率作为降权或降级原因。
- Top N 需要支持行业分散约束，例如 `max_per_industry` / `max_industry_weight`，避免候选全部挤在单一拥挤赛道。

### 4.3 推荐预设 factor_profile

#### short_term_momentum

适合短线/波段：

```text
momentum 0.35
liquidity 0.20
flow 0.20
risk_control 0.15
quality 0.05
valuation 0.05
```

建议因子：

- `momentum_5d`
- `momentum_20d`
- `volume_ratio`
- `turnover_rate_20d`
- `northbound_net_5d`
- `main_net_inflow_5d`
- `volatility_20d`
- `max_drawdown_20d`

#### medium_term_balanced

适合 2-8 周：

```text
momentum 0.25
liquidity 0.15
quality 0.25
valuation 0.15
flow 0.10
risk_control 0.10
```

#### long_term_quality

适合中长期配置：

```text
quality 0.35
valuation 0.25
risk_control 0.15
momentum 0.10
liquidity 0.10
flow 0.05
```

#### 策略约束建议

实测 2026-06-30 → 2026-07-01 的 `long_term_quality` Top5 过度集中在 AI/半导体硬件链，
隔日平均收益为负，说明仅靠当前质量/动量截面分不足以保证候选质量。MCP 侧策略层建议加入：

- 行业分散：Top10 单行业不超过 2-3 只，或单行业权重不超过 35%。
- 拥挤度惩罚：近 20 日涨幅高、波动扩张、回撤控制弱且资金流未确认时降权。
- 估值有效性：`valuation` 不能缺失后填中性分；缺失时降低综合分可信度。
- 资金确认：`northbound_net_*`、主力净流入、成交额趋势至少命中一项，否则动量高分只能进入观察。
- 风险硬门槛：`risk_control < 50`、重大公告风险、ST/停牌/一字板直接降级。
- 回测闭环：每个 profile 至少输出最近 20/60/120 交易日 hit_rate、avg_return、max_drawdown，低于阈值时不得标记为生产策略。

### 4.4 出参

```json
{
  "status": "success",
  "as_of_date": "2026-06-30",
  "source": "stockmanager",
  "method": "cross_sectional_factor_rank_v1",
  "universe": {
    "index": "000906.SH",
    "raw_size": 800,
    "tradable_size": 732,
    "ranked_size": 200
  },
  "style": "medium_term",
  "weights": {
    "momentum": 0.25,
    "liquidity": 0.15,
    "quality": 0.25,
    "valuation": 0.15,
    "flow": 0.10,
    "risk_control": 0.10
  },
  "rows": [
    {
      "rank": 1,
      "ts_code": "600519.SH",
      "name": "贵州茅台",
      "industry": "白酒",
      "quant_score": 82.4,
      "universe_percentile": 0.96,
      "factor_scores": {
        "momentum": 72,
        "liquidity": 91,
        "quality": 96,
        "valuation": 64,
        "flow": 69,
        "risk_control": 82
      },
      "factor_snapshot": {
        "momentum_20d": 0.083,
        "turnover_rate_20d": 0.72,
        "roe_ttm": 0.31,
        "pe_percentile_3y": 0.35,
        "northbound_net_20d": 230000000
      },
      "tradability": {
        "is_tradable": true,
        "suspended": false,
        "st_flag": false,
        "limit_status": "normal",
        "one_price_limit": false,
        "reason": null
      },
      "risk_flags": [],
      "score_explain": [
        "quality top 5%",
        "liquidity top 10%",
        "valuation neutral",
        "no hard risk flags"
      ],
      "warnings": []
    }
  ],
  "warnings": []
}
```

### 4.5 排名规则要求

MCP 侧必须保证：

- `quant_score` 为 0-100，越高越好。
- `rows` 已按 `quant_score` 降序排列。
- `universe_percentile` 为 0-1，越高越靠前。
- 非交易标的默认剔除；如果没有剔除，必须在 `tradability.is_tradable=false` 中标注。
- 所有用到的财务数据必须满足 `announce_date <= trade_date`，禁止未来函数。
- 因子标准化必须说明方法，建议 winsorize + rank percentile。
- 行业中性化如果启用，必须在 `method` 或 `warnings` 中说明。

## 5. 可选工具：evaluate_signal_formula

当前 TradingAgents client 已经封装该工具。建议 MCP 侧将其定位为研究/实验工具，而不是 DailyPipeline 的线上主入口。

### 5.1 入参

```json
{
  "formula": "rank(momentum_20d)*0.35 + rank(turnover_rate_20d)*0.20 + rank(roe_ttm)*0.25 + rank(1/pe_percentile_3y)*0.20",
  "start_date": "2025-07-01",
  "end_date": "2026-06-30",
  "universe_index": "000906.SH",
  "rebalance": "weekly",
  "top_n": 20,
  "cost_bps": 10
}
```

### 5.2 出参

```json
{
  "status": "success",
  "method": "signal_formula_eval_v1",
  "formula": "...",
  "metrics": {
    "rank_ic_mean": 0.041,
    "rank_ic_ir": 0.82,
    "top_quantile_return": 0.186,
    "bottom_quantile_return": -0.032,
    "long_short_return": 0.218,
    "turnover": 0.36,
    "coverage_pct": 0.93
  },
  "warnings": []
}
```

### 5.3 DSL 最小函数集

建议支持：

- `rank(x)`
- `zscore(x)`
- `winsorize(x, lower, upper)`
- `neutralize(x, industry)`
- `ts_mean(x, n)`
- `pct_change(close, n)`
- `ifnull(x, fallback)`
- `min(a, b)` / `max(a, b)`

第一阶段如果 DSL 成本高，可以先只支持预设 `factor_profile`，后续再开放公式。

## 6. 可选工具：run_signal_backtest

### 6.1 用途

用于每月或每周验证已产生信号，不参与实时选股路径。

### 6.2 入参

```json
{
  "signals": [
    {
      "trade_date": "2026-06-03",
      "ts_code": "600519.SH",
      "signal": "BUY",
      "final_score": 82,
      "quant_score": 78,
      "llm_confidence": 88,
      "horizon_days": 20
    }
  ],
  "start_date": "2026-06-01",
  "end_date": "2026-06-30",
  "holding_rule": "horizon_or_stop",
  "cost_bps": 10,
  "benchmark": "000300.SH"
}
```

### 6.3 出参

```json
{
  "status": "success",
  "method": "signal_backtest_v1",
  "metrics": {
    "hit_rate": 0.58,
    "avg_return": 0.034,
    "excess_return": 0.021,
    "sharpe": 1.12,
    "max_drawdown": -0.073,
    "turnover": 0.42
  },
  "attribution": {
    "quant_score_bucket": [
      {"bucket": "80-100", "count": 12, "avg_return": 0.046},
      {"bucket": "60-80", "count": 31, "avg_return": 0.018}
    ],
    "llm_confidence_bucket": [
      {"bucket": "80-100", "count": 10, "avg_return": 0.052}
    ]
  },
  "recommendations": {
    "alpha_quant_delta": 0.05,
    "notes": ["high quant score bucket outperformed in last 30 days"]
  },
  "warnings": []
}
```

TradingAgents 前期只展示 `recommendations`，不自动改生产权重。

### 6.4 可选审计增强工具（best-effort，非门禁）

以下方法均为**可选**：TradingAgents 的 `audit_backtest_result` 仅在满足前置条件时调用，任何一个缺失、抛错或返回非 dict/`status==error` 时都会降级为 `{"available": false, "reason": ...}` 挂到 `validation` 上，**不影响** `production_gate_passed`（该门禁只看必需指标、provenance、前视/幸存者偏差、成本声明与 purged CV）。这些块用于展示，不参与生产就绪判定，也**不做参数搜索**。

| 方法 | 触发条件 | 语义 |
| --- | --- | --- |
| `compute_purged_cv_sharpe(equity_curve, n_splits, purge_days)` | 结果带非空 `equity_curve`/`curve` | Purged K-fold CV，TradingAgents 再纯后处理为 `walk_forward` 折次一致性摘要（不跑额外回测） |
| `analyze_execution_slippage(trades, participation_rates=None)` | 结果带非空 `trades` 且 MCP 暴露该方法 | 按成交明细拆解执行滑点/成本；出参回显在 `validation.execution_slippage` |
| `run_ablation_study(base_experiment, ablations)` | 调用方**显式声明** `backtest_base_experiment` + `backtest_ablations` 列表 | 对显式声明的「关闭某因子」变体各跑一次，返回 base-vs-ablation 贡献表；**不是**参数搜索器 |

出参约定：成功返回 `dict`（可含 `status: "success"`）；失败返回 `{"status": "error", "error": "..."}`。TradingAgents 会把非 dict 返回值 `str()` 化后记入 `error` 字段，绝不抛异常。

## 7. TradingAgents 消费方式

### 7.1 DailyPipeline / MarketScanner

调用顺序：

```text
rank_factor_candidates
  -> rows top N
  -> emit scanner_candidates / daily_pipeline_candidates
  -> top K 注入 Agent 分析
  -> signal_fusion
  -> signals 入库
```

如果 `rank_factor_candidates` 不可用：

- 不使用 hash-based pseudo factor。
- UI 和报告提示：`StockManager quant ranking unavailable`。
- 允许用户选择 demo fallback，但必须显式标记 `demo/placeholder`。

**候选 payload 可选字段 `deep_analysis`**：当 `daily_pipeline_deep_analysis_enabled=true` 时，量化排名 + LLM 复核后会对 Top N（`daily_pipeline_deep_analysis_limit`，默认 1）候选串行跑重量级多智能体 `StockAnalysisSkill`，把其 `structured_conclusion`（`rating`/`target_price`/`confidence`/`reasons`/`plan`）回写到候选 payload 的 `deep_analysis` 字段（signal 行、选股卡片、反思快照一并携带）。默认关闭（每只跑完整 agent graph，分钟级 + token 成本）；best-effort——深度分析失败或为空不阻断流水线，该字段缺省即视为未增强。

### 7.2 Agent Prompt 注入

对每个候选股注入压缩后的 Quant Evidence：

```text
## Quant Evidence from StockManager
- as_of_date: 2026-06-30
- quant_score: 82.4 / 100, universe_percentile: top 4%
- factor_scores: momentum=72, liquidity=91, quality=96, valuation=64, flow=69, risk_control=82
- tradability: tradable, no ST, normal limit status
- risk_flags: none
- score_explain: quality top 5%; liquidity top 10%; no hard risk flags

请验证该量化信号是否有新闻/行业/政策/基本面支撑，并指出可能推翻该信号的风险。
```

Agent 输出应包含：

```json
{
  "llm_view": "positive",
  "llm_confidence": 82,
  "catalyst_score": 76,
  "risk_override": false,
  "invalidates_quant": false,
  "risk_flags": ["行业政策波动"],
  "reasoning": "..."
}
```

### 7.3 Signal Fusion

TradingAgents 侧融合规则：

```text
final_score = alpha * quant_score + (1 - alpha) * llm_confidence
```

默认 alpha：

| 风格 | quant alpha | llm alpha |
|---|---:|---:|
| `short_term` | 0.70 | 0.30 |
| `medium_term` | 0.55 | 0.45 |
| `long_term` | 0.35 | 0.65 |

硬性规则：

- `risk_override=true`：最多输出 `HOLD`。
- `invalidates_quant=true`：剔除或降为 `WATCHLIST`。
- `quant_score < 60`：LLM 不能单独拉成 `BUY`。
- `llm_confidence < 50`：量化高分也只进入 `WATCHLIST`。
- `tradability.is_tradable=false`：不得输出 `BUY`。

## 8. 验收标准

MCP 侧完成实验后，TradingAgents 按以下标准验收。

### 8.1 基础连通

- `GET /health` 返回 200。
- `GET /capabilities` 返回 `signal_rank_available=true`。
- `/mcp` 可以 `list_tools`，包含：
  - `get_factor_snapshot`
  - `rank_factor_candidates`
  - `evaluate_signal_formula`，可选
  - `run_signal_backtest`，可选

### 8.2 get_factor_snapshot

用例：

```json
{
  "ts_codes": ["600519.SH", "300750.SZ"],
  "trade_date": "2026-06-30"
}
```

验收：

- 返回 `rows.length == 2` 或 `status=partial` 并说明缺失原因。
- 每行包含 `ts_code/name/industry/factor_scores/tradability/risk_flags`。
- `factor_scores` 至少包含 `momentum/liquidity/quality/risk_control`。
- 所有 score 在 0-100。

### 8.3 rank_factor_candidates

用例：

```json
{
  "universe_index": "000906.SH",
  "trade_date": "2026-06-30",
  "style": "medium_term",
  "limit": 10,
  "candidate_limit": 100
}
```

验收：

- 返回 `rows.length <= 10`。
- `rows` 按 `quant_score` 降序排列。
- 每行包含 `quant_score/factor_scores/tradability/score_explain`。
- 非交易标的要么被过滤，要么明确 `is_tradable=false`。
- 同一输入重复调用结果应稳定，除非底层数据更新且 `data_version` 变化。

### 8.4 数据安全

- 不允许使用 `trade_date` 之后的数据。
- 财务数据必须返回或内部遵守 `announce_date <= trade_date`。
- 如果数据源有缺口，返回 `partial` 和 `warnings`，不要静默补 0。

### 8.5 性能

建议目标：

- `get_factor_snapshot` 200 只股票：10 秒内。
- `rank_factor_candidates` CSI800：30 秒内。
- 如果超过 30 秒，建议返回异步 job：
  - `job_id`
  - `get_job_status`
  - `get_job_result`

## 9. Phase 1 最小可交付范围

第一阶段 MCP 侧只需要交付：

1. `GET /health`
2. `GET /capabilities`
3. `get_factor_snapshot`
4. `rank_factor_candidates`

其中 `rank_factor_candidates` 可以先不支持 DSL，只支持三套 profile：

- `short_term_momentum`
- `medium_term_balanced`
- `long_term_quality`

TradingAgents 侧收到这四项后即可替换伪因子，并开始做 Agent 融合。

## 10. Phase 2 增强范围

后续增强：

1. `evaluate_signal_formula`
2. `run_signal_backtest`
3. 行业中性化参数
4. 因子贡献归因
5. historical signal replay
6. 权重调整建议
