"""One model policy for conversations, specialists and background analysis."""
from __future__ import annotations

import math
import os
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


class TaskModelSelection(BaseModel):
    """Public task selection: credentials and endpoints remain server-owned."""

    model_config = {"extra": "forbid"}
    provider: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$")
    model: str = Field(min_length=1, max_length=128)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value):
        return ModelPolicy(default_model=value).default_model


def task_model_config(config: dict[str, Any], selection: TaskModelSelection | dict | None) -> dict:
    """Freeze a task's complete model choice without mutating global defaults."""
    frozen = freeze_model_config(config)
    if selection is None:
        return frozen
    choice = TaskModelSelection.model_validate(selection)
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV

    if choice.provider not in PROVIDER_API_KEY_ENV:
        raise ValueError("不支持的模型渠道")
    env = PROVIDER_API_KEY_ENV[choice.provider]
    if env and not os.environ.get(env):
        raise ValueError(f"模型渠道未配置密钥，请在后端设置 {env} 并重启服务")
    if choice.provider != config.get("llm_provider"):
        if choice.provider == "openai_compatible":
            raise ValueError("请先在设置中配置自定义渠道地址")
        frozen["backend_url"] = None
    frozen.update(llm_provider=choice.provider,
                  model_policy={"default_model": choice.model, "deep_model": None},
                  quick_think_llm=choice.model, deep_think_llm=choice.model, agent_model=choice.model)
    return frozen


def model_policy(config: dict[str, Any]) -> ModelPolicy:
    explicit = config.get("model_policy")
    if explicit is not None:
        return ModelPolicy.model_validate(explicit)
    # Migrate the previously chosen conversation model into the global default.
    default = config.get("agent_model") or config.get("quick_think_llm") or "gpt-5.4-mini"
    deep = config.get("deep_think_llm")
    return ModelPolicy(default_model=default, deep_model=deep if deep and deep != default else None)


def resolve_model(config: dict[str, Any], purpose: str = "default", override: str | None = None,
                  *, require_config: bool = False) -> str:
    if override is not None:
        return ModelPolicy(default_model=override).default_model
    if require_config and not any(config.get(key) for key in ("model_policy", "agent_model", "quick_think_llm", "deep_think_llm")):
        raise ValueError("未配置默认模型")
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
    if config.get("llm_provider") == "qianwen":
        kwargs["extra_body"] = {"enable_thinking": bool(config.get("qianwen_thinking", False))}
        kwargs["max_tokens"] = 4096
    field_map = {"google": ("google_thinking_level", "thinking_level"),
                 "openai": ("openai_reasoning_effort", "reasoning_effort"),
                 "anthropic": ("anthropic_effort", "effort")}
    fields = field_map.get(str(config.get("llm_provider", "")).lower())
    if fields and config.get(fields[0]):
        kwargs[fields[1]] = config[fields[0]]
    if config.get("temperature") not in (None, ""):
        kwargs["temperature"] = float(config["temperature"])
    return kwargs


def model_response_timeout(config: dict, key: str = "agent_model_planning_timeout", default: float = 60.0) -> float:
    """Bound a model round by the shared task deadline; retain explicit overrides."""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(0.05, min(value, 180.0))
