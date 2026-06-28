"""Phase 2 chat routing integration tests."""

from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager


def test_ws_chat_routes_to_market_scanner_and_streams_result(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "chat.db"))
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
