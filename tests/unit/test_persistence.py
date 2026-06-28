"""Unit tests for the persistence layer."""

import pytest
from pathlib import Path
import tempfile

from tradingagents.core.persistence import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield Database(db_path)


def test_save_and_get_run(db):
    db.save_run("run-1", "stock_analysis", {"ticker": "AAPL"}, "pending")
    run = db.get_run("run-1")
    assert run is not None
    assert run["skill_id"] == "stock_analysis"
    assert run["status"] == "pending"


def test_update_run_status(db):
    db.save_run("run-2", "stock_analysis", {}, "pending")
    db.update_run_status("run-2", "completed", completed_at="2026-06-17T12:00:00")
    run = db.get_run("run-2")
    assert run["status"] == "completed"
    assert run["completed_at"] == "2026-06-17T12:00:00"


def test_save_and_list_reports(db):
    db.save_report("r1", "run-1", "AAPL", "Buy", "Full report content", "/path/to/report")
    db.save_report("r2", "run-2", "MSFT", "Hold", "Another report", None)
    reports = db.list_reports()
    assert len(reports) == 2


def test_list_reports_by_ticker(db):
    db.save_report("r1", "run-1", "AAPL", "Buy", "Content", None)
    db.save_report("r2", "run-2", "MSFT", "Hold", "Content", None)
    reports = db.list_reports(ticker="AAPL")
    assert len(reports) == 1
    assert reports[0]["ticker"] == "AAPL"


def test_get_nonexistent_run(db):
    assert db.get_run("nonexistent") is None


def test_get_nonexistent_report(db):
    assert db.get_report("nonexistent") is None


def test_list_runs(db):
    db.save_run("r1", "s1", {}, "completed")
    db.save_run("r2", "s2", {}, "pending")
    runs = db.list_runs()
    assert len(runs) == 2
