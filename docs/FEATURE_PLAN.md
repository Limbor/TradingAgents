# 新增功能详尽计划：Free ChatAgent + 跨标的模式提取

基于最新代码（commit `5d7befa`，经 5 批修复后）。两个功能相互独立，可并行或分阶段实施。

---

## 功能 A：Free ChatAgent + 四分类意图 + 轻量工具注册表

### A.0 现状与缺口（探查确认）

| 维度 | 现状 | 缺口 |
|---|---|---|
| 意图路由 | `Orchestrator.route()` 双层（regex + LLMRouter），只返回 `RouteResult(skill\|None)` | 无 chat_answer/tool_answer/clarify 分类；`skill=None` 时直接回写死英文 fallback |
| LLMRouter | `_do_route` 用 `bind_tools(skill_schemas)`，但**无 tool_call 时丢弃 `response.content`**（llm_router.py:170） | LLM 已能自由回答，后端没接住 |
| 工具体系 | 只有 `BaseSkill`（长任务，走 RunManager.create_run + 流式事件） | 无"秒级同步返回、不创建 run"的轻量工具抽象 |
| /ws/chat | 刚重构支持 cancel + 后台消费者；`route.skill is None` 分支发 `chat_reply` fallback（stream.py:211） | 该分支应交给 ChatAgent 产出 chat_answer/tool_answer/clarify |
| 前端 | `ChatMessage.kind: "text"\|"task"` 二分（useChatStore.ts:4）；MessageList 渲染纯文本气泡 + TaskCard | 无 `tool` kind、无 ToolCard、无 citations、无 markdown 渲染 |

**核心洞察**：ChatAgent 的最小侵入切入点是 `ws_chat` 的 `route.skill is None` 分支——regex 高置信 skill 路由（0ms、0 token）完全保留，只在 regex 无法高置信匹配时才唤起 ChatAgent LLM。这既兑现"自由对话"承诺，又不增加 skill 触发路径的延迟/成本。

### A.1 架构设计

```
ws_chat(user_text)
  ├─ orchestrator.route(user_text)              # 现有双层路由，保留
  │    confidence >= 0.8 + skill ≠ None
  │      → 创建 run，流式事件（现有路径，零改动）
  │
  └─ skill is None OR confidence < 0.8           # 新增：交给 ChatAgent
        → chat_agent.handle(user_text, session_id, context)
             │
             │  LLM(quick_think) with tools = skill_schemas + lightweight_tool_schemas
             │  system prompt: 四分类意图说明 + <user_input> 防注入（沿用 llm_router 的做法）
             │
             ├─ LLM 调用 skill tool        → intent="skill_run"  → 交给 run_manager 创建 run
             ├─ LLM 调用 lightweight tool  → intent="tool_answer"→ await handler(**args) → 附 result
             ├─ LLM 无 tool_call，有 content→ intent="chat_answer"→ 直接返回 content
             └─ LLM 显式 clarify / content 为空 → intent="clarify"  → 返回追问问题
```

**关键设计决策**：
1. **单次 LLM 调用 + tool_use 统一分类**：不在 Python 层写 if/else 分类，而是让 LLM 一次性看到所有工具（skill + lightweight），由它决定调用 skill 工具、调用轻量工具、还是自由回答。这比"先分类再执行"少一次 LLM 往返，且与现有 LLMRouter 模式一致。
2. **regex 快路径保留**：高置信 skill 路由（"分析茅台"、"每日选股"等）仍走 0ms regex，不经过 ChatAgent LLM。只有低置信/无匹配才花 token。
3. **ChatAgent 与 Orchestrator 平级**：不改 Orchestrator（职责单一：路由到 skill）。ChatAgent 是更高层抽象，在 ws_chat 层组合两者。
4. **轻量工具与 Skill 平级**：`ToolRegistry` 与 `SkillRegistry` 独立，都向 ChatAgent LLM 提供 tool schemas，但执行路径不同（Skill→RunManager 流式；Tool→直接 await 秒级返回）。

### A.2 新增文件

#### `tradingagents/core/tool_registry.py` — 轻量工具注册表

