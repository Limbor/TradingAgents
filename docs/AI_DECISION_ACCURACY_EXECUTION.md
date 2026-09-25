# AI 辅助决策胜率提升执行手册

目标不是单独提高命中率，而是在真实可成交、扣除成本后提高样本外超额收益，且不显著恶化回撤与换手。

## 已落地的安全基线

- LLM 数值方向使用 `llm_score`（0 看空、50 中性、100 看多）；`llm_confidence` 仅表示判断可靠性。
- RankIC 优先使用行业/基准超额收益；没有超额收益时才回退绝对收益，并在 scorecard 中记录回退数量。
- 自适应 α 只使用同时具备 Quant/LLM 分数的配对样本；存在日期时按独立交易日计算有效样本。
- 自适应 α 默认关闭。开启后默认只读取隔离的 `backtest_eval` 样本，至少 40 个有效周期才可能应用。
- 回测生产闸门要求逐折 walk-forward 明细、至少 3 折且每折 Sharpe 为正；明细缺失时拒绝上线。
- 最大回撤兼容正负两种供应商口径，统一按绝对幅度检查。

## 1. 累积隔离评估样本

以下命令会访问 StockManager MCP；`--llm-sample` 会产生 LLM 调用成本。建议先使用独立评估数据库：

```bash
python scripts/accumulate_eval_samples.py \
  --track both \
  --start 2024-01-02 \
  --end 2025-12-31 \
  --universe 000906.SH \
  --style medium_term \
  --horizon 5 \
  --sample-k 20 \
  --llm-sample 5 \
  --seed 42 \
  --db-path /path/to/evaluation.db
```

至少覆盖 40 个不同交易日，并跨越上涨、下跌和震荡阶段。不要用同一时间窗口反复选择参数并报告最终结果。

## 2. Quant 与 LLM 配对比较

`compare` 模式对同一日期、同一标的生成两个变体，未来收益只获取一次；收益口径为下一交易日开盘买入、持有期末收盘卖出，并扣除双边佣金和滑点：

```bash
python scripts/backtest_signal_fusion.py \
  --start-date 2025-01-01 \
  --end-date 2025-12-31 \
  --horizon 5 \
  --universe 000906.SH \
  --style medium_term \
  --mode compare \
  --limit 5 \
  --candidate-limit 200 \
  --transaction-cost-bps 10 \
  --slippage-bps 5
```

重点查看输出中的 `paired_count`、`paired_accuracy_delta_pp`、`fused_improved` 和 `fused_degraded`，不要直接比较两批不同样本的聚合胜率。

## 3. Champion/Challenger 晋级规则

建议至少影子运行 40–60 个交易日，同时保留以下变体：

1. Champion：静态 α 的 Quant + LLM 风险门控。
2. Challenger A：Quant-only。
3. Challenger B：显式 `llm_score` 分数融合。
4. Challenger C：Top 1–3 完整多 Agent 深度分析。
5. Challenger D：隔离评估样本驱动的自适应 α。

只有同时满足以下条件才开启自适应 α 或替换 Champion：

- 扣费后超额收益及配对准确率增量不依赖单一月份或单一行业。
- Purged CV 和 walk-forward 闸门通过，且没有负 Sharpe 折次。
- 最大回撤、换手率和滑点没有明显恶化。
- Quant/LLM 分数的 RankIC 在保留测试窗中仍为正。
- 所用模型、提示词、数据版本和参数均已记录，可复现。

## 4. 后续市场状态路由

市场状态权重、行业暴露和组合相关性属于下一阶段。必须先取得上述配对影子数据，再将趋势、震荡、风险退潮等状态作为预先声明的分层变量；禁止在同一测试集上反复搜索状态阈值。
