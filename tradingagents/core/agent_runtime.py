"""Shared execution contract for coordinator, specialists and workflow templates.

LangGraph owns workflow dependencies; this module owns model rounds, tool
permissions, inherited scope, budgets and durable child-run records.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.runnables.config import merge_configs
from pydantic import BaseModel, Field

from tradingagents.core.llm_usage import estimate_tokens, normalize_usage
from tradingagents.core.model_policy import freeze_model_config, resolve_model


class RuntimeLimitError(RuntimeError):
    """A shared task budget or cancellation boundary was reached."""


class ScopeError(ValueError):
    """A child attempted to expand its parent's scope."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    purpose: str = "default"
    max_rounds: int = 12
    tools: frozenset[str] = frozenset()
    report_key: str | None = None


class AgentResult(BaseModel):
    run_id: str
    role: str
    status: str
    model: str
    kind: str = "agent"
    output: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tool_observations: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
    request_started: bool | None = None
    reused_from: dict[str, Any] | None = None
    research_policy_key: str | None = None
    context_stats: dict[str, Any] | None = None
    report_digest: dict[str, Any] | None = None
    completed_at: str | None = None


@dataclass
class TaskBudget:
    max_model_calls: int = 120
    max_tool_calls: int = 160
    max_children: int = 400
    deadline: float = field(default_factory=lambda: time.monotonic() + 2100)
    model_calls: int = 0
    tool_calls: int = 0
    children: int = 0
    max_tokens: int = 500_000
    consumed_tokens: int = 0
    reserved_tokens: int = 0
    cancelled: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self):
        if self.cancelled.is_set():
            raise RuntimeLimitError("任务已取消")
        if time.monotonic() >= self.deadline:
            raise RuntimeLimitError("任务执行时间已用尽")
        if self.consumed_tokens > self.max_tokens:
            raise RuntimeLimitError("本轮 token 预算已用尽，已停止继续调用模型")

    def consume(self, kind: str):
        with self._lock:
            self.check()
            attr, limit = {
                "model": ("model_calls", self.max_model_calls),
                "tool": ("tool_calls", self.max_tool_calls),
                "child": ("children", self.max_children),
            }[kind]
            if getattr(self, attr) >= limit:
                raise RuntimeLimitError(f"任务 {kind} 调用预算已用尽")
            setattr(self, attr, getattr(self, attr) + 1)

    def snapshot(self):
        with self._lock:
            return {key: getattr(self, key) for key in ("model_calls", "tool_calls", "children",
                    "consumed_tokens", "reserved_tokens", "max_tokens")}

    def reserve_tokens(self, estimated: int):
        with self._lock:
            self.check()
            if self.consumed_tokens + self.reserved_tokens + estimated > self.max_tokens:
                raise RuntimeLimitError("本轮 token 预算不足，已停止继续调用模型；已取得的数据保留在任务档案中")
            self.reserved_tokens += estimated

    def settle_tokens(self, reserved: int, actual: int | None):
        with self._lock:
            self.reserved_tokens -= reserved
            # Missing usage is charged conservatively against the guard, but
            # never fabricated as provider-reported usage or a cash debit.
            self.consumed_tokens += reserved if actual is None else actual