```python
@dataclass
class LightweightTool:
    name: str                              # 工具名，如 "get_portfolio_summary"
    description: str                       # 给 LLM 看的描述
    parameters: dict                       # JSON schema (Pydantic model_json_schema)
    handler: Callable[..., Awaitable[Any]] # async，秒级返回
    display: str = "text"                  # "text" | "table" | "card" — 前端渲染提示

class ToolRegistry:
    def register(self, tool: LightweightTool) -> None
    def get(self, name: str) -> LightweightTool | None
    def list_all(self) -> list[LightweightTool]
    def to_openai_schemas(self) -> list[dict]   # 给 LLM bind_tools 用，复用 intent_schema 模式
```

#### `tradingagents/core/chat_agent.py` — ChatAgent

```python
@dataclass
class ChatResponse:
    intent: Literal["chat_answer", "tool_answer", "skill_run", "clarify"]
    content: str = ""                      # chat_answer / clarify 的文本
    tool_name: str = ""                    # tool_answer 的工具名
    tool_args: dict = {}
    tool_result: Any = None                # tool_answer 的执行结果
    tool_display: str = "text"
    citations: list[dict] = []             # [{tool, args, summary}] 工具引用
    skill_id: str = ""                     # skill_run 的 skill
    skill_params: dict = {}
    clarify_question: str = ""             # clarify 的追问
    clarify_options: list[str] = []        # 可选选项

class ChatAgent:
    def __init__(self, config, skill_registry, tool_registry, db, llm_router=None)
    async def handle(self, user_text, session_id, context=None) -> ChatResponse
    # 内部：_build_messages / _invoke_llm / _execute_tool / _parse_response
    # 复用 LLMRouter 的 session buffer 机制（LRU + TTL）
    # LLM client 缓存（沿用 llm_router._get_llm_with_tools 模式）
```

**system prompt 要点**（基于 SPEC 2.5 + 防注入）：
- 四类意图说明 + 何时用 skill 工具 vs 轻量工具 vs 自由回答
- 用户消息包 `<user_input>` 标签（沿用 batch 4 的 llm_router 防注入）
- 输出必须是"可解释的工作台回复"，不暴露 CoT
- 引用工具数据时附 `as_of_date/source/warnings`

#### `frontend/src/pages/Chat/ToolCard.tsx` — 工具结果卡

展示 tool 名、参数、结构化结果（支持 table/card/text 三种 display），复用 TaskCard 的 `tryParseStructured` 模式。

### A.3 修改的现有文件

| 文件 | 改动 |
|---|---|
| `tradingagents/api/app.py` | lifespan 里 `registry.auto_discover()` 之后创建 `ToolRegistry`，注册 5 个轻量工具，实例化 `ChatAgent`，挂 `app.state.chat_agent` / `app.state.tool_registry` |
| `tradingagents/api/ws/stream.py` | `ws_chat` 的 `route.skill is None` 分支（L211）改为调 `chat_agent.handle()`；按 `ChatResponse.intent` 发新消息类型（chat_answer/tool_answer/clarify/skill_run）；skill_run 仍走现有 run 创建流程 |
| `tradingagents/core/intent_schema.py` | 新增 `generate_lightweight_tool_schemas(tool_registry)` 或泛化为 `generate_tool_schemas(registry_or_tool_registry)` |
| `frontend/src/stores/useChatStore.ts` | `ChatMessageKind` 加 `"tool"`；`ChatMessage` 加 `toolCall?` / `citations?` / `clarifyOptions?` 字段 |
| `frontend/src/pages/Chat/hooks.ts` | WS 分发加 `chat_answer`/`tool_answer`/`clarify` 三个分支 |
| `frontend/src/pages/Chat/MessageList.tsx` | 加 `kind === "tool"` → `<ToolCard/>` 分支；text 消息渲染 `citations` 脚注；引入 `react-markdown` 渲染 assistant 文本（现有是纯文本） |

### A.4 v1 轻量工具清单（5 个，对应 SPEC 2.5）

