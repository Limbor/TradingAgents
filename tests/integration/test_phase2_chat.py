"""Phase 2 chat routing integration tests."""

import pytest
from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.chat_agent import ChatResponse
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager
from tradingagents.default_config import DEFAULT_CONFIG


class _FakeChatAgent:
    def __init__(self, response: ChatResponse):
        self.response = response

    async def handle(self, user_text, session_id, context=None):
        return self.response


@pytest.mark.parametrize(
    ("response", "expected_type", "payload_key", "expected_value"),
    [
        (ChatResponse(intent="chat_answer", content="WATCHLIST 表示继续观察。"), "chat_answer", "content", "WATCHLIST 表示继续观察。"),
        (
            ChatResponse(
                intent="tool_answer",
                tool_name="get_portfolio_summary",
                tool_result={"total": 2},
                tool_display="table",
            ),
            "tool_answer",
            "tool",
            "get_portfolio_summary",
        ),
        (
            ChatResponse(intent="clarify", clarify_question="请问要分析哪只股票？", clarify_options=["贵州茅台"]),
            "clarify",
            "question",
            "请问要分析哪只股票？",
        ),
    ],
)
def test_ws_chat_emits_free_chat_response_types(
    tmp_path, monkeypatch, response, expected_type, payload_key, expected_value
):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "chat-response.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    app = create_app()
    with TestClient(app) as client:
        client.app.state.chat_agent = _FakeChatAgent(response)
        client.app.state.orchestrator = Orchestrator(
            client.app.state.registry,
            client.app.state.config,
        )
        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({"message": "解释一下这个概念"})
            message = websocket.receive_json()

    assert message["type"] == expected_type
    assert message["payload"][payload_key] == expected_value


def test_ws_chat_rejects_untrusted_browser_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "chat-origin.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    app = create_app()
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/ws/chat", headers={"origin": "https://evil.example"}
        ) as websocket,
    ):
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert message["payload"]["message"] == "Unauthorized"


def test_ws_chat_routes_to_market_scanner_and_streams_result(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "chat.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    app = create_app()
    with TestClient(app) as client:
        db = Database(tmp_path / "chat.db")
        client.app.state.db = db
        client.app.state.run_manager = RunManager(db=db)
        client.app.state.orchestrator = Orchestrator(
            client.app.state.registry,
            client.app.state.config,
        )

        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({"message": "筛选A股 top 2 score 60"})
            messages = []
            while True:
                message = websocket.receive_json()
                messages.append(message)
                if message["type"] == "run_complete":
                    break

        assert messages[0]["type"] == "chat_reply"
        assert messages[0]["payload"]["skill_triggered"] == "market_scanner"
        assert any(message["type"] == "scanner_candidates" for message in messages)
        assert any(message["type"] == "report_chunk" for message in messages)
        run_id = messages[0]["payload"]["run_id"]
        assert db.get_run(run_id)["status"] == "completed"


def test_ws_chat_routes_portfolio_update_to_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "chat-portfolio.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    app = create_app()
    with TestClient(app) as client:
        db = Database(tmp_path / "chat-portfolio.db")
        client.app.state.db = db
        client.app.state.run_manager = RunManager(db=db)
        client.app.state.orchestrator = Orchestrator(
            client.app.state.registry,
            client.app.state.config,
        )

        with client.websocket_connect("/ws/chat") as websocket:
            websocket.send_json({"message": "添加持仓 601899.SH 100 18 20"})
            while True:
                message = websocket.receive_json()
                if message["type"] == "run_complete":
                    break

        holding = db.get_holding("601899.SH")
        assert holding is not None
        assert holding["quantity"] == 100
        assert holding["avg_cost"] == 18