@dataclass(frozen=True)
class AgentContext:
    root_id: str
    run_id: str
    config: dict[str, Any]
    budget: TaskBudget
    parent_id: str | None = None
    host_run_id: str | None = None
    role: str | None = None
    cancel_scope: threading.Event = field(default_factory=threading.Event)
    account_id: str | None = None
    symbols: frozenset[str] = frozenset()
    as_of_date: str | None = None
    info_cutoff: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    memory_refs: tuple[str, ...] = ()
    memory_prompt: str = ""
    research_reuse_allowed: bool = True
    db: Any = None
    emit: Callable[[dict], None] | None = None

    @classmethod
    def root(cls, config, *, root_id=None, account_id=None, symbols=(), as_of_date=None,
             db=None, emit=None, timeout=2100):
        root_id = root_id or str(uuid.uuid4())
        return cls(root_id, root_id, freeze_model_config(config),
                   TaskBudget(deadline=time.monotonic() + timeout,
                              max_tokens=max(4096, int(config.get("agent_max_tokens_per_task", 500_000)))), account_id=account_id,
                   symbols=frozenset(_symbol(s) for s in symbols), as_of_date=as_of_date,
                   db=db, emit=emit)

    def check(self):
        self.budget.check()
        if self.cancel_scope.is_set():
            raise RuntimeLimitError("子任务已取消")

    def child(self, *, symbols=None, account_id=None, as_of_date=None):
        requested = self.symbols if symbols is None else frozenset(_symbol(s) for s in symbols)
        if self.symbols and not requested.issubset(self.symbols):
            raise ScopeError("子 Agent 不能扩大标的范围")
        if self.account_id and account_id not in (None, self.account_id):
            raise ScopeError("子 Agent 不能切换账户")
        if self.as_of_date and as_of_date and as_of_date > self.as_of_date:
            raise ScopeError("子 Agent 不能读取基准日之后的数据")
        self.check()
        self.budget.consume("child")
        return replace(self, run_id=str(uuid.uuid4()), parent_id=self.run_id,
                       symbols=requested, account_id=account_id or self.account_id,
                       as_of_date=as_of_date or self.as_of_date)


def _symbol(value):
    return str(value).upper().replace(".SS", ".SH")


_current: contextvars.ContextVar[AgentContext | None] = contextvars.ContextVar("agent_context", default=None)


def current_context():
    return _current.get()


@contextmanager
def use_context(context: AgentContext):
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def bind_scope(*, symbols=None, as_of_date=None, info_cutoff=None, memory_refs=None, memory_prompt=None, evidence_refs=None,
               research_reuse_allowed=None):
    """Narrow a running workflow's context and attach server-verified references."""
    context = current_context()
    if context is None:
        return
    requested = context.symbols if symbols is None else frozenset(_symbol(s) for s in symbols)
    if context.symbols and not requested.issubset(context.symbols):
        raise ScopeError("执行范围超出父任务标的")
    if context.as_of_date and as_of_date and as_of_date > context.as_of_date:
        raise ScopeError("执行日期超出父任务基准日")
    if info_cutoff and context.info_cutoff and info_cutoff > context.info_cutoff:
        raise ScopeError("执行信息时间超出父任务")
    _current.set(replace(context, symbols=requested, as_of_date=as_of_date or context.as_of_date,
                         info_cutoff=info_cutoff or context.info_cutoff,
                         memory_refs=tuple(memory_refs) if memory_refs is not None else context.memory_refs,
                         memory_prompt=memory_prompt if memory_prompt is not None else context.memory_prompt,
                         research_reuse_allowed=context.research_reuse_allowed and research_reuse_allowed is not False,
                         evidence_refs=tuple(evidence_refs) if evidence_refs is not None else context.evidence_refs))


