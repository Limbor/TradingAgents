"""Market scanner skill backed by StockManager quant ranking."""

from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate, payload_rows, payload_warnings
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress

FACTOR_DATA_SOURCE = "stockmanager_mcp"


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
    mcp_used: bool
    warnings: list[str] = Field(default_factory=list)


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
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备扫描",
            status="completed",
            detail=f"{input_params.market} · top {input_params.limit} · min score {input_params.min_score}",
            progress_pct=5,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Market Scanner",
                "status": f"正在扫描 {'A股' if input_params.market == 'cn_a' else '美股'} 候选池",
            },
        )
        yield skill_progress(
            stage_id="quant_rank",
            stage_label="量化候选池排名",
            status="running",
            detail="正在读取市场候选池并计算因子排名",
            agent="Market Scanner",
            progress_pct=25,
        )

        candidates, mcp_used, warnings, quant_meta = await _rank_candidates(input_params, config)
        yield skill_progress(
            stage_id="quant_rank",
            stage_label="量化候选池排名",
            status="completed",
            detail=f"获取 {len(candidates)} 个原始候选",
            agent="Market Scanner",
            progress_pct=55,
        )
        candidates = _apply_theme_and_score_filters(candidates, input_params)

        if warnings:
            yield SkillEvent(
                event_type="agent_status",
                data={"agent": "Market Scanner", "status": "；".join(warnings)},
            )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Market Scanner",
                "status": f"正在按最低分 {input_params.min_score} 过滤并排序候选标的",
            },
        )
        yield skill_progress(
            stage_id="filter_sort",
            stage_label="过滤与排序",
            status="completed",
            detail=f"输出 {len(candidates)} 个候选标的",
            agent="Market Scanner",
            progress_pct=75,
        )

        yield SkillEvent(
            event_type="scanner_candidates",
            data={
                "market": input_params.market,
                "candidates": candidates,
                "mcp_used": mcp_used,
                "warnings": warnings,
                "quant_meta": quant_meta,
            },
        )
        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "scanner_report",
                "content": _render_report(input_params.market, candidates, mcp_used, warnings),
                "is_final": True,
            },
        )
        yield skill_progress(
            stage_id="report",
            stage_label="生成扫描报告",
            status="completed",
            detail="候选表和可读报告已生成",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "market": input_params.market,
                "candidates": candidates,
                "mcp_used": mcp_used,
                "warnings": warnings,
                "quant_meta": quant_meta,
            },
        )

    async def cancel(self) -> None:
        return None


async def _rank_candidates(
    input_params: MarketScannerInput,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, list[str], dict[str, Any]]:
    if input_params.market == "us":
        candidates = [_score_us_sample(item) for item in _US_SAMPLE_UNIVERSE]
        return candidates, False, ["US scanner currently uses a local sample universe."], {"source": "us_sample"}

    client = await get_mcp_client(config)
    if client is None:
        if config.get("market_scanner_demo_fallback", False):
            return _demo_candidates(), False, ["StockManager MCP 不可用，当前为显式 demo fallback。"], {"source": "demo_fallback"}
        return [], False, ["StockManager MCP 不可用，已停止真实 A 股筛选。"], {"source": "unavailable"}

    payload = await client.rank_factor_candidates(
        universe_index=str(config.get("market_scanner_universe_index") or "000906.SH"),
        trade_date=str(config.get("market_scanner_trade_date") or _today_iso()),
        style=str(config.get("investment_style") or "medium_term"),
        limit=max(input_params.limit, 20),
        candidate_limit=int(config.get("market_scanner_candidate_limit", 120)),
        factor_profile=_factor_profile_for_style(str(config.get("investment_style") or "medium_term")),
        filters=_default_filters(),
        return_factor_snapshot=True,
    )
    warnings = payload_warnings(payload)
    rows = payload_rows(payload)
    status = (payload or {}).get("status")
    if status == "error" or not rows:
        message = (payload or {}).get("message") or "StockManager returned no ranked candidates."
        return [], True, [*warnings, str(message)], _quant_meta(payload)

    candidates = []
    for row in rows:
        candidate = normalize_quant_candidate(row)
        candidate.update(fuse_candidate_signal(candidate, str(config.get("investment_style") or "medium_term")))
        candidate["setup"] = _setup_from_candidate(candidate)
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = _candidate_rationale(candidate)
        candidates.append(candidate)
    return candidates, True, warnings, _quant_meta(payload)


def _get_score(item: dict[str, Any]) -> float:
    return float(item.get("score") or item.get("quant_score") or 0)


def _apply_theme_and_score_filters(
    candidates: list[dict[str, Any]],
    input_params: MarketScannerInput,
) -> list[dict[str, Any]]:
    filtered = [
        item
        for item in candidates
        if not input_params.theme
        or input_params.theme.lower() in str(item.get("theme", "")).lower()
        or input_params.theme in str(item.get("name", ""))
        or input_params.theme in str(item.get("industry", ""))
    ]
    scored = sorted(filtered, key=_get_score, reverse=True)
    return [item for item in scored if _get_score(item) >= input_params.min_score][: input_params.limit]


