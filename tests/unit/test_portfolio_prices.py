import asyncio
from datetime import date

import tradingagents.core.portfolio_prices as portfolio_prices


def test_latest_close_from_mcp_uses_compact_dates(monkeypatch):
    calls = []

    class FakeClient:
        async def get_stock_daily(self, ts_codes, start_date, end_date, adj_type="qfq"):
            calls.append((ts_codes, start_date, end_date, adj_type))
            return {
                "rows": [
                    {"ts_code": "600519.SH", "trade_date": "20260703", "close": 1688.0},
                ]
            }

    async def fake_get_mcp_client(config):
        return FakeClient()

    monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client", fake_get_mcp_client)
    quote = asyncio.run(portfolio_prices._latest_close_from_mcp("600519.SH", {}, date(2026, 7, 3)))

    assert quote["close"] == 1688.0
    assert calls[0][1].isdigit()
    assert calls[0][2].isdigit()


def test_latest_row_from_symbol_grouped_payload():
    row = portfolio_prices._latest_row_from_payload(
        {
            "data": {
                "600519.SH": [
                    {"trade_date": "20260702", "close": 1660},
                    {"trade_date": "20260703", "close": 1688},
                ]
            }
        },
        "600519.SH",
    )

    assert row["close"] == 1688