def role_node(node, spec):
    from tradingagents.core.research_context import (
        REPORT_ROLES,
        compact_research_state,
        report_digest,
        research_policy_key,
        research_still_valid,
        restore_debate_history,
    )

    def prepare(run, state):
        view, run.context_stats = compact_research_state(state)
        cached = (state.get("research_reuse") or {}).get(spec.report_key)
        if cached and not research_still_valid(cached, current_context(), state):
            cached = None
            run.warnings.append("已有研究的有效期或范围已变化，本轮重新获取数据")
        if cached and spec.report_key in REPORT_ROLES:
            run.reused_from = cached["source"]
        _save(current_context(), run)
        return view, cached

    def record(run, output, state, cached=None):
        run.output = {key: value for key, value in output.items() if key != "messages"}
        context = current_context()
        run.research_policy_key = (research_policy_key(context.config, state.get("asset_type", "stock"), context.memory_prompt)
                                   if context.research_reuse_allowed else None)
        run.evidence_refs = list(context.evidence_refs)
        run.memory_refs = list(context.memory_refs)
        if context.db is not None:
            # wait_for/tool runners can execute in a copied ContextVar scope.
            # Read durable child receipts rather than assuming their updates
            # to evidence_refs propagated back to the role's caller.
            children = [row for row in context.db.list_agent_runtime(context.root_id)
                        if row.get("parent_id") == run.run_id and row.get("kind") == "tool"]
            for child in children:
                result = child.get("output") or {}
                observation = {"run_id": child["run_id"], "tool": child["role"].removeprefix("tool:"),
                               "status": child["status"], "as_of_date": child.get("as_of_date")}
                if "result" in result:
                    value = result["result"]
                    text = value if isinstance(value, str) else _json(value)
                    observation.update(result_excerpt=text[:800], excerpt_truncated=len(text) > 800)
                elif result.get("error_type"):
                    observation["error_type"] = result["error_type"]
                run.tool_observations.append(observation)
                if child["status"] == "completed":
                    run.evidence_refs.append(child["run_id"])
            run.evidence_refs = list(dict.fromkeys(run.evidence_refs))
        if run.reused_from and cached:
            run.evidence_refs = list(cached["evidence_refs"])
            run.tool_observations = list(cached["tool_observations"])
        if spec.report_key and isinstance(output.get(spec.report_key), str):
            run.report_digest = report_digest(output[spec.report_key], as_of_date=context.as_of_date,
                source={"run_id": (run.reused_from or {}).get("run_id") or run.run_id, "field": spec.report_key},
                observations=run.tool_observations)
        return {**output, "specialist_results": [{**run.model_dump(), "status": "completed"}],
                "agent_evidence_refs": list(run.evidence_refs)}

    def execute(state):
        with agent_run(spec) as run:
            bind_scope(evidence_refs=tuple(dict.fromkeys((*current_context().evidence_refs, *state.get("agent_evidence_refs", [])))))
            view, cached = prepare(run, state)
            if run.reused_from:
                output = {spec.report_key: cached["report"]}
            else:
                output = node.invoke(view) if hasattr(node, "invoke") else node(view)
            output = restore_debate_history(output, state, view)
            return record(run, output, state, cached)

    async def aexecute(state):
        with agent_run(spec) as run:
            bind_scope(evidence_refs=tuple(dict.fromkeys((*current_context().evidence_refs, *state.get("agent_evidence_refs", [])))))
            view, cached = prepare(run, state)
            if run.reused_from:
                output = {spec.report_key: cached["report"]}
            else:
                output = await invoke_node(node, view)
            output = restore_debate_history(output, state, view)
            return record(run, output, state, cached)

    return RunnableLambda(execute, afunc=aexecute)


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _save(context, result: AgentResult):
    if result.status != "running" and result.completed_at is None:
        result.completed_at = datetime.now(timezone.utc).isoformat()
    record = result.model_dump()
    record.update(root_id=context.root_id, parent_id=context.parent_id, host_run_id=context.host_run_id,
                  account_id=context.account_id, symbols=sorted(context.symbols),
                  as_of_date=context.as_of_date, info_cutoff=context.info_cutoff, budget=context.budget.snapshot(),
                  model_policy=context.config.get("model_policy"), provider=context.config.get("llm_provider"))
    if context.db is not None:
        context.db.save_agent_runtime(record)
    if context.emit:
        context.emit(record)


@contextmanager
def agent_run(spec: AgentSpec, context: AgentContext | None = None, *, kind="agent"):
    parent = context or current_context() or AgentContext.root({})
    child = replace(parent.child(), role=spec.name)
    model = resolve_model(child.config, spec.purpose)
    result = AgentResult(run_id=child.run_id, role=spec.name, status="running", model=model, kind=kind,
                         evidence_refs=list(child.evidence_refs), memory_refs=list(child.memory_refs))
    _save(child, result)
    try:
        with use_context(child):
            yield result
            if result.status == "running":
                child.check()
                result.status = "completed"
    except (asyncio.CancelledError, RuntimeLimitError):
        result.status = "cancelled" if child.budget.cancelled.is_set() or child.cancel_scope.is_set() else "interrupted"
        raise
    except BaseException:
        result.status = "failed"
        raise
    finally:
        _save(child, result)


