"""StockManager MCP transport-contract tests."""

import asyncio
from types import SimpleNamespace

from tradingagents.core.mcp_client import MCPConfig, StockManagerMCPClient


class _Session:
    def __init__(self, result=None, delay=0.0):
        self.result = result
        self.delay = delay

    async def call_tool(self, name, arguments):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def _client(result=None, *, timeout=0.1, delay=0.0):
    client = StockManagerMCPClient(MCPConfig(tool_timeout=timeout))
    client._connected = True
    client._session = _Session(result, delay)
    return client


def _result(text: str, *, is_error=False):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        isError=is_error,
    )


def test_contract_accepts_json_object_and_list():
    assert asyncio.run(_client(_result('{"status":"success","rows":[]}'))._call_tool("rank", {}))["status"] == "success"
    assert asyncio.run(_client(_result("[]"))._call_tool("rank", {})) == []


def test_contract_wraps_mcp_error_and_invalid_json():
    error = asyncio.run(_client(_result("backend failed", is_error=True))._call_tool("rank", {}))
    invalid = asyncio.run(_client(_result("not-json"))._call_tool("rank", {}))

    assert error["error"]["code"] == "mcp_tool_error"
    assert error["rows"] == []
    assert invalid["error"]["code"] == "invalid_json"


def test_contract_rejects_scalar_json():
    payload = asyncio.run(_client(_result("42"))._call_tool("rank", {}))
    assert payload["error"]["code"] == "invalid_payload_type"


def test_contract_timeout_marks_session_for_reconnect():
    client = _client(_result("{}"), timeout=0.01, delay=0.05)
    assert asyncio.run(client._call_tool("slow", {})) is None
    assert client.is_connected is False


# --- Async job lifecycle contract (submit -> poll status -> fetch result) ------


def test_async_job_methods_route_through_contract():
    """run_backtest / get_job_status / get_job_result all parse JSON payloads
    through the shared _call_tool contract (so recovery gets typed dicts)."""
    submitted = asyncio.run(
        _client(_result('{"job_id":"job-1","status":"queued"}')).run_backtest(
            strategy="ff_residual", config="prod", start="2024-01-01", end="2024-06-30"
        )
    )
    assert submitted["job_id"] == "job-1"
    status = asyncio.run(
        _client(_result('{"status":"completed","job_id":"job-1"}')).get_job_status("job-1")
    )
    assert status["status"] == "completed"
    result = asyncio.run(
        _client(_result('{"total_return":0.1,"sharpe":1.0}')).get_job_result("job-1")
    )
    assert result["sharpe"] == 1.0


def test_async_job_status_preserves_error_envelope():
    """A backend error on a job poll surfaces as the standard error envelope
    (never raises) so route recovery can mark the backtest failed."""
    status = asyncio.run(
        _client(_result("job backend exploded", is_error=True)).get_job_status("job-1")
    )
    assert status["error"]["code"] == "mcp_tool_error"


def test_async_job_result_rejects_invalid_json():
    payload = asyncio.run(_client(_result("not-json-at-all")).get_job_result("job-1"))
    assert payload["error"]["code"] == "invalid_json"


def test_async_job_poll_timeout_marks_reconnect():
    """A stuck job poll times out to None and flags the session for reconnect,
    so a later poll re-establishes the MCP session instead of hanging."""
    client = _client(_result('{"status":"running"}'), timeout=0.01, delay=0.05)
    assert asyncio.run(client.get_job_status("job-1")) is None
    assert client.is_connected is False


def test_run_ablation_study_routes_through_contract():
    result = asyncio.run(
        _client(_result('{"job_id":"ab-1","status":"queued"}')).run_ablation_study(
            {"strategy": "ff"}, [{"disable": "momentum"}]
        )
    )
    assert result["job_id"] == "ab-1"
