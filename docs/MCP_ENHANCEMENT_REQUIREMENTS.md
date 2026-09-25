# StockManager MCP 优化与新增需求文档

> 提出方：TradingAgents（消费方）
> 接收方：StockManager MCP 服务端
> 关联契约：`docs/MCP_QUANT_SIGNAL_CONTRACT.md`（下称"契约"）
> 状态：待 MCP 端评审排期

---

## 一、背景与消费方现状

TradingAgents 通过 Streamable HTTP（默认 `http://127.0.0.1:8765/mcp`，tool_timeout=120s）消费 StockManager MCP。当前生产链路实际调用情况：

| MCP 工具 | 消费方 | 用途 |
|---|---|---|
| `rank_factor_candidates` | DailyPipeline / MarketScanner | 选股主链路（含决策门控、因子快照） |
| `get_factor_snapshot` | Chat 轻量工具（个股因子快照卡） | 单股/多股因子查询 |
| `get_risk_announcements` | RiskMonitor / Reflection | 持仓风险扫描、信号后风险证据 |
| `get_stock_daily` | Reflection / 持仓补价 | 信号后 N 日收益、最新收盘价 |
| `list_strategies_and_configs` / `run_backtest` / `get_job_status` / `get_job_result` | StrategyBacktest | 异步策略回测 |
| `compute_purged_cv_sharpe` | 回测审计（best-effort） | 样本外夏普验证 |

前端消费面：Chat 任务卡（实时步骤时间线）、候选股表格（CandidateTable）、Dashboard 过滤器面板（15 项过滤参数已全量透传 MCP `filters`）、持仓风险列表。

本文档共 5 项需求（R1–R5，按优先级排序）+ 回归约束 + 暂缓项。

---

## 二、需求总览

| 编号 | 需求 | 类型 | 优先级 | 涉及工具 |
|---|---|---|---|---|
| R1 | 选股排名异步 job 化 + 进度上报 | 优化 | P0 | `rank_factor_candidates`, `get_job_status`, `get_job_result` |
| R2 | 排名出参补行情字段 latest_price / pct_chg | 优化 | P0 | `rank_factor_candidates` |
| R3 | 风险公告批量查询 | 优化 | P1 | `get_risk_announcements` |
| R4 | capabilities 增强：契约版本与特性声明 | 优化 | P1 | `GET /capabilities` |
| R5 | run_signal_backtest 落地（超额收益口径） | 新增 | P1 | `run_signal_backtest`（或 `run_backtest` 映射） |

---

## 三、需求明细

### R1. `rank_factor_candidates` 异步 job 化 + 进度上报（P0）

**现状问题**

- 选股 + 因子计算 + 决策门控全部在一个同步调用内完成，CSI800 级别 universe 耗时可达数十秒；消费方只能设置 120s 大超时干等。
- 契约 §8.5 已约定"超过 30 秒建议返回异步 job"，但当前排名路径未实现。
- 消费方前端有实时任务步骤时间线（WebSocket 事件驱动），这一步目前只能显示一行"调用数据工具"，无中间进度，是体验上最明显的黑盒。

**需求描述**

1. `rank_factor_candidates` 新增可选入参 `async_mode`（bool，默认 `false`，保证向后兼容）：
   - `async_mode=true`：立即返回 `{"status": "accepted", "job_id": "..."}`（2 秒内）。
   - `async_mode=false`：维持现有同步行为不变。
2. 复用现有 `get_job_status` / `get_job_result` 机制，`get_job_status` 出参增加进度块：

```json
{
  "status": "running",
  "job_id": "...",
  "progress": {
    "stage": "factor_compute",
    "stage_label": "因子计算",
    "progress_pct": 62,
    "detail": "processed 496/800",
    "started_at": "2026-07-30T10:00:00+08:00"
  }
}
```

3. 建议 stage 枚举（可增不可减语义）：`universe_resolve` → `prefilter` → `factor_compute` → `scoring` → `decision_gate` → `done`。
4. `get_job_result` 返回体与同步调用出参**完全一致**（含 `rows` / `data_coverage` / `warnings` / `data_version` 等全部字段）。

