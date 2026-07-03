"""Fast LLM review for quant-ranked equity candidates.

V2 changes:
- Accepts CandidateContext for real-time data injection into prompt
- Prompt includes news, announcements, northbound flow, risk events
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

from tradingagents.core.signal_fusion import quant_evidence_markdown
from tradingagents.llm_clients import create_llm_client

if TYPE_CHECKING:
    from tradingagents.core.candidate_enrichment import CandidateContext

logger = logging.getLogger(__name__)


class CandidateLLMReview(BaseModel):
    """Structured LLM assessment for one quant-ranked candidate."""

    llm_view: Literal["strong_positive", "positive", "neutral", "negative", "strong_negative"] = "neutral"
    catalyst_strength: Literal["confirmed", "likely", "speculative", "none"] = "speculative"
    risk_assessment: Literal["low", "moderate", "high", "critical"] = "moderate"
    risk_override: bool = False
    invalidates_quant: bool = False
    key_catalysts: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    reasoning: str = ""
    llm_confidence: float | None = Field(default=None, ge=0, le=100)
    catalyst_score: float | None = Field(default=None, ge=0, le=100)

    @field_validator("llm_view", mode="before")
    @classmethod
    def _coerce_llm_view(cls, value: Any) -> str:
        text = str(value or "neutral").lower()
        if text in {"strong_positive", "positive", "neutral", "negative", "strong_negative"}:
            return text
        if text == "bullish":
            return "positive"
        if text == "bearish":
            return "negative"
        return "neutral"

    @field_validator("catalyst_strength", mode="before")
    @classmethod
    def _coerce_catalyst_strength(cls, value: Any) -> str:
        text = str(value or "speculative").lower()
        return text if text in {"confirmed", "likely", "speculative", "none"} else "speculative"

    @field_validator("risk_assessment", mode="before")
    @classmethod
    def _coerce_risk_assessment(cls, value: Any) -> str:
        text = str(value or "moderate").lower()
        return text if text in {"low", "moderate", "high", "critical"} else "moderate"

    def as_fusion_payload(self) -> dict[str, Any]:
        return {
            "llm_confidence": self.llm_confidence,
            "llm_view": self.llm_view,
            "catalyst_strength": self.catalyst_strength,
            "risk_assessment": self.risk_assessment,
            "risk_override": self.risk_override,
            "invalidates_quant": self.invalidates_quant,
            "risk_flags": self.risk_flags,
            "reasoning": self.reasoning,
            "catalyst_score": self.catalyst_score,
        }


class CandidateReviewer:
    """Review top quant candidates with the configured quick LLM."""

    def __init__(self, llm: Any, *, style: str, trade_date: str) -> None:
        self._llm = llm
        self._style = style
        self._trade_date = trade_date
        self._structured_llm = self._bind_structured(llm)

    async def review(
        self,
        candidate: dict[str, Any],
        context: "CandidateContext | None" = None,
    ) -> CandidateLLMReview:
        """Review a candidate with optional real-time context."""
        prompt = _build_prompt(candidate, self._style, self._trade_date, context=context)
        return await asyncio.to_thread(self._review_sync, prompt)

    def _review_sync(self, prompt: str) -> CandidateLLMReview:
        if self._structured_llm is not None:
            try:
                result = self._structured_llm.invoke(prompt)
                return _coerce_review(result)
            except Exception as exc:
                logger.warning("Candidate LLM structured review failed; falling back to text JSON: %s", exc)

        response = self._llm.invoke(prompt)
        content = getattr(response, "content", response)
        return _parse_review_text(str(content))

    @staticmethod
    def _bind_structured(llm: Any) -> Any | None:
        try:
            return llm.with_structured_output(CandidateLLMReview)
        except (AttributeError, NotImplementedError) as exc:
            logger.info("Candidate LLM reviewer will use JSON text mode: %s", exc)
            return None


def build_candidate_reviewer(
    config: dict[str, Any],
    *,
    style: str,
    trade_date: str,
) -> CandidateReviewer | None:
    """Create a reviewer from the unified LLM backbone, returning None on config errors."""
    try:
        provider = config.get("llm_provider", "openai")
        model = config.get("quick_think_llm") or config.get("deep_think_llm")
        if not model:
            raise ValueError("quick_think_llm/deep_think_llm is not configured")
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=config.get("backend_url"),
            **_provider_kwargs(config),
        )
        return CandidateReviewer(client.get_llm(), style=style, trade_date=trade_date)
    except Exception as exc:
        logger.warning("Daily pipeline LLM reviewer unavailable: %s", exc)
        return None


def _provider_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    provider = str(config.get("llm_provider", "")).lower()
    if provider == "google" and config.get("google_thinking_level"):
        kwargs["thinking_level"] = config["google_thinking_level"]
    if provider == "openai" and config.get("openai_reasoning_effort"):
        kwargs["reasoning_effort"] = config["openai_reasoning_effort"]
    if provider == "anthropic" and config.get("anthropic_effort"):
        kwargs["effort"] = config["anthropic_effort"]
    if config.get("temperature") not in (None, ""):
        kwargs["temperature"] = float(config["temperature"])
    return kwargs


def _build_prompt(
    candidate: dict[str, Any],
    style: str,
    trade_date: str,
    context: "CandidateContext | None" = None,
) -> str:
    style_label = {
        "short_term": "短线，重视动量、成交、资金流和隔日风险",
        "medium_term": "中线，平衡趋势、基本面、估值和催化剂",
        "long_term": "长线，重视质量、估值安全边际、行业景气和风险事件",
    }.get(style, "中线，平衡趋势、基本面、估值和催化剂")
    evidence = candidate.get("quant_evidence") or quant_evidence_markdown(candidate)

    # Build context section if available
    context_section = ""
    if context is not None:
        try:
            context_section = "\n\n" + context.to_prompt_section()
        except Exception:
            pass

    return f"""你是 A 股候选股票的快速复核 Agent。请基于下方结构化量化证据和市场信息进行判断。