class RuntimeModel(Runnable):
    """Native LangChain bindings with shared budget and role execution records."""
    def __init__(self, native, spec: AgentSpec, config: dict | None = None):
        self.native = native
        self.spec = spec
        self.config_snapshot = freeze_model_config(config or {})

    def for_agent(self, name, purpose="default"):
        return RuntimeModel(self.native, AgentSpec(name, purpose), self.config_snapshot)

    def bind_tools(self, tools, **kwargs):
        return RuntimeModel(self.native.bind_tools(tools, **kwargs), self.spec, self.config_snapshot)

    def with_structured_output(self, schema, **kwargs):
        return RuntimeModel(self.native.with_structured_output(schema, **kwargs), self.spec, self.config_snapshot)

    def _context(self):
        return current_context() or AgentContext.root(self.config_snapshot)

    @staticmethod
    def _output(result):
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
        return {"content": getattr(result, "content", str(result)),
                "tool_calls": getattr(result, "tool_calls", [])}

    @staticmethod
    def _inject_context(input, context):
        if not context.memory_prompt:
            return input
        section = "\n\n<shared_strategy_memory>" + context.memory_prompt + "</shared_strategy_memory>"
        if isinstance(input, str):
            return input + section
        messages = input.to_messages() if hasattr(input, "to_messages") else list(input) if isinstance(input, list) else None
        if messages is None:
            return input
        if messages and isinstance(messages[0], SystemMessage):
            original = messages[0].content
            content = original + section if isinstance(original, str) else [*original, {"type": "text", "text": section}]
            messages[0] = messages[0].model_copy(update={"content": content})
        elif messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
            messages[0] = {**messages[0], "content": str(messages[0].get("content", "")) + section}
        else:
            messages.insert(0, SystemMessage(content=section))
        return messages

    @staticmethod
    def _validated_output(result, context):
        from tradingagents.core.strategy_memory import validate_memory_usage
        output = RuntimeModel._output(result)
        if isinstance(output.get("memory_usage"), list):
            output = {**output, "memory_usage": validate_memory_usage(output["memory_usage"], [{"id": ref} for ref in context.memory_refs])}
        return output

    def invoke(self, input, config=None, **kwargs):
        context = self._context()
        with agent_run(self.spec, context, kind="model") as run:
            run.request_started = False
            input = self._inject_context(input, context)
            reserved = estimate_tokens(input) + 4096
            context.budget.reserve_tokens(reserved)
            try:
                config = self._usage_config(config, run)
                context.budget.consume("model")
                run.request_started = True
                result = self.native.invoke(input, config=config, **kwargs) if config is not None else self.native.invoke(input, **kwargs)
                run.usage = run.usage or normalize_usage(result)
            finally:
                context.budget.settle_tokens(reserved, (run.usage or {}).get("total_tokens") if run.request_started else 0)
            context.check()
            run.output = self._validated_output(result, context)
            return result

    async def ainvoke(self, input, config=None, **kwargs):
        context = self._context()
        with agent_run(self.spec, context, kind="model") as run:
            run.request_started = False
            input = self._inject_context(input, context)
            reserved = estimate_tokens(input) + 4096
            context.budget.reserve_tokens(reserved)
            try:
                config = self._usage_config(config, run)
                context.budget.consume("model")
                run.request_started = True
                call = self.native.ainvoke(input, config=config, **kwargs) if config is not None else self.native.ainvoke(input, **kwargs)
                result = await asyncio.wait_for(call, max(0.001, context.budget.deadline - time.monotonic()))
                run.usage = run.usage or normalize_usage(result)
            finally:
                context.budget.settle_tokens(reserved, (run.usage or {}).get("total_tokens") if run.request_started else 0)
            context.check()
            run.output = self._validated_output(result, context)
            return result

    def _usage_config(self, config, run):
        if not isinstance(self.native, Runnable):
            return config

        class Receipt(BaseCallbackHandler):
            def on_llm_end(self, response, **_kwargs):
                # This runs before structured-output parsing. A parsing error
                # must not erase usage from a successful API response.
                for generations in response.generations:
                    for generation in generations:
                        usage = normalize_usage(getattr(generation, "message", None))
                        if usage:
                            run.usage = usage
                            return
                raw = response.llm_output or {}
                run.usage = normalize_usage({"token_usage": raw.get("token_usage", raw)})

        return merge_configs(config, {"callbacks": [Receipt()]})


def runtime_model(native, role, config=None, purpose="default"):
    if isinstance(native, RuntimeModel):
        return native.for_agent(role, purpose)
    return RuntimeModel(native, AgentSpec(role, purpose), config)


