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
