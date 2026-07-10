"""Natural language routing for the skill platform."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tradingagents.skills.base import BaseSkill
from tradingagents.skills.registry import SkillRegistry
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.symbol_utils import detect_market

logger = logging.getLogger(__name__)


NAME_TO_TICKER = {
    # A股 — 白酒
    "茅台": "600519.SH", "贵州茅台": "600519.SH",
    "五粮液": "000858.SZ", "泸州老窖": "000568.SZ",
    "山西汾酒": "600809.SH", "洋河": "002304.SZ",
    # A股 — 新能源 / 电池
    "宁德": "300750.SZ", "宁德时代": "300750.SZ", "宁王": "300750.SZ",
    "比亚迪": "002594.SZ",
    "隆基": "601012.SH", "隆基绿能": "601012.SH",
    "阳光电源": "300274.SZ",
    # A股 — 金融
    "平安": "601318.SH", "中国平安": "601318.SH",
    "招商银行": "600036.SH", "招行": "600036.SH",
    "工商银行": "601398.SH", "工行": "601398.SH",
    "中信证券": "600030.SH",
    "东方财富": "300059.SZ",
    # A股 — 有色 / 资源
    "紫金": "601899.SH", "紫金矿业": "601899.SH",
    "中国铝业": "601600.SH",
    "北方稀土": "600111.SH",
    # A股 — 科技 / 半导体
    "中芯国际": "688981.SH",
    "海光": "688041.SH", "海光信息": "688041.SH",
    "中际旭创": "300308.SZ",
    "立讯精密": "002475.SZ", "立讯": "002475.SZ",
    "科大讯飞": "002230.SZ",
    # A股 — 消费 / 医药
    "恒瑞医药": "600276.SH", "恒瑞": "600276.SH",
    "迈瑞医疗": "300760.SZ",
    "伊利": "600887.SH", "伊利股份": "600887.SH",
    "海天味业": "603288.SH",
    # A股 — 制造
    "三一重工": "600031.SH",
    "美的": "000333.SZ", "美的集团": "000333.SZ",
    "格力": "000651.SZ", "格力电器": "000651.SZ",
    # 美股
    "苹果": "AAPL",
    "英伟达": "NVDA",
    "微软": "MSFT",
    "特斯拉": "TSLA",
}


@dataclass
class RouteResult:
    """Resolved route from a chat message."""

    skill: BaseSkill | None
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


class Orchestrator:
    """Route natural language messages to registered skills.

    Dual-layer architecture:
    1. Regex FastPath (0ms) — deterministic pattern matching
    2. LLM Router (200-800ms) — called when regex confidence < 0.8

    The LLM layer is optional and gracefully degrades.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        config: dict[str, Any] | None = None,
        db: Any = None,
        llm_router: Any | None = None,
    ):
        self.registry = registry
        self.config = config or {}
        self.db = db
        self.llm_router = llm_router

    def _resolve_known_ticker(self, text: str) -> str | None:
        """Resolve a stock name in ``text`` to a ticker.

        Checks the hardcoded ``NAME_TO_TICKER`` alias table first (fast path),
        then falls back to a DB lookup over previously-analyzed reports so less
        mainstream names (e.g. "生益科技") still resolve without a hardcoded
        entry. Returns None if nothing matches.
        """
        ticker = _extract_known_name(text)
        if ticker:
            return ticker
        if self.db is not None:
            try:
                result = self.db.search_ticker_by_name(text)
                if result:
                    return result
            except Exception:
                pass  # name lookup is best-effort; never break routing
        return None

    async def route(
        self,
        user_message: str,
        session_id: str = "default",
        context: dict[str, Any] | None = None,
    ) -> RouteResult:
        """Route a user message to the best matching skill.

        Strategy:
        1. Try regex fast path first
        2. If confidence >= 0.8, return immediately
        3. If confidence < 0.8 and LLM router available, try LLM
        4. Use whichever result has higher confidence
        5. On LLM failure, fall back to regex result

        ``context`` is an optional side-channel for structured data the text
        itself cannot carry — e.g. the selection plan to hand off to
        ``stock_analysis`` when the user clicks "分析" on a candidate. It is
        merged into the routed skill's params by :meth:`_with_analysis_context`.
        """
        regex_result = await self._regex_route(user_message)

        # High-confidence regex match — skip LLM
        if regex_result.confidence >= 0.8:
            return self._with_analysis_context(regex_result, context)

        # Try LLM router if available
        if self.llm_router is not None:
            try:
                llm_result = await self.llm_router.route(user_message, session_id=session_id)
                if llm_result.skill_id and llm_result.confidence > regex_result.confidence:
                    # Convert LLM RouteResult to orchestrator RouteResult
                    skill = self.registry.get(llm_result.skill_id)
                    if skill is not None:
                        return self._with_analysis_context(
                            RouteResult(
                                skill=skill,
                                params=llm_result.params,
                                confidence=llm_result.confidence,
                                reason=llm_result.reason,
                            ),
                            context,
                        )
            except Exception as exc:
                logger.debug("LLM router fallback to regex: %s", exc)

        return self._with_analysis_context(regex_result, context)

    def _with_analysis_context(
        self,
        result: RouteResult,
        context: dict[str, Any] | None,
    ) -> RouteResult:
        """Merge selection + holding context into stock_analysis params.

        When the user clicks "分析" on a candidate (selection results) the
        frontend sends the candidate's already-structured plan (entry/stop/
        targets/conditions/reasoning) as ``context.selection_context``; when
        they click "分析" on a holding (portfolio UI) it sends the position's
        raw fields (quantity/avg_cost/current_price) as
        ``context.holding_context``. Injecting either here lets the
        multi-agent graph see the relevant prior conclusion / current
        position instead of re-analyzing from scratch and potentially
        contradicting it.
        """
        if not context or result.skill is None:
            return result
        if result.skill.metadata.id != "stock_analysis":
            return result
        selection = context.get("selection_context")
        if isinstance(selection, dict) and selection:
            result.params["selection_context"] = selection
        holding = context.get("holding_context")
        if isinstance(holding, dict) and holding:
            result.params["holding_context"] = holding
        return result

    async def _regex_route(self, user_message: str) -> RouteResult:
        """Deterministic regex-based routing (the original fast path)."""
        text = user_message.strip()
        lowered = text.lower()

        if _contains_any(lowered, ["收盘复盘", "每日复盘", "次日计划", "daily review", "after close"]):
            return self._route_daily_review(text)

        if _contains_any(lowered, ["每日选股", "早报", "今日机会", "daily pipeline", "每日扫描"]):
            return self._route_daily_pipeline(text)

        if _contains_any(lowered, ["风险监控", "持仓风险", "风险扫描", "预警", "risk monitor"]):
            return self._route_risk_monitor(text)

        if _has_analysis_intent(lowered) and (_extract_ticker(text) or self._resolve_known_ticker(text)):
            return self._route_stock_analysis(text)

        if _contains_any(lowered, ["持仓", "仓位", "组合", "portfolio", "holding", "position", "rebalance"]):
            return self._route_portfolio(text)

        if _contains_any(lowered, ["筛选", "选股", "扫描", "机会", "scanner", "scan", "找股票"]):
            return self._route_scanner(text)

        if _contains_any(lowered, ["分析", "看看", "研报", "怎么样", "analyze", "report", "stock"]):
            return self._route_stock_analysis(text)

        ticker = _extract_ticker(text)
        if ticker:
            return self._route_stock_analysis(text)

        matches = self.registry.find_by_trigger(text)
        if len(matches) == 1:
            return RouteResult(matches[0], {}, 0.55, "Matched a skill trigger")

        return RouteResult(None, {}, 0.0, "No matching skill")

    def _route_stock_analysis(self, text: str) -> RouteResult:
        skill = self.registry.get("stock_analysis")
        ticker = _extract_ticker(text) or self._resolve_known_ticker(text)
        if not ticker:
            # No ticker/name resolved — do NOT silently fall back to a hardcoded
            # default (previously 600519.SH / 贵州茅台). Return low confidence so
            # the LLM router can take over or the frontend can ask the user to
            # clarify the target.
            return RouteResult(
                None,
                {},
                0.0,
                "Stock analysis intent but no ticker/name found; needs clarification",
            )
        market = detect_market(ticker)
        try:
            from tradingagents.core.portfolio_prices import resolve_portfolio_name
            ticker_name = resolve_portfolio_name(ticker)
        except Exception:
            ticker_name = ticker
        params = {
            "ticker": ticker,
            "ticker_name": ticker_name,
            "analysis_date": _extract_date(text) or get_temporal_context(self.config, market=market).market_asof_date,
        }
        # Inject recent reflection context if DB is available
        if self.db is not None:
            try:
                reflections = self.db.list_reflections(ticker=ticker, limit=3)
                if reflections:
                    params["reflection_context"] = [
                        {
                            "trade_date": r.get("trade_date"),
                            "decision": r.get("original_decision"),
                            "actual_return": r.get("actual_return"),
                            "was_correct": r.get("was_correct"),
                            "reflection": r.get("reflection_text"),
                        }
                        for r in reflections
                    ]
            except Exception:
                pass  # reflection context is optional; don't break routing
        return RouteResult(skill, params, 0.86, "Matched stock analysis intent")

    def _route_portfolio(self, text: str) -> RouteResult:
        skill = self.registry.get("portfolio_management")
        lowered = text.lower()
        action = "analyze"
        if _contains_any(lowered, ["添加", "新增", "更新", "买入", "add", "update", "upsert"]):
            action = "upsert"
        elif _contains_any(lowered, ["删除", "移除", "清掉", "delete", "remove"]):
            action = "delete"
        elif _contains_any(lowered, ["列出", "查看", "list", "show"]):
            action = "list"

        params: dict[str, Any] = {"action": action}
        ticker = _extract_ticker(text) or self._resolve_known_ticker(text)
        if ticker:
            params["symbol"] = ticker

        numeric_text = re.sub(r"\b\d{6}\.(?:SH|SZ|SS|BJ)\b", " ", text.upper())
        numeric_text = re.sub(r"(?<!\d)\d{6}(?!\d)", " ", numeric_text)
        # Strip dates (e.g. 2024-01-08 / 2024/1/8 / 2024.1.8) before extracting
        # numbers, otherwise they get parsed as quantity/avg_cost/current_price.
        numeric_text = re.sub(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b", " ", numeric_text)
        numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", numeric_text)]
        if action == "upsert":
            if "symbol" not in params:
                # upsert requires a target symbol — do NOT default to a hardcoded
                # ticker. Ask the user to clarify which stock to add/update.
                return RouteResult(
                    None,
                    {},
                    0.0,
                    "Portfolio upsert needs a symbol; needs clarification",
                )
            if numbers:
                params["quantity"] = numbers[0]
            if len(numbers) >= 2:
                params["avg_cost"] = numbers[1]
            if len(numbers) >= 3:
                params["current_price"] = numbers[2]
            params.setdefault("quantity", 100)
            params.setdefault("avg_cost", 100.0)

        return RouteResult(skill, params, 0.82, "Matched portfolio intent")

    def _route_scanner(self, text: str) -> RouteResult:
        skill = self.registry.get("market_scanner")
        market = "us" if _contains_any(text.lower(), ["美股", "us", "nasdaq", "s&p"]) else "cn_a"
        limit_match = re.search(r"(?:top|前)\s*(\d+)", text.lower())
        score_match = re.search(r"(?:score|分数|评分)[^\d]*(\d+)", text.lower())
        params: dict[str, Any] = {"market": market}
        if limit_match:
            params["limit"] = min(max(int(limit_match.group(1)), 1), 20)
        if score_match:
            params["min_score"] = min(max(int(score_match.group(1)), 0), 100)
        for theme in ("白酒", "新能源", "有色", "AI", "Cloud", "消费", "化工"):
            if theme.lower() in text.lower():
                params["theme"] = theme
                break
        return RouteResult(skill, params, 0.84, "Matched market scanner intent")

    def _route_daily_pipeline(self, text: str) -> RouteResult:
        skill = self.registry.get("daily_pipeline")
        params: dict[str, Any] = {}
        date_value = _extract_date(text)
        if date_value:
            params["trade_date"] = date_value
        limit_match = re.search(r"(?:top|前)\s*(\d+)", text.lower())
        if limit_match:
            params["limit"] = min(max(int(limit_match.group(1)), 1), 20)
        if _contains_any(text, ["非双创", "排除双创", "不要双创", "主板", "非科创", "非创业", "排除科创", "排除创业"]):
            params["board_filter"] = "main_board"
        elif _contains_any(text, ["只看双创", "双创板", "科创创业", "科创板和创业板"]):
            params["board_filter"] = "dual_growth_only"
        return RouteResult(skill, params, 0.86, "Matched daily pipeline intent")

    def _route_risk_monitor(self, text: str) -> RouteResult:
        skill = self.registry.get("risk_monitor")
        params: dict[str, Any] = {}
        days_match = re.search(r"(\d+)\s*(?:天|days?)", text.lower())
        if days_match:
            params["lookback_days"] = min(max(int(days_match.group(1)), 1), 365)
        return RouteResult(skill, params, 0.86, "Matched risk monitor intent")

    def _route_daily_review(self, text: str) -> RouteResult:
        skill = self.registry.get("daily_review")
        params: dict[str, Any] = {}
        date_value = _extract_date(text)
        if date_value:
            params["trade_date"] = date_value
        limit_match = re.search(r"(?:top|前)\s*(\d+)", text.lower())
        if limit_match:
            params["daily_limit"] = min(max(int(limit_match.group(1)), 1), 20)
        if _contains_any(text, ["非双创", "排除双创", "不要双创", "主板", "非科创", "非创业"]):
            params["board_filter"] = "main_board"
        elif _contains_any(text, ["只看双创", "双创板", "科创创业"]):
            params["board_filter"] = "dual_growth_only"
        return RouteResult(skill, params, 0.9, "Matched daily review intent")


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _has_analysis_intent(text: str) -> bool:
    return _contains_any(text, ["分析", "看看", "研报", "怎么样", "analyze", "report", "stock"])


def _extract_ticker(text: str) -> str | None:
    upper = text.upper()
    explicit = re.search(r"\b\d{6}\.(?:SH|SZ|SS|BJ)\b|\b[A-Z]{1,5}\b", upper)
    if explicit:
        ticker = explicit.group(0)
        if ticker in {"A", "AI", "US", "ETF"}:
            return None
        return ticker.replace(".SS", ".SH")

    bare_cn = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if bare_cn:
        digits = bare_cn.group(1)
        if digits.startswith("6"):
            return f"{digits}.SH"
        if digits.startswith(("0", "3")):
            return f"{digits}.SZ"
        if digits.startswith(("4", "8")):
            return f"{digits}.BJ"
    return None


def _extract_known_name(text: str) -> str | None:
    for name, ticker in NAME_TO_TICKER.items():
        if name in text:
            return ticker
    return None


def _extract_date(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"
