"""Generate OpenAI function-calling tool schemas from registered skills.

Used by LLMRouter to present available skills as tools for intent recognition.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from tradingagents.core.tool_registry import ToolRegistry

from tradingagents.skills.registry import SkillRegistry


def generate_tool_schemas(registry: SkillRegistry) -> list[dict[str, Any]]:
    """Generate OpenAI function calling format tool schemas for all registered skills.

    Each skill becomes a tool with:
    - name: skill.metadata.id
    - description: skill.metadata.description (bilingual intent description)
    - parameters: JSON schema from skill.input_schema (Pydantic model)
    """
    tools: list[dict[str, Any]] = []
    for meta in registry.list_all():
        skill = registry.get(meta.id)
        if skill is None:
            continue

        # Build description with trigger keywords for better routing
        trigger_hint = ", ".join(meta.triggers[:5]) if meta.triggers else ""
        description = meta.description
        if trigger_hint:
            description += f"\n触发词/Triggers: {trigger_hint}"

        tool: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": meta.id,
                "description": description,
                "parameters": skill.input_schema.model_json_schema(),
            },
        }
        tools.append(tool)
    return tools


def generate_lightweight_tool_schemas(tool_registry: "ToolRegistry") -> list[dict[str, Any]]:
    """Generate OpenAI function-calling tool schemas from a ToolRegistry.

    This function mirrors :func:`generate_tool_schemas` but operates on
    lightweight tools (non-skill, no-run tools) instead of skills.

    Each lightweight tool becomes a tool with:
    - name: tool.name
    - description: tool.description
    - parameters: JSON schema from tool.parameters
    """
    return tool_registry.to_openai_schemas()