**验收标准**

- CSI800 用例：`async_mode=true` 提交后 2s 内拿到 `job_id`；轮询期间 `progress_pct` 单调不减；最终 `get_job_result` 与同参数同步调用结果一致（同 `data_version` 下逐行相等）。
- `async_mode` 缺省时行为与现网完全一致（回归）。
- job 失败时 `get_job_status` 返回 `status="error"` + 契约 §1.3 错误码，不允许 job 永久停留在 running。

---

### R2. 排名出参补行情字段 `latest_price` / `pct_chg`（P0）

**现状问题**

`rank_factor_candidates` 的 `rows[]` 不含当日行情，消费方需要再调 `get_stock_daily` 补收盘价；补价失败时前端候选表出现"缺收盘价"空态，且交易计划（entry_zone/stop_loss）推导缺基准价。

**需求描述**

`rows[]` 每行新增两个字段：

```json
{
  "ts_code": "600519.SH",
  "quant_score": 82.4,
  "latest_price": 1688.00,
  "pct_chg": 1.23,
  "...": "既有字段不变"
}
```

- `latest_price`：`trade_date` 当日收盘价，复权口径与请求的 `adj_type` 一致（未传则用不复权原始价，并在 `method` 或字段注释中声明口径）。
- `pct_chg`：当日涨跌幅（%）。
- 数据缺失时字段置 `null` 并追加 `warnings` 说明（对齐契约 §8.4"不要静默补 0"）。

**验收标准**

- 正常交易日 CSI300 用例：全部 rows 的 `latest_price` 非空且与 `get_stock_daily` 同日收盘价一致。
- 停牌标的：`latest_price` 为最近可得收盘价或 `null`，`warnings` 说明，`tradability.is_tradable=false` 不受影响。

---

### R3. `get_risk_announcements` 批量查询（P1）

**现状问题**

当前仅支持单只 `ts_code`。消费方 RiskMonitor 是高频用户动作（"检查我的持仓风险"），需遍历持仓逐只调用（客户端限 8 并发），20+ 只持仓时延迟明显。

**需求描述**

1. 入参新增 `ts_codes`（数组，与 `ts_code` 互斥，二者必传其一；保留 `ts_code` 向后兼容）：

```json
{
  "ts_codes": ["600519.SH", "300750.SZ", "000001.SZ"],
  "start_date": "2026-04-01",
  "end_date": "2026-07-01",
  "keywords": ["立案", "问询", "违规", "处罚", "减持", "业绩", "预亏", "退市"]
}
```

2. 出参 `rows[]` 扁平返回，每条公告带 `ts_code` 字段（消费方自行分组）；或按 `results: {ts_code: [...]}` 分组返回——二选一，在 capabilities 中声明（见 R4）。
3. 单次批量上限建议 50 只；超限返回 `INVALID_ARGUMENT`。
4. 部分标的失败时返回 `status="partial"` + `warnings` 逐只说明，不整体失败。

**验收标准**

- 30 只持仓批量查询 5s 内返回；结果与逐只调用的并集一致。
- 单只 `ts_code` 老调用方式行为不变（回归）。

---

### R4. capabilities 增强：契约版本与特性声明（P1）

**现状问题**

契约与实现之间发生过漂移（例：契约 §6 要求 `run_signal_backtest`，实现只有 `run_backtest`），消费方只能在业务调用失败时才发现。现有 `GET /capabilities`（契约 §2）只有布尔开关，粒度不足。

**需求描述**

`GET /capabilities` 在契约 §2 现有字段基础上增加：

```json
{
  "service": "stockmanager-mcp",
  "version": "0.2.0",
  "contract_version": "2026.07",
  "tool_aliases": {
    "run_signal_backtest": "run_backtest"
  },
  "tool_features": {
    "rank_factor_candidates": ["async_mode", "latest_price"],
    "get_risk_announcements": ["batch_ts_codes", "flat_rows"]
  }
}
```

