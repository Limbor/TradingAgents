"""Tests for the third batch of P1/P2 bug fixes.

Covers:
- WebSocket queue backpressure (bounded + drop-oldest) and cancel_all awaiting
- Holding price refresh no longer races with user edits (update_holding_price)
- OHLCV cache no longer keyed by date (single file per code, gap fill)
- RiskMonitor keyword-severity grading (no more pure-count misclassification)
"""

import asyncio
import os

import pandas as pd

from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager
from tradingagents.skills.risk_monitor.skill import _classify_risk_rows

# ---------------------------------------------------------------------------
# Fix A: WebSocket backpressure + cancel_all
# ---------------------------------------------------------------------------


def test_subscribe_queue_is_bounded():
    rm = RunManager()
    q = rm.subscribe("run-x")
    assert q.maxsize == 256


def test_broadcast_drops_oldest_when_full():
    """A full queue must drop the oldest event rather than block the producer."""

    async def run():
        rm = RunManager()
        q = rm.subscribe("run-x")
        for i in range(q.maxsize):
            q.put_nowait({"seq": i})
        assert q.full()

        await rm._broadcast("run-x", {"seq": 999})

        first = q.get_nowait()
        assert first["seq"] != 0  # oldest was dropped
        contents = []
        while not q.empty():
            contents.append(q.get_nowait())
        assert 999 in [c["seq"] for c in contents]

    asyncio.run(run())


def test_cancel_all_awaits_tasks():
    """cancel_all must wait for cancelled tasks to finish, not return instantly."""

    cleaned_up = {"done": False}

    async def slow_skill():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cleaned_up["done"] = True
            raise

    class _FakeSkill:
        async def cancel(self):
            pass

    class _FakeRun:
        def __init__(self, task):
            self._task = task
            self._skill = _FakeSkill()

    async def runner():
        rm = RunManager()
        task = asyncio.create_task(slow_skill())
        rm._runs["r1"] = _FakeRun(task)
        await asyncio.sleep(0.01)  # let the task start
        await asyncio.wait_for(rm.cancel_all(), timeout=5.0)
        assert cleaned_up["done"] is True
        assert task.done()

    asyncio.run(runner())


# ---------------------------------------------------------------------------
# Fix B: holding price refresh race
# ---------------------------------------------------------------------------


