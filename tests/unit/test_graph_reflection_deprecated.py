"""Verify graph reflection is deprecated so core reflection owns the loop.

Prevents regression of the double-processing fix: ``_resolve_pending_entries``
must stay a no-op so it does not flip memory_log ``| pending]`` tags before
``core/reflection.py::run_reflection_batch`` can run the deep attribution +
strategy-lesson path on the same entries.
"""


def test_resolve_pending_entries_is_noop():
    import inspect
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    src = inspect.getsource(TradingAgentsGraph._resolve_pending_entries)
    assert "DEPRECATED" in src
    # Must not resolve entries synchronously anymore.
    assert "batch_update_with_outcomes" not in src
    assert "reflect_on_final_decision" not in src
    assert "get_pending_entries" not in src
