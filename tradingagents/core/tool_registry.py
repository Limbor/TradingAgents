"""Lightweight tool registry for fast, non-run tools.

Lightweight tools are synchronous/async functions that return results in
milliseconds (no run, no streaming). They are distinct from Skills which
are long-running agent workflows managed by RunManager.

Used by ChatAgent to offer instant answers for portfolio queries, artifact
search, run status, MCP snapshots, and strategy lesson lookups.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

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
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    display: str = "text"


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
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return schemas