- `contract_version`：实现所对齐的契约版本号，契约文档每次增补同步递增。
- `tool_aliases`：契约工具名 → 实际实现工具名的映射（无映射则省略）。
- `tool_features`：各工具已支持的可选特性标记（R1/R2/R3 的特性开关均在此声明），消费方按特性探测降级，避免版本硬编码。

**验收标准**

- 消费方启动健康检查可仅凭 capabilities 判断 R1–R3 是否可用，无需试探性调用。
- 老消费方（不读新字段）不受影响（回归）。

---

### R5. `run_signal_backtest` 落地（P1）

**现状问题**

契约 §6 已定义该工具（入参 signals 列表、出参 metrics/attribution/recommendations），但 MCP 侧仅有通用 `run_backtest`。消费方的"量化×LLM 信号闭环验证"（回测信号绩效 → 输出 `alpha_quant_delta` 权重建议）因此无法按契约走通。

**需求描述**

二选一，并在 R4 的 capabilities 中声明：

- **方案 A（推荐）**：按契约 §6.2/§6.3 原样实现 `run_signal_backtest`。
- **方案 B**：内部复用 `run_backtest`，但注册契约工具名（或在 `tool_aliases` 声明映射），且**出参字段必须补齐到 §6.3 口径**。

在 §6.3 现有 `metrics` 基础上，明确要求补充超额口径字段（对应契约 Phase 2 第 7 条"超额口径回测指标"）：

```json
{
  "metrics": {
    "hit_rate": 0.58,
    "avg_return": 0.034,
    "excess_return": 0.021,
    "avg_excess_return": 0.018,
    "sector_excess_return": 0.012,
    "sharpe": 1.12,
    "max_drawdown": -0.073,
    "turnover": 0.42
  }
}
```

- `avg_excess_return`：逐信号相对 `benchmark` 的超额收益均值。
- `sector_excess_return`：逐信号相对其所属申万一级行业指数的超额收益均值（行业指数缺失时置 `null` + warnings）。
- 长耗时场景沿用 R1 的 job 机制。

**验收标准**

- 契约 §6.2 示例入参可跑通，出参含上述全部 metrics 字段与 `attribution` / `recommendations` 块。
- `recommendations.alpha_quant_delta` 有值时附 `notes` 说明依据（消费方仅展示、不自动改生产权重，见契约 §6.3 备注）。

---

## 四、回归约束（既有契约行为，不得回退）

以下为历史验收已确认的行为，本次改造不得破坏：

1. `enable_decision=true` 时 `rows[].decision` 必须填充 `{action, demoted, demote_reasons}`；触发 gate_reasons 却返回空 decision 不可接受。
2. `score_explain` 必须显式写明拥挤度状态（如 `crowding: passed` 或 `crowding penalty -X`）。
3. 任何 `score_confidence < 1.0` 必须在 `warnings` 或 `confidence_breakdown` 中给出确切原因（缺失因子、低流动性等）。
4. 缺因子分支：flow/valuation 缺失 → 降分 + warning；quality 缺失但 momentum/liquidity/flow > 60 → 排名不受影响 + `quality_missing` warning。
5. 数据安全（契约 §8.4）：不使用 `trade_date` 之后数据；缺口返回 `partial` + warnings，不静默补 0。
6. 通信形式不变：Streamable HTTP `/mcp`、`GET /health`、错误码沿用契约 §1.3。

---

## 五、明确暂缓项（本次不要求实现）

| 工具 | 暂缓原因 |
|---|---|
| `evaluate_signal_formula` | 研究/实验用途，消费方尚无前端入口 |
| `run_factor_experiment` | 同上 |
| `analyze_execution_slippage` | 消费方暂无真实成交记录作为输入 |

---

## 六、建议实施顺序

1. **第一批（P0）**：R2（纯出参扩展，改动最小）→ R1（job 化，依赖既有 job 机制）。
2. **第二批（P1）**：R4（为 R1/R2/R3 提供特性探测）→ R3 → R5。
3. 每批交付后由 TradingAgents 侧按各需求"验收标准"+ 第四节回归约束做契约验收（沿用既往两轮复验流程）。
