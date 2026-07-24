"""Tests for scripts/accumulate_eval_samples.py (both tracks, isolation)."""

import argparse
import asyncio
import tempfile
from pathlib import Path

import pytest

import scripts.accumulate_eval_samples as acc
from tradingagents.core.persistence import Database
from tradingagents.core.reflection_enroll import BACKTEST_EVAL_SOURCE_TYPE


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "acc.db")


def _args(**over):
    base = {
        "track": "daily", "start": "2024-01-02", "end": "2024-01-02",
        "universe": "000906.SH", "style": "medium_term", "horizon": 5,
        "mode": "quant_only", "sample_k": 20, "llm_sample": 0,
        "limit": 10, "candidate_limit": 200, "seed": 42,
        "benchmark": "000300.SH", "db_path": "", "dry_run": False,
        "extract_lessons": False,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _rows(symbols):
    return [
        {
            "ts_code": s, "name": f"N{i}", "quant_score": 40 + i * 10,
            "factor_scores": {"momentum": 60 + i, "quality": 55},
            "tradability": {"is_tradable": True}, "risk_flags": [],
        }
        for i, s in enumerate(symbols)
    ]


class FakeMCPClient:
    def __init__(self, rows, constituents=None):
        self._rows = rows
        self._constituents = constituents or []

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def rank_factor_candidates(self, **kwargs):
        return {"rows": self._rows}

    async def get_index_constituents(self, index_code, trade_date):
        return {"rows": [{"ts_code": s} for s in self._constituents]}


async def _fake_outcome(engine, symbol, signal_date, horizon, benchmark_symbol):
    return {"actual_return": 0.03, "excess_return": 0.01, "horizon_days": horizon}


def test_daily_track_enrolls_eval_cases_without_decision_records(db, monkeypatch):
    monkeypatch.setattr(acc, "fetch_eval_outcome", _fake_outcome)
    symbols = ["600000.SH", "600001.SH", "600002.SH"]
    client = FakeMCPClient(_rows(symbols))

    stats = asyncio.run(acc.run_daily_track(client, db, None, _args(limit=3)))

    assert stats["enrolled"] == 3
    assert stats["reflected"] == 3
    eval_cases = db.list_reflection_cases(source_type=BACKTEST_EVAL_SOURCE_TYPE, limit=100)
    assert len(eval_cases) == 3
    assert all(c["status"] == "reflected" for c in eval_cases)
    assert all(c["snapshot_payload"].get("investment_style") == "medium_term" for c in eval_cases)
    # SAFETY INVARIANT: eval accumulation never touches the production ledger.
    assert db.list_decision_records(limit=100) == []


def test_single_track_random_cross_section_isolated(db, monkeypatch):
    monkeypatch.setattr(acc, "fetch_eval_outcome", _fake_outcome)
    symbols = ["600000.SH", "600001.SH", "600002.SH", "600003.SH"]
    client = FakeMCPClient(_rows(symbols), constituents=symbols)

    args = _args(track="single", sample_k=4, llm_sample=0)
    rng = __import__("random").Random(args.seed)
    stats = asyncio.run(acc.run_single_track(client, db, None, args, rng, {}))

    assert stats["enrolled"] == 4
    assert stats["reflected"] == 4
    assert stats["llm_enrolled"] == 0
    eval_cases = db.list_reflection_cases(source_type=BACKTEST_EVAL_SOURCE_TYPE, limit=100)
    assert len(eval_cases) == 4
    assert db.list_decision_records(limit=100) == []


def test_single_track_llm_subsample_records_confidence(db, monkeypatch):
    monkeypatch.setattr(acc, "fetch_eval_outcome", _fake_outcome)

    async def fake_llm(base_config, ticker, analysis_date):
        return {"rating": "Buy", "confidence": 72.0}

    monkeypatch.setattr(acc, "analyze_with_llm", fake_llm)

    symbols = ["600000.SH", "600001.SH", "600002.SH"]
    client = FakeMCPClient(_rows(symbols), constituents=symbols)
    args = _args(track="single", sample_k=3, llm_sample=1)
    rng = __import__("random").Random(args.seed)

    stats = asyncio.run(acc.run_single_track(client, db, None, args, rng, {}))

    assert stats["llm_enrolled"] == 1
    eval_cases = db.list_reflection_cases(source_type=BACKTEST_EVAL_SOURCE_TYPE, limit=100)
    with_conf = [c for c in eval_cases if c["snapshot_payload"].get("llm_confidence") == 72.0]
    assert len(with_conf) == 1
    assert db.list_decision_records(limit=100) == []


def test_llm_subset_distills_candidate_lesson_without_breaking_isolation(db, monkeypatch):
    """--extract-lessons turns the LLM subset into candidate (non-active) lessons.

    The reflection engine's neutral lesson path fires for the LLM sample, writing
    an attribution_payload and a strategy lesson that stays governance=candidate /
    active=False — reflection material, never auto-fed to production. Eval cases
    remain non-eligible and the production ledger stays untouched.
    """
    from tradingagents.core.reflection import ReflectionEngine

    monkeypatch.setattr(acc, "fetch_eval_outcome", _fake_outcome)

    async def fake_llm(base_config, ticker, analysis_date):
        return {"rating": "Hold", "confidence": 55.0}

    monkeypatch.setattr(acc, "analyze_with_llm", fake_llm)

    engine = ReflectionEngine(db, {})

    async def fake_attr(case, outcome, post_signal_evidence):
        return {
            "attribution": "missed_upside",
            "confidence": "high",
            "strategy_lesson": "观望过于保守，漏掉正向机会。",
            "suggested_adjustment": "复核过滤/降级条件。",
            "missed_evidence": [],
        }

    monkeypatch.setattr(engine, "generate_attribution", fake_attr)

    symbols = ["600000.SH", "600001.SH", "600002.SH"]
    client = FakeMCPClient(_rows(symbols), constituents=symbols)
    args = _args(track="single", sample_k=3, llm_sample=1, extract_lessons=True)
    rng = __import__("random").Random(args.seed)

    stats = asyncio.run(acc.run_single_track(client, db, engine, args, rng, {}))

    assert stats["llm_enrolled"] == 1
    assert stats["lessons"] == 1
    # A candidate (non-active) lesson was persisted as reflection material.
    active = db.list_strategy_lessons(limit=100, active_only=True)
    all_lessons = db.list_strategy_lessons(limit=100, active_only=False)
    assert active == []
    assert len(all_lessons) == 1
    lesson = all_lessons[0]
    assert lesson["active"] is False
    assert lesson["governance_status"] == "candidate"
    assert lesson["lesson_type"] == "opportunity_cost"
    # The attributed case carries the attribution_payload; eval stays non-eligible.
    eval_cases = db.list_reflection_cases(source_type=BACKTEST_EVAL_SOURCE_TYPE, limit=100)
    attributed = [c for c in eval_cases if c["attribution_payload"].get("attribution") == "missed_upside"]
    assert len(attributed) == 1
    assert all(c["eligible_for_strategy_learning"] is False for c in eval_cases)
    # SAFETY INVARIANT: production ledger untouched.
    assert db.list_decision_records(limit=100) == []


def test_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(acc, "fetch_eval_outcome", _fake_outcome)
    client = FakeMCPClient(_rows(["600000.SH", "600001.SH"]))

    stats = asyncio.run(acc.run_daily_track(client, db, None, _args(limit=2, dry_run=True)))

    assert stats["enrolled"] == 2  # counted, but not persisted
    assert db.list_reflection_cases(source_type=BACKTEST_EVAL_SOURCE_TYPE, limit=100) == []
    assert db.list_decision_records(limit=100) == []


def test_constituent_symbols_parses_flat_list_and_dict_rows():
    # Real StockManager shape: a flat ``constituents`` list of ts_code strings.
    flat = {"constituents": ["600519.SH", "601318.sh", "", "000001.SZ"]}
    assert acc._constituent_symbols(flat) == ["600519.SH", "601318.SH", "000001.SZ"]
    # Dict-row fallback (older / other shapes).
    rows = {"rows": [{"ts_code": "600000.SH"}, {"con_code": "600001.SH"}]}
    assert acc._constituent_symbols(rows) == ["600000.SH", "600001.SH"]
    assert acc._constituent_symbols(None) == []
    assert acc._constituent_symbols({"constituents": []}) == []


def test_confidence_from_conclusion_prefers_numeric_then_rating_proxy():
    # A model-emitted numeric confidence always wins.
    assert acc._confidence_from_conclusion({"rating": "Buy", "confidence": 66}) == 66.0
    # No numeric confidence -> monotone rating proxy (Buy > Hold > Sell).
    assert acc._confidence_from_conclusion({"rating": "Buy"}) == 80.0
    assert acc._confidence_from_conclusion({"rating": "hold"}) == 50.0
    assert acc._confidence_from_conclusion({"rating": "Sell"}) == 20.0
    # Unknown / missing rating and no confidence -> None (sample simply skipped).
    assert acc._confidence_from_conclusion({"rating": "???"}) is None
    assert acc._confidence_from_conclusion({}) is None
