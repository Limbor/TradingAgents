import asyncio
from types import SimpleNamespace

from tradingagents.api.app import (
    _daily_pipeline_followup_params,
    _start_daily_pipeline_followup,
)


def test_followup_params_hands_top_ranked_candidate_to_stock_analysis():
    params = _daily_pipeline_followup_params(
        {
            "trade_date": "2026-08-14",
            "candidates": [
                {
                    "symbol": "600519.SH",
                    "final_decision": "WATCHLIST",
                    "final_score": 82.5,
                    "entry_zone": [1400, 1450],
                },
                {"symbol": "000001.SZ"},
            ],
        }
    )

    assert params is not None
    assert params["ticker"] == "600519.SH"
    assert params["analysis_date"] == "2026-08-14"
    assert params["selection_context"]["final_decision"] == "WATCHLIST"
    assert params["selection_context"]["display_score"] == 82.5
    assert params["selection_context"]["entry_zone"] == [1400, 1450]


def test_followup_params_skips_invalid_rows_and_handles_empty_result():
    assert _daily_pipeline_followup_params(None) is None
    assert _daily_pipeline_followup_params({"candidates": []}) is None
    assert _daily_pipeline_followup_params(
        {"candidates": [None, {}, {"ts_code": "000001.SZ"}]}
    )["ticker"] == "000001.SZ"


def test_scheduled_followup_creates_separate_persisted_run():
    class Registry:
        def get(self, skill_id):
            return SimpleNamespace(metadata=SimpleNamespace(id=skill_id))

    class RunManager:
        def __init__(self):
            self.calls = []

        async def create_run(self, skill, params, config):
            self.calls.append((skill, params, config))
            return SimpleNamespace(id="analysis-run")

    manager = RunManager()
    app = SimpleNamespace(
        state=SimpleNamespace(
            config={"daily_pipeline_scheduled_followup_enabled": True},
            registry=Registry(),
            run_manager=manager,
        )
    )
    completed = SimpleNamespace(
        id="pipeline-run",
        result={"trade_date": "2026-08-14", "candidates": [{"symbol": "600519.SH"}]},
    )

    followup = asyncio.run(_start_daily_pipeline_followup(app, completed))

    assert followup.id == "analysis-run"
    assert len(manager.calls) == 1
    assert manager.calls[0][1]["ticker"] == "600519.SH"


def test_scheduled_followup_can_be_disabled():
    app = SimpleNamespace(
        state=SimpleNamespace(config={"daily_pipeline_scheduled_followup_enabled": False})
    )
    completed = SimpleNamespace(id="pipeline-run", result={"candidates": [{"symbol": "X"}]})

    assert asyncio.run(_start_daily_pipeline_followup(app, completed)) is None
