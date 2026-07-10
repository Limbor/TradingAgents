"""Unit tests for A-share microstructure helpers without live AKShare calls."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.dataflows import akshare_cn_specific as cn


@pytest.fixture()
def fake_ak(monkeypatch):
    ak = SimpleNamespace()
    monkeypatch.setattr(cn, "ak_lazy_import", lambda: ak)
    monkeypatch.setattr(cn, "akshare_call", lambda func, *args, **kwargs: func(*args, **kwargs))
    return ak


@pytest.mark.unit
def test_market_structure_snapshot_flags_limit_up(fake_ak):
    fake_ak.stock_zh_a_st_em = lambda: pd.DataFrame({"代码": []})
    fake_ak.stock_zh_a_hist = lambda **kwargs: pd.DataFrame(
        [
            {
                "日期": "2026-06-24",
                "开盘": 21.0,
                "收盘": 23.0,
                "最高": 23.0,
                "最低": 20.9,
                "成交量": 100,
                "成交额": 100000,
                "涨跌幅": 3.0,
                "换手率": 8.0,
                "振幅": 5.0,
            },
            {
                "日期": "2026-06-25",
                "开盘": 25.3,
                "收盘": 25.3,
                "最高": 25.3,
                "最低": 25.3,
                "成交量": 120,
                "成交额": 120000,
                "涨跌幅": 10.0,
                "换手率": 10.0,
                "振幅": 0.0,
            },
        ]
    )
    fake_ak.stock_zt_pool_em = lambda date: pd.DataFrame({"代码": ["600667"], "名称": ["太极实业"]})

    out = cn.get_market_structure_snapshot("600667", "2026-06-25")

    assert "LIMIT_UP_OR_NEAR_LIMIT_UP" in out
    assert "T+1" in out
    assert "600667.SH" in out


@pytest.mark.unit
def test_theme_heat_includes_hot_rank_and_boards(fake_ak):
    fake_ak.stock_hot_rank_em = lambda: pd.DataFrame(
        {"当前排名": [4], "代码": ["SH600667"], "股票名称": ["太极实业"]}
    )
    fake_ak.stock_board_concept_name_em = lambda: pd.DataFrame(
        {"板块名称": ["先进封装"], "涨跌幅": [5.5]}
    )
    fake_ak.stock_board_industry_name_em = lambda: pd.DataFrame(
        {"板块名称": ["半导体"], "涨跌幅": [4.2]}
    )

    out = cn.get_theme_heat("600667", "2026-06-25", top_n=5)

    assert "Retail heat rank" in out
    assert "先进封装" in out
    assert "半导体" in out


@pytest.mark.unit
def test_theme_heat_uses_eastmoney_direct_fallback(fake_ak, monkeypatch):
    fake_ak.stock_hot_rank_em = lambda: (_ for _ in ()).throw(RuntimeError("proxy failed"))
    fake_ak.stock_board_concept_name_em = lambda: (_ for _ in ()).throw(RuntimeError("akshare wrapper failed"))
    fake_ak.stock_board_industry_name_em = lambda: pd.DataFrame({"板块名称": ["半导体"], "涨跌幅": [4.2]})

    def fake_hot_fallback():
        return pd.DataFrame(
            {
                "当前排名": [4],
                "代码": ["SH600667"],
                "股票名称": ["太极实业"],
                "source": ["eastmoney_direct:hotrank"],
            }
        )

    def fake_board_fallback(fn_name, top_n):
        if fn_name == "stock_board_concept_name_em":
            return pd.DataFrame(
                {
                    "类型": ["概念板块"],
                    "板块代码": ["BK1234"],
                    "板块名称": ["机器人"],
                    "source": ["eastmoney_direct:test"],
                }
            )
        return None

    monkeypatch.setattr(cn, "_eastmoney_hot_rank_fallback", fake_hot_fallback)
    monkeypatch.setattr(cn, "_eastmoney_board_heat_fallback", fake_board_fallback)

    out = cn.get_theme_heat("600667", "2026-06-25", top_n=5)

    # Hot rank is now recovered via direct fallback instead of being "unavailable".
    assert "Retail heat rank" in out
    assert "Eastmoney direct fallback" in out
    assert "太极实业" in out
    assert "Retail heat rank unavailable" not in out
    assert "机器人" in out
    assert "半导体" in out


@pytest.mark.unit
def test_lhb_detail_filters_to_lookback(fake_ak):
    fake_ak.stock_lhb_stock_detail_em = lambda symbol: pd.DataFrame(
        {
            "上榜日": ["2026-06-25", "2026-01-01"],
            "代码": ["600667", "600667"],
            "解读": ["连续涨幅偏离", "旧数据"],
        }
    )

    out = cn.get_lhb_detail("600667", "2026-06-26", look_back_days=10)

    assert "Dragon-Tiger List detail" in out
    assert "连续涨幅偏离" in out
    assert "旧数据" not in out


@pytest.mark.unit
def test_eastmoney_hot_rank_fallback_parses_rank(monkeypatch):
    """The direct fallback must build a filterable DataFrame from the raw
    Eastmoney rank payload even when the price-enrichment step returns nothing,
    so the popularity rank itself is never silently lost."""
    import requests as real_requests

    rank_payload = {"data": [{"sc": "SZ000665", "rk": 1}, {"sc": "SH600667", "rk": 4}]}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def post(self, *args, **kwargs):
            return FakeResp(rank_payload)

        def get(self, *args, **kwargs):
            # Empty diff simulates the push2 quote step failing/returning nothing.
            return FakeResp({"data": {"diff": []}})

    monkeypatch.setattr(real_requests, "Session", FakeSession)

    df = cn._eastmoney_hot_rank_fallback()

    assert df is not None
    assert "代码" in df.columns
    assert "当前排名" in df.columns
    assert "SH600667" in df["代码"].tolist()
    assert df.loc[df["代码"] == "SH600667", "当前排名"].iloc[0] == 4
