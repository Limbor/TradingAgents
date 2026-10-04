"""Memory versions, frozen examples, governance and isolated paired replay."""
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.api.routes.reflections import router
from tradingagents.core.cross_symbol_pattern_miner import CrossSymbolPatternMiner, PatternBucket
from tradingagents.core.memory_evaluation import evaluate_memory_pairs
from tradingagents.core.persistence import Database
from tradingagents.core.strategy_memory import load_strategy_lessons, select_strategy_lessons


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = datetime(2026, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)

    monkeypatch.setattr("tradingagents.core.persistence.datetime", Clock)
    return lambda day: setattr(Clock, "value", datetime.fromisoformat(day).replace(tzinfo=timezone.utc))


def save(db, finding="旧经验", **kwargs):
    db.save_strategy_lesson("lesson", "signal_quality", "industry", finding, target="医药生物", **kwargs)


def retrieve(db, day):
    return select_strategy_lessons(load_strategy_lessons(db, day), {"industry": "医药生物"}, as_of_date=day)


def test_cutoff_reconstructs_approval_update_and_retirement(tmp_path, clock):
    db = Database(tmp_path / "memory.db")
    save(db, active=False)
    clock("2026-01-10")
    assert db.approve_strategy_lesson("lesson")
    clock("2026-01-20")
    db.update_strategy_lesson("lesson", finding="新经验", active=False, governance_status="candidate")
    clock("2026-01-22")
    assert db.approve_strategy_lesson("lesson")
    clock("2026-01-25")
    assert db.deactivate_strategy_lesson("lesson")
    assert retrieve(db, "2026-01-05") == []
    assert retrieve(db, "2026-01-15")[0]["finding"] == "旧经验"
    assert retrieve(db, "2026-01-21") == []
    assert retrieve(db, "2026-01-23")[0]["finding"] == "新经验"
    assert retrieve(db, "2026-01-30") == []
    versions = db.list_strategy_lesson_versions("lesson")
    assert len(versions) == 5
    assert len(Database(tmp_path / "memory.db").list_strategy_lesson_versions("lesson")) == 5


def test_existing_database_backfills_only_last_known_version(tmp_path, clock):
    path = tmp_path / "legacy.db"
    db = Database(path)
    save(db)
    clock("2026-01-20")
    save(db, "最新内容")
    assert db.get_strategy_lesson("lesson")["created_at"].startswith("2026-01-01")
    with db._conn() as conn:
        conn.execute("DROP TRIGGER lesson_version_insert")
        conn.execute("DROP TRIGGER lesson_version_update")
        conn.execute("DROP TABLE strategy_lesson_versions")
    db = Database(path)
    assert len(db.list_strategy_lesson_versions("lesson")) == 1
    assert retrieve(db, "2026-01-15") == []
    assert retrieve(db, "2026-01-25")[0]["finding"] == "最新内容"


def test_approval_freezes_examples_even_after_case_update_or_pruning(tmp_path, clock):
    db = Database(tmp_path / "memory.db")
    clock("2026-01-05")
    for cid, correct in (("win", True), ("loss", False)):
        db.save_reflection_case(cid, "system_signal", "symbol", True, "600000.SH", "2026-01-01",
                                status="reflected", outcome_payload={"was_correct": correct, "actual_return": 0.1 if correct else -0.1},
                                attribution_payload={"strategy_lesson": "当时经验"})
    clock("2026-01-10")
    save(db, active=False, payload={"case_id": "win", "evidence_cases": ["loss"]})
    assert db.approve_strategy_lesson("lesson")
    clock("2026-02-01")
    db.update_reflection_case("win", outcome_payload={"actual_return": 9})
    with db._conn() as conn:
        conn.execute("DELETE FROM reflection_cases WHERE id = 'loss'")
    selected = retrieve(db, "2026-01-15")
    assert [row["outcome"] for row in selected[0]["examples"]] == ["correct", "incorrect"]
    assert selected[0]["examples"][0]["actual_return"] == 0.1
    assert retrieve(db, "2026-01-04") == []


def test_miner_does_not_inflate_evidence_or_silently_change_approved_content(tmp_path):
    db = Database(tmp_path / "mine.db")
    miner = CrossSymbolPatternMiner(db, {})
    bucket = PatternBucket(dimension="industry=医药生物", samples=[], n=5, scored_n=5,
                           correct_count=4, win_rate=0.8, baseline_win_rate=0.5,
                           lift=0.3, avg_return=0.05)
    result = {"lessons_created": 0, "lessons_updated": 0}
    first = miner._save_or_update_lesson(bucket, "finding", "adjustment", result)
    second = miner._save_or_update_lesson(bucket, "finding", "adjustment", result)
    assert second["evidence_count"] == db.get_strategy_lesson(first["id"])["evidence_count"] == 5
    assert db.approve_strategy_lesson(first["id"])
    unchanged = miner._save_or_update_lesson(bucket, "finding", "adjustment", result)
    assert unchanged["active"]
    changed = miner._save_or_update_lesson(bucket, "changed finding", "adjustment", result)
    assert not changed["active"]
    assert changed["governance_status"] == "candidate"


