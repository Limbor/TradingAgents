# 脚本索引

从仓库根目录运行 Python 脚本时使用 `.venv/bin/python`；脚本不因名称含 `smoke` 或 `validate` 就保证离线或只读。下面明确用途与运行边界。

## 日常开发与维护

| 脚本 | 用途 | 边界 |
|---|---|---|
| `dev.sh` | 启停/重启前后端及本地 StockManager 服务 | 管理进程与开发日志，不清空账本 |
| `verify.sh` | Ruff、非 live 后端测试、前端 lint/test/build、diff 检查 | 本地质量门；浏览器 E2E 在 CI 单独运行 |
| `build_desktop.sh` | PyInstaller API sidecar + Tauri 应用构建 | 生成本地安装与构建产物 |
| `clean_workspace.py` | 固定白名单清理再生成缓存 | 默认仅预览；`--apply` 删除；拒绝 Git 跟踪路径与符号链接 |
| `gen_cn_calendar.py` | 交易日历缓存修复工具 | 调用外部日历接口并写缓存，保留为故障恢复入口 |

```bash
./scripts/dev.sh status
.venv/bin/python scripts/clean_workspace.py
.venv/bin/python scripts/clean_workspace.py --apply
```

清理命令只处理 `build/`、pytest/Ruff 缓存、浏览器测试输出、Tauri debug 缓存、源码字节码和 macOS 元数据。它不处理依赖、密钥、服务日志/PID、历史报告、数据库、归档和 release 交付产物。避免在相同目录进行测试/构建时同时清理。

## 隔离模拟盘验收

| 脚本 | 用途 | 边界 |
|---|---|---|
| `verify_agent_paper_engine.py` | 合成行情与账本跑 StockManager 真正策略引擎 | 临时目录；无模型/供应商调用 |
| `verify_agent_paper_live.py` | 隔离双服务，确定性 runner 验证提案与确认 | 复制源码到临时目录，不推进真实账本 |
| `verify_existing_paper_readonly.py` | 对现有账本的只读副本验证 Agent | 只读打开源库、验证使用副本 |

## 连接、数据与模型诊断

| 脚本 | 用途 | 边界 |
|---|---|---|
| `smoke_mcp_contract.py` | HTTP MCP 健康、能力和因子契约 | 显式 `RUN_LIVE_MCP_TESTS=1`，只读外部调用 |
| `smoke_mcp_requirements.py` | MCP 增强需求 R1–R5 验收 | 调用实际 MCP 服务 |
| `smoke_cn_indicator.py` | Tushare/AKShare 指标与日期路由排查 | 外部行情调用 |
| `smoke_strategy_backtest.py` | 规则回测契约 | 显式 live 开关；外部计算 |
| `smoke_structured_output.py` | 研究/交易/风控模型的结构化输出 | 调用真实模型，会消耗额度 |
| `smoke_decision_audit.py` | 事后结果评估链路 | 对显式提供的数据库副本写评估结果，调用外部行情 |

旧 `test_mcp_connection.py` 已移除：它使用写死的本机 stdio 路径和凭据，当前 HTTP MCP 契约验证由 `smoke_mcp_contract.py` / `smoke_mcp_requirements.py` 提供。根目录临时 `test.py` 已移除，指标诊断使用 `smoke_cn_indicator.py` 和正式测试套件。

## 历史回测、评估与记忆治理

| 脚本 | 用途 | 边界 |
|---|---|---|
| `backtest_signal_fusion.py` | 按历史交易日比较量化/模型复核信号 | 行情/MCP 调用；fused/compare 模式消耗模型额度 |
| `backtest_cn_deepseek.py` | 小范围 A 股方向实验 | 默认本地评分但仍需市场数据；DeepSeek 上传需显式选项 |
| `accumulate_eval_samples.py` | 积累历史评估案例 | 外部行情/排名，写评估样本；不代表真实成交 |
| `evaluate_strategy_memory.py` | 冻结快照上的有/无记忆成对评测 | 调用指定模型；可选保存产物 |
| `review_strategy_lessons.py` | 检查并批准/停用候选经验 | 查询可只读；批准等操作写经验治理状态 |
| `backfill_neutral_reflection.py` | 旧中性决策的一次性迁移修复 | 支持 dry-run；实际执行会更新历史归因，保留供旧库迁移 |
| `validate_daily_run.py` | 信号生成及之后的结果核验 | 调用市场/模型，写验证结果 |
| `validate_decision_history.py` | 统计已存储的历史决策 | 读取本地库进行验证 |
| `compare_selection_analysis.py` | 选股与后续个股分析对齐统计 | SQLite 只读 |

`backtest_signal_fusion.py` 默认结果保存在 `<results_dir>/backtests/`，通常为 `~/.tradingagents/logs/backtests/`；文件名包含模式，避免相同日期的量化/融合结果覆盖。可用 `--output /path/to/result.json` 指定位置。其他脚本沿用其明确的输出参数或应用数据目录。

本轮将 `scripts/` 中 3 个历史回测 JSON 和 `vibe_images/` 中 4 个临时图片保存到 `.local-artifacts/2026-10-03/`。归档 SHA256 清单在该目录；它属于本机历史资料，不提交仓库。