| 工具名 | 包装的底层方法 | 用途 | display |
|---|---|---|---|
| `get_portfolio_summary` | `db.list_holdings()` + 本地 P&L 计算 | "我的持仓/仓位/盈亏怎么样" | table |
| `search_artifacts` | `db.list_artifacts(q=..., limit=10)` | "刚才为什么推荐生益科技" | card |
| `get_recent_runs` | `db.list_runs(limit=10)` | "最近任务状态" | table |
| `get_mcp_factor_snapshot` | `mcp_client.get_factor_snapshot(...)` | "中际旭创现在估值和资金流" | card |
| `get_strategy_lessons` | `db.list_strategy_lessons(active_only=True)` | "近期反思经验" | text |

每个工具的 handler 是一个 async 函数，接收 Pydantic 校验后的参数，返回可 JSON 序列化的 dict。handler 内部做异常捕获，失败返回 `{"error": "...", "warnings": [...]}` 而非抛出。

### A.5 /ws/chat 消息协议扩展

**client → server**：不变（`{message, context?, session_id?}` + `{action:"cancel"}`）。

**server → client 新增类型**：

| type | payload | 触发 |
|---|---|---|
| `chat_answer` | `{content, citations?: [{tool,args,summary}]}` | ChatAgent intent=chat_answer |
| `tool_answer` | `{tool, args, result, display}` | ChatAgent intent=tool_answer |
| `clarify` | `{question, options?: [str]}` | ChatAgent intent=clarify |
| `skill_run` | `{run_id, skill_id, params, ...}` | ChatAgent intent=skill_run（或保留现有 `chat_reply` 带 run_id） |

**兼容性**：保留现有 `chat_reply`（高置信 skill 路由仍发它）。新类型只在 ChatAgent 路径产出。前端旧逻辑不受影响。

### A.6 实施步骤

1. **ToolRegistry + 5 个轻量工具**（`tool_registry.py` + 工具 handler 模块）— 独立、可单测
2. **ChatAgent 核心逻辑**（`chat_agent.py`）— LLM 调用 + 四分类解析 + 工具执行；可单测（mock LLM）
3. **app.py 接线**（lifespan 初始化 ToolRegistry/ChatAgent）
4. **ws_chat 集成**（`route.skill is None` 分支调 ChatAgent，发新消息类型）
5. **前端 store + hooks + MessageList + ToolCard**（三消息类型渲染）
6. **测试**：ChatAgent 单测（mock LLM 返回各类 intent）、工具 handler 单测、ws_chat 集成测试、前端组件测试

### A.7 测试策略

- `tests/unit/test_chat_agent.py`：mock LLM 返回 (a) skill tool_call (b) lightweight tool_call (c) 纯 content (d) 空 content → 验证四分类正确
- `tests/unit/test_tool_registry.py`：注册/查询/schema 生成；5 个工具 handler 各自的单测（mock db/mcp）
- `tests/integration/test_chat_ws.py`：扩展现有 `test_phase2_chat.py`，验证 chat_answer/tool_answer/clarify/skill_run 四种 WS 响应
- 前端：MessageList 渲染 tool/clarify 消息的快照测试（如有 vitest）

### A.8 验收标准（对应 SPEC 2.5）

1. "解释一下 WATCHLIST 是什么意思" → chat_answer，不创建 run
2. "生益科技现在估值和资金流怎么样" → tool_answer（调 get_mcp_factor_snapshot），附 as_of_date/source/warnings
3. "刚才为什么推荐生益科技" → tool_answer（调 search_artifacts）+ chat_answer 解释
4. "跑一下每日选股 top 5" → skill_run（regex 快路径，不经过 ChatAgent LLM）
5. "帮我比较两只持仓哪个更该减仓" → tool_answer（get_portfolio_summary）+ chat_answer，必要时建议 skill_run
6. "分析一下"（无 ticker）→ clarify（"请问要分析哪只股票？"）

---

## 功能 B：跨标的模式提取

### B.0 现状与缺口（探查确认）

