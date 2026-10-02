"""Unit tests for tradingagents.dataflows.interface market-aware routing.

Covers:
  - _infer_market based on ticker-first args + default_market fallback
  - get_vendor resolving strings vs market-keyed dicts
  - route_to_vendor picks primary vendor per market, falls back on
    rate-limit errors, and raises when all vendors fail.
"""
from __future__ import annotations

import pytest

from tradingagents.dataflows import interface as iface
from tradingagents.dataflows.akshare_common import AKShareRateLimitError


@pytest.fixture()
def patched_config(monkeypatch):
    """Swap config.get_config for a mutable dict owned by the test."""
    cfg = {
        "default_market": "us",
        "data_vendors": {
            "core_stock_apis": {"us": "yfinance", "cn_a": "akshare"},
            "news_data": "yfinance",  # string form applies to all markets
            "fundamental_data": {"us": "yfinance", "cn_a": "tushare"},
        },
        "tool_vendors": {},
    }
    monkeypatch.setattr(iface, "get_config", lambda: cfg)
    return cfg


@pytest.mark.unit
class TestResolveVendorValue:
    def test_string_applied_to_all_markets(self):
        assert iface._resolve_vendor_value("yfinance", "us") == "yfinance"
        assert iface._resolve_vendor_value("yfinance", "cn_a") == "yfinance"

    def test_dict_keyed_by_market(self):
        d = {"us": "yfinance", "cn_a": "akshare"}
        assert iface._resolve_vendor_value(d, "us") == "yfinance"
        assert iface._resolve_vendor_value(d, "cn_a") == "akshare"
        assert iface._resolve_vendor_value(d, "unknown") is None

    def test_none_returns_none(self):
        assert iface._resolve_vendor_value(None, "us") is None


@pytest.mark.unit
class TestGetVendor:
    def test_category_string(self, patched_config):
        assert iface.get_vendor("news_data", "get_news", "us") == "yfinance"
        assert iface.get_vendor("news_data", "get_news", "cn_a") == "yfinance"

    def test_category_dict(self, patched_config):
        assert iface.get_vendor("core_stock_apis", "get_stock_data", "us") == "yfinance"
        assert iface.get_vendor("core_stock_apis", "get_stock_data", "cn_a") == "akshare"

    def test_tool_override_beats_category(self, patched_config):
        patched_config["tool_vendors"]["get_stock_data"] = "alpha_vantage"
        assert iface.get_vendor("core_stock_apis", "get_stock_data", "us") == "alpha_vantage"

    def test_tool_override_market_dict(self, patched_config):
        patched_config["tool_vendors"]["get_stock_data"] = {"cn_a": "tushare"}
        # Tool override missing for US -> falls back to category default.
        assert iface.get_vendor("core_stock_apis", "get_stock_data", "us") == "yfinance"
        assert iface.get_vendor("core_stock_apis", "get_stock_data", "cn_a") == "tushare"

    def test_unknown_category_returns_none(self, patched_config):
        assert iface.get_vendor("does_not_exist", None, "us") is None


@pytest.mark.unit
class TestInferMarket:
    def test_ticker_first_method_cn(self, patched_config):
        assert iface._infer_market("get_stock_data", ("600519.SH",)) == "cn_a"
        assert iface._infer_market("get_fundamentals", ("000001",)) == "cn_a"

    def test_ticker_first_method_us(self, patched_config):
        assert iface._infer_market("get_stock_data", ("SPY",)) == "us"

    def test_non_ticker_method_uses_default_market(self, patched_config):
        # get_global_news is NOT in _TICKER_FIRST_METHODS
        assert iface._infer_market("get_global_news", ()) == "us"
        patched_config["default_market"] = "cn_a"
        assert iface._infer_market("get_global_news", ()) == "cn_a"

    def test_empty_args_falls_back_to_default(self, patched_config):
        assert iface._infer_market("get_stock_data", ()) == "us"
        patched_config["default_market"] = "cn_a"
        assert iface._infer_market("get_stock_data", ()) == "cn_a"

    def test_non_string_first_arg_falls_back(self, patched_config):
        assert iface._infer_market("get_stock_data", (12345,)) == "us"

    def test_explicit_market_wins_for_non_ticker_tool(self, patched_config):
        assert iface._infer_market("get_macro_calendar", ("2026-06-26",), "cn_a") == "cn_a"


