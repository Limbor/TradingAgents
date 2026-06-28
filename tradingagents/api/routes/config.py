"""Configuration endpoints — get and update runtime config."""

import os

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ConfigResponse(BaseModel):
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    output_language: str
    max_debate_rounds: int
    max_risk_discuss_rounds: int
    checkpoint_enabled: bool
    backend_url: str | None = None
    api_keys: dict[str, bool] = {}


class ConfigUpdate(BaseModel):
    llm_provider: str | None = None
    deep_think_llm: str | None = None
    quick_think_llm: str | None = None
    output_language: str | None = None
    max_debate_rounds: int | None = None
    max_risk_discuss_rounds: int | None = None
    checkpoint_enabled: bool | None = None
    backend_url: str | None = None


class ModelOption(BaseModel):
    label: str
    value: str


class ProviderDetail(BaseModel):
    id: str
    name: str
    quick_models: list[ModelOption]
    deep_models: list[ModelOption]


_PROVIDER_DISPLAY_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google (Gemini)",
    "xai": "xAI (Grok)",
    "deepseek": "DeepSeek",
    "qwen": "Qwen (International)",
    "qwen-cn": "Qwen (China)",
    "glm": "GLM (Z.AI)",
    "glm-cn": "GLM (BigModel CN)",
    "minimax": "MiniMax (International)",
    "minimax-cn": "MiniMax (China)",
    "openrouter": "OpenRouter",
    "mistral": "Mistral",
    "kimi": "Kimi (Moonshot)",
    "groq": "Groq",
    "nvidia": "NVIDIA",
    "ollama": "Ollama (Local)",
    "azure": "Azure OpenAI",
    "bedrock": "AWS Bedrock",
    "openai_compatible": "OpenAI Compatible",
}

_API_KEY_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "qwen-cn": "DASHSCOPE_API_KEY",
    "glm": "GLM_API_KEY",
    "glm-cn": "GLM_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "groq": "GROQ_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}


def _get_api_key_status() -> dict[str, bool]:
    """Check which API keys are configured."""
    result = {}
    for provider, env_var in _API_KEY_ENV_VARS.items():
        result[provider] = bool(os.environ.get(env_var))
    return result


def _build_config_response(config: dict) -> ConfigResponse:
    return ConfigResponse(
        llm_provider=config.get("llm_provider", "openai"),
        deep_think_llm=config.get("deep_think_llm", "gpt-5.5"),
        quick_think_llm=config.get("quick_think_llm", "gpt-5.4-mini"),
        output_language=config.get("output_language", "English"),
        max_debate_rounds=config.get("max_debate_rounds", 1),
        max_risk_discuss_rounds=config.get("max_risk_discuss_rounds", 1),
        checkpoint_enabled=config.get("checkpoint_enabled", False),
        backend_url=config.get("backend_url"),
        api_keys=_get_api_key_status(),
    )


@router.get("/config", response_model=ConfigResponse)
async def get_config(request: Request):
    """Get current runtime configuration."""
    return _build_config_response(request.app.state.config)


@router.put("/config", response_model=ConfigResponse)
async def update_config(request: Request, body: ConfigUpdate):
    """Update runtime configuration."""
    config = request.app.state.config

    for field in (
        "llm_provider", "deep_think_llm", "quick_think_llm",
        "output_language", "max_debate_rounds", "max_risk_discuss_rounds",
        "checkpoint_enabled", "backend_url",
    ):
        val = getattr(body, field, None)
        if val is not None:
            config[field] = val

    return _build_config_response(config)


@router.get("/config/providers", response_model=list[ProviderDetail])
async def list_providers(request: Request):
    """List available LLM providers with their model options."""
    from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

    providers = []
    for provider_id, mode_options in MODEL_OPTIONS.items():
        quick_models = [
            ModelOption(label=label, value=value)
            for label, value in mode_options.get("quick", [])
        ]
        deep_models = [
            ModelOption(label=label, value=value)
            for label, value in mode_options.get("deep", [])
        ]
        providers.append(
            ProviderDetail(
                id=provider_id,
                name=_PROVIDER_DISPLAY_NAMES.get(provider_id, provider_id),
                quick_models=quick_models,
                deep_models=deep_models,
            )
        )
    return providers