def test_update_holding_price_only_touches_price(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_holding(symbol="600519.SH", quantity=100, avg_cost=1800.0, notes="core")
    # Concurrent user edit to quantity after the refresher read the row.
    db.upsert_holding(symbol="600519.SH", quantity=200, avg_cost=1800.0, notes="core-edited")
    # Refresher updates ONLY the price.
    updated = db.update_holding_price("600519.SH", 1850.0)
    assert updated is True
    holding = db.get_holding("600519.SH")
    # Quantity/notes preserved — the old upsert_holding path could overwrite
    # them with stale values read before the user edit.
    assert holding["quantity"] == 200
    assert holding["notes"] == "core-edited"
    assert holding["current_price"] == 1850.0


def test_update_holding_price_missing_symbol_returns_false(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.update_holding_price("999999.SH", 10.0) is False


# ---------------------------------------------------------------------------
# Fix C: OHLCV cache no longer fragmented by date
# ---------------------------------------------------------------------------


def test_akshare_cache_file_is_per_code_not_per_date(tmp_path, monkeypatch):
    """Two different curr_dates must hit the same cache file (keyed by code only)."""
    import tradingagents.dataflows.akshare_stock as ak

    monkeypatch.setattr(ak, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(ak, "normalize_for_akshare", lambda s: "600519")

    download_calls = {"count": 0}

    def fake_download(code, start, end):
        download_calls["count"] += 1
        dates = pd.date_range(start=start, end=end, freq="D")
        return pd.DataFrame({"Date": dates, "Close": [10.0] * len(dates)})

    monkeypatch.setattr(ak, "_download_ak_ohlcv", fake_download)

    ak.load_ohlcv_cn("600519.SH", "2026-07-01")
    ak.load_ohlcv_cn("600519.SH", "2026-07-02")

    files = os.listdir(tmp_path)
    ak_files = [f for f in files if f.startswith("600519-AKShare")]
    assert len(ak_files) == 1, f"expected one cache file, got {ak_files}"
    assert ak_files[0] == "600519-AKShare-data.csv"


def test_akshare_cache_reuses_when_covering_curr_date(tmp_path, monkeypatch):
    """When the cache already covers curr_date, no download should happen."""
    import tradingagents.dataflows.akshare_stock as ak

    monkeypatch.setattr(ak, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(ak, "normalize_for_akshare", lambda s: "600519")

    download_calls = {"count": 0}

    def fake_download(code, start, end):
        download_calls["count"] += 1
        # Return data up to 2026-07-05 so curr_date=2026-07-03 is covered.
        dates = pd.date_range(start="2021-07-01", end="2026-07-05", freq="D")
        return pd.DataFrame({"Date": dates, "Close": [10.0] * len(dates)})

    monkeypatch.setattr(ak, "_download_ak_ohlcv", fake_download)

    ak.load_ohlcv_cn("600519.SH", "2026-07-03")
    first_calls = download_calls["count"]
    ak.load_ohlcv_cn("600519.SH", "2026-07-03")  # same curr_date → cache hit
    assert download_calls["count"] == first_calls  # no re-download


def test_tushare_cache_file_is_per_code_not_per_date(tmp_path, monkeypatch):
    import tradingagents.dataflows.tushare_stock as ts

    monkeypatch.setattr(ts, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(ts, "normalize_for_tushare", lambda s: "600519.SH")

    def fake_download(code_ts, start, end):
        dates = pd.date_range(start=start, end=end, freq="D")
        return pd.DataFrame({"Date": dates, "Close": [10.0] * len(dates)})

    monkeypatch.setattr(ts, "_download_ts_ohlcv", fake_download)

    ts.load_ohlcv_ts("600519.SH", "2026-07-01")
    ts.load_ohlcv_ts("600519.SH", "2026-07-02")

    files = os.listdir(tmp_path)
    ts_files = [f for f in files if f.startswith("600519.SH-TS")]
    assert len(ts_files) == 1, f"expected one cache file, got {ts_files}"
    assert ts_files[0] == "600519.SH-TS-data.csv"


# ---------------------------------------------------------------------------
# Fix D: RiskMonitor keyword-severity grading
# ---------------------------------------------------------------------------


def test_risk_classifier_critical_keyword_is_red():
    lvl, _ = _classify_risk_rows([{"title": "收到立案调查通知书"}], ["立案", "问询"])
    assert lvl == "red"


def test_risk_classifier_moderate_keyword_is_orange():
    lvl, _ = _classify_risk_rows([{"title": "股东减持计划"}], ["立案", "问询", "减持"])
    assert lvl == "orange"


def test_risk_classifier_dedups_duplicate_announcements():
    """Three identical 减持 announcements must dedup to one (orange), not red."""
    rows = [{"title": "股东减持公告"}, {"title": "股东减持公告"}, {"title": "股东减持公告"}]
    lvl, msg = _classify_risk_rows(rows, ["减持"])
    assert lvl == "orange"
    assert "1 matched" in msg or "moderate" in msg.lower()


def test_risk_classifier_empty_is_green():
    lvl, _ = _classify_risk_rows([], ["立案"])
    assert lvl == "green"


def test_risk_classifier_count_fallback_for_generic_keywords():
    """When no severity-mapped keyword matches but many rows exist, fall back
    to count-based red to preserve the original conservative behavior."""
    rows = [{"title": "通知一"}, {"title": "通知二"}, {"title": "通知三"}]
    lvl, _ = _classify_risk_rows(rows, ["通知"])
    assert lvl == "red"
