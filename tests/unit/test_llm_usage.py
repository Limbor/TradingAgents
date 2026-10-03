"""Usage accounting must not fabricate free calls, prices, or cache hits."""
import pytest

from tradingagents.core.llm_usage import normalize_usage, summarize_usage


def record(run_id="call", *, usage=None, provider="qianwen", model="qwen3.8-max", kind="model", status="completed"):
    return {"run_id": run_id, "kind": kind, "role": "Fundamentals Analyst", "status": status,
            "provider": provider, "model": model, "usage": usage, "output": {}}


def receipt(**updates):
    return {"input_tokens": 10000, "output_tokens": 1000, "total_tokens": 11000,
            "cache_read_tokens": 8000, "cache_creation_tokens": 0, "reasoning_tokens": 100,
            "request_id": "api-call", **updates}


def test_parent_rows_and_duplicate_receipts_are_not_charged_twice():
    data = receipt()
    summary = summarize_usage([record(usage=data), record("host-link", usage=data),
                               record("workflow", kind="workflow", usage=data)])
    assert summary["model_calls"] == summary["reported_calls"] == 1
    assert summary["input_tokens"] == 10000
    assert summary["total_tokens"] == 11000  # cache and reasoning are subsets
    assert summary["cost_cny"] == pytest.approx(0.072)
    assert summary["cache_hit_rate"] == 0.8
    assert summary["cost_complete"]


def test_failed_parsing_still_has_usage_and_network_failure_remains_unknown():
    summary = summarize_usage([record(usage=receipt(), status="failed"),
                               record("network", status="failed"), record("active", status="running")])
    assert summary["reported_calls"] == summary["missing_calls"] == summary["pending_calls"] == 1
    assert summary["cost_cny"] == pytest.approx(0.072)
    assert summary["incomplete"] and not summary["cost_complete"]


def test_unknown_prices_and_unknown_cache_details_do_not_become_zero_cost():
    for data, provider in [(receipt(), "unknown"), (receipt(cache_read_tokens=None), "qianwen")]:
        summary = summarize_usage([record(usage=data, provider=provider)])
        assert summary["cost_cny"] is None
        assert summary["unpriced_calls"] == 1
        assert not summary["cost_complete"]


def test_legacy_raw_metadata_and_real_zero_usage_are_supported():
    data = {"response_metadata": {"id": "response-1", "token_usage": {
        "prompt_tokens": 10000, "completion_tokens": 1000,
        "prompt_tokens_details": {"cached_tokens": 8000},
        "completion_tokens_details": {"reasoning_tokens": 100}}}}
    assert normalize_usage(data)["cache_read_tokens"] == 8000
    assert normalize_usage(data)["reasoning_tokens"] == 100
    assert normalize_usage({"usage_metadata": {"input_tokens": 0, "output_tokens": 0}})["total_tokens"] == 0
    assert normalize_usage({"content": "no receipt"}) is None
    assert normalize_usage({"usage_metadata": {"input_tokens": -1, "output_tokens": 1}}) is None


def test_explicit_cache_creation_and_hit_use_the_correct_max_price():
    usage = normalize_usage({"response_metadata": {"token_usage": {
        "prompt_tokens": 10000, "completion_tokens": 1000,
        "prompt_tokens_details": {"cached_tokens": 8000, "cache_creation_input_tokens": 2000}}}})
    assert summarize_usage([record(usage=usage)])["cost_cny"] == pytest.approx(0.074)


def test_empty_tasks_do_not_claim_a_zero_price():
    summary = summarize_usage([])
    assert summary["model_calls"] == 0 and summary["cost_cny"] is None


def test_persisted_turn_and_conversation_totals_include_legacy_receipts(tmp_path):
    from tradingagents.core.agent_harness import AgentStore
    from tradingagents.core.persistence import Database

    db = Database(tmp_path / "usage.db")
    store = AgentStore(db)
    conversation = store.create_conversation("研究", None)
    first = store.create_task(conversation["id"], "分析")
    store.set_status(first["id"], "completed")
    second = store.create_task(conversation["id"], "再看基本面")
    for task, identity in [(first, "a"), (second, "b")]:
        row = record(identity, usage=receipt(request_id=identity))
        if task == first:
            row.pop("usage")
            row["output"] = {"usage_metadata": {"input_tokens": 10000, "output_tokens": 1000,
                              "input_token_details": {"cache_read": 8000}}, "id": identity}
        row["root_id"] = task["id"]
        db.save_agent_runtime(row)
    detail = AgentStore(Database(tmp_path / "usage.db")).conversation_detail(conversation["id"])
    assert detail["usage_stats"]["input_tokens"] == 20000
    assert detail["usage_stats"]["cost_cny"] == pytest.approx(0.144)
    assert all(task["usage_stats"]["input_tokens"] == 10000 for task in detail["tasks"])