@pytest.mark.unit
class TestRouteToVendor:
    def test_routes_to_primary_vendor(self, patched_config, monkeypatch):
        calls = []

        def yf(*a, **kw):
            calls.append(("yfinance", a, kw))
            return "us-data"

        def ak(*a, **kw):
            calls.append(("akshare", a, kw))
            return "cn-data"

        monkeypatch.setitem(
            iface.VENDOR_METHODS, "get_stock_data",
            {"yfinance": yf, "akshare": ak, "alpha_vantage": yf},
        )

        # US ticker -> yfinance
        assert iface.route_to_vendor("get_stock_data", "SPY") == "us-data"
        # CN ticker -> akshare
        assert iface.route_to_vendor("get_stock_data", "600519.SH") == "cn-data"

        vendors_used = [c[0] for c in calls]
        assert vendors_used == ["yfinance", "akshare"]

    def test_falls_back_on_rate_limit(self, patched_config, monkeypatch):
        def primary(*a, **kw):
            raise AKShareRateLimitError("ak throttle")

        def secondary(*a, **kw):
            return "from-yfinance"

        monkeypatch.setitem(
            iface.VENDOR_METHODS, "get_stock_data",
            {"alpha_vantage": primary, "yfinance": secondary},
        )
        patched_config["data_vendors"]["core_stock_apis"] = {
            "us": "alpha_vantage,yfinance", "cn_a": "akshare",
        }

        assert iface.route_to_vendor("get_stock_data", "SPY") == "from-yfinance"

    def test_falls_back_across_akshare_rate_limit(self, patched_config, monkeypatch):
        def ak_throttled(*a, **kw):
            raise AKShareRateLimitError("ak throttle")

        def yf_ok(*a, **kw):
            return "yf-rescue"

        monkeypatch.setitem(
            iface.VENDOR_METHODS, "get_stock_data",
            {"akshare": ak_throttled, "yfinance": yf_ok},
        )
        # For CN ticker, akshare primary -> throttled.
        # yfinance is now skipped for cn_a market (it only serves US
        # equities), so no fallback is available -> "All vendors".
        with pytest.raises(RuntimeError, match="All vendors rate-limited"):
            iface.route_to_vendor("get_stock_data", "600519")

    def test_non_rate_limit_errors_propagate(self, patched_config, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("unexpected")

        def never_called(*a, **kw):  # pragma: no cover
            raise AssertionError("should not fall back on non-rate-limit errors")

        monkeypatch.setitem(
            iface.VENDOR_METHODS, "get_stock_data",
            {"yfinance": boom, "alpha_vantage": never_called},
        )
        with pytest.raises(RuntimeError, match="unexpected"):
            iface.route_to_vendor("get_stock_data", "SPY")

    def test_all_vendors_rate_limited_raises(self, patched_config, monkeypatch):
        def rl1(*a, **kw):
            raise AKShareRateLimitError("1")

        def rl2(*a, **kw):
            raise AKShareRateLimitError("2")

        monkeypatch.setitem(
            iface.VENDOR_METHODS, "get_stock_data",
            {"alpha_vantage": rl1, "akshare": rl2},
        )
        patched_config["data_vendors"]["core_stock_apis"] = {
            "us": "alpha_vantage", "cn_a": "akshare",
        }
        with pytest.raises(RuntimeError, match="All vendors rate-limited"):
            iface.route_to_vendor("get_stock_data", "SPY")

    def test_unknown_method_raises(self, patched_config):
        with pytest.raises(ValueError, match="not supported"):
            iface.route_to_vendor("not_a_real_method", "SPY")

    def test_explicit_cn_market_routes_cn_macro_calendar(self, patched_config, monkeypatch):
        patched_config["data_vendors"]["macro_data"] = {"us": "fred", "cn_a": "akshare"}

        def ak_macro(*a, **kw):
            return "cn-macro"

        monkeypatch.setitem(
            iface.VENDOR_METHODS, "get_macro_calendar",
            {"akshare": ak_macro},
        )

        assert iface.route_to_vendor("get_macro_calendar", "2026-06-26", market="cn_a") == "cn-macro"

    def test_get_category_for_method_unknown_raises(self):
        with pytest.raises(ValueError):
            iface.get_category_for_method("no_such_method")
