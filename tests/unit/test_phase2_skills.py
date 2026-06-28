"""Tests for Phase 2 extension skills."""

import asyncio

from tradingagents.core.persistence import Database
from tradingagents.skills.market_scanner.skill import MarketScannerInput, MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioInput, PortfolioManagementSkill


def test_portfolio_skill_upsert_and_analyze(tmp_path):
    async def run():
        db = Database(tmp_path / "portfolio.db")
        skill = PortfolioManagementSkill()
        events = [
            event
            async for event in skill.execute(
                PortfolioInput(
                    action="upsert",
                    symbol="600519.SH",
                    quantity=10,
                    avg_cost=1500,
                    current_price=1650,
                ),
                {"db": db},
            )
        ]

        assert db.get_holding("600519.SH") is not None
        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["summary"]["holding_count"] == 1
        assert complete.data["summary"]["unrealized_pnl"] == 1500

    asyncio.run(run())


def test_market_scanner_returns_ranked_candidates():
    async def run():
        skill = MarketScannerSkill()
        events = [
            event
            async for event in skill.execute(
                MarketScannerInput(market="cn_a", min_score=60, limit=3),
                {},
            )
        ]

        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        candidates = complete.data["candidates"]
        assert 1 <= len(candidates) <= 3
        assert candidates == sorted(candidates, key=lambda item: item["score"], reverse=True)

    asyncio.run(run())
