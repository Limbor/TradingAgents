# StockManager 策略模拟盘接入

TradingAgents 在 `/paper` 提供统一的策略模拟盘工作台。StockManager Web API 和其 SQLite 会话账本继续负责策略计算、成交、资金及持仓；TradingAgents 通过本机 HTTP 桥接读取结果，并将当前会话绑定到交易 Agent 对话。TradingAgents 的手工持仓 `/portfolio` 与模拟盘账本互不写入。

前端按「决策工作台 → 交易 Agent → 策略研究 → 模拟盘 → 决策审计」组织主流程。总览从真实选股产物展示候选、量化判断、Agent 判断和证据；`/chat` 同屏展示对话、任务步骤和实际证据来源；`/research` 提交并查看策略回测；`/paper` 在当前会话账本旁直接与 Agent 对话。市场全景、持仓管理、产物库、反思记录和设置保留在次级导航。

## 本地启动

1. 确认 StockManager 位于同级目录并安装了其 `.venv` 依赖。
2. 在 TradingAgents 仓库运行 `./scripts/dev.sh start`，脚本会启动或复用本机 StockManager Web 后端（`127.0.0.1:8787`），只启动 TradingAgents 前端。打开前端的「模拟盘」。`./scripts/dev.sh status` 可查看连接状态，`stop` 只会停止由此脚本启动的 StockManager 进程。
3. StockManager 使用其他本机端口时，在启动 TradingAgents 前设置 `STOCKMANAGER_WEB_URL=http://127.0.0.1:<port>`。慢速会话可通过 `STOCKMANAGER_WEB_TIMEOUT` 调整读取超时，默认 90 秒。

如果 StockManager Web 已在升级前启动，需重启该进程以加载新增的组合配置列表接口。推进策略需要 StockManager 原有的数据源配置（包括 `TUSHARE_TOKEN`）。

TradingAgents 只接受本机 HTTP 地址，并仅暴露会话、状态、净值、成交、计划及推进所需的固定路由。推进操作需要在页面确认。StockManager 原有的 MCP 服务仍负责量化信号工具，与模拟盘 Web API 是两个独立入口。

## 本阶段范围

- 单策略与组合策略会话创建和选择；组合策略配置由 StockManager 自动列出。
- 净值、现金、持仓、成交、下一日计划和组合决策展示。
- 按目标交易日异步推进，显示任务进度并在完成后刷新页面。
- 模拟盘内直接与 Agent 对话，当前 `session_id` 在后续轮次中保持绑定；切换会话时只显示对应会话的对话记录。Agent 的 `get_paper_session` 只读工具从 StockManager 拉取最新证据。
- Agent 对模拟盘问题先核对账本再回答；模型不可用时显示带日期和来源的事实摘要。

本阶段没有自动执行 Agent 建议，也没有将 Agent 建议写入 StockManager 的策略模拟盘。策略模拟盘继续按既有策略执行；Agent 负责解释和分析。

## 初版验收路径

1. 重启已运行的 TradingAgents 与 StockManager Web 后端，运行 `./scripts/dev.sh status` 确认三项服务正常。
2. 打开「模拟盘」，选一个已有会话，核对权益、持仓、成交、组合决策和下一日计划；页面「刷新」会重新读取所有区块。
3. 在左侧 Agent 区点击建议问题，或点击账本中的「问 Agent 原因」；检查回答的会话、数据基准日和来源，再用一句短追问验证会话保持。切换到另一个会话，检查对话内容不会串盘。
4. 如需测试写入，选择测试会话和目标日期，在确认框确认推进；任务完成后检查净值和成交。页面刷新后仍可恢复进行中的任务状态。

当前 Agent 仅分析与解释策略模拟盘；不会通过对话直接推进交易日或写入成交。
