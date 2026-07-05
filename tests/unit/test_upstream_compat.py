from pydantic import BaseModel

from tradingagents.agents.utils.structured import invoke_structured_or_freetext
from tradingagents.reporting import write_report_sections, write_report_tree


class DummySchema(BaseModel):
    value: str


class NoneStructuredLLM:
    def invoke(self, prompt):
        return None


class PlainLLM:
    def invoke(self, prompt):
        class Response:
            content = "free text fallback"

        return Response()


def test_structured_output_none_falls_back_to_free_text():
    result = invoke_structured_or_freetext(
        NoneStructuredLLM(),
        PlainLLM(),
        "prompt",
        lambda item: item.value,
        "Test Agent",
    )

    assert result == "free text fallback"


def test_write_report_sections_creates_tree_and_complete_report(tmp_path):
    complete = write_report_sections(
        {
            "market_report": "market body",
            "final_trade_decision": "portfolio body",
        },
        "600519.SH",
        tmp_path / "reports",
    )

    assert complete.name == "complete_report.md"
    assert complete.exists()
    assert (tmp_path / "reports" / "1_analysts" / "market.md").read_text(encoding="utf-8") == "market body"
    assert (tmp_path / "reports" / "5_portfolio" / "decision.md").read_text(encoding="utf-8") == "portfolio body"
    assert "Market Analyst" in complete.read_text(encoding="utf-8")


def test_write_report_tree_accepts_graph_final_state(tmp_path):
    complete = write_report_tree(
        {
            "market_report": "market",
            "investment_debate_state": {
                "bull_history": "bull",
                "bear_history": "bear",
                "judge_decision": "manager",
            },
            "risk_debate_state": {"judge_decision": "final"},
        },
        "AAPL",
        tmp_path / "tree",
    )

    text = complete.read_text(encoding="utf-8")
    assert "Bull Researcher" in text
    assert "Portfolio Manager" in text
