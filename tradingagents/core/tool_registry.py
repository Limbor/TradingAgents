"""Lightweight tool registry for fast, non-run tools.

Lightweight tools are synchronous/async functions that return results in
milliseconds (no run, no streaming). They are distinct from Skills which
are long-running agent workflows managed by RunManager.

Used by ChatAgent to offer instant answers for portfolio queries, artifact
search, run status, MCP snapshots, and strategy lesson lookups.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)


@dataclass
class LightweightTool:
    """A lightweight, synchronous-return tool registered in the ToolRegistry.

    Attributes:
        name: Tool identifier, e.g. ``get_portfolio_summary``.
        description: Natural-language description for LLM tool_use routing.
        parameters: JSON Schema (dict) defining the tool's input parameters.
        handler: Async callable that receives validated kwargs and returns a
            JSON-serialisable dict. Must handle its own exceptions and return
            ``{"error": "...", "warnings": [...]}`` on failure.
        display: Frontend rendering hint: ``"text"``, ``"table"``, or ``"card"``.
        output_schema: JSON Schema for a successful handler result. Error
            responses with an ``error`` field are audited without validation.
        permission: Harness permission tier. Legacy ChatAgent only exposes
            read tools; the Harness rejects other tiers before dispatch.
        scope: Whether the call is global, bound to a paper account, or bound
            to a symbol from the user's goal.
        timeout_seconds: Maximum duration of a single Harness tool call.
        retry_policy: Whether a dated source can be retried once on a date
            conflict. Other failures remain visible for replanning.
        data_source: Auditable source label used when a handler cannot return one.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    display: str = "text"
    output_schema: dict[str, Any] | None = None
    permission: Literal["read", "compute", "paper_write", "live_trade"] = "read"
    scope: Literal["global", "paper", "symbol"] = "global"
    timeout_seconds: float = 100.0
    retry_policy: Literal["none", "date_conflict_once"] = "none"
    data_source: str | None = None


class ToolRegistry:
    """Registry for lightweight tools (non-skill, no-run tools).

    Supports registration, lookup, and OpenAI function-calling schema generation.
    """

    def __init__(self) -> None:
        self._tools: dict[str, LightweightTool] = {}

    def register(self, tool: LightweightTool) -> None:
        """Register a lightweight tool.

        Raises ValueError if a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(f"Lightweight tool '{tool.name}' already registered")
        if tool.permission not in {"read", "compute", "paper_write", "live_trade"}:
            raise ValueError(f"Invalid permission for tool '{tool.name}'")
        if tool.scope not in {"global", "paper", "symbol"}:
            raise ValueError(f"Invalid scope for tool '{tool.name}'")
        if not math.isfinite(tool.timeout_seconds) or tool.timeout_seconds <= 0:
            raise ValueError(f"Invalid timeout for tool '{tool.name}'")
        if tool.retry_policy not in {"none", "date_conflict_once"}:
            raise ValueError(f"Invalid retry policy for tool '{tool.name}'")
        if tool.data_source is not None and (
            not isinstance(tool.data_source, str) or not tool.data_source.strip()
        ):
            raise ValueError(f"Invalid data source for tool '{tool.name}'")
        Draft202012Validator.check_schema(tool.parameters)
        if tool.output_schema is not None:
            Draft202012Validator.check_schema(tool.output_schema)
        self._tools[tool.name] = tool
        logger.info("Registered lightweight tool: %s", tool.name)

    def get(self, name: str) -> LightweightTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_all(self) -> list[LightweightTool]:
        """List all registered tools."""
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        """Generate OpenAI function-calling tool schemas for all registered tools.

        Returns a list of dicts compatible with ``llm.bind_tools(...)``.
        """
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if tool.permission != "read":
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return schemas