class ToolExecutor:
    """One permission and scope boundary for native and application tools."""
    def __init__(self, tools, *, context=None, allowed=None, timeout=30):
        self.tools = {tool.name: tool for tool in tools}
        self.context = context or current_context()
        self.allowed = frozenset(self.tools if allowed is None else allowed)
        self.timeout = timeout

    def validate(self, name, args):
        if name not in self.allowed or name not in self.tools:
            raise ScopeError(f"未授权工具：{name}")
        if not isinstance(args, dict):
            raise ScopeError("工具参数必须为对象")
        if getattr(self.tools[name], "permission", "read") != "read":
            raise ScopeError("此执行器只允许读取工具；写操作需要审批执行器")
        context = self.context
        if context is None:
            return
        context.check()
        for key in ("symbol", "ticker", "ts_code", "company_name"):
            value = args.get(key)
            if value and context.symbols and _symbol(value) not in context.symbols:
                raise ScopeError("工具参数超出标的范围")
        for key in ("symbols", "tickers", "ts_codes"):
            values = args.get(key)
            if values and context.symbols and (not isinstance(values, list) or any(_symbol(s) not in context.symbols for s in values)):
                raise ScopeError("工具参数超出标的范围")
        for key in ("account_id", "session_id", "paper_session_id"):
            value = args.get(key)
            if value and context.account_id and value != context.account_id:
                raise ScopeError("工具参数超出账户范围")
        information_tool = any(part in name.lower() for part in ("news", "announcement", "fundamental", "balance_sheet", "cashflow", "income_statement"))
        date_limit = context.info_cutoff[:10] if information_tool and context.info_cutoff else context.as_of_date
        for key in ("curr_date", "end_date", "trade_date", "as_of_date", "date"):
            value = args.get(key)
            normalized = str(value)[:10] if value else None
            if value and len(str(value)) == 8 and str(value).isdigit():
                normalized = f"{str(value)[:4]}-{str(value)[4:6]}-{str(value)[6:8]}"
            if normalized:
                try:
                    date.fromisoformat(normalized)
                except ValueError as exc:
                    raise ScopeError("工具日期参数无效") from exc
            if normalized and date_limit and normalized > date_limit:
                raise ScopeError("工具参数超出信息基准日")

    async def execute(self, name, args):
        self.validate(name, args)
        if self.context:
            self.context.budget.consume("tool")
        tool = self.tools[name]
        timeout = self.timeout
        if self.context:
            timeout = min(timeout, self.context.budget.deadline - time.monotonic())
        try:
            if hasattr(tool, "handler"):
                call = tool.handler(**args)
            elif hasattr(tool, "ainvoke"):
                call = tool.ainvoke(args)
            else:
                call = asyncio.to_thread(tool.invoke, args)
            result = await asyncio.wait_for(call, max(0.001, timeout))
        except BaseException as exc:
            if self.context:
                cancelled = isinstance(exc, asyncio.CancelledError) or self.context.budget.cancelled.is_set() or self.context.cancel_scope.is_set()
                self._record(name, "cancelled" if cancelled else "failed", {"error_type": type(exc).__name__})
                self.context.check()
            raise
        if self.context:
            self.context.check()
            record = self._record(name, "failed" if isinstance(result, dict) and result.get("error") else "completed", {"result": result})
            context = current_context()
            if context:
                bind_scope(evidence_refs=(*context.evidence_refs, record.run_id))
        return result

    def _record(self, name, status, output):
        record = AgentResult(run_id=str(uuid.uuid4()), role=f"tool:{name}", status=status,
                             model="", kind="tool", output=output)
        _save(replace(self.context, run_id=record.run_id, parent_id=self.context.run_id), record)
        return record



