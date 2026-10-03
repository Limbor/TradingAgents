# 文档索引

## 先读这些

| 文档 | 说明 |
|---|---|
| [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) | 当前整体框架、入口、模块职责、数据边界及维护方式 |
| [PROGRESS.md](PROGRESS.md) | 按时间记录的实现进度；顶部为最新变更，旧章节保留历史状态 |
| [AGENT_INITIAL_ACCEPTANCE.md](AGENT_INITIAL_ACCEPTANCE.md) | 本地启动与验收场景 |
| [../scripts/README.md](../scripts/README.md) | 脚本用途、外部调用与写入边界 |

## 专项实现与契约

- [UNIFIED_AGENT_MIGRATION.md](UNIFIED_AGENT_MIGRATION.md)：统一 Agent 迁移与已完成验证。
- [STRATEGY_MEMORY.md](STRATEGY_MEMORY.md)：经验治理、版本、检索、追踪与成对评测。
- [STOCKMANAGER_PAPER_INTEGRATION.md](STOCKMANAGER_PAPER_INTEGRATION.md)：独立模拟盘账本集成。
- [MCP_QUANT_SIGNAL_CONTRACT.md](MCP_QUANT_SIGNAL_CONTRACT.md)：StockManager 量化数据契约。
- [MCP_ENHANCEMENT_REQUIREMENTS.md](MCP_ENHANCEMENT_REQUIREMENTS.md)：外部服务增强要求与验收；要求不等于全部已实现。
- [QIANWEN_INTEGRATION.md](QIANWEN_INTEGRATION.md)：千问渠道集成及模型选择。
- [DESKTOP_PACKAGING.md](DESKTOP_PACKAGING.md)：Tauri 与 API sidecar 打包。
- [AI_DECISION_ACCURACY_EXECUTION.md](AI_DECISION_ACCURACY_EXECUTION.md)：历史样本、回测与决策质量评估方法。

## 历史设计与规划

- [SPEC.md](SPEC.md)：早期技术设计与分阶段路线，包含已被替代的模型配置和目录说明。
- [FEATURE_PLAN.md](FEATURE_PLAN.md)：早期 Free ChatAgent 与模式提取计划。
- [TRADING_AGENT_HARNESS_DESIGN.md](TRADING_AGENT_HARNESS_DESIGN.md)：Harness 设计起点与后续追加的实现记录。
- [A股Agent架构说明.md](A股Agent架构说明.md)：A 股分析设计背景。

历史文档用于理解演进，不删除设计依据；判断当前行为时以总览、最新进度、实际代码及测试为准。