| 维度 | 现状 | 缺口 |
|---|---|---|
| reflection_cases | 完整 schema，5 个 JSON payload（snapshot/outcome/attribution/post_signal_evidence/lesson），status pending→reflected | 仅 `decision_grade`+eligible 的 case 生成 lesson |
| strategy_lessons | scope 支持 global/symbol/industry/board/factor；匹配层已支持全 5 种 scope | **生成层只产 global/industry 两种**；无 symbol/board/factor/cross_symbol 生成；`evidence_count` 硬编码 1 无累积；**无 update/deactivate 方法** |
| 归因 | 五分类（ex_ante_miss/ex_post_shock/market_regime_shift/noise/inconclusive）+ confidence + missed_evidence/new_information | 归因数据已在 attribution_payload，可作为跨标的特征 |
| 批处理 | `run_reflection_batch` 每天 16:30 跑，max_per_run=20；末尾 prune 90 天 | 跨标的挖掘应作为批处理后的新阶段 |
| 聚合查询 | `get_reflection_summary` 只查旧 reflections 表，不查 reflection_cases，无特征维度聚合 | 需新写聚合逻辑 |
| 回注链路 | daily_pipeline `_load_strategy_lessons` → `_matching_lessons` → 注入 LLM prompt（前 5 条） | **已打通**，scope=global 的跨标的 lesson 自动匹配所有候选 |

**核心洞察**：回注链路已打通（scope=global 的 lesson 自动注入所有候选的 LLM review），只需补"生成跨标的 lesson"这一环。特征数据全在 reflected cases 的 JSON payload 里，无需改 schema。吞吐矛盾（每天产 80 pending、只处理 20）意味着跨标的挖掘基于 reflected cases，样本量受积压消化速度制约——所以挖掘应周/月级而非日级。

### B.1 架构设计

```
run_reflection_batch()                           # 现有，末尾新增调用
  ├─ 处理 pending cases → reflected              # 现有
  ├─ prune_reflection_cases(90d)                 # 现有
  └─ cross_symbol_miner.mine(db, lookback_days=30)  # 新增阶段
       │
       ├─ 1. 拉取 reflected cases（近 30 天）     # list_reflection_cases(status="reflected")
       ├─ 2. 特征分桶 + 聚合（纯 Python）        # 按 B.3 的特征维度分组
       │      计算每组 n / 胜率 / 平均收益 / 置信区间
       ├─ 3. 显著性过滤（纯统计）                # n>=5 且 |win_rate - 基准| > 0.15
       ├─ 4. LLM 解释 + 措辞（可选）             # 输入显著分组统计，LLM 生成 finding + suggested_adjustment
       ├─ 5. 去重/合并                           # 与现有 lessons 对比，累积 evidence_count
       └─ 6. 写入 strategy_lessons               # scope="global", lesson_type="cross_symbol_pattern"
```

**关键设计决策**：
1. **统计先行，LLM 后置，不发明模式**：纯统计做特征分桶 + 显著性过滤（有数据支撑），LLM 只负责解释因果 + 措辞成 finding/adjustment。这避免 LLM 编造模式。
2. **独立模块 + 批处理末尾调用**：`cross_symbol_pattern_miner.py` 独立可测，`run_reflection_batch` 末尾调一次。也可独立注册为周级任务（样本量大时更有意义）。
3. **复用 strategy_lessons 表**：用 `scope="global"` + `lesson_type="cross_symbol_pattern"`，自动走现有匹配/注入链路。需补 `update_strategy_lesson` / `deactivate_strategy_lesson` 方法支持累积更新。
4. **挖掘频率**：日级样本量太小（20/天），建议默认周级触发（每周一反思任务后跑），或手动 API 触发。

### B.2 新增文件

#### `tradingagents/core/cross_symbol_pattern_miner.py`

```python
@dataclass
class PatternBucket:
    dimension: str            # "data_coverage.flow=missing" 等组合键
    samples: list[dict]       # 匹配的 case 摘要
    n: int
    win_rate: float           # was_correct=True 比例（仅计非 None）
    avg_return: float
    baseline_win_rate: float  # 同期全样本基准
    lift: float               # win_rate - baseline

class CrossSymbolPatternMiner:
    def __init__(self, db, config, llm=None)
    async def mine(self, lookback_days=30, min_samples=5, min_lift=0.15) -> dict
    # 内部：
    #   _load_reflected_cases(lookback_days) -> list[dict]
    #   _extract_features(case) -> dict[str, str]   # 从 snapshot/attribution/outcome 提取可聚合特征
    #   _aggregate(cases, dimensions) -> list[PatternBucket]
    #   _filter_significant(buckets, min_samples, min_lift) -> list[PatternBucket]
    #   _llm_explain(bucket) -> {finding, suggested_adjustment}   # 可选，LLM 失败则用模板
    #   _dedupe_and_merge(new_findings, existing_lessons) -> list[策略 lesson]
    #   _save_lessons(lessons)
```

