# StockManager 策略模拟盘接入

TradingAgents 在 `/paper` 提供统一的策略模拟盘工作台。StockManager Web API 和其 SQLite 会话账本继续负责策略计算、成交、资金及持仓；TradingAgents 通过本机 HTTP 桥接读取结果，并将当前会话绑定到交易 Agent 对话。TradingAgents 的手工持仓 `/portfolio` 与模拟盘账本互不写入。

前端按「决策工作台 → 交易 Agent → 策略研究 → 模拟盘 → 决策审计」组织主流程。总览从真实选股产物展示候选、量化判断、Agent 判断和证据；`/chat` 同屏展示对话、任务步骤和实际证据来源；`/research` 提交并查看策略回测；`/paper` 在当前会话账本旁直接与 Agent 对话。市场全景、持仓管理、产物库、反思记录和设置保留在次级导航。

## 本地启动

1. 确认 StockManager 位于同级目录并安装了其 `.venv` 依赖。
2. 在 TradingAgents 仓库运行 `./scripts/dev.sh start`，脚本会启动或复用本机 StockManager Web（`127.0.0.1:8787`）和 MCP（`127.0.0.1:8765/mcp`），并启动 TradingAgents 后端与前端。打开前端的「模拟盘」。`./scripts/dev.sh status` 可分别查看四个服务；`stop` 只会停止由此脚本启动的进程。
3. StockManager 使用其他本机端口时，在启动 TradingAgents 前设置 `STOCKMANAGER_WEB_URL=http://127.0.0.1:<port>`。慢速会话可通过 `STOCKMANAGER_WEB_TIMEOUT` 调整读取超时，默认 90 秒。

如果 StockManager Web 已在升级前启动，需重启该进程以加载新增的组合配置列表接口。推进策略需要 StockManager 原有的数据源配置（包括 `TUSHARE_TOKEN`）。

TradingAgents 只接受本机 HTTP 地址，并仅暴露会话、状态、净值、成交、计划及推进所需的固定路由。推进操作需要在页面确认。StockManager 原有的 MCP 服务仍负责量化信号工具，与模拟盘 Web API 是两个独立入口。

## 本阶段范围

- 单策略与组合策略会话创建和选择；组合策略配置由 StockManager 自动列出。
- 净值、现金、持仓、成交、下一日计划和组合决策展示。
- 按目标交易日异步推进，显示任务进度并在完成后刷新页面。
- 模拟盘内直接与 Agent 对话，当前 `session_id` 固定绑定到持久会话；切换会话时只显示对应会话的任务和对话记录。Agent 的 `get_paper_session` 只读工具从 StockManager 拉取最新证据，任务事件和证据写入 TradingAgents SQLite。
- 创建绑定模拟盘的 Agent 对话前，后端先读取 StockManager 状态接口，核对账户 ID 与账本日期；账户不存在、服务断开或返回冲突数据时不会保存该对话。
- Agent 对模拟盘问题先核对账本再回答；模型不可用时显示带日期和来源的事实摘要。任务计划、工具执行和数据警告在界面中可见，刷新页面后可恢复任务记录。

Agent 不自动执行建议。用户明确要求推进到指定日期时，Agent 会生成含当前账本基准日的动作预览；用户确认后，服务端复核账本并请求 StockManager 推进。Agent 不提供逐笔模拟订单或实盘交易。

## 初版验收路径

1. 重启已运行的 TradingAgents 与 StockManager 服务，运行 `./scripts/dev.sh status` 确认四项服务正常。
2. 打开「模拟盘」，选一个已有会话，核对权益、持仓、成交、组合决策和下一日计划；页面「刷新」会重新读取所有区块。
3. 在左侧 Agent 区点击建议问题，或点击账本中的「问 Agent 原因」；检查回答的会话、数据基准日和来源，再用一句短追问验证会话保持。切换到另一个会话，检查对话内容不会串盘。
4. 如需测试写入，选择测试会话和目标日期，在确认框确认推进；任务完成后检查净值和成交。页面刷新后仍可恢复进行中的任务状态。

Agent 仍可只读分析与解释策略模拟盘。要从对话推进交易日，需明确给出目标日期，并在动作预览卡上确认；执行结果以 StockManager 账本为准。

2026-09-27 的隔离联调使用 StockManager 实际 `runtime_v2` 路由和临时 SQLite 账本，经本机 HTTP 接入 TradingAgents REST API。单策略、组合策略账户均可创建绑定对话，并完成“查看当前模拟盘账户状态”任务；保存的 `get_paper_session` 证据包含对应账户和账本基准日。不存在的账户返回 404 且不会创建对话。联调用固定文本代替模型合成，验证的是接口、取证、事件及持久化链路；尚需使用用户实际策略会话验收模型回答与数据质量。
