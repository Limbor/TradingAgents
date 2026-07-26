"""Regression tests for candidate LLM review fallback behavior.

DeepSeek's structured-output path occasionally returns ``None`` (no parsed
result) instead of raising. Previously that ``None`` was coerced through
``str(None) == "None"`` into a fake-neutral review with ``reasoning="None"``
and no warning. Now it must fall back to the free-text JSON path, and a
truly unparseable text response must yield the tagged neutral fallback.
"""

from __future__ import annotations

import pytest

from tradingagents.core.llm_candidate_review import (
    CandidateLLMReview,
    CandidateReviewer,
    _extract_json,
    _parse_review_text,
)

VALID_REVIEW_JSON = (
    '{"llm_view": "positive", "catalyst_strength": "likely",'
    ' "risk_assessment": "moderate", "reasoning": "信号成立",'
    ' "llm_confidence": 70}'
)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """LLM stub whose structured path returns None (DeepSeek quirk)."""

    def __init__(self, structured_result, text_content: str) -> None:
        self._structured_result = structured_result
        self._text_content = text_content
        self.text_invoked = False

    def with_structured_output(self, schema):
        outer = self

        class _Structured:
            def invoke(self, prompt):
                return outer._structured_result

        return _Structured()

    def invoke(self, prompt):
        self.text_invoked = True
        return _FakeResponse(self._text_content)


def _review(llm: _FakeLLM) -> CandidateLLMReview:
    reviewer = CandidateReviewer(llm, style="medium_term", trade_date="2026-07-27")
    return reviewer._review_sync("prompt")


def test_structured_none_falls_back_to_text_json():
    llm = _FakeLLM(structured_result=None, text_content=VALID_REVIEW_JSON)
    review = _review(llm)
    assert llm.text_invoked
    assert review.llm_view == "positive"
    assert review.reasoning == "信号成立"
    assert review.llm_confidence == 70


def test_structured_none_and_empty_text_yields_tagged_fallback():
    llm = _FakeLLM(structured_result=None, text_content="")
    review = _review(llm)
    assert llm.text_invoked
    assert "llm_review_unparseable" in review.risk_flags
    assert review.reasoning != "None"


def test_structured_success_skips_text_path():
    structured = CandidateLLMReview(llm_view="negative", reasoning="风险过高")
    llm = _FakeLLM(structured_result=structured, text_content=VALID_REVIEW_JSON)
    review = _review(llm)
    assert not llm.text_invoked
    assert review.llm_view == "negative"


@pytest.mark.parametrize("text", ["", "None", "null", "  None  "])
def test_extract_json_rejects_non_json_sentinels(text):
    with pytest.raises(ValueError):
        _extract_json(text)


def test_parse_review_text_none_is_tagged_not_stored_as_reasoning():
    review = _parse_review_text("None")
    assert review.llm_view == "neutral"
    assert "llm_review_unparseable" in review.risk_flags
    assert review.reasoning != "None"


def test_extract_json_still_accepts_prose_with_content():
    # Non-empty prose without JSON is still wrapped as reasoning (unchanged).
    import json

    result = json.loads(_extract_json("量化信号成立，但缺乏催化剂"))
    assert result["reasoning"] == "量化信号成立，但缺乏催化剂"
