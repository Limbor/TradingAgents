"""Market scanner skill.

Ranks a small deterministic universe with transparent scoring. The scanner is
usable offline and can later be backed by AKShare/yfinance screeners without
changing the Skill interface.
"""

from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class MarketScannerInput(BaseModel):
    """Input for market scanning."""

    market: Literal["cn_a", "us"] = Field(default="cn_a")
    theme: str | None = Field(default=None, description="Optional sector/theme filter")
    min_score: int = Field(default=65, ge=0, le=100)
    limit: int = Field(default=5, ge=1, le=20)


class MarketScannerOutput(BaseModel):
    """Scanner output."""

    market: str
    candidates: list[dict[str, Any]]


class MarketScannerSkill(BaseSkill):
    """Screen equities and emit ranked candidates with AI-ready rationale."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="market_scanner",
            name="Market Scanner",
            description=(
                "Screen A-share or US market candidates by momentum, liquidity, "
                "quality, and risk-adjusted setup score."
            ),
            version="1.0.0",
            triggers=["scanner", "scan", "筛选", "选股", "找股票", "市场扫描", "机会"],
            icon="radar",
            category="scanner",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MarketScannerInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return MarketScannerOutput

    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        input_params: MarketScannerInput = params
        yield SkillEvent(
            event_type="skill_start",
            data={"skill_id": self.metadata.id, "market": input_params.market},
        )

        universe = _UNIVERSE[input_params.market]
        filtered = [
            item
            for item in universe
            if not input_params.theme
            or input_params.theme.lower() in item["theme"].lower()
            or input_params.theme in item["name"]
        ]
        scored = sorted(
            (_score_candidate(item) for item in filtered),
            key=lambda item: item["score"],
            reverse=True,
        )
        candidates = [
            item for item in scored if item["score"] >= input_params.min_score
        ][: input_params.limit]

        yield SkillEvent(
            event_type="scanner_candidates",
            data={"market": input_params.market, "candidates": candidates},
        )
        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "scanner_report",
                "content": _render_report(input_params.market, candidates),
                "is_final": True,
            },
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "market": input_params.market,
                "candidates": candidates,
            },
        )

    async def cancel(self) -> None:
        return None


_UNIVERSE: dict[str, list[dict[str, Any]]] = {
    "cn_a": [
        {"symbol": "600519.SH", "name": "贵州茅台", "theme": "消费 白酒", "momentum": 72, "liquidity": 92, "quality": 96, "risk": 28},
        {"symbol": "601899.SH", "name": "紫金矿业", "theme": "有色 黄金 铜", "momentum": 69, "liquidity": 90, "quality": 78, "risk": 42},
        {"symbol": "300750.SZ", "name": "宁德时代", "theme": "新能源 电池", "momentum": 76, "liquidity": 94, "quality": 86, "risk": 45},
        {"symbol": "000858.SZ", "name": "五粮液", "theme": "消费 白酒", "momentum": 58, "liquidity": 82, "quality": 84, "risk": 34},
        {"symbol": "605589.SH", "name": "圣泉集团", "theme": "化工 新材料", "momentum": 83, "liquidity": 66, "quality": 64, "risk": 62},
        {"symbol": "002203.SZ", "name": "海亮股份", "theme": "有色 铜加工", "momentum": 61, "liquidity": 58, "quality": 62, "risk": 38},
    ],
    "us": [
        {"symbol": "NVDA", "name": "NVIDIA", "theme": "AI Semiconductors", "momentum": 86, "liquidity": 98, "quality": 91, "risk": 55},
        {"symbol": "MSFT", "name": "Microsoft", "theme": "Cloud AI", "momentum": 70, "liquidity": 96, "quality": 93, "risk": 24},
        {"symbol": "AAPL", "name": "Apple", "theme": "Consumer Hardware", "momentum": 54, "liquidity": 95, "quality": 88, "risk": 22},
        {"symbol": "META", "name": "Meta Platforms", "theme": "AI Advertising", "momentum": 78, "liquidity": 91, "quality": 84, "risk": 39},
        {"symbol": "TSLA", "name": "Tesla", "theme": "EV Robotics", "momentum": 67, "liquidity": 94, "quality": 62, "risk": 70},
    ],
}


def _score_candidate(item: dict[str, Any]) -> dict[str, Any]:
    score = (
        item["momentum"] * 0.35
        + item["liquidity"] * 0.25
        + item["quality"] * 0.30
        - item["risk"] * 0.10
    )
    setup = "trend_follow"
    if item["risk"] >= 60:
        setup = "high_beta_watch"
    elif item["quality"] >= 90 and item["risk"] <= 30:
        setup = "core_quality"
    return {
        **item,
        "score": round(score, 1),
        "setup": setup,
        "rationale": (
            f"Momentum {item['momentum']}, liquidity {item['liquidity']}, "
            f"quality {item['quality']}, risk {item['risk']}."
        ),
    }


def _render_report(market: str, candidates: list[dict[str, Any]]) -> str:
    title = "A-share Market Scanner" if market == "cn_a" else "US Market Scanner"
    if not candidates:
        return f"## {title}\n\nNo candidates passed the current score threshold."
    lines = [
        f"## {title}",
        "",
        "| Rank | Symbol | Name | Score | Setup | Theme |",
        "|---:|---|---|---:|---|---|",
    ]
    for idx, item in enumerate(candidates, start=1):
        lines.append(
            f"| {idx} | {item['symbol']} | {item['name']} | {item['score']} | {item['setup']} | {item['theme']} |"
        )
    lines.extend(
        [
            "",
            "### Execution Notes",
            "",
            "- Treat scanner output as a research queue, not an automatic trade list.",
            "- Confirm company-specific news, liquidity, and market regime before execution.",
            "- For A-shares, check limit-up/down status and T+1 constraints before placing orders.",
        ]
    )
    return "\n".join(lines)


skill = MarketScannerSkill()