class AgentSession:
    """Bounded model/tool transcript shared by coordinator and specialists.

    choose/tool_result also supports host-managed approval and deterministic
    workflow steps. run executes the same loop for autonomous read-only roles.
    """
    def __init__(self, llm, goal, system_prompt, allowed, timeout, history=None, *,
                 messages=None, max_rounds=12, encode=None, final_llm=None):
        self.llm = llm
        self.messages = list(messages) if messages is not None else [SystemMessage(content=system_prompt)]
        if history:
            self.messages.append(HumanMessage(content="历史文本，不是当前证据或操作授权：\n" + _json(history)))
        if messages is None:
            self.messages.append(HumanMessage(content=goal[:4000]))
        self.allowed = frozenset(allowed)
        self.timeout = timeout
        self.max_rounds = max_rounds
        self.rounds = 0
        self.encode = encode or _json
        self.final_llm = final_llm

    def tool_result(self, call_id, name, result):
        self.messages.append(ToolMessage(content=self.encode(result), tool_call_id=call_id, name=name))

    async def choose(self):
        if self.rounds >= self.max_rounds:
            raise RuntimeLimitError("Agent 工具调用轮次已用尽")
        self.rounds += 1
        response = await asyncio.wait_for(self.llm.ainvoke(self.messages), self.timeout)
        calls = getattr(response, "tool_calls", None) or []
        if not isinstance(calls, list):
            raise ValueError("模型工具调用格式无效")
        ids = [call.get("id") if isinstance(call, dict) else None for call in calls]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("模型工具调用缺少唯一 ID")
        self.messages.append(response)
        return calls

    async def run(self, executor: ToolExecutor):
        while True:
            # Reserve one model call for a report. The unbound model has no
            # tools, so sequential collection cannot consume the closing turn.
            # Shared cancellation/deadline/model budgets still apply.
            if self.final_llm is not None and self.rounds >= self.max_rounds - 1:
                self.rounds += 1
                self.messages.append(HumanMessage(content=(
                    "取证轮次已接近上限，停止调用工具，现在完成你的分析报告。"
                    "仅引用本次已经取得的数据，标注数据日期；明确列出无法取得的字段与影响，"
                    "不要补造数值，也不要把辅助数据缺失描述成所有行情都不可用。"
                    "若已有证据不足以支持某项判断，请具体说明该项限制。"
                )))
                response = await asyncio.wait_for(self.final_llm.ainvoke(self.messages), self.timeout)
                if getattr(response, "tool_calls", None):
                    raise RuntimeLimitError("分析收尾阶段仍请求工具，未能生成报告")
                if not getattr(response, "content", None):
                    raise RuntimeLimitError("分析收尾阶段未返回报告")
                self.messages.append(response)
                return response
            calls = await self.choose()
            if not calls:
                return self.messages[-1]
            for call in calls:
                try:
                    if call.get("name") not in self.allowed:
                        raise ScopeError("模型请求了未授权工具")
                    result = await executor.execute(call["name"], call.get("args", {}))
                except RuntimeLimitError:
                    raise
                except Exception as exc:
                    result = {"error": str(exc)}
                self.tool_result(call["id"], call.get("name", ""), result)


async def invoke_node(node, state):
    if hasattr(node, "ainvoke"):
        return await node.ainvoke(state)
    if inspect.iscoroutinefunction(node):
        return await node(state)
    return await asyncio.to_thread(node, state)


async def managed_stream(spec: AgentSpec, stream, config):
    """Delegate a workflow without leaking context across async-generator yields.

    Each iteration reinstalls its context and restores the caller's context
    before yielding. Inline deep analyses therefore cannot narrow a sibling's
    symbols or contaminate a later candidate with the previous one's memory.
    """
    parent = current_context() or AgentContext.root(config, db=config.get("db"))
    own_run = parent.role != spec.name
    context = replace(parent.child(), role=spec.name) if own_run else parent
    result = AgentResult(run_id=context.run_id, role=spec.name, kind="workflow", status="running",
                         model=resolve_model(context.config, spec.purpose))
    if own_run:
        _save(context, result)
    try:
        while True:
            try:
                with use_context(context):
                    event = await stream.__anext__()
                    context = current_context()
            except StopAsyncIteration:
                result.status = "completed"
                break
            if event.event_type == "skill_complete":
                result.output = event.data
            yield event
    except (asyncio.CancelledError, GeneratorExit):
        result.status = "cancelled"
        raise
    except BaseException:
        result.status = "failed"
        raise
    finally:
        with use_context(context):
            await stream.aclose()
        if own_run:
            result.evidence_refs = list(context.evidence_refs)
            result.memory_refs = list(context.memory_refs)
            _save(context, result)