### B.3 特征维度（从 reflected cases 的 payload 提取）

| 维度 | 来源 payload | 字段 | 分桶 |
|---|---|---|---|
| 决策类型 | snapshot | `final_decision` | BUY/WATCHLIST/MONITOR/SKIP |
| 量化分 | snapshot | `quant_score` | <60 / 60-75 / 75+ |
| 行业 | snapshot | `industry` | 直接分组（用 `_industry_group` 归一化） |
| 板块 | snapshot | `board` | main/chinext/star/beijing |
| 数据覆盖 | snapshot | `data_coverage` (dict) | valuation/flow/quality/liquidity/momentum/risk_control 各 available/missing |
| 风险标签 | snapshot | `risk_flags` | list 展开做类别聚合 |
| 催化剂强度 | snapshot | `catalyst_strength` | confirmed/likely/speculative/none |
| LLM 视图 | snapshot | `llm_view` | strong_positive...strong_negative |
| 风险评估 | snapshot | `risk_assessment` | low/moderate/high/critical |
| 归因类型 | attribution | `attribution` | 五分类 |
| 归因置信度 | attribution | `confidence` | low/medium/high |
| 收益 | outcome | `actual_return` | <-5% / -3~+3% / >+3% |

**v1 优先挖掘的组合**（对应"高估值+资金流缺失胜率下降"类需求）：
- `data_coverage.valuation=available` + `data_coverage.flow=missing` + `final_decision=BUY`
- `quant_score>=75` + `catalyst_strength∈{speculative,none}` + `risk_assessment∈{high,critical}`
- `attribution=ex_ante_miss` + `missed_evidence` 词频聚合

### B.4 修改的现有文件

| 文件 | 改动 |
|---|---|
| `tradingagents/core/persistence.py` | 新增 `update_strategy_lesson(id, **fields)`（累积 evidence_count/confidence）和 `deactivate_strategy_lesson(id)`；新增 `list_reflection_cases_rich(lookback_days, status="reflected")` 或扩展现有 list 支持时间窗口 |
| `tradingagents/core/reflection.py` | `run_reflection_batch` 末尾（prune 后）调 `cross_symbol_miner.mine()`；best-effort，失败不阻断反思批处理 |
| `tradingagents/api/routes/reflections.py` | 新增 `POST /reflections/mine-patterns` 手动触发跨标的挖掘（供调试/演示） |
| `tradingagents/skills/daily_pipeline/skill.py` 或 `llm_candidate_review.py` | `_load_strategy_lessons` 排序优先 cross_symbol_pattern + 按 confidence/evidence_count 排序，避免挤占前 5 条名额（llm_candidate_review.py:195 `[:5]`） |

### B.5 LLM 在模式挖掘中的角色

| 阶段 | 方法 | 职责 |
|---|---|---|
| 1. 特征分桶 + 聚合 | 纯 Python | 按 B.3 维度分组，计算 n/胜率/平均收益/置信区间 |
| 2. 显著性过滤 | 纯统计 | n>=5 且 |lift| > 0.15 |
| 3. 模式解释 | LLM（可选） | 输入显著分组统计，生成 finding 自然语言 + suggested_adjustment |
| 4. 去重/合并 | LLM 或规则 | 与现有 lessons 对比，语义重复则合并（累积 evidence_count） |

**边界**：统计必须有数据支撑，LLM 不发明模式、只解释和措辞。LLM 不可用时用模板生成 finding（如"近{lookback}天，{dimension} 的 BUY 候选胜率 {win_rate:.0%}（基准 {baseline:.0%}，n={n}）"）。

### B.6 实施步骤

