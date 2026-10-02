"""Consumer-side tests for MCP async rank, batch risk scan and feature detection.

Covers the R1/R2/R3/R4 consumer integration from
docs/MCP_ENHANCEMENT_REQUIREMENTS.md:
- rank_factor_candidates async job path (submit -> poll progress -> unwrap result)
- graceful sync fallback when the server lacks the async_mode feature
- get_risk_announcements_batch feature gating
- risk_monitor batched scan grouping + per-symbol fallback
- drive_with_progress bridging progress callbacks into skill event streams
"""

import asyncio
import contextlib
import json
from types import SimpleNamespace

from tradingagents.core.mcp_client import (
    MCPConfig,
    StockManagerMCPClient,
    _unwrap_job_result,
)
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate
from tradingagents.skills._shared import drive_with_progress, rank_progress_detail
from tradingagents.skills.risk_monitor.skill import RiskMonitorInput, _scan_risks


def _result(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)


class _RouterSession:
    """Fake MCP session routing call_tool by tool name via a handler."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return _result(json.dumps(self.handler(name, arguments)))


def _client(handler, *, features=None):
    client = StockManagerMCPClient(MCPConfig(tool_timeout=1.0, job_poll_timeout=5.0))
    client._connected = True
    client._session = _RouterSession(handler)
    client._status.capabilities = {"tool_features": features or {}}
    return client


RANK_FEATURES = {"rank_factor_candidates": ["async_mode", "latest_price"]}
BATCH_FEATURES = {"get_risk_announcements": ["batch_ts_codes", "flat_rows"]}


def test_supports_tool_feature():
    client = _client(lambda n, a: {}, features=RANK_FEATURES)
    assert client.supports_tool_feature("rank_factor_candidates", "async_mode") is True
    assert client.supports_tool_feature("rank_factor_candidates", "nope") is False
    assert client.supports_tool_feature("get_risk_announcements", "batch_ts_codes") is False


def test_rank_sync_path_when_feature_absent():
    def handler(name, arguments):
        assert name == "rank_factor_candidates"
        assert "async_mode" not in arguments
        return {"status": "success", "rows": [{"ts_code": "600519.SH"}]}

    client = _client(handler, features={})
    payload = asyncio.run(
        client.rank_factor_candidates(universe_index="000300.SH", trade_date="20260729")
    )
    assert payload["rows"][0]["ts_code"] == "600519.SH"


def test_rank_none_response_becomes_typed_transport_error():
    client = _client(lambda n, a: {}, features={})

    async def no_response(name, arguments):
        return None

    client._call_tool = no_response
    payload = asyncio.run(
        client.rank_factor_candidates(universe_index="000300.SH", trade_date="20260729")
    )
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "mcp_transport_failure"


def test_cross_task_session_uses_isolated_transport():
    async def scenario():
        client = _client(lambda n, a: {"status": "success"}, features={})
        owner_gate = asyncio.Event()
        owner = asyncio.create_task(owner_gate.wait())
        client._session_owner_task = owner
        calls = []

        async def isolated(name, arguments):
            calls.append((name, arguments))
            return {"status": "success", "rows": []}

        client._call_tool_isolated = isolated
        try:
            payload = await client._call_tool("rank_factor_candidates", {"x": 1})
        finally:
            owner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await owner
        return payload, calls

    payload, calls = asyncio.run(scenario())
    assert payload["status"] == "success"
    assert calls == [("rank_factor_candidates", {"x": 1})]


def test_rank_async_path_polls_progress_and_unwraps_envelope():
    status_calls = {"n": 0}

    def handler(name, arguments):
        if name == "rank_factor_candidates":
            assert arguments["async_mode"] is True
            return {"status": "accepted", "job_id": "j1"}
        if name == "get_job_status":
            status_calls["n"] += 1
            if status_calls["n"] == 1:
                return {
                    "status": "running",
                    "progress": {"stage": "factor_compute", "progress_pct": 40},
                }
            return {"status": "succeeded", "progress": {"stage": "done", "progress_pct": 100}}
        if name == "get_job_result":
            return {
                "job_id": "j1",
                "status": "succeeded",
                "result": {
                    "status": "success",
                    "rows": [{"ts_code": "600519.SH", "latest_price": 1688.0}],
                    "warnings": ["inner"],
                },
                "warnings": ["envelope"],
            }
        raise AssertionError(f"unexpected tool {name}")

    updates = []

    async def on_progress(update):
        updates.append(update)

    client = _client(handler, features=RANK_FEATURES)
    payload = asyncio.run(
        client.rank_factor_candidates(
            universe_index="000300.SH",
            trade_date="20260729",
            progress_callback=on_progress,
        )
    )
    assert payload["rows"][0]["latest_price"] == 1688.0
    # Envelope warnings are merged alongside the tool's own warnings.
    assert payload["warnings"] == ["inner", "envelope"]
    assert [u["stage"] for u in updates] == ["factor_compute", "done"]


def test_rank_async_submit_without_job_id_returns_payload_as_sync():
    def handler(name, arguments):
        assert name == "rank_factor_candidates"
        return {"status": "success", "rows": [{"ts_code": "000001.SZ"}]}

    client = _client(handler, features=RANK_FEATURES)
    payload = asyncio.run(
        client.rank_factor_candidates(universe_index="000300.SH", trade_date="20260729")
    )
    assert payload["rows"][0]["ts_code"] == "000001.SZ"


def test_rank_async_job_failure_maps_to_error_envelope():
    def handler(name, arguments):
        if name == "rank_factor_candidates":
            return {"status": "accepted", "job_id": "j2"}
        if name == "get_job_status":
            return {"status": "failed", "error": "boom"}
        raise AssertionError(f"unexpected tool {name}")

    client = _client(handler, features=RANK_FEATURES)
    payload = asyncio.run(
        client.rank_factor_candidates(universe_index="000300.SH", trade_date="20260729")
    )
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "mcp_job_failed"
    assert payload["rows"] == []


def test_unwrap_job_result_passthrough_for_non_envelope():
    assert _unwrap_job_result({"status": "success", "rows": []}) == {
        "status": "success",
        "rows": [],
    }
    assert _unwrap_job_result(None) is None


def test_risk_batch_requires_feature_declaration():
    client = _client(lambda n, a: {"status": "success", "rows": []}, features={})
    assert (
        asyncio.run(client.get_risk_announcements_batch(["600519.SH"], "2026-01-01", "2026-07-30"))
        is None
    )

    def handler(name, arguments):
        assert name == "get_risk_announcements"
        assert arguments["ts_codes"] == ["600519.SH", "300750.SZ"]
        return {"status": "success", "rows": [{"ts_code": "600519.SH", "title": "减持"}]}

    client = _client(handler, features=BATCH_FEATURES)
    payload = asyncio.run(
        client.get_risk_announcements_batch(
            ["600519.SH", "300750.SZ"], "2026-01-01", "2026-07-30", keywords=["减持"]
        )
    )
    assert payload["rows"][0]["ts_code"] == "600519.SH"


def test_normalize_quant_candidate_carries_latest_price_and_pct_chg():
    row = {
        "ts_code": "300308.SZ",
        "quant_score": 85.4,
        "latest_price": 951.0,
        "pct_chg": 4.74,
    }
    candidate = normalize_quant_candidate(row)
    assert candidate["latest_price"] == 951.0
    assert candidate["close"] == 951.0
    assert candidate["pct_chg"] == 4.74


def test_drive_with_progress_streams_updates_then_result():
    async def scenario():
        async def work(on_progress):
            await on_progress({"stage": "prefilter", "progress_pct": 10})
            await on_progress({"stage": "factor_compute", "progress_pct": 60})
            return "final"

        events = []
        async for kind, value in drive_with_progress(work):
            events.append((kind, value))
        return events

    events = asyncio.run(scenario())
    assert events[-1] == ("result", "final")
    progress = [value for kind, value in events if kind == "progress"]
    assert [p["stage"] for p in progress] == ["prefilter", "factor_compute"]


def test_rank_progress_detail_formats_stage_and_pct():
    detail = rank_progress_detail(
        {"stage": "factor_compute", "progress_pct": 62, "detail": "processed 496/800"}
    )
    assert detail == "因子计算 · 62% · processed 496/800"
    assert rank_progress_detail({"stage": "unknown_stage"}) == "unknown_stage"


class _FakeRiskClient:
    """Risk-monitor fake exposing both batch and per-symbol scan methods."""

    def __init__(self, batch_payload=None, batch_returns_none=False):
        self.batch_payload = batch_payload
        self.batch_returns_none = batch_returns_none
        self.batch_calls = []
        self.single_calls = []

    async def get_risk_announcements_batch(self, ts_codes, start, end, keywords=None):
        self.batch_calls.append(list(ts_codes))
        if self.batch_returns_none:
            return None
        return self.batch_payload

    async def get_risk_announcements(self, ts_code, start, end, keywords=None):
        self.single_calls.append(ts_code)
        return {"status": "success", "rows": [], "warnings": []}


def _run_scan(monkeypatch, client):
    # ``from ... import skill`` would grab the module-level RiskMonitorSkill
    # instance (same name); resolve the module object itself instead.
    import importlib

    risk_skill = importlib.import_module("tradingagents.skills.risk_monitor.skill")

    async def fake_get_client(config):
        return client

    monkeypatch.setattr(risk_skill, "get_mcp_client", fake_get_client)
    monkeypatch.setattr(
        "tradingagents.core.portfolio_prices.resolve_portfolio_name", lambda symbol: symbol
    )
    holdings = [{"symbol": "600519.SH"}, {"symbol": "300750.SZ"}]
    return asyncio.run(_scan_risks(holdings, RiskMonitorInput(), {}))


def test_scan_risks_batch_groups_rows_by_symbol(monkeypatch):
    client = _FakeRiskClient(
        batch_payload={
            "status": "success",
            "rows": [
                {"ts_code": "600519.SH", "title": "股东减持计划"},
                {"ts_code": "600519.SH", "title": "收到问询函"},
            ],
            "warnings": ["partial coverage"],
        }
    )
    risks, mcp_used = _run_scan(monkeypatch, client)

    assert mcp_used is True
    assert client.batch_calls == [["600519.SH", "300750.SZ"]]
    assert client.single_calls == []
    by_symbol = {risk["symbol"]: risk for risk in risks}
    assert len(by_symbol["600519.SH"]["announcements"]) == 2
    assert by_symbol["600519.SH"]["level"] == "orange"
    assert by_symbol["300750.SZ"]["level"] == "green"
    assert by_symbol["300750.SZ"]["warnings"] == ["partial coverage"]


def test_scan_risks_falls_back_to_per_symbol_loop(monkeypatch):
    client = _FakeRiskClient(batch_returns_none=True)
    risks, mcp_used = _run_scan(monkeypatch, client)

    assert mcp_used is True
    assert client.single_calls == ["600519.SH", "300750.SZ"]
    assert all(risk["level"] == "green" for risk in risks)
