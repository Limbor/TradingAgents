# TradingAgents 开发进度汇总

> 最后更新: 2026-06-18
> 对应设计文档: [SPEC.md](./SPEC.md)

---

## 目录

1. [原始能力 (v0.2.5 基线)](#1-原始能力-v025-基线)
2. [目标能力 (v2.0 愿景)](#2-目标能力-v20-愿景)
3. [整体进度总览](#3-整体进度总览)
4. [Phase 1 — API 层 + 基础前端](#4-phase-1--api-层--基础前端)
5. [Phase 2 — 技能扩展 + 对话式交互](#5-phase-2--技能扩展--对话式交互)
6. [Phase 3 — 桌面应用 + 高级功能](#6-phase-3--桌面应用--高级功能)
7. [已知问题与代码瑕疵](#7-已知问题与代码瑕疵)
8. [下一步建议](#8-下一步建议)

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
Phase 3 (桌面应用 + 高级)    ░░░░░░░░░░░░░░░░░░░░░░   ~0%  ← 未开始
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
| Tauri 桌面打包 | ❌ 缺失 | desktop/ 目录不存在 |
| 集成/E2E 测试 | ✅ 完成 | [tests/integration/test_phase1_flow.py](../tests/integration/test_phase1_flow.py) |

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

## 6. Phase 3 — 桌面应用 + 高级功能

### 全部未开始 ❌

| Week | 里程碑 | 状态 |
|------|--------|------|
| W9 | Tauri 集成 (Sidecar/窗口/托盘) | ❌ desktop/ 不存在 |
| W10 | 回测 Skill (backtrader) | ❌ |
| W11 | 通知系统 (macOS 推送) | ❌ |
| W12 | 打包分发 (.dmg 构建) | ❌ |

---

## 7. 已知问题

### 7.1 架构偏差（功能性缺口）

- **LLM 意图路由尚未接入**: 当前 Orchestrator 使用离线规则路由，覆盖 Phase 2 验收用例和常见技能触发。后续可加入 LLM fallback，但不作为当前正确性的依赖。
- **Phase 1/2 已收尾**: `RunManager` 已集成 SQLite 持久化，报告入库已使用真实 `run_id`，WebSocket 已统一终态事件，Chat 可自然语言触发技能。

---

## 8. 下一步建议

### 8.1 代码质量

前后端代码经 `npm run build`、Python 单元/集成测试和浏览器路由验证，当前无语法错误，可以正常编译运行。一键启动脚本 `scripts/dev.sh` 已就绪，支持 start/stop/restart/status。

### 8.2 Phase 3 优先推进

1. 初始化 `desktop/` Tauri 项目骨架
2. 编写 PyInstaller sidecar 打包脚本
3. 验证前后端在 Tauri WebView 中的兼容性
4. 继续增强 Orchestrator：加入 LLM fallback、更多中文股票别名、参数澄清

### 8.3 质量保障

1. 补充前端测试（vitest）— 覆盖 store 状态流转、WebSocket 重连
2. 增加浏览器 E2E 自动化 — 覆盖 Dashboard 创建运行、Chat 自然语言路由、Analysis 实时更新、Reports 详情查看

### 8.4 Phase 3 准备

1. 初始化 `desktop/` Tauri 项目骨架
2. 编写 PyInstaller sidecar 打包脚本
3. 验证前后端在 Tauri WebView 中的兼容性
