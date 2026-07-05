# TradingAgents v2 — 架构设计与开发指南

> 版本: 1.0.0-draft  
> 日期: 2026-07-04  
> 作者: TradingAgents Team

---

## 目录

1. [项目愿景与目标](#1-项目愿景与目标)
2. [系统架构总览](#2-系统架构总览)
3. [技术栈选型](#3-技术栈选型)
4. [后端架构 — API 层](#4-后端架构--api-层)
5. [技能系统设计 — Skill Framework](#5-技能系统设计--skill-framework)
6. [前端架构 — React Web App](#6-前端架构--react-web-app)
7. [macOS 桌面打包 — Tauri](#7-macos-桌面打包--tauri)
8. [核心代码实现](#8-核心代码实现)
9. [数据层与状态管理](#9-数据层与状态管理)
10. [安全与认证](#10-安全与认证)
11. [测试策略](#11-测试策略)
12. [部署与分发](#12-部署与分发)
13. [开发迭代路线图](#13-开发迭代路线图)
14. [附录](#附录)

---

## 1. 项目愿景与目标

### 1.1 当前状态

TradingAgents v0.2.5 是一个基于 LangGraph 的多智能体金融分析框架：
- 13 个 agent 组成 5 阶段管道（分析 → 研究辩论 → 交易 → 风险辩论 → 决策）
- 支持 18+ LLM 提供商，A 股/美股双市场
- 唯一入口是 Rich CLI 终端界面

### 1.2 目标状态

将 TradingAgents 升级为一个**可视化的多技能 Agent 平台**：

| 维度 | 目标 |
|------|------|
| **界面** | Web-first 可视化工作台，远期打包为 macOS 原生桌面应用（Tauri） |
| **架构** | 从"单一分析管道"进化为"可插拔技能框架"，并通过 StockManager MCP 委托 A 股量化计算 |
| **交互** | Chat-first 智能体工作台：自由问答、轻量工具增强和关键 Skill 触发共用同一入口 |
| **扩展** | 新 Skill 只需实现标准接口即可接入，无需改动核心框架 |

### 1.3 设计原则

1. **渐进式改造** — 不破坏现有功能，在现有 `tradingagents/` 包之上分层构建
2. **前后端分离** — Python 后端（FastAPI + WebSocket）驱动所有逻辑，前端纯展示
3. **Skill 即图** — 每个技能是一个独立的 LangGraph，共享基础设施（LLM client、数据层、记忆）
4. **量化能力外置** — A 股行情、因子、回测、交易计划委托给本地 StockManager MCP 服务，TradingAgents 专注 LLM 决策链路
5. **离线优先** — 桌面应用无需远端自建服务（除 LLM/API 数据源），数据本地缓存
6. **类型安全** — 前后端均采用严格类型（Pydantic + TypeScript）

---

## 2. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        macOS App (Tauri v2)                               │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    React Frontend (WebView)                        │  │
│  │  ┌──────────┐ ┌──────────────┐ ┌───────────┐ ┌───────────────┐  │  │
│  │  │ Chat UI  │ │ Agent Graph  │ │ Dashboard │ │  Report View  │  │  │
│  │  │          │ │ Visualizer   │ │           │ │               │  │  │
│  │  └────┬─────┘ └──────┬───────┘ └─────┬─────┘ └───────┬───────┘  │  │
│  │       └───────────────┴───────────────┴───────────────┘           │  │
│  │                          ↕ WebSocket + REST                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                 Python Backend (Sidecar Process)                    │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │              FastAPI + WebSocket Server                       │  │  │
│  │  │  /api/v1/skills   /api/v1/analyze   /ws/stream              │  │  │
│  │  └────────────────────────┬────────────────────────────────────┘  │  │
│  │                           │                                        │  │
│  │  ┌────────────────────────▼────────────────────────────────────┐  │  │
│  │  │              Skill Orchestrator (意图路由)                    │  │  │
│  │  │  NLU Router → Skill Registry → Execution Engine              │  │  │
│  │  └────────────────────────┬────────────────────────────────────┘  │  │
│  │                           │                                        │  │
│  │  ┌──────────┬──────────┬──▼──────┬────────────┬────────────────┐  │  │
│  │  │  Skill:  │  Skill:  │ Skill: │  Skill:    │  Skill:        │  │  │
│  │  │  Stock   │  Portf.  │ Back-  │  Market    │  News          │  │  │
│  │  │  Analysis│  Mgmt    │ test   │  Scanner   │  Monitor       │  │  │
│  │  └────┬─────┴────┬─────┴───┬────┴─────┬──────┴────────┬───────┘  │  │
│  │       └──────────┴─────────┴──────────┴───────────────┘           │  │
│  │                           │                                        │  │
│  │  ┌────────────────────────▼────────────────────────────────────┐  │  │
│  │  │                    Core Layer                                 │  │  │
│  │  │  LLM Clients │ DataFlows │ Memory │ Config │ Event Bus      │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.1 分层说明

| 层级 | 职责 | 关键技术 |
|------|------|----------|
| **Presentation** | 用户交互、可视化 | React 18, TypeScript, @xyflow/react, Recharts |
| **API Gateway** | HTTP/WS 接口、认证、限流 | FastAPI, Uvicorn, WebSocket |
| **Orchestration** | 意图识别、技能路由、执行调度 | LLM Router, Skill Registry |
| **Skill** | 独立的 agent 工作流 | LangGraph StateGraph |
| **Core** | 共享基础能力 | LLM factory, DataFlows, Memory, Config |
| **MCP Service** | A 股数据、因子、回测、交易计划 | StockManager MCP Server |
| **Infrastructure** | 打包分发、进程管理 | Tauri v2, PyInstaller (sidecar) |

### 2.2 StockManager MCP 集成边界

TradingAgents v2 的 A 股增强链路采用“双进程本地服务”模式：

```
React UI / Chat
      │
      ▼
TradingAgents FastAPI + Agent Skills
      │  HTTP/Streamable MCP
      ▼
http://127.0.0.1:8765/mcp
      │
      ▼
StockManager MCP Server
```

| 边界 | TradingAgents | StockManager MCP |
|------|---------------|------------------|
| 决策职责 | 意图路由、Agent 辩论、报告生成、持仓语境、前端展示 | 不负责自然语言决策 |
| 量化职责 | 读取结构化证据并解释 | 行情、复权、交易日历、因子、回测、交易计划 |
| 通信方式 | 作为 MCP Client 连接 localhost 服务 | 独立启动 HTTP MCP 服务 |
| 默认地址 | `STOCKMANAGER_MCP_URL=http://127.0.0.1:8765/mcp` | `STOCKMANAGER_MCP_HOST=127.0.0.1`, `STOCKMANAGER_MCP_PORT=8765` |
| 长任务 | 创建 run/job、轮询状态、WebSocket 推送进度 | `run_backtest` / `run_factor_experiment` 返回 `job_id` |
| 降级 | capability flags 控制 UI 和技能可用性 | health/capabilities 暴露当前可用工具 |

内部默认不再由 TradingAgents 通过 `uv run python stockmanager-mcp/server.py` 拉起 StockManager。开发环境手动启动 StockManager MCP；远期桌面版由统一 sidecar supervisor 启动两个本地服务。TradingAgents 启动时先访问 `/health` 和 `/capabilities`，健康检查失败则快速降级，不继续打开 MCP 长连接；真实 tool 调用统一走 HTTP/Streamable MCP，并受 `STOCKMANAGER_MCP_TIMEOUT` 约束。

### 2.3 内容摄取与 LLM Backbone

新闻、公告、研报和社区情绪不默认全量塞入 Agent prompt，而是按三层处理：

| 层级 | 内容 | 用途 |
|------|------|------|
| 快速层 | 标题、摘要/摘录、来源、链接、发布时间、情绪/风险标签 | 常规分析，控制 token 和延迟 |
| 深度层 | Top N 重要新闻/公告/研报正文或 PDF 抽取文本 | 重大事件分析、风控、持仓建议 |
| 原文层 | 原文 URL、PDF/HTML、缓存路径、hash | 审计追溯，不直接整篇进入 prompt |

A 股个股新闻在 AKShare 返回“新闻内容/正文”列时，可输出受限正文摘录；公告 PDF、研报全文和社区长贴全文后续通过 MCP/document fetcher 接入，并统一转为 Evidence Card。

LLM 配置采用统一 Backbone：

| 配置 | 说明 |
|------|------|
| `llm_provider` | 全局 provider，如 DeepSeek、Qwen、OpenAI、OpenAI Compatible |
| `quick_think_llm` | Chat 路由 fallback、分析员、辩论、交易员等快速任务 |
| `deep_think_llm` | Research Manager、Portfolio Manager 等高权重决策任务 |
| `backend_url` | OpenAI-compatible 或私有网关地址 |

系统不维护独立的 Chat API 配置；ChatAgent、LLM Router 和候选股 LLM Review 默认复用 quick model，完整个股分析中的高权重裁决继续使用 deep model。

### 2.4 Chat-first 交互模型

TradingAgents 的主交互面从“所有 Skill 跳转 Analysis”调整为“Chat 任务流 + Dashboard 驾驶舱 + Analysis 详情页”：

| 页面 | 定位 | 默认行为 |
|------|------|----------|
| Chat | 交易 Agent 主入口 | 用户输入或按钮触发的 Skill 均以任务卡片形式执行，展示执行进度、结构化结果和最终摘要 |
| Dashboard | 账户/机会驾驶舱 | 展示持仓市值、成本、盈亏、集中度、风险状态、MCP 健康和近期运行 |
| Portfolio | 持仓工作台 | 管理持仓明细，承接风险仪表盘和持仓建议 |
| Watchlist | 候选池工作台 | 展示每日选股和候选追踪，手动触发时进入 Chat 执行 |
| Analysis | 深度详情页 | 仅用于完整股票分析、Agent 图谱、报告章节、调试和审计，不作为轻量 Skill 的默认终点 |

Chat 中展示的是“执行链路状态”和“可审计摘要”，不是原始模型 CoT。后端 Skill 应优先发送用户可理解的 `agent_status` / `tool_call` / structured result 事件，例如“正在查询股票公告”“正在计算持仓盈亏”“正在生成风险摘要”。前端按 `run_id` 聚合这些事件，形成一张任务卡片；任务卡片可以跳转 Analysis 查看完整报告。

### 2.5 自由 ChatAgent 与工具增强

Chat 不应把每一句话都强行路由为长任务 Skill。后续 Chat 编排层采用四类意图：

| 意图类型 | 行为 | 示例 |
|----------|------|------|
| `chat_answer` | 直接调用统一 LLM Backbone 生成自然语言回复，可引用已知上下文 | “解释一下 WATCHLIST 是什么意思” |
| `tool_answer` | 调用轻量工具后回答，不创建长 run | “生益科技现在估值和资金流怎么样” |
| `skill_run` | 创建 run 并进入 Skill 任务卡 | “跑一下每日选股 top 5”“分析 600519” |
| `clarify` | 参数不足或风险较高时追问 | “你要看主板还是全市场？” |

ChatAgent 可读取和调用的上下文/工具分层：

| 层级 | 能力 | 边界 |
|------|------|------|
| 本地上下文 | Library artifacts、最近 runs、持仓、策略反思、用户配置 | 只读为主，用户明确操作时才写入 |
| 轻量工具 | MCP 股票名称/行情/因子快照、持仓摘要、artifact search、reflection lessons | 单次低延迟查询，不进入完整 Skill |
| 长任务 Skill | daily_pipeline、stock_analysis、risk_monitor、daily_review、portfolio_management | 有 run_id、事件流、artifact、可审计结果 |

ChatAgent 输出必须是“可解释的工作台回复”，不能暴露模型内部 CoT。需要展示过程时，使用可审计的阶段摘要，例如“正在读取持仓”“正在查询 MCP 因子快照”“需要启动每日选股任务”。若轻量回答中引用工具数据，必须附带 `as_of_date/source/warnings` 摘要；若判断需要完整 Agent 管道，应建议或自动触发对应 Skill。

---

## 3. 技术栈选型

### 3.1 后端

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | **FastAPI** | 原生 async，自动 OpenAPI 文档，Pydantic 集成 |
| WebSocket | **FastAPI WebSocket** | 内建支持，无额外依赖 |
| 任务队列 | **asyncio.TaskGroup** | 本地单机，无需 Celery/Redis 等外部队列 |
| Agent 框架 | **LangGraph** (已有) | 已证明可行的 agent 编排框架 |
| LLM 客户端 | 已有的 `llm_clients/` | 支持 18+ 提供商 |
| 数据层 | 已有的 `dataflows/` | yfinance, AKShare, TuShare, FRED 等 |
| 持久化 | **SQLite** (via aiosqlite) | 零配置，适合桌面应用 |
| Python 版本 | **3.11+** | TaskGroup, tomllib, 性能提升 |

### 3.2 前端

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | **React 18** | 生态成熟，组件丰富 |
| 语言 | **TypeScript** | 类型安全，接口对齐后端 Pydantic |
| 构建工具 | **Vite** | 快速 HMR，原生 ESM |
| UI 库 | **shadcn/ui** + **Tailwind CSS** | 可定制，无运行时开销 |
| 状态管理 | **Zustand** | 轻量、类型友好、支持 middleware |
| 数据获取 | **@tanstack/react-query** | 自动缓存、重试、WebSocket 集成 |
| 图表 | **Recharts** | 声明式、React 原生 |
| Agent 图可视化 | **@xyflow/react** (React Flow) | 节点/边拖拽、自动布局 |
| Markdown 渲染 | **react-markdown** + **remark-gfm** | 支持 GFM 表格、代码高亮 |
| 路由 | **React Router v7** | 文件系统路由可选 |

### 3.3 桌面打包

| 组件 | 选型 | 理由 |
|------|------|------|
| 外壳 | **Tauri v2** | Rust 内核, 内存占用远小于 Electron，原生 macOS API |
| 后端 Sidecar | **PyInstaller / PyOxidizer** 打包的 Python 进程 | 无需用户安装 Python |
| IPC | Tauri Commands + localhost HTTP/WS | 简单可靠，前端代码在桌面/Web 环境通用 |

### 3.4 为什么不用 Electron？

| 维度 | Tauri v2 | Electron |
|------|----------|----------|
| 内存占用 | ~30-50MB | ~150-300MB |
| 包体积 | ~10-20MB (不含 Python) | ~100MB+ |
| macOS 原生能力 | Rust 直接调用 Cocoa | 需要 node addon |
| 安全模型 | Capabilities-based, 最小权限 | 全权限 |
| 学习曲线 | 需要 Rust 基础 | 纯 JS/TS |

对于本项目，后端逻辑全在 Python sidecar，Rust 只负责窗口管理和系统集成，代码量极少。

---

## 4. 后端架构 — API 层

### 4.1 目录结构

```
tradingagents/
├── api/                          # 新增: API 层
│   ├── __init__.py
│   ├── app.py                    # FastAPI 应用工厂
│   ├── config.py                 # API 配置 (host, port, CORS)
│   ├── deps.py                   # 依赖注入 (skill registry, config)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── skills.py             # GET /skills, POST /skills/{id}/run
│   │   ├── analyze.py            # POST /analyze (快捷入口)
│   │   ├── reports.py            # GET /reports, GET /reports/{id}
│   │   ├── config.py             # GET/PUT /config
│   │   ├── memory.py             # GET /memory
│   │   └── health.py             # GET /health
│   ├── ws/
│   │   ├── __init__.py
│   │   └── stream.py             # WebSocket /ws/run/{run_id}
│   ├── models/
│   │   ├── __init__.py
│   │   ├── requests.py           # 请求模型
│   │   ├── responses.py          # 响应模型
│   │   └── events.py             # WebSocket 事件模型
│   └── middleware/
│       ├── __init__.py
│       └── error_handler.py      # 全局异常处理
├── core/                          # 新增: 共享核心层
│   ├── __init__.py
│   ├── event_bus.py              # 进程内事件总线
│   ├── run_manager.py            # 运行生命周期管理
│   ├── persistence.py            # SQLite 持久化
│   └── orchestrator.py           # 意图路由 + 技能调度
├── skills/                        # 新增: 技能模块
│   ├── __init__.py
│   ├── base.py                   # BaseSkill ABC
│   ├── registry.py               # Skill 注册与发现
│   └── stock_analysis/           # 现有功能封装为 Skill
│       ├── __init__.py
│       ├── skill.py
│       └── ...
├── agents/                        # 已有: agent 定义 (不动)
├── dataflows/                     # 已有: 数据层 (不动)
├── graph/                         # 已有: LangGraph 编排 (不动)
├── llm_clients/                   # 已有: LLM 抽象 (不动)
└── default_config.py              # 已有: 配置 (扩展)
```

### 4.2 API 设计

#### REST 端点

```
GET    /api/v1/health                    # 健康检查
GET    /api/v1/skills                    # 列出所有可用技能
GET    /api/v1/skills/{skill_id}/schema  # 获取技能的输入 schema

POST   /api/v1/runs                      # 创建一次运行（启动 Skill 执行）
GET    /api/v1/runs                      # 列出运行记录
GET    /api/v1/runs/{run_id}             # 获取运行状态与结果
DELETE /api/v1/runs/{run_id}             # 取消运行

GET    /api/v1/reports                   # 列出历史报告
GET    /api/v1/reports/{report_id}       # 获取报告详情

GET    /api/v1/config                    # 获取当前配置
PUT    /api/v1/config                    # 更新配置
GET    /api/v1/config/providers          # 列出可用 LLM 提供商及模型

GET    /api/v1/profile                   # 获取投资风格画像
PUT    /api/v1/profile                   # 更新投资风格画像

GET    /api/v1/holdings                  # 获取持仓列表
PUT    /api/v1/holdings/{symbol}         # 新增/更新持仓
DELETE /api/v1/holdings/{symbol}         # 删除持仓

GET    /api/v1/memory                    # 获取记忆日志
```

#### WebSocket 端点

```
WS     /ws/run/{run_id}                  # 实时流: agent 状态、报告片段、工具调用
WS     /ws/chat                          # 自然语言对话（意图路由模式）
```

### 4.3 WebSocket 事件协议

所有 WebSocket 消息遵循统一信封格式：

```typescript
interface WSMessage {
  type: "agent_status" | "report_chunk" | "tool_call" | "run_complete" | "error" | "chat_reply";
  run_id: string;
  timestamp: string;      // ISO 8601
  payload: AgentStatusEvent | ReportChunkEvent | ToolCallEvent | RunCompleteEvent | ErrorEvent | ChatReplyEvent;
}
```

事件类型：

| type | payload | 说明 |
|------|---------|------|
| `agent_status` | `{agent: string, status: "pending"|"running"|"completed"|"failed", duration_ms?: number}` | Agent 状态变更 |
| `report_chunk` | `{section: string, content: string, is_final: boolean}` | 报告流式片段 |
| `tool_call` | `{agent: string, tool: string, args: object, result?: string}` | 工具调用事件 |
| `run_complete` | `{decision: string, rating: string, report_url: string}` | 运行完成 |
| `error` | `{code: string, message: string, recoverable: boolean}` | 错误 |
| `chat_reply` | `{content: string, skill_triggered?: string}` | 聊天回复 |

---

## 5. 技能系统设计 — Skill Framework

### 5.1 设计理念

每个 Skill 是一个**自包含的 agent 工作流**，拥有：
- 独立的 LangGraph StateGraph
- 独立的输入/输出 Schema
- 共享的底层基础设施（LLM client、数据源、记忆、配置）

```
                    ┌─────────────────────────────┐
                    │       Skill Registry         │
                    │  register() / discover()     │
                    └──────────────┬──────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
    ┌─────▼──────┐          ┌─────▼──────┐          ┌─────▼──────┐
    │   Skill A  │          │   Skill B  │          │   Skill C  │
    │ (Analysis) │          │ (Portfolio)│          │ (Backtest) │
    │            │          │            │          │            │
    │ StateGraph │          │ StateGraph │          │ StateGraph │
    │ Agents     │          │ Agents     │          │ Agents     │
    │ Tools      │          │ Tools      │          │ Tools      │
    └─────┬──────┘          └─────┬──────┘          └─────┬──────┘
          │                        │                        │
          └────────────────────────┼────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │         Core Layer           │
                    │  LLM │ Data │ Memory │ Config│
                    └─────────────────────────────┘
```

### 5.2 BaseSkill 抽象

```python
# tradingagents/skills/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from pydantic import BaseModel


@dataclass
class SkillMetadata:
    """技能元数据，用于注册和路由。"""
    id: str                          # 唯一标识: "stock_analysis"
    name: str                        # 显示名: "Stock Analysis"
    description: str                 # 用于意图路由的描述
    version: str                     # 语义版本号
    triggers: list[str] = field(default_factory=list)  # 触发关键词
    icon: str = ""                   # 前端图标名
    category: str = "general"        # 分类: "analysis", "portfolio", "research"


@dataclass
class SkillEvent:
    """技能执行过程中发射的事件。"""
    event_type: str                  # "agent_status", "report_chunk", "tool_call", etc.
    data: dict[str, Any]


class BaseSkill(ABC):
    """所有技能的基类。"""

    @property
    @abstractmethod
    def metadata(self) -> SkillMetadata:
        """返回技能元数据。"""
        ...

    @property
    @abstractmethod
    def input_schema(self) -> type[BaseModel]:
        """返回输入参数的 Pydantic 模型类。"""
        ...

    @property
    @abstractmethod
    def output_schema(self) -> type[BaseModel]:
        """返回输出结果的 Pydantic 模型类。"""
        ...

    @abstractmethod
    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        """执行技能，以异步迭代器形式发射事件流。

        Args:
            params: 验证后的输入参数
            config: 运行时配置（LLM 设置等）

        Yields:
            SkillEvent: 执行过程中的事件
        """
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """取消正在执行的技能。"""
        ...

    def validate_params(self, raw: dict) -> BaseModel:
        """验证输入参数，返回 Pydantic 实例。"""
        return self.input_schema.model_validate(raw)
```

### 5.3 Skill Registry

```python
# tradingagents/skills/registry.py

import importlib
import pkgutil
from typing import Any

from .base import BaseSkill, SkillMetadata


class SkillRegistry:
    """技能注册表 — 负责技能的发现、注册和查找。"""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """注册一个技能实例。"""
        meta = skill.metadata
        if meta.id in self._skills:
            raise ValueError(f"Skill '{meta.id}' already registered")
        self._skills[meta.id] = skill

    def get(self, skill_id: str) -> BaseSkill | None:
        """按 ID 获取技能。"""
        return self._skills.get(skill_id)

    def list_all(self) -> list[SkillMetadata]:
        """列出所有已注册技能的元数据。"""
        return [s.metadata for s in self._skills.values()]

    def find_by_trigger(self, text: str) -> list[BaseSkill]:
        """根据触发词匹配技能（用于简单关键词路由）。"""
        text_lower = text.lower()
        matches = []
        for skill in self._skills.values():
            for trigger in skill.metadata.triggers:
                if trigger.lower() in text_lower:
                    matches.append(skill)
                    break
        return matches

    def auto_discover(self, package_path: str = "tradingagents.skills") -> None:
        """自动发现并注册 package 下所有实现了 BaseSkill 的模块。

        约定: 每个技能子包的 __init__.py 中导出一个 `skill` 变量，
        类型为 BaseSkill 的实例。
        """
        package = importlib.import_module(package_path)
        for importer, modname, ispkg in pkgutil.iter_modules(
            package.__path__, prefix=package.__name__ + "."
        ):
            if not ispkg:
                continue
            try:
                mod = importlib.import_module(modname)
                if hasattr(mod, "skill") and isinstance(mod.skill, BaseSkill):
                    self.register(mod.skill)
            except Exception:
                continue
```

### 5.4 Stock Analysis Skill（封装现有功能）

```python
# tradingagents/skills/stock_analysis/skill.py

from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class StockAnalysisInput(BaseModel):
    ticker: str = Field(description="Stock ticker symbol, e.g. 'AAPL' or '600519.SS'")
    analysis_date: str = Field(description="Analysis date in YYYY-MM-DD format")
    analysts: list[str] = Field(
        default=["market", "social", "news", "fundamentals"],
        description="Analysts to include",
    )
    debate_rounds: int = Field(default=1, ge=1, le=5)
    risk_rounds: int = Field(default=1, ge=1, le=5)


class StockAnalysisOutput(BaseModel):
    rating: str
    executive_summary: str
    investment_thesis: str
    price_target: float | None = None
    time_horizon: str | None = None
    report_path: str | None = None


class StockAnalysisSkill(BaseSkill):
    """将现有的 TradingAgentsGraph 封装为 Skill。"""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="stock_analysis",
            name="Stock Analysis",
            description="Multi-agent stock analysis with bull/bear debate, risk assessment, and portfolio decision",
            version="1.0.0",
            triggers=["分析", "analyze", "看看", "怎么样", "研报", "report"],
            icon="chart-line",
            category="analysis",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return StockAnalysisInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return StockAnalysisOutput

    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        """执行股票分析。

        将现有的 TradingAgentsGraph.propagate() 桥接为异步事件流。
        """
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        input_params: StockAnalysisInput = params

        # 构建配置
        run_config = {
            **config,
            "max_debate_rounds": input_params.debate_rounds,
            "max_risk_discuss_rounds": input_params.risk_rounds,
        }

        ta = TradingAgentsGraph(run_config)

        # 发射开始事件
        yield SkillEvent(
            event_type="skill_start",
            data={"skill_id": "stock_analysis", "ticker": input_params.ticker},
        )

        # 使用 astream_events 获取实时事件
        async for event in ta.astream_propagate(
            ticker=input_params.ticker,
            date=input_params.analysis_date,
            selected_analysts=input_params.analysts,
        ):
            yield SkillEvent(
                event_type=event["type"],
                data=event["data"],
            )

    async def cancel(self) -> None:
        pass


# 模块级导出，供 auto_discover 使用
skill = StockAnalysisSkill()
```

### 5.5 计划中的 Skill 一览

| Skill ID | 名称 | 描述 | 状态 |
|----------|------|------|------|
| `stock_analysis` | 股票分析 | 现有的 13-agent 分析管道 | Phase 1 |
| `portfolio_management` | 组合管理 | 持仓跟踪、再平衡建议、绩效归因 | Phase 2 |
| `market_scanner` | 市场扫描 | 基于 StockManager MCP 动态候选池和 Evidence Card 的 A 股筛选 | Phase 3 |
| `daily_pipeline` | 每日选股 | 定时执行 MCP 初筛、量化因子打分、Top N LLM 快筛融合和早报入库；Top 5 完整 13-Agent 深度分析为后续增强 | Phase 3 |
| `risk_monitor` | 风险监控 | 扫描持仓 ST、停牌、涨跌停、公告、财务恶化和解禁风险 | Phase 3 |
| `position_advisor` | 持仓建议 | 注入成本价、持仓天数、止损价和风险证据，输出卖出/加仓/减仓建议 | Phase 4 |
| `strategy_backtest` | 策略回测 | 委托 StockManager MCP 异步 `run_backtest`，展示 job 进度和结果 | Phase 4 |
| `decision_audit` | 决策复盘 | 推荐记录 vs 实际操作 vs 实际收益的月度归因 | Phase 4 |
| `news_monitor` | 新闻监控 | 实时监控指定标的新闻和情绪变化 | Phase 4 |
| `industry_compare` | 行业对比 | 同行业多标的横向对比分析 | Phase 4 |
| `financial_education` | 金融教育 | 财报解读、概念解释、投资入门 | Phase 4 |
| `alert_system` | 预警通知 | 价格/指标触发推送 macOS 通知 | Phase 5 |

### 5.6 意图路由（Orchestrator）

当前 `Orchestrator` 仍承担 Skill 路由职责；下一步会在它之前增加 `ChatAgent` 编排层：

```
User Message
    │
    ▼
ChatAgent Intent Classifier
    ├── chat_answer  ──► LLM 自由回复
    ├── tool_answer  ──► Lightweight Tool Registry ──► LLM 汇总回复
    ├── skill_run    ──► Orchestrator ──► Skill Run / WS 任务卡
    └── clarify      ──► 参数追问
```

`Orchestrator` 的职责收敛为“把明确的任务意图映射到 Skill + params”。自由聊天、轻量工具查询、上下文解释不应创建 run，也不应进入 Analysis 页面。

建议接口：

```python
class ChatIntent(BaseModel):
    intent_type: Literal["chat_answer", "tool_answer", "skill_run", "clarify"]
    skill_id: str | None = None
    params: dict[str, Any] = {}
    tool_plan: list[dict[str, Any]] = []
    response_hint: str = ""
    confidence: float = 0.0
```

轻量工具第一版范围：

| Tool | 用途 |
|------|------|
| `get_portfolio_summary` | 回答“我的持仓/仓位/盈亏怎么样” |
| `search_artifacts` | 回答“刚才报告/历史选股为什么这么判断” |
| `get_recent_runs` | 解释最近任务状态 |
| `get_mcp_factor_snapshot` | 单票估值、资金流、动量、质量快速查询 |
| `get_strategy_lessons` | 引用近期反思经验 |

```python
# tradingagents/core/orchestrator.py

from typing import Any

from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.skills.base import BaseSkill
from tradingagents.skills.registry import SkillRegistry


ROUTER_SYSTEM_PROMPT = """\
You are a routing agent for a financial trading assistant platform.
Given a user message, determine which skill should handle it.

Available skills:
{skills_description}

Respond with ONLY a JSON object:
{{"skill_id": "<skill_id or null>", "params": {{...extracted parameters...}}}}

If no skill matches, set skill_id to null and params to {{}}.
"""


class Orchestrator:
    """意图路由器 — 将自然语言映射到技能调用。"""

    def __init__(self, registry: SkillRegistry, config: dict[str, Any]):
        self.registry = registry
        self.config = config
        self._llm = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm", "gpt-4o-mini"),
            base_url=config.get("backend_url"),
        ).get_llm()

    async def route(self, user_message: str) -> tuple[BaseSkill | None, dict]:
        """路由用户消息到对应技能。

        Returns:
            (skill, params): 匹配的技能和提取的参数。skill 为 None 表示无匹配。
        """
        # 第一层: 关键词匹配（快速路径，不消耗 token）
        keyword_matches = self.registry.find_by_trigger(user_message)
        if len(keyword_matches) == 1:
            return keyword_matches[0], {}

        # 第二层: LLM 意图识别
        skills_desc = "\n".join(
            f"- {s.id}: {s.description}" for s in self.registry.list_all()
        )
        prompt = ROUTER_SYSTEM_PROMPT.format(skills_description=skills_desc)

        response = await self._llm.ainvoke([
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message},
        ])

        import json
        try:
            result = json.loads(response.content)
            skill_id = result.get("skill_id")
            params = result.get("params", {})
            if skill_id:
                skill = self.registry.get(skill_id)
                return skill, params
        except (json.JSONDecodeError, AttributeError):
            pass

        return None, {}
```

---

## 6. 前端架构 — React Web App

### 6.1 目录结构

```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── src/
│   ├── main.tsx                     # 入口
│   ├── App.tsx                      # 根组件 + Router
│   ├── api/
│   │   ├── client.ts               # HTTP client (axios/fetch wrapper)
│   │   ├── ws.ts                    # WebSocket 连接管理
│   │   └── types.ts                 # 从后端 OpenAPI spec 自动生成的类型
│   ├── stores/
│   │   ├── useRunStore.ts           # 运行状态 (Zustand)
│   │   ├── useConfigStore.ts        # 配置状态
│   │   └── useChatStore.ts          # 聊天状态
│   ├── hooks/
│   │   ├── useWebSocket.ts          # WebSocket hook
│   │   ├── useSkillExecution.ts     # 技能执行 hook
│   │   └── useAgentGraph.ts         # Agent 图数据 hook
│   ├── pages/
│   │   ├── Dashboard/
│   │   │   ├── index.tsx            # 主面板
│   │   │   ├── SkillCard.tsx        # 技能卡片
│   │   │   └── RecentRuns.tsx       # 最近运行
│   │   ├── Analysis/
│   │   │   ├── index.tsx            # 分析页
│   │   │   ├── AgentGraph.tsx       # Agent 执行图
│   │   │   ├── ReportPanel.tsx      # 报告面板
│   │   │   └── ProgressTracker.tsx  # 进度追踪
│   │   ├── Chat/
│   │   │   ├── index.tsx            # 聊天页
│   │   │   ├── MessageList.tsx      # 消息列表
│   │   │   └── InputBar.tsx         # 输入框
│   │   ├── Reports/
│   │   │   ├── index.tsx            # 报告列表
│   │   │   └── ReportDetail.tsx     # 报告详情
│   │   └── Settings/
│   │       ├── index.tsx            # 设置页
│   │       ├── LLMConfig.tsx        # LLM 配置
│   │       └── DataSourceConfig.tsx # 数据源配置
│   ├── components/
│   │   ├── ui/                      # shadcn/ui 组件
│   │   ├── Layout/
│   │   │   ├── Sidebar.tsx          # 侧边栏导航
│   │   │   ├── Header.tsx           # 顶部栏
│   │   │   └── AppShell.tsx         # 布局壳
│   │   ├── AgentNode.tsx            # Agent 图的节点组件
│   │   ├── MarkdownRenderer.tsx     # Markdown 渲染
│   │   └── StockChart.tsx           # 股票图表
│   └── lib/
│       ├── utils.ts                 # 工具函数
│       └── constants.ts             # 常量
```

### 6.2 核心页面设计

#### Dashboard — 首页

```
┌──────────────────────────────────────────────────────────┐
│  TradingAgents                           [Settings] [⚙]  │
├────────┬─────────────────────────────────────────────────┤
│        │                                                  │
│  Nav   │  ┌─────────────────────────────────────────┐    │
│        │  │  Welcome back!                           │    │
│  📊    │  │  Last analysis: AAPL (2h ago) → Buy      │    │
│  💬    │  └─────────────────────────────────────────┘    │
│  📄    │                                                  │
│  ⚙️    │  Skills                                          │
│        │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐           │
│        │  │Stock │ │Port. │ │Back- │ │Market│           │
│        │  │Anal. │ │Mgmt  │ │test  │ │Scan  │           │
│        │  └──────┘ └──────┘ └──────┘ └──────┘           │
│        │                                                  │
│        │  Recent Runs                                     │
│        │  ┌─────────────────────────────────────────┐    │
│        │  │ AAPL  │ Buy  │ 2h ago  │ [View Report]  │    │
│        │  │ 600519│ Hold │ 1d ago  │ [View Report]  │    │
│        │  └─────────────────────────────────────────┘    │
│        │                                                  │
└────────┴─────────────────────────────────────────────────┘
```

#### Analysis — 分析执行页

```
┌──────────────────────────────────────────────────────────┐
│  Analyzing AAPL — 2026-06-17                  [Cancel]    │
├──────────────────────────────┬───────────────────────────┤
│                              │                            │
│   Agent Execution Graph      │   Live Report             │
│                              │                            │
│   [Market] ──► [News] ──►   │   ## Market Analysis      │
│        ↓                     │   Price trend shows...    │
│   [Sentiment] ──► [Fund.]   │                            │
│        ↓                     │   ## Sentiment            │
│   [Bull] ←──→ [Bear]        │   Social media shows...   │
│        ↓                     │                            │
│   [Research Mgr]             │   ## News Analysis        │
│        ↓                     │   Recent developments...  │
│   [Trader]                   │                            │
│        ↓                     │                            │
│   [Agg] ←→ [Cons] ←→ [Neu] │                            │
│        ↓                     │                            │
│   [Portfolio Mgr] ✓         │                            │
│                              │                            │
├──────────────────────────────┴───────────────────────────┤
│  Tool Calls: get_stock_data(AAPL) → 200 | get_news...    │
└──────────────────────────────────────────────────────────┘
```

### 6.3 WebSocket 连接管理

```typescript
// frontend/src/api/ws.ts

type WSEventHandler = (message: WSMessage) => void;

interface WSMessage {
  type: string;
  run_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

class WebSocketManager {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<WSEventHandler>> = new Map();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private baseUrl: string;

  constructor(baseUrl: string = "ws://localhost:8422") {
    this.baseUrl = baseUrl;
  }

  connect(runId: string): void {
    const url = `${this.baseUrl}/ws/run/${runId}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      const message: WSMessage = JSON.parse(event.data);
      this.dispatch(message);
    };

    this.ws.onclose = (event) => {
      if (!event.wasClean && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000);
        setTimeout(() => this.connect(runId), delay);
      }
    };
  }

  on(eventType: string, handler: WSEventHandler): () => void {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType)!.add(handler);
    return () => this.handlers.get(eventType)?.delete(handler);
  }

  private dispatch(message: WSMessage): void {
    const handlers = this.handlers.get(message.type);
    if (handlers) {
      handlers.forEach((handler) => handler(message));
    }
    // Also dispatch to wildcard handlers
    const allHandlers = this.handlers.get("*");
    if (allHandlers) {
      allHandlers.forEach((handler) => handler(message));
    }
  }

  disconnect(): void {
    this.ws?.close(1000, "Client disconnect");
    this.ws = null;
  }
}

export const wsManager = new WebSocketManager();
```

### 6.4 Agent Graph 可视化组件

```typescript
// frontend/src/pages/Analysis/AgentGraph.tsx

import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { AgentNode } from "@/components/AgentNode";

interface AgentStatus {
  agent: string;
  status: "pending" | "running" | "completed" | "failed";
  duration_ms?: number;
}

interface AgentGraphProps {
  agentStatuses: Record<string, AgentStatus>;
  selectedAnalysts: string[];
}

const nodeTypes = { agent: AgentNode };

export function AgentGraph({ agentStatuses, selectedAnalysts }: AgentGraphProps) {
  const { nodes, edges } = useMemo(
    () => buildGraphLayout(agentStatuses, selectedAnalysts),
    [agentStatuses, selectedAnalysts]
  );

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}

function buildGraphLayout(
  statuses: Record<string, AgentStatus>,
  analysts: string[]
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Phase 1: Analysts (row 0)
  const analystNames: Record<string, string> = {
    market: "Market Analyst",
    social: "Sentiment Analyst",
    news: "News Analyst",
    fundamentals: "Fundamentals Analyst",
  };

  analysts.forEach((key, i) => {
    const name = analystNames[key];
    nodes.push({
      id: name,
      type: "agent",
      position: { x: i * 200, y: 0 },
      data: { label: name, ...statuses[name] },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    });
    if (i > 0) {
      edges.push({
        id: `${analysts[i - 1]}-${key}`,
        source: analystNames[analysts[i - 1]],
        target: name,
        animated: statuses[name]?.status === "running",
      });
    }
  });

  // Phase 2: Research (row 1)
  const researchNodes = ["Bull Researcher", "Bear Researcher", "Research Manager"];
  researchNodes.forEach((name, i) => {
    nodes.push({
      id: name,
      type: "agent",
      position: { x: i * 200, y: 150 },
      data: { label: name, ...statuses[name] },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    });
  });

  // Connect last analyst to Bull Researcher
  const lastAnalyst = analystNames[analysts[analysts.length - 1]];
  edges.push({ id: "to-bull", source: lastAnalyst, target: "Bull Researcher" });
  edges.push({ id: "bull-bear", source: "Bull Researcher", target: "Bear Researcher", animated: true });
  edges.push({ id: "bear-bull", source: "Bear Researcher", target: "Bull Researcher", animated: true });
  edges.push({ id: "bull-mgr", source: "Bull Researcher", target: "Research Manager" });

  // Phase 3-5: Trader, Risk, Portfolio (rows 2-4)
  nodes.push({
    id: "Trader",
    type: "agent",
    position: { x: 200, y: 300 },
    data: { label: "Trader", ...statuses["Trader"] },
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
  });
  edges.push({ id: "mgr-trader", source: "Research Manager", target: "Trader" });

  const riskNodes = ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"];
  riskNodes.forEach((name, i) => {
    nodes.push({
      id: name,
      type: "agent",
      position: { x: i * 200, y: 450 },
      data: { label: name, ...statuses[name] },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    });
  });
  edges.push({ id: "trader-agg", source: "Trader", target: "Aggressive Analyst" });

  nodes.push({
    id: "Portfolio Manager",
    type: "agent",
    position: { x: 200, y: 600 },
    data: { label: "Portfolio Manager", ...statuses["Portfolio Manager"] },
    targetPosition: Position.Top,
  });
  edges.push({ id: "risk-pm", source: "Neutral Analyst", target: "Portfolio Manager" });

  return { nodes, edges };
}
```

---

## 7. macOS 桌面打包 — Tauri

### 7.1 Tauri 项目结构

```
desktop/
├── src-tauri/
│   ├── Cargo.toml              # Rust 依赖
│   ├── tauri.conf.json         # Tauri 配置
│   ├── src/
│   │   ├── main.rs             # 入口
│   │   ├── commands.rs         # Tauri commands (Rust ↔ JS)
│   │   ├── sidecar.rs          # Python sidecar 管理
│   │   └── tray.rs             # 系统托盘
│   └── icons/                  # 应用图标
├── src/                        # 复用 frontend/src (symlink 或 monorepo)
├── package.json
└── vite.config.ts
```

### 7.2 Sidecar 进程管理

```rust
// desktop/src-tauri/src/sidecar.rs

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::State;

pub struct PythonSidecar {
    process: Mutex<Option<Child>>,
    port: u16,
}

impl PythonSidecar {
    pub fn new(port: u16) -> Self {
        Self {
            process: Mutex::new(None),
            port,
        }
    }

    pub fn start(&self) -> Result<(), String> {
        let child = Command::new("tradingagents-server")
            .args(["--port", &self.port.to_string()])
            .spawn()
            .map_err(|e| format!("Failed to start Python backend: {}", e))?;

        *self.process.lock().unwrap() = Some(child);
        Ok(())
    }

    pub fn stop(&self) {
        if let Some(mut child) = self.process.lock().unwrap().take() {
            let _ = child.kill();
        }
    }

    pub fn port(&self) -> u16 {
        self.port
    }
}

#[tauri::command]
pub fn get_backend_url(sidecar: State<PythonSidecar>) -> String {
    format!("http://localhost:{}", sidecar.port())
}
```

### 7.3 Tauri 配置

```json
{
  "$schema": "https://raw.githubusercontent.com/nicedoc/schemas/main/tauri/2.1.0.json",
  "productName": "TradingAgents",
  "version": "1.0.0",
  "identifier": "com.tradingagents.app",
  "build": {
    "frontendDist": "../dist",
    "devUrl": "http://localhost:5173",
    "beforeBuildCommand": "npm run build",
    "beforeDevCommand": "npm run dev"
  },
  "app": {
    "windows": [
      {
        "title": "TradingAgents",
        "width": 1440,
        "height": 900,
        "minWidth": 1024,
        "minHeight": 700,
        "decorations": true,
        "transparent": false
      }
    ],
    "trayIcon": {
      "iconPath": "icons/tray.png",
      "tooltip": "TradingAgents"
    }
  },
  "bundle": {
    "active": true,
    "targets": ["dmg", "app"],
    "macOS": {
      "minimumSystemVersion": "13.0",
      "frameworks": [],
      "signingIdentity": null
    },
    "resources": ["sidecar/*"],
    "externalBin": ["sidecar/tradingagents-server"]
  },
  "plugins": {}
}
```

### 7.4 Python 后端打包为 Sidecar

```bash
# 使用 PyInstaller 将后端打包为单文件可执行
pyinstaller \
  --name tradingagents-server \
  --onefile \
  --hidden-import tradingagents \
  --hidden-import uvicorn \
  --add-data "tradingagents:tradingagents" \
  tradingagents/api/server.py
```

打包后的二进制放入 `desktop/src-tauri/sidecar/` 目录，Tauri 会自动将其纳入 `.app` bundle。

---

## 8. 核心代码实现

### 8.1 FastAPI 应用工厂

```python
# tradingagents/api/app.py

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.registry import SkillRegistry
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.run_manager import RunManager

from .routes import skills, reports, config, health, runs
from .ws import stream


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理。"""
    # Startup
    registry = SkillRegistry()
    registry.auto_discover()

    app.state.registry = registry
    app.state.config = dict(DEFAULT_CONFIG)
    app.state.orchestrator = Orchestrator(registry, app.state.config)
    app.state.run_manager = RunManager()

    yield

    # Shutdown
    await app.state.run_manager.cancel_all()


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(
        title="TradingAgents API",
        version="1.0.0",
        description="Multi-agent financial trading analysis platform",
        lifespan=lifespan,
    )

    # CORS (桌面应用通过 localhost 访问)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "tauri://localhost"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(skills.router, prefix="/api/v1", tags=["skills"])
    app.include_router(runs.router, prefix="/api/v1", tags=["runs"])
    app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
    app.include_router(config.router, prefix="/api/v1", tags=["config"])

    # WebSocket 路由
    app.include_router(stream.router)

    return app
```

### 8.2 运行管理器

```python
# tradingagents/core/run_manager.py

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from tradingagents.skills.base import BaseSkill, SkillEvent


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Run:
    """代表一次 Skill 执行。"""
    id: str
    skill_id: str
    params: dict[str, Any]
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[SkillEvent] = field(default_factory=list)
    _task: asyncio.Task | None = field(default=None, repr=False)


class RunManager:
    """管理所有运行的生命周期。"""

    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def create_run(
        self,
        skill: BaseSkill,
        params: dict[str, Any],
        config: dict[str, Any],
    ) -> Run:
        """创建并启动一次运行。"""
        run = Run(
            id=str(uuid.uuid4()),
            skill_id=skill.metadata.id,
            params=params,
        )
        self._runs[run.id] = run
        self._subscribers[run.id] = []

        # 在后台任务中执行
        run._task = asyncio.create_task(self._execute(run, skill, params, config))
        return run

    async def _execute(
        self,
        run: Run,
        skill: BaseSkill,
        params: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        """执行技能并广播事件。"""
        run.status = RunStatus.RUNNING
        run.started_at = datetime.utcnow()

        validated_params = skill.validate_params(params)

        try:
            async for event in skill.execute(validated_params, config):
                run.events.append(event)
                await self._broadcast(run.id, event)

            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.utcnow()
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.utcnow()
        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = str(e)
            run.completed_at = datetime.utcnow()
            await self._broadcast(
                run.id,
                SkillEvent(event_type="error", data={"message": str(e)}),
            )

    async def _broadcast(self, run_id: str, event: SkillEvent) -> None:
        """向所有订阅者广播事件。"""
        for queue in self._subscribers.get(run_id, []):
            await queue.put(event)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """订阅某个 run 的事件流。"""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        """取消订阅。"""
        if run_id in self._subscribers:
            self._subscribers[run_id] = [
                q for q in self._subscribers[run_id] if q is not queue
            ]

    def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list_runs(self, limit: int = 50) -> list[Run]:
        runs = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    async def cancel_run(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run and run._task and not run._task.done():
            run._task.cancel()
            return True
        return False

    async def cancel_all(self) -> None:
        for run in self._runs.values():
            if run._task and not run._task.done():
                run._task.cancel()
```

### 8.3 WebSocket 流端点

```python
# tradingagents/api/ws/stream.py

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tradingagents.core.run_manager import RunManager
from tradingagents.skills.base import SkillEvent

router = APIRouter()


@router.websocket("/ws/run/{run_id}")
async def ws_run_stream(websocket: WebSocket, run_id: str):
    """WebSocket 端点：订阅某次运行的实时事件流。"""
    await websocket.accept()

    run_manager: RunManager = websocket.app.state.run_manager
    run = run_manager.get_run(run_id)

    if not run:
        await websocket.send_json({"type": "error", "payload": {"message": "Run not found"}})
        await websocket.close(code=4004)
        return

    # 发送历史事件（断线重连时）
    for event in run.events:
        await websocket.send_json(_serialize_event(run_id, event))

    # 订阅新事件
    queue = run_manager.subscribe(run_id)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(_serialize_event(run_id, event))
            except asyncio.TimeoutError:
                # 发送心跳
                await websocket.send_json({"type": "heartbeat", "timestamp": _now()})
    except WebSocketDisconnect:
        pass
    finally:
        run_manager.unsubscribe(run_id, queue)


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """WebSocket 端点：自然语言对话（意图路由模式）。"""
    await websocket.accept()

    orchestrator = websocket.app.state.orchestrator
    run_manager: RunManager = websocket.app.state.run_manager
    config = websocket.app.state.config

    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("message", "")

            if not user_message:
                continue

            # 路由到技能
            skill, params = await orchestrator.route(user_message)

            if skill is None:
                await websocket.send_json({
                    "type": "chat_reply",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {
                        "content": "I'm not sure which skill to use for that. Could you be more specific?",
                        "skill_triggered": None,
                    },
                })
                continue

            # 创建运行
            run = await run_manager.create_run(skill, params, config)

            await websocket.send_json({
                "type": "chat_reply",
                "run_id": run.id,
                "timestamp": _now(),
                "payload": {
                    "content": f"Starting {skill.metadata.name}...",
                    "skill_triggered": skill.metadata.id,
                },
            })

            # 转发事件
            queue = run_manager.subscribe(run.id)
            try:
                while True:
                    event = await asyncio.wait_for(queue.get(), timeout=300.0)
                    await websocket.send_json(_serialize_event(run.id, event))
                    if event.event_type in ("run_complete", "error"):
                        break
            finally:
                run_manager.unsubscribe(run.id, queue)

    except WebSocketDisconnect:
        pass


def _serialize_event(run_id: str, event: SkillEvent) -> dict:
    return {
        "type": event.event_type,
        "run_id": run_id,
        "timestamp": _now(),
        "payload": event.data,
    }


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"
```

### 8.4 REST 路由实现

```python
# tradingagents/api/routes/runs.py

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class CreateRunRequest(BaseModel):
    skill_id: str
    params: dict = {}


class RunResponse(BaseModel):
    id: str
    skill_id: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


@router.post("/runs", response_model=RunResponse)
async def create_run(request: Request, body: CreateRunRequest):
    """创建一次新的 Skill 运行。"""
    registry = request.app.state.registry
    run_manager = request.app.state.run_manager
    config = request.app.state.config

    skill = registry.get(body.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{body.skill_id}' not found")

    run = await run_manager.create_run(skill, body.params, config)
    return RunResponse(
        id=run.id,
        skill_id=run.skill_id,
        status=run.status.value,
        created_at=run.created_at.isoformat(),
    )


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(request: Request, limit: int = 50):
    """列出运行记录。"""
    run_manager = request.app.state.run_manager
    runs = run_manager.list_runs(limit=limit)
    return [
        RunResponse(
            id=r.id,
            skill_id=r.skill_id,
            status=r.status.value,
            created_at=r.created_at.isoformat(),
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            error=r.error,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(request: Request, run_id: str):
    """获取单次运行详情。"""
    run_manager = request.app.state.run_manager
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResponse(
        id=run.id,
        skill_id=run.skill_id,
        status=run.status.value,
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error=run.error,
    )


@router.delete("/runs/{run_id}")
async def cancel_run(request: Request, run_id: str):
    """取消运行中的任务。"""
    run_manager = request.app.state.run_manager
    success = await run_manager.cancel_run(run_id)
    if not success:
        raise HTTPException(status_code=404, detail="Run not found or already finished")
    return {"status": "cancelled"}
```

```python
# tradingagents/api/routes/skills.py

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str
    version: str
    category: str
    icon: str


@router.get("/skills", response_model=list[SkillInfo])
async def list_skills(request: Request):
    """列出所有可用技能。"""
    registry = request.app.state.registry
    return [
        SkillInfo(
            id=m.id,
            name=m.name,
            description=m.description,
            version=m.version,
            category=m.category,
            icon=m.icon,
        )
        for m in registry.list_all()
    ]


@router.get("/skills/{skill_id}/schema")
async def get_skill_schema(request: Request, skill_id: str):
    """获取技能的输入 schema（JSON Schema 格式）。"""
    registry = request.app.state.registry
    skill = registry.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return skill.input_schema.model_json_schema()
```

### 8.5 事件总线

```python
# tradingagents/core/event_bus.py

import asyncio
from collections import defaultdict
from typing import Any, Callable, Awaitable

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    """进程内异步事件总线。

    用于 Skill 执行层与 API 层之间的解耦通信。
    LangGraph 的 callback 发射事件 → EventBus 广播 → WebSocket 端点转发给前端。
    """

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """订阅事件。返回取消订阅的函数。"""
        self._handlers[event_type].append(handler)
        return lambda: self._handlers[event_type].remove(handler)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """发射事件，通知所有订阅者。"""
        handlers = self._handlers.get(event_type, [])
        if handlers:
            await asyncio.gather(*(h(data) for h in handlers))

    def clear(self) -> None:
        """清除所有订阅。"""
        self._handlers.clear()
```

### 8.6 TradingAgentsGraph 异步流桥接

现有的 `TradingAgentsGraph.propagate()` 是同步的。需要添加异步流式接口：

```python
# tradingagents/graph/trading_graph.py 中新增方法

async def astream_propagate(
    self,
    ticker: str,
    date: str,
    selected_analysts: list[str] | None = None,
) -> AsyncIterator[dict]:
    """异步流式执行分析，yield 事件字典。

    这是 propagate() 的异步版本，用于 API 层实时推送。
    """
    if selected_analysts is None:
        selected_analysts = ["market", "social", "news", "fundamentals"]

    # 解析 instrument context
    instrument_context = self.resolve_instrument_context(ticker)
    past_context = self._load_memory_context(ticker)

    # 创建初始状态
    initial_state = self.propagator.create_initial_state(
        company_name=ticker,
        trade_date=date,
        asset_type=self._detect_asset_type(ticker),
        past_context=past_context,
        instrument_context=instrument_context,
    )

    # 编译图
    graph = self.graph_setup.setup_graph(selected_analysts)
    compiled = graph.compile(checkpointer=self._get_checkpointer())

    # 使用 astream_events 获取细粒度事件
    graph_args = self.propagator.get_graph_args()

    async for event in compiled.astream_events(
        initial_state,
        version="v2",
        config=graph_args.get("config", {}),
    ):
        kind = event["event"]

        if kind == "on_chain_start" and "name" in event:
            yield {
                "type": "agent_status",
                "data": {"agent": event["name"], "status": "running"},
            }

        elif kind == "on_chain_end" and "name" in event:
            yield {
                "type": "agent_status",
                "data": {"agent": event["name"], "status": "completed"},
            }

        elif kind == "on_tool_start":
            yield {
                "type": "tool_call",
                "data": {
                    "tool": event.get("name", "unknown"),
                    "args": event.get("data", {}).get("input", {}),
                },
            }

        elif kind == "on_chain_stream":
            # 捕获报告内容更新
            chunk = event.get("data", {})
            if isinstance(chunk, dict):
                for key in ("market_report", "sentiment_report", "news_report",
                           "fundamentals_report", "investment_plan",
                           "trader_investment_plan", "final_trade_decision"):
                    if key in chunk and chunk[key]:
                        yield {
                            "type": "report_chunk",
                            "data": {
                                "section": key,
                                "content": chunk[key],
                                "is_final": False,
                            },
                        }
```

---

## 9. 数据层与状态管理

### 9.1 SQLite 持久化

```python
# tradingagents/core/persistence.py

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


DB_PATH = Path.home() / ".tradingagents" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    ticker TEXT NOT NULL,
    rating TEXT,
    content TEXT NOT NULL,
    report_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_skill ON runs(skill_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id);
"""


class Database:
    """SQLite 持久化层。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_run(self, run_id: str, skill_id: str, params: dict, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (id, skill_id, params, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, skill_id, json.dumps(params), status, datetime.utcnow().isoformat()),
            )

    def update_run_status(self, run_id: str, status: str, **kwargs) -> None:
        sets = ["status = ?"]
        values = [status]
        for key in ("result", "error", "started_at", "completed_at"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                val = kwargs[key]
                values.append(json.dumps(val) if isinstance(val, dict) else val)
        values.append(run_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", values)

    def save_report(
        self, report_id: str, run_id: str, ticker: str, rating: str, content: str, path: str | None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO reports (id, run_id, ticker, rating, content, report_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (report_id, run_id, ticker, rating, content, path, datetime.utcnow().isoformat()),
            )

    def list_reports(self, limit: int = 50, ticker: str | None = None) -> list[dict]:
        query = "SELECT * FROM reports"
        params: list[Any] = []
        if ticker:
            query += " WHERE ticker = ?"
            params.append(ticker)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
```

### 9.2 前端状态管理 (Zustand)

```typescript
// frontend/src/stores/useRunStore.ts

import { create } from "zustand";

interface AgentStatus {
  agent: string;
  status: "pending" | "running" | "completed" | "failed";
  duration_ms?: number;
}

interface ReportSection {
  section: string;
  content: string;
  is_final: boolean;
}

interface ToolCall {
  agent: string;
  tool: string;
  args: Record<string, unknown>;
  timestamp: string;
}

interface RunState {
  currentRunId: string | null;
  status: "idle" | "running" | "completed" | "failed";
  agentStatuses: Record<string, AgentStatus>;
  reportSections: Record<string, ReportSection>;
  toolCalls: ToolCall[];
  error: string | null;

  // Actions
  startRun: (runId: string) => void;
  updateAgentStatus: (status: AgentStatus) => void;
  updateReportSection: (section: ReportSection) => void;
  addToolCall: (call: ToolCall) => void;
  completeRun: () => void;
  failRun: (error: string) => void;
  reset: () => void;
}

export const useRunStore = create<RunState>((set) => ({
  currentRunId: null,
  status: "idle",
  agentStatuses: {},
  reportSections: {},
  toolCalls: [],
  error: null,

  startRun: (runId) =>
    set({
      currentRunId: runId,
      status: "running",
      agentStatuses: {},
      reportSections: {},
      toolCalls: [],
      error: null,
    }),

  updateAgentStatus: (status) =>
    set((state) => ({
      agentStatuses: { ...state.agentStatuses, [status.agent]: status },
    })),

  updateReportSection: (section) =>
    set((state) => ({
      reportSections: { ...state.reportSections, [section.section]: section },
    })),

  addToolCall: (call) =>
    set((state) => ({
      toolCalls: [...state.toolCalls, call],
    })),

  completeRun: () => set({ status: "completed" }),
  failRun: (error) => set({ status: "failed", error }),
  reset: () =>
    set({
      currentRunId: null,
      status: "idle",
      agentStatuses: {},
      reportSections: {},
      toolCalls: [],
      error: null,
    }),
}));
```

---

## 10. 安全与认证

### 10.1 安全模型

桌面应用场景下的安全策略：

| 威胁 | 缓解措施 |
|------|----------|
| API key 泄露 | 存储在 macOS Keychain (via `keyring` 库)，不写入配置文件 |
| 本地端口暴露 | 绑定 127.0.0.1，随机端口，Tauri capabilities 限制 |
| WebSocket 劫持 | Origin 检查 + 短期 token 认证 |
| 恶意插件/Skill | Skill 白名单，内置 Skills 签名验证 |
| 数据缓存安全 | SQLite 文件权限 600，敏感字段加密 |

### 10.2 API Key 管理

```python
# tradingagents/core/keychain.py

import keyring
import os

SERVICE_NAME = "com.tradingagents.app"


def get_api_key(provider: str) -> str | None:
    """从 Keychain 获取 API key，fallback 到环境变量。"""
    key = keyring.get_password(SERVICE_NAME, f"{provider}_api_key")
    if key:
        return key
    # Fallback to env var
    env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env_var = env_map.get(provider, f"{provider.upper()}_API_KEY")
    return os.environ.get(env_var)


def set_api_key(provider: str, key: str) -> None:
    """将 API key 存入 Keychain。"""
    keyring.set_password(SERVICE_NAME, f"{provider}_api_key", key)


def delete_api_key(provider: str) -> None:
    """从 Keychain 删除 API key。"""
    try:
        keyring.delete_password(SERVICE_NAME, f"{provider}_api_key")
    except keyring.errors.PasswordDeleteError:
        pass
```

---

## 11. 测试策略

### 11.1 测试金字塔

```
                    ┌──────────┐
                    │   E2E    │  Playwright (前端) + pytest (API)
                   ┌┴──────────┴┐
                   │ Integration │  Skill 执行、WS 事件流、数据库
                  ┌┴────────────┴┐
                  │   Unit Tests  │  Skill 逻辑、路由、序列化
                 ┌┴──────────────┴┐
                 │   Type Checks   │  mypy (Python) + tsc (TS)
                └──────────────────┘
```

### 11.2 测试目录结构

```
tests/
├── unit/
│   ├── test_skill_registry.py
│   ├── test_orchestrator.py
│   ├── test_run_manager.py
│   ├── test_event_bus.py
│   └── test_persistence.py
├── integration/
│   ├── test_api_routes.py
│   ├── test_ws_stream.py
│   └── test_skill_execution.py
├── e2e/
│   └── test_full_analysis.py
└── conftest.py

frontend/
├── src/__tests__/
│   ├── components/
│   ├── stores/
│   └── hooks/
├── e2e/
│   ├── analysis.spec.ts
│   └── chat.spec.ts
└── vitest.config.ts
```

### 11.3 关键测试示例

```python
# tests/unit/test_skill_registry.py

import pytest
from tradingagents.skills.base import BaseSkill, SkillMetadata, SkillEvent
from tradingagents.skills.registry import SkillRegistry
from pydantic import BaseModel
from typing import Any, AsyncIterator


class MockInput(BaseModel):
    ticker: str


class MockOutput(BaseModel):
    result: str


class MockSkill(BaseSkill):
    @property
    def metadata(self):
        return SkillMetadata(
            id="mock",
            name="Mock Skill",
            description="A mock skill for testing",
            version="0.1.0",
            triggers=["test", "mock"],
        )

    @property
    def input_schema(self):
        return MockInput

    @property
    def output_schema(self):
        return MockOutput

    async def execute(self, params, config) -> AsyncIterator[SkillEvent]:
        yield SkillEvent(event_type="done", data={"result": "ok"})

    async def cancel(self):
        pass


def test_register_and_get():
    registry = SkillRegistry()
    skill = MockSkill()
    registry.register(skill)
    assert registry.get("mock") is skill


def test_duplicate_registration_raises():
    registry = SkillRegistry()
    skill = MockSkill()
    registry.register(skill)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(skill)


def test_find_by_trigger():
    registry = SkillRegistry()
    registry.register(MockSkill())
    matches = registry.find_by_trigger("run a test analysis")
    assert len(matches) == 1
    assert matches[0].metadata.id == "mock"


def test_find_by_trigger_no_match():
    registry = SkillRegistry()
    registry.register(MockSkill())
    matches = registry.find_by_trigger("completely unrelated")
    assert len(matches) == 0
```

---

## 12. 部署与分发

### 12.1 开发环境

```bash
# 后端
cd /path/to/TradingAgents
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 前端
cd frontend
npm install
npm run dev

# 启动后端 API (开发模式)
python -m tradingagents.api.server --reload --port 8422

# 启动前端 (开发模式)
cd frontend && npm run dev  # → http://localhost:5173
```

### 12.2 Monorepo 结构

```
TradingAgents/
├── tradingagents/          # Python 后端 (已有 + 新增 api/, core/, skills/)
├── cli/                    # CLI (已有)
├── frontend/               # React 前端 (新增)
├── desktop/                # Tauri 桌面打包 (新增)
├── tests/                  # Python 测试
├── docs/                   # 文档 (本文档所在)
├── scripts/                # 构建/部署脚本
├── pyproject.toml          # Python 项目配置
└── package.json            # 顶层 workspace 管理 (可选)
```

### 12.3 构建与分发流程

```bash
# 1. 打包 Python 后端为可执行文件
cd TradingAgents
pyinstaller --name tradingagents-server --onefile \
  --add-data "tradingagents:tradingagents" \
  tradingagents/api/server.py

# 2. 将 sidecar 复制到 Tauri 目录
cp dist/tradingagents-server desktop/src-tauri/sidecar/

# 3. 构建 Tauri 应用
cd desktop
npm run tauri build

# 产物: desktop/src-tauri/target/release/bundle/dmg/TradingAgents_1.0.0_aarch64.dmg
```

### 12.4 CI/CD (GitHub Actions)

```yaml
# .github/workflows/build.yml
name: Build & Release

on:
  push:
    tags: ["v*"]

jobs:
  build-macos:
    runs-on: macos-14  # Apple Silicon
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Setup Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          targets: aarch64-apple-darwin

      - name: Build Python sidecar
        run: |
          pip install pyinstaller
          pip install -e .
          pyinstaller --name tradingagents-server --onefile tradingagents/api/server.py
          cp dist/tradingagents-server desktop/src-tauri/sidecar/

      - name: Build frontend
        run: |
          cd frontend && npm ci && npm run build

      - name: Build Tauri app
        uses: tauri-apps/tauri-action@v0
        with:
          projectPath: desktop
```

---

## 13. 开发迭代路线图

### Phase 1: API 层 + 基础前端（4 周）

**目标**: 用 Web 界面替代 CLI 完成基本分析流程

| 周 | 里程碑 | 交付物 |
|----|--------|--------|
| W1 | API 骨架 | FastAPI 应用、health/skills/runs 路由、WebSocket 事件流 |
| W2 | Skill 框架 | BaseSkill、Registry、StockAnalysisSkill 封装、astream 桥接 |
| W3 | 前端骨架 | React 项目初始化、Dashboard 页、Analysis 页（Agent Graph + Report Panel） |
| W4 | 联调 | WebSocket 实时更新、报告展示、基本 E2E 测试 |

**验收标准**: 在浏览器中输入股票代码，实时看到 agent 执行图更新和报告流式输出。

### Phase 2: 技能扩展 + 对话式交互（4 周）

**目标**: 平台化 — 支持多技能 + 自然语言交互

| 周 | 里程碑 | 交付物 |
|----|--------|--------|
| W5 | 意图路由 | Orchestrator 实现、Chat 页面、WS /chat 端点 |
| W6 | Portfolio Skill | 持仓管理技能（CRUD 持仓、绩效计算、再平衡建议） |
| W7 | Market Scanner | 条件筛选 + AI 评分（集成 AKShare/yfinance 筛选接口） |
| W8 | 设置 + 历史 | Settings 页（LLM/数据源配置）、Library 产物库、SQLite 持久化 |

**验收标准**: 用自然语言说"帮我看看茅台"，自动路由到分析技能并流式展示。

### Phase 3: MCP 集成 + A 股决策增强（4 周）

**目标**: 将 TradingAgents 定位为决策层 Agent，将 A 股数据、因子、回测前置能力委托给 StockManager MCP，本阶段先完成动态选股和风险监控闭环。

| 周 | 里程碑 | 交付物 |
|----|--------|--------|
| W9 | MCP Contract + Client | localhost HTTP MCP client、health/capabilities、MCP adapter、降级策略 |
| W10 | UserProfile + Scheduler | 投资风格配置、SQLite/API/前端设置卡、每日 08:30 定时任务框架 |
| W11 | DailyPipeline | MCP 候选池、风格权重量化打分、Top N LLM 快筛融合、Evidence Card、早报入库、Watchlist 手动触发 |
| W12 | RiskMonitor + 前端补全 | 持仓风险扫描、Portfolio 持仓 CRUD/P&L、风险报告事件 |

**验收标准**:

1. TradingAgents 通过 `http://127.0.0.1:8765/mcp` 连接 StockManager MCP，并在健康检查中返回 capability flags。
2. MCP 不可用时，基础分析可降级到本地 AKShare/TuShare，回测/因子/交易计划能力在 UI 中禁用并说明原因。
3. Settings 可配置短线/长线投资风格，并影响 DailyPipeline 因子权重和 Quant × LLM 融合 alpha。
4. 每日选股结果包含可审计 Evidence Card、`quant_score`、`llm_confidence`、`fusion_mode`，并写入报告历史；Top 5 完整深度 Agent 分析作为 Phase 3 后半段增强。
5. RiskMonitor 可扫描当前持仓并通过 MCP 风险公告生成报告；Portfolio 风险仪表盘分级作为后续前端增强。

### Phase 4: ChatAgent + 自动化 + 学习闭环（4 周）

**目标**: 在稳定 MCP 合约之上，把 Chat 升级为自由问答 + 轻量工具增强 + Skill 触发的统一入口，并补齐持仓建议、异步回测和决策复盘。

| 周 | 里程碑 | 交付物 |
|----|--------|--------|
| W13 | Free ChatAgent | `chat_answer/tool_answer/skill_run/clarify` 意图分类、轻量工具注册表、自由回复、上下文引用 |
| W14 | PositionAdvisor | 成本价/持仓天数/止损价上下文注入，卖出/加仓/减仓自然语言路由 |
| W15 | StrategyBacktest + DecisionAudit | MCP 异步 `run_backtest`、job 状态轮询、回测报告、月度复盘 |
| W16 | 记忆增强 + 财务预警 | 跨标的模式提取、财务恶化和公告风险预警 |

### Phase 5: 桌面打包（远期）

**目标**: 在 Web + MCP 链路稳定后，再打包为 macOS 原生应用。

| 周 | 里程碑 | 交付物 |
|----|--------|--------|
| W17 | Tauri 集成 | Sidecar supervisor、窗口管理、系统托盘 |
| W18 | 通知系统 | macOS 通知推送、价格/风险预警 |
| W19 | 打包分发 | PyInstaller sidecar、.dmg 构建、自动更新机制 |

### Phase 6: 打磨与扩展（持续）

- 行业对比分析 Skill
- 金融教育 Skill
- 多语言 UI（i18n）
- 数据导出（PDF 报告）
- 性能优化（缓存策略、并发分析）
- 自定义 Agent 创建（用户可定义新 agent 加入管道）

---

## 附录

### A. 环境变量一览

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TRADINGAGENTS_API_PORT` | API 服务端口 | 8422 |
| `TRADINGAGENTS_API_HOST` | API 绑定地址 | 127.0.0.1 |
| `STOCKMANAGER_MCP_URL` | StockManager MCP 服务地址 | http://127.0.0.1:8765/mcp |
| `STOCKMANAGER_MCP_ENABLED` | 是否启用 StockManager MCP 集成 | true |
| `STOCKMANAGER_MCP_TIMEOUT` | MCP tool 默认超时秒数 | 30 |
| `TRADINGAGENTS_SCHEDULER_ENABLED` | 是否启用 FastAPI 内置每日调度 | true |
| `TRADINGAGENTS_NEWS_BODY_SNIPPET_ITEMS` | 个股新闻正文摘录条数上限 | 5 |
| `TRADINGAGENTS_NEWS_BODY_SNIPPET_CHARS` | 单条新闻正文摘录字符上限 | 600 |
| `TRADINGAGENTS_LLM_PROVIDER` | LLM 提供商 | openai |
| `TRADINGAGENTS_DEEP_THINK_LLM` | 深度思考模型 | gpt-5.5 |
| `TRADINGAGENTS_QUICK_THINK_LLM` | 快速思考模型 | gpt-5.4-mini |
| `TRADINGAGENTS_OUTPUT_LANGUAGE` | 输出语言 | English |
| `VITE_WS_BASE_URL` | 前端 WebSocket 直连地址（开发时绕开 Vite WS proxy） | ws://127.0.0.1:8422 |
| `OPENAI_API_KEY` | OpenAI API Key | - |
| `ANTHROPIC_API_KEY` | Anthropic API Key | - |
| `GOOGLE_API_KEY` | Google API Key | - |
| `TUSHARE_TOKEN` | TuShare 数据令牌 | - |

### B. 端口约定

| 服务 | 端口 | 说明 |
|------|------|------|
| FastAPI 后端 | 8422 | REST + WebSocket |
| Vite 开发服务器 | 5173 | 前端 HMR |
| StockManager MCP | 8765 | 本地 HTTP/Streamable MCP 服务 |
| Tauri DevTools | 自动分配 | 开发调试 |

### C. 文件路径约定

```
~/.tradingagents/
├── app.db              # SQLite 数据库
├── cache/              # 数据缓存
│   └── cn_trade_cal.csv
├── logs/               # 分析报告
│   └── {TICKER}_{TIMESTAMP}/
├── memory/
│   └── trading_memory.md
└── config/
    └── user_config.json  # 用户配置覆盖
```

### D. 已有代码到新架构的映射

| 已有模块 | 新架构位置 | 改动程度 |
|----------|-----------|----------|
| `tradingagents/agents/` | 不动，被 `skills/stock_analysis/` 引用 | 无改动 |
| `tradingagents/graph/` | 不动，新增 `astream_propagate` 方法 | 最小改动 |
| `tradingagents/dataflows/` | 不动，被 Skills 共享使用 | 无改动 |
| `tradingagents/llm_clients/` | 不动，被 `core/orchestrator.py` 和 Skills 共享 | 无改动 |
| `tradingagents/default_config.py` | 扩展，新增 API 相关配置键 | 小改动 |
| `cli/` | 保留作为替代入口，不影响 API | 无改动 |

### E. 依赖新增

```toml
# pyproject.toml 新增依赖
[project.optional-dependencies]
api = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "aiosqlite>=0.20.0",
    "keyring>=25.0.0",
]
desktop = [
    "pyinstaller>=6.0",
]
```

### F. 开发命令速查

```bash
# 启动后端（开发）
uvicorn tradingagents.api.app:create_app --factory --reload --port 8422

# 启动前端（开发）
cd frontend && npm run dev

# 运行 Python 测试
pytest tests/ -x -q

# 运行前端测试
cd frontend && npm run test

# 类型检查
mypy tradingagents/
cd frontend && npx tsc --noEmit

# 代码格式化
ruff format tradingagents/ tests/
cd frontend && npx prettier --write src/

# 构建 Tauri 应用（开发）
cd desktop && npm run tauri dev

# 构建 Tauri 应用（生产）
cd desktop && npm run tauri build
```

---

> **下一步**: 从 Phase 1 Week 1 开始 — 创建 `tradingagents/api/` 目录结构并实现 FastAPI 应用骨架。