@pytest.mark.asyncio
async def test_paired_replay_never_sends_outcomes_and_excludes_future_memory(tmp_path, clock):
    db = Database(tmp_path / "replay.db")
    save(db)
    clock("2026-02-01")
    db.update_strategy_lesson("lesson", finding="未来经验")
    calls = []

    class Reviewer:
        async def review(self, candidate, strategy_lessons):
            calls.append((candidate, strategy_lessons))
            return {"llm_view": "positive" if strategy_lessons else "negative",
                    "memory_usage": [{"lesson_id": "lesson", "status": "referenced", "reason": "同行业"}]}

    samples = [{"trade_date": "2026-01-15", "snapshot_as_of": "2026-01-15",
                "candidate": {"symbol": "600000.SH", "industry": "医药生物", "excess_return": 999,
                              "memory_trace": {"snapshots": ["future"]}}, "excess_return": 0.05}]
    report = await evaluate_memory_pairs(samples, db, lambda *_: Reviewer())
    assert report["memory_pairs"] == 1
    assert report["mean_delta_signed_excess"] == pytest.approx(0.1)
    assert report["arms"]["without_memory"]["hit_rate"] == 0
    assert report["arms"]["with_memory"]["hit_rate"] == 1
    assert report["sufficient_samples"] is False
    assert all("excess_return" not in candidate and "memory_trace" not in candidate for candidate, _ in calls)
    assert calls[0][1] == []
    assert calls[1][1][0]["finding"] == "旧经验"
    assert db.list_reflection_cases() == []
    assert len(db.list_strategy_lesson_versions("lesson")) == 2
    with pytest.raises(ValueError, match="snapshot_as_of"):
        await evaluate_memory_pairs([{**samples[0], "snapshot_as_of": "2026-01-16"}], db, lambda *_: Reviewer())


def test_memory_routes_expose_single_case_versions_and_real_usage(tmp_path):
    db = Database(tmp_path / "api.db")
    db.save_reflection_case("case", "system_signal", "symbol", True, "600000.SH", "2026-01-01")
    save(db, payload={"case_id": "case"})
    with db._conn() as conn:
        conn.execute("INSERT INTO agent_conversations (id,title,created_at,updated_at) VALUES ('c','test','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO agent_tasks (id,conversation_id,goal,status,result_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     ("t", "c", "test", "completed", json.dumps({"memory_trace": {"injected_ids": ["lesson"], "usage": [
                         {"lesson_id": "lesson", "status": "referenced", "reason": "test"}]} }), "2026-01-01", "2026-01-01"))
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/strategy-lessons/lesson/cases").json()[0]["id"] == "case"
    assert len(client.get("/strategy-lessons/lesson/versions").json()) == 1
    overview = client.get("/strategy-memory/overview").json()
    assert overview["inventory"]["available"] == 1
    assert overview["usage"]["reported_tasks"] == 1
    assert overview["evaluation"] is None


@pytest.mark.asyncio
async def test_paired_replay_excludes_later_same_day_approvals(tmp_path, clock):
    db = Database(tmp_path / 'intraday.db')
    clock('2026-01-01T08:00:00')
    save(db, active=False)
    clock('2026-01-15T04:00:00')  # noon Shanghai approval
    db.approve_strategy_lesson('lesson')
    calls = []

    class Reviewer:
        async def review(self, candidate, strategy_lessons):
            calls.append(candidate)
            return {'llm_view': 'neutral'}

    sample = {'trade_date': '2026-01-15', 'snapshot_as_of': '2026-01-15',
              'candidate': {'symbol': '600000.SH', 'industry': '医药生物'}, 'excess_return': .1}
    early = await evaluate_memory_pairs([{**sample, 'snapshot_cutoff': '2026-01-15T03:00:00Z'}], db, lambda *_: Reviewer())
    assert early['total_pairs'] == 0 and calls == []
    late = await evaluate_memory_pairs([{**sample, 'snapshot_cutoff': '2026-01-15T05:00:00Z'}], db, lambda *_: Reviewer())
    assert late['memory_pairs'] == 1 and len(calls) == 2
    with pytest.raises(ValueError, match='snapshot_cutoff'):
        await evaluate_memory_pairs([{**sample, 'snapshot_cutoff': '2026-01-16T00:00:00Z'}], db, lambda *_: Reviewer())
