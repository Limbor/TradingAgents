"""One model policy for conversations, specialists and background analysis."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ModelPolicy(BaseModel):
    default_model: str = Field(min_length=1, max_length=128)
    deep_model: str | None = Field(default=None, max_length=128)

    @field_validator("default_model", "deep_model")
    @classmethod
    def normalize_model(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value or value == "custom":
            raise ValueError("请输入实际模型 ID")
        return value


def model_policy(config: dict[str, Any]) -> ModelPolicy:
    explicit = config.get("model_policy")
    if explicit is not None:
        return ModelPolicy.model_validate(explicit)
    # Migrate the previously chosen conversation model into the global default.
    default = config.get("agent_model") or config.get("quick_think_llm") or "gpt-5.4-mini"
    deep = config.get("deep_think_llm")
    return ModelPolicy(default_model=default, deep_model=deep if deep and deep != default else None)


def resolve_model(config: dict[str, Any], purpose: str = "default", override: str | None = None) -> str:
    if override is not None:
        return ModelPolicy(default_model=override).default_model
    policy = model_policy(config)
    return (policy.deep_model or policy.default_model) if purpose == "deep" else policy.default_model


def freeze_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Copy mutable settings, retaining service objects without cloning locks."""
    frozen = {}
    for key, value in config.items():
        frozen[key] = deepcopy(value) if isinstance(value, (dict, list, set)) else value
    policy = model_policy(config)
    frozen.update(model_policy=policy.model_dump(), quick_think_llm=policy.default_model,
                  deep_think_llm=policy.deep_model or policy.default_model, agent_model=policy.default_model)
    return frozen


def config_update(config: dict, updates: dict) -> dict:
    """Translate old clients into the unified policy; explicit new policy wins."""
    result = dict(updates)
    if "model_policy" in updates:
        result["model_policy"] = ModelPolicy.model_validate(updates["model_policy"]).model_dump()
    elif {"quick_think_llm", "deep_think_llm", "agent_model"}.intersection(updates):
        policy = model_policy(config)
        default = updates.get("agent_model") or updates.get("quick_think_llm") or policy.default_model
        if "agent_model" in updates and updates["agent_model"] is None:
            default = updates.get("quick_think_llm") or config.get("quick_think_llm") or policy.default_model
        deep = updates.get("deep_think_llm", policy.deep_model)
        result["model_policy"] = ModelPolicy(default_model=default, deep_model=deep if deep != default else None).model_dump()
    if "model_policy" in updates:
        policy = ModelPolicy.model_validate(result["model_policy"])
        result.update(quick_think_llm=policy.default_model, deep_think_llm=policy.deep_model or policy.default_model,
                      agent_model=policy.default_model)
    return result


def provider_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """Shared provider reasoning and sampling settings for every Agent role."""
    kwargs = {}
    field_map = {"google": ("google_thinking_level", "thinking_level"),
                 "openai": ("openai_reasoning_effort", "reasoning_effort"),
                 "anthropic": ("anthropic_effort", "effort")}
    fields = field_map.get(str(config.get("llm_provider", "")).lower())
    if fields and config.get(fields[0]):
        kwargs[fields[1]] = config[fields[0]]
    if config.get("temperature") not in (None, ""):
        kwargs["temperature"] = float(config["temperature"])
    return kwargs