交易日: {trade_date}
投资风格: {style_label}

{evidence}{context_section}

请输出严格 JSON，字段如下：
{{
  "llm_view": "strong_positive|positive|neutral|negative|strong_negative",
  "catalyst_strength": "confirmed|likely|speculative|none",
  "risk_assessment": "low|moderate|high|critical",
  "risk_override": true/false,
  "invalidates_quant": true/false,
  "key_catalysts": ["..."],
  "key_risks": ["..."],
  "risk_flags": ["..."],
  "reasoning": "一句话说明为什么该量化信号仍成立、需要观察或应被否定",
  "llm_confidence": 0-100,
  "catalyst_score": 0-100
}}

判定规则：
- 不要直接重复量化结论，要判断量化信号在新闻、公告、行业、政策、估值和资金流上下文里是否仍成立。
- 如果量化高分主要来自单一动量且风险控制/波动/回撤偏弱，降低 llm_confidence。
- 如果 ST、停牌、一字涨跌停、重大风险标记、因子缺失严重，设置 risk_override 或 invalidates_quant。
- 没有明确催化剂时不要为了迎合买入而给高置信度。
- 如果有重大利空公告（退市预警、业绩暴雷、违规处罚），应设置 risk_override=true。
- 如果有明确利好催化（重组、增持、业绩超预期），可上调 catalyst_score 和 llm_confidence。
- 北向资金大幅净卖出时应降低信心。
"""


def _coerce_review(value: Any) -> CandidateLLMReview:
    if isinstance(value, CandidateLLMReview):
        return value
    if isinstance(value, BaseModel):
        return CandidateLLMReview.model_validate(value.model_dump())
    if isinstance(value, dict):
        return CandidateLLMReview.model_validate(value)
    return _parse_review_text(str(getattr(value, "content", value)))


def _parse_review_text(text: str) -> CandidateLLMReview:
    try:
        return CandidateLLMReview.model_validate_json(_extract_json(text))
    except Exception as exc:
        logger.warning("Candidate LLM review JSON parse failed: %s", exc)
        return CandidateLLMReview(
            llm_view="neutral",
            llm_confidence=50,
            reasoning="LLM review returned unparseable output; kept as neutral.",
            risk_flags=["llm_review_unparseable"],
        )


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return json.dumps({"reasoning": stripped[:500]})