1. **persistence 补 update/deactivate strategy_lesson 方法**（独立、可单测）
2. **CrossSymbolPatternMiner 核心**（`cross_symbol_pattern_miner.py`）：特征提取 + 聚合 + 显著性过滤（纯统计，可单测）
3. **LLM 解释层**（可选）：`_llm_explain` + 模板 fallback
4. **去重/合并逻辑**：与现有 lessons 对比，累积 evidence_count
5. **反思批处理接线**：`run_reflection_batch` 末尾调用 + 手动 API 端点
6. **daily_pipeline 回注优化**：lesson 排序优先 cross_symbol_pattern
7. **测试**：聚合/显著性/去重单测；端到端（造样本→挖掘→lesson→回注）

### B.7 测试策略

- `tests/unit/test_cross_symbol_pattern_miner.py`：
  - `_extract_features`：从 mock case 提取正确特征
  - `_aggregate` + `_filter_significant`：造 10 条样本（5 条 flow=missing 胜率 20%，5 条 flow=available 胜率 80%）→ 验证 lift 显著的分组被保留
  - `_dedupe_and_merge`：已有同 finding lesson → evidence_count 累积而非新建
  - LLM 不可用 → 模板 fallback 生成 finding
- `tests/unit/test_persistence.py` 扩展：`update_strategy_lesson` 累积 evidence_count；`deactivate_strategy_lesson` 置 active=0
- 集成测试：造一批 reflected cases → 调 `mine()` → 验证 strategy_lessons 表写入 → 验证 daily_pipeline `_matching_lessons` 能匹配到

### B.8 验收标准

1. 反思批处理完成后，strategy_lessons 表出现 `lesson_type="cross_symbol_pattern"` 的条目
2. 每个 lesson 的 `finding` 含统计支撑（n、胜率、基准、lift），`suggested_adjustment` 可执行
3. `evidence_count` 随新样本累积更新（非每次新建）
4. daily_pipeline 的 LLM review prompt 能看到跨标的 lesson（前 5 条优先）
5. 手动 `POST /reflections/mine-patterns` 可触发并返回挖掘摘要
6. LLM 不可用时仍能生成模板化 lesson（不阻断）

---

## 总体实施顺序（建议）

两个功能独立，可并行。若串行，建议 **A 先 B 后**（A 是用户可见的交互升级，B 是后端学习闭环增强）。

### 阶段 1（功能 A，约 3-4 天）
- A.1 ToolRegistry + 5 轻量工具（1 天）
- A.2 ChatAgent 核心（1 天）
- A.3 ws_chat 集成 + app.py 接线（0.5 天）
- A.4 前端三消息类型 + ToolCard（1 天）
- A.5 测试 + 验收（0.5 天）

### 阶段 2（功能 B，约 2-3 天）
- B.1 persistence update/deactivate 方法（0.5 天）
- B.2 CrossSymbolPatternMiner 统计核心（1 天）
- B.3 LLM 解释 + 去重合并（0.5 天）
- B.4 批处理接线 + 回注优化（0.5 天）
- B.5 测试 + 验收（0.5 天）

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| ChatAgent LLM 调用增加延迟/成本 | regex 高置信快路径保留，只低置信才调 LLM；LLM client 缓存；session buffer 复用 |
| ChatAgent 误分类（把该 skill 的判为 chat） | system prompt 明确 skill 触发场景；skill 工具描述带 triggers；可加规则兜底（含"分析/选股/扫描"等词时强制走 skill） |
| 轻量工具 handler 失败影响 ChatAgent | handler 内部全捕获，失败返回 error dict，ChatAgent 据此生成"工具暂不可用"回复 |
| 跨标的样本量不足（反思积压） | min_samples 阈值（默认 5）；样本不足时跳过该维度；建议周级挖掘 |
| LLM 模式解释编造因果 | 统计先行 + LLM 只措辞；finding 必须含 n/胜率/基准数字；LLM 不可用走模板 |
| 跨标的 lesson 挤占 daily_pipeline prompt 名额 | `_load_strategy_lessons` 按 confidence+evidence_count 排序，cross_symbol_pattern 优先 |

### 不在本次范围

- PositionAdvisor / StrategyBacktest / DecisionAudit（SPEC W14-W15，独立功能）
- 财务预警增强（SPEC W16 后半）
- 桌面打包（Phase 5）
