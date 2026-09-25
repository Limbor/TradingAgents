# StockManager 策略模拟盘接入

TradingAgents 在 `/paper` 提供统一的策略模拟盘工作台。StockManager Web API 和其 SQLite 会话账本继续负责策略计算、成交、资金及持仓；TradingAgents 通过本机 HTTP 桥接读取结果，并将当前会话绑定到交易 Agent 对话。TradingAgents 的手工持仓 `/portfolio` 与模拟盘账本互不写入。

## 本地启动

1. 在 StockManager 仓库启动现有 Web 后端，默认监听 `127.0.0.1:8787`。例如在其虚拟环境运行 `uvicorn stockmanager.web.app:app --host 127.0.0.1 --port 8787`。
2. 在 TradingAgents 仓库运行 `./scripts/dev.sh start`，打开前端的「模拟盘」。
3. StockManager 使用其他本机端口时，在启动 TradingAgents 前设置 `STOCKMANAGER_WEB_URL=http://127.0.0.1:<port>`。慢速会话可通过 `STOCKMANAGER_WEB_TIMEOUT` 调整读取超时，默认 90 秒。

TradingAgents 只接受本机 HTTP 地址，并仅暴露会话、状态、净值、成交、计划及推进所需的固定路由。推进操作需要在页面确认。StockManager 原有的 MCP 服务仍负责量化信号工具，与模拟盘 Web API 是两个独立入口。

## 本阶段范围

- 单策略与组合策略会话创建和选择；组合策略使用 StockManager 的 allocator 配置路径。
- 净值、现金、持仓、成交、下一日计划和组合决策展示。
- 按目标交易日异步推进，显示任务进度并在完成后刷新页面。
- 从模拟盘进入 Agent 对话，当前 `session_id` 在对话后续轮次中保持绑定。Agent 的 `get_paper_session` 只读工具从 StockManager 拉取最新证据。

本阶段没有自动执行 Agent 建议，也没有将 Agent 建议写入 StockManager 的策略模拟盘。策略模拟盘继续按既有策略执行；Agent 负责解释和分析。