def _demo_candidates() -> list[dict[str, Any]]:
    rows = [
        {
            "ts_code": "600519.SH", "name": "贵州茅台", "industry": "消费 白酒",
            "quant_score": 82.0, "factor_scores": {"momentum": 72, "liquidity": 92, "quality": 96, "risk_control": 85},
            "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
            "risk_flags": [], "score_explain": ["demo quality strong", "demo liquidity strong"],
        },
        {
            "ts_code": "300750.SZ", "name": "宁德时代", "industry": "新能源 电池",
            "quant_score": 76.5, "factor_scores": {"momentum": 78, "liquidity": 91, "quality": 82, "risk_control": 71},
            "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
            "risk_flags": [], "score_explain": ["demo momentum strong"],
        },
        {
            "ts_code": "601899.SH", "name": "紫金矿业", "industry": "有色 黄金 铜",
            "quant_score": 71.0, "factor_scores": {"momentum": 74, "liquidity": 88, "quality": 73, "risk_control": 68},
            "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
            "risk_flags": [], "score_explain": ["demo trend above average"],
        },
    ]
    candidates = []
    for row in rows:
        candidate = normalize_quant_candidate(row)
        candidate.update(fuse_candidate_signal(candidate, "medium_term"))
        candidate["setup"] = _setup_from_candidate(candidate)
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = _candidate_rationale(candidate)
        candidates.append(candidate)
    return candidates


_US_SAMPLE_UNIVERSE: list[dict[str, Any]] = [
    {"symbol": "NVDA", "name": "NVIDIA", "theme": "AI Semiconductors", "momentum": 86, "liquidity": 98, "quality": 91, "risk": 55},
    {"symbol": "MSFT", "name": "Microsoft", "theme": "Cloud AI", "momentum": 70, "liquidity": 96, "quality": 93, "risk": 24},
    {"symbol": "AAPL", "name": "Apple", "theme": "Consumer Hardware", "momentum": 54, "liquidity": 95, "quality": 88, "risk": 22},
    {"symbol": "META", "name": "Meta Platforms", "theme": "AI Advertising", "momentum": 78, "liquidity": 91, "quality": 84, "risk": 39},
    {"symbol": "TSLA", "name": "Tesla", "theme": "EV Robotics", "momentum": 67, "liquidity": 94, "quality": 62, "risk": 70},
]


def _score_us_sample(item: dict[str, Any]) -> dict[str, Any]:
    score = (
        item["momentum"] * 0.35
        + item["liquidity"] * 0.25
        + item["quality"] * 0.30
        - item["risk"] * 0.10
    )
    candidate = {
        **item,
        "score": round(score, 1),
        "quant_score": round(score, 1),
        "factor_data_source": "us_sample",
        "factor_scores": {
            "momentum": item["momentum"],
            "liquidity": item["liquidity"],
            "quality": item["quality"],
            "risk_control": max(0, 100 - item["risk"]),
        },
        "tradability": {"is_tradable": True},
        "risk_flags": [],
        "score_explain": ["local US sample universe"],
    }
    candidate.update(fuse_candidate_signal(candidate, "medium_term"))
    candidate["setup"] = _setup_from_candidate(candidate)
    candidate["rationale"] = _candidate_rationale(candidate)
    return candidate


def _quant_meta(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    return {
        "source": FACTOR_DATA_SOURCE if payload else "unavailable",
        "status": payload.get("status"),
        "method": payload.get("method"),
        "as_of_date": payload.get("as_of_date"),
        "factor_profile": payload.get("factor_profile"),
        "universe": payload.get("universe") or {},
    }


def _candidate_rationale(candidate: dict[str, Any]) -> str:
    explains = candidate.get("score_explain") or []
    if explains:
        return "; ".join(str(item) for item in explains)
    fs = candidate.get("factor_scores") if isinstance(candidate.get("factor_scores"), dict) else {}
    if fs:
        return ", ".join(f"{key} {value}" for key, value in fs.items())
    return "StockManager quant ranking candidate."


def _setup_from_candidate(candidate: dict[str, Any]) -> str:
    if candidate.get("signal") == "BUY":
        return "quant_buy"
    if candidate.get("risk_flags"):
        return "risk_watch"
    return "watchlist"


def _factor_profile_for_style(style: str) -> str:
    if style == "short_term":
        return "short_term_momentum"
    if style == "long_term":
        return "long_term_quality"
    return "medium_term_balanced"


def _default_filters() -> dict[str, Any]:
    return {
        "exclude_st": True,
        "exclude_suspended": True,
        "exclude_one_price_limit": True,
        "min_amount_20d": 0,
    }


def _today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def _render_report(
    market: str,
    candidates: list[dict[str, Any]],
    mcp_used: bool,
    warnings: list[str],
) -> str:
    title = "A-share Market Scanner" if market == "cn_a" else "US Market Scanner"
    if not candidates:
        warning_text = "\n".join(f"- {warning}" for warning in warnings)
        return (
            f"## {title}\n\n"
            f"- Factor source: **{FACTOR_DATA_SOURCE if mcp_used else 'unavailable/sample'}**\n\n"
            f"No candidates passed the current score threshold.\n\n{warning_text}"
        )
    lines = [
        f"## {title}",
        "",
        f"- Source: **{'StockManager MCP quant ranking' if mcp_used else 'local sample/demo'}**",
        f"- Factor source: **{FACTOR_DATA_SOURCE if mcp_used else 'sample/demo'}**",
        *([f"- Warning: {warning}" for warning in warnings] if warnings else []),
        "",
        "| Rank | Symbol | Name | Signal | Final | Quant | Setup | Theme |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for idx, item in enumerate(candidates, start=1):
        lines.append(
            f"| {idx} | {item.get('symbol', '?')} | {item.get('name', '?')} | {item.get('signal', '')} | "
            f"{item.get('final_score', item.get('score', ''))} | {item.get('quant_score', item.get('score', ''))} | "
            f"{item.get('setup', '')} | {item.get('theme', '')} |"
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
